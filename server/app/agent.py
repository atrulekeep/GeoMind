"""GeoMind Agent：LangGraph 状态机 + 流式事件

图结构：
  START → plan（LLM 决策：直接回答 or 发工具调用）
            ├─ 有 tool_calls 且未超步数 → execute（顺序执行工具，结果回喂）→ plan
            └─ 无 tool_calls / 超步数 → END

agent_events() 把节点执行过程以事件流对外暴露，SSE 端点直接消费：
  status / tool_call / tool_result / text_delta / done / error
"""

import asyncio
import json
import uuid
from typing import Any, AsyncIterator

import httpx
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from typing_extensions import TypedDict

from .config import Settings
from .registry import DatasetRegistry
from .session import Session
from .tools import TOOL_SPECS, SceneStore, dispatch

MAX_STEPS = 12
APPROVAL_TIMEOUT = 180

# runId → 等待用户审批结果的 future（main.py 的 /api/approval 写入）
APPROVALS: dict[str, asyncio.Future[dict[str, Any]]] = {}

# 需要人工确认的破坏性操作
def _needs_approval(name: str, args: dict[str, Any]) -> dict[str, Any] | None:
    if name == 'render_scene' and args.get('replaceScene'):
        return {
            'kind': 'replace_scene',
            'tool': name,
            'summary': '将清空当前全部图层、底图与地形，并按新描述全量重建场景',
        }
    return None

SYSTEM_PROMPT = """你是 GeoMind，一个三维 GIS 智能体，通过调用本地工具完成空间分析并生成三维场景。

工作原则：
1. 先调用 list_datasets 了解可用数据集，不确定字段时用 describe_dataset 查看；不要臆测数据集 id、字段名或坐标。
2. 需要"每个区域内点的数量"时，调用 aggregate_points_by_region。
3. 出图只能通过 render_scene：
   - 三维柱状图：kind=polygon-extrusion。指标优先用 metricField 直读数据集自带字段（如 beijing_district_gdp 的 gdp2023，
     服务端读取并返回 metricSummary 真实数值，回复中引用，禁止自己转述或编造数字）；
     聚合结果则放入 metricValues（metricGroupField 与聚合 groupField 一致，metricName 如 hospital_count），
     heightScale 取一个让柱体清晰可见的值（计数为个位数~几十时可取 300~1000）；
   - 平面填色：kind=polygon-fill；点位：kind=points；
   - 文字标注：kind=labels，labelFields 列出要显示的字段（如 ["name", "gdp2023"] 拼接为"区名 数值"），
     labelUnit 给数值追加单位（GDP 场景 labelUnit='亿元' → "东城区 3574.3 亿元"）；
     要在柱顶显示文字时，labels 图层与柱图层用同一 datasetId、同一指标来源、同一 heightScale，文字自动对齐柱顶；
   - 边界线：kind=polygon-outline，只画区域轮廓（width 像素宽），用户要求"用边界线区分各区/描边"时使用，
     不要用半透明填充面去近似边界线（会与柱体重叠闪烁）；
   - 用户说"热力图/强度分布"时，当前没有栅格热力能力：必须先 aggregate_points_by_region 聚合，
     再用 polygon-extrusion 三维柱表达区级强度差异，并在回复中说明这是三维柱替代，不要只画点和面；
   - 通过 cameraDatasetId 让视角对准数据集。
4. 需要展示地形时设置 terrain=arcgis；可按需用 basemap 切换底图（gaode/osm/arcgis-satellite）。
5. 加载三维模型：kind=gltf-model，modelId 从 list_datasets 返回的 models 中选，nameValue 必须与点数据（如 beijing_landmarks）中的名称完全一致；
   不要凭记忆拼写名称，先 describe_dataset 确认。
6. 场景默认按多轮 patch 合并：同 id 图层被替换、新图层追加、未提及的图层与底图/地形保留；
   只有当用户明确要求"清空/全部换成/不要某类图层"时，才设置 replaceScene=true 全量重建。
   注意：replaceScene 是破坏性操作，系统会暂停并请求用户批准；若被拒绝，改用 merge 方案保留既有图层。
7. 工具返回 error 时，阅读原因并修正参数重试。
8. 遇到不确定的空间分析方法（密度归一化、缓冲区、空间连接含义）或场景参数取值（heightScale、配色、pointSize）时，
   先调用 search_knowledge 检索本地知识库；任务方法明确时不要无谓检索。
9. 坐标与几何由服务端处理，任何参数中都不要出现你自己编造的经纬度；场景成功后用简洁中文总结做法与关键数据结论。"""


class AgentState(TypedDict):
    messages: list[dict[str, Any]]
    traces: list[dict[str, Any]]
    step: int


class AgentRuntimeError(Exception):
    """模型接口等致命错误，立即终止图执行"""


# ---------- 图构建 ----------


def build_graph(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    model: str,
    registry: DatasetRegistry,
    store: SceneStore,
    emit,
    auto_approve: bool = False,
):
    async def plan_node(state: AgentState) -> dict[str, Any]:
        await emit({'type': 'status', 'phase': 'planning', 'step': state['step'] + 1})

        content_parts: list[str] = []
        tool_acc: dict[int, dict[str, Any]] = {}

        request_kwargs = {
            'method': 'POST',
            'url': f'{base_url}/chat/completions',
            'headers': {'Authorization': f'Bearer {api_key}'},
            'json': {
                'model': model,
                'messages': state['messages'],
                'tools': TOOL_SPECS,
                'tool_choice': 'auto',
                'temperature': 0.2,
                'stream': True,
            },
        }
        async with client.stream(**request_kwargs) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode()[:300]
                raise AgentRuntimeError(f'模型接口错误 HTTP {resp.status_code}：{body}')
            async for line in resp.aiter_lines():
                if not line.startswith('data:'):
                    continue
                payload = line[len('data:'):].strip()
                if payload == '[DONE]':
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                delta = chunk.get('choices', [{}])[0].get('delta', {})
                if delta.get('content'):
                    content_parts.append(delta['content'])
                    await emit({'type': 'text_delta', 'delta': delta['content']})
                for piece in delta.get('tool_calls') or []:
                    idx = piece.get('index', 0)
                    slot = tool_acc.setdefault(
                        idx,
                        {
                            'id': piece.get('id', f'call_{idx}'),
                            'type': 'function',
                            'function': {'name': '', 'arguments': ''},
                        },
                    )
                    if piece.get('id'):
                        slot['id'] = piece['id']
                    fn = piece.get('function') or {}
                    if fn.get('name'):
                        slot['function']['name'] += fn['name']
                    if fn.get('arguments'):
                        slot['function']['arguments'] += fn['arguments']

        tool_calls = [tool_acc[i] for i in sorted(tool_acc)]
        assistant: dict[str, Any] = {'role': 'assistant', 'content': ''.join(content_parts)}
        traces = list(state['traces'])
        if tool_calls:
            assistant['tool_calls'] = tool_calls
            for call in tool_calls:
                raw_args = call['function'].get('arguments') or '{}'
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    args = {'_parseError': raw_args[:200]}
                trace = {
                    'callId': call['id'],
                    'name': call['function']['name'],
                    'args': args,
                }
                traces.append(trace)
                await emit({'type': 'tool_call', 'name': trace['name'], 'args': args, 'callId': call['id']})

        return {
            'messages': state['messages'] + [assistant],
            'traces': traces,
            'step': state['step'] + 1,
        }

    async def execute_node(state: AgentState) -> dict[str, Any]:
        await emit({'type': 'status', 'phase': 'executing', 'step': state['step']})
        calls = state['messages'][-1].get('tool_calls') or []
        messages = list(state['messages'])
        traces = [dict(t) for t in state['traces']]

        # HITL 唯一决策点：节点开头对本轮破坏性调用集中审批；
        # interrupt 恢复时节点会从头重放，因此 gate 必须在任何 dispatch 之前
        gated: dict[str, dict[str, Any]] = {}
        decision: dict[str, Any] | None = None
        if not auto_approve:
            for call in calls:
                cargs = next((t['args'] for t in traces if t['callId'] == call['id']), {})
                payload = _needs_approval(call['function']['name'], cargs)
                if payload is not None:
                    gated[call['id']] = payload
        if gated:
            first = next(iter(gated.values()))
            layer_n = len(store.current.layers) if store.current else 0
            decision = interrupt({
                **first,
                'callIds': list(gated.keys()),
                'currentLayerCount': layer_n,
            })

        for call in calls:
            name = call['function']['name']
            args = next((t['args'] for t in traces if t['callId'] == call['id']), {})
            if call['id'] in gated and decision is not None and not decision.get('approved'):
                reason = decision.get('reason') or '用户未批准'
                result = {'error': f'操作被用户拒绝（{reason}）。请改用 merge 增量方案，或放弃该请求并向用户说明。'}
            else:
                result = await dispatch(name, args, registry, store)
            for trace in traces:
                if trace['callId'] == call['id']:
                    trace['result'] = _truncate(result)
            event: dict[str, Any] = {
                'type': 'tool_result',
                'callId': call['id'],
                'name': name,
                'result': _truncate(result),
            }
            if name == 'render_scene' and 'error' not in result:
                event['scene'] = store.current.model_dump() if store.current else None
            await emit(event)
            messages.append(
                {
                    'role': 'tool',
                    'tool_call_id': call['id'],
                    'content': json.dumps(result, ensure_ascii=False)[:8000],
                }
            )
        return {'messages': messages, 'traces': traces}

    def route_after_plan(state: AgentState) -> str:
        last = state['messages'][-1]
        if last.get('tool_calls') and state['step'] < MAX_STEPS:
            return 'execute'
        return END

    graph = StateGraph(AgentState)
    graph.add_node('plan', plan_node)
    graph.add_node('execute', execute_node)
    graph.add_edge(START, 'plan')
    graph.add_conditional_edges('plan', route_after_plan, {'execute': 'execute', END: END})
    graph.add_edge('execute', 'plan')
    # interrupt 必须有 checkpointer；thread_id 即 runId（agent_events 生成）
    return graph.compile(checkpointer=MemorySaver())


# ---------- 对外：流式事件 / 兼容封装 ----------


async def agent_events(
    message: str,
    settings: Settings,
    registry: DatasetRegistry,
    session: Session,
    auto_approve: bool = False,
) -> AsyncIterator[dict[str, Any]]:
    endpoint = settings.endpoint()
    missing_key = endpoint.provider == 'deepseek' and not settings.deepseek_api_key
    if missing_key:
        text = '未配置 DEEPSEEK_API_KEY：请复制 server/.env.example 为 server/.env 并填入你的 DeepSeek API Key。'
        yield {'type': 'error', 'message': text}
        yield {'type': 'done', 'reply': text, 'traces': [], 'scene': None}
        return

    store = session.store
    initial: AgentState = {
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            *session.history,
            {'role': 'user', 'content': message},
        ],
        'traces': [],
        'step': 0,
    }

    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def emit(event: dict[str, Any]) -> None:
        queue.put_nowait(event)

    run_id = uuid.uuid4().hex
    thread_config = {'configurable': {'thread_id': run_id}}
    approval_future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
    APPROVALS[run_id] = approval_future

    async with httpx.AsyncClient(timeout=180) as client:
        graph = build_graph(
            client,
            endpoint.base_url,
            endpoint.api_key,
            endpoint.model,
            registry,
            store,
            emit,
            auto_approve=auto_approve,
        )

        async def pump() -> None:
            error_msg: str | None = None
            final_state: AgentState | None = None
            try:
                result = await graph.ainvoke(initial, thread_config)
                # 每遇到一次 interrupt：下发审批事件 → 等用户决定 → Command 恢复同 thread
                while result.get('__interrupt__'):
                    intr = result['__interrupt__'][0]
                    await emit({'type': 'approval_required', 'runId': run_id, **intr.value})
                    try:
                        decision = await asyncio.wait_for(approval_future, APPROVAL_TIMEOUT)
                    except asyncio.TimeoutError:
                        decision = {'approved': False, 'reason': '等待审批超时（180 秒），已自动拒绝'}
                    result = await graph.ainvoke(Command(resume=decision), thread_config)
                final_state = result
            except AgentRuntimeError as exc:
                error_msg = str(exc)
            except Exception as exc:  # 防御：任何异常都要让前端结束等待
                error_msg = f'{type(exc).__name__}: {exc}'

            if error_msg is not None:
                queue.put_nowait({'type': 'error', 'message': error_msg})
                queue.put_nowait({'type': 'done', 'reply': error_msg, 'traces': [], 'scene': None})
            else:
                assert final_state is not None
                # 成功后才提交历史，失败轮不污染上下文
                session.commit(final_state['messages'])
                reply = (final_state['messages'][-1].get('content') or '').strip()
                traces = final_state['traces']
                if not reply:
                    if final_state['messages'][-1].get('tool_calls'):
                        reply = '已达到最大工具调用轮数，请把指令描述得更具体一些。'
                    elif traces:
                        # 部分本地小模型最后一轮只发工具调用、不给文字总结
                        rendered = any(
                            t['name'] == 'render_scene' and 'error' not in t.get('result', {})
                            for t in traces
                        )
                        names = '、'.join(dict.fromkeys(t['name'] for t in traces))
                        reply = f'已按指令调用工具（{names}），三维场景已{"更新" if rendered else "准备"}。'
                queue.put_nowait(
                    {
                        'type': 'done',
                        'reply': reply,
                        'traces': traces,
                        'scene': store.current.model_dump() if store.current else None,
                    }
                )
            queue.put_nowait(None)

        task = asyncio.create_task(pump())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
            await task
        finally:
            APPROVALS.pop(run_id, None)


async def run_agent(
    message: str,
    settings: Settings,
    registry: DatasetRegistry,
    session: Session,
) -> tuple[str, list[dict[str, Any]]]:
    reply, traces = '', []
    async for event in agent_events(message, settings, registry, session):
        if event['type'] == 'done':
            reply, traces = event['reply'], event['traces']
        elif event['type'] == 'error':
            reply = event['message']
    return reply, traces


def _truncate(obj: dict[str, Any]) -> dict[str, Any]:
    serialized = json.dumps(obj, ensure_ascii=False)
    if len(serialized) <= 1500:
        return obj
    return {'truncated': serialized[:1500]}
