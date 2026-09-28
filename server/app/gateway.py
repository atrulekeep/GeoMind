"""LLM 网关：所有 Agent 共享的 LLM 接入层

职责：
1. 统一 LLM API 接入（切模型只改 .env，不改 Agent 代码）
2. TraceID 注入（每次调用可追踪）
3. Token 统计 / 并发控制 / 限流（按 Agent + 按用户）
4. 调用日志（供数据中台回流消费）
5. 用户身份传递（不做权限决策，只把 user_id 向下透传）

设计原则：
- 不引入新进程/新服务，作为库被 Agent import
- agent.py 只需把 httpx.stream() 改成 gateway.stream_chat()
- _call_log 是内存列表，生产环境可换 DB（接口不变）
- 网关不是 Agent：无 LLM 决策，纯管道层
- 权限决策在业务 Agent 层执行（如 OpsPilot 的 SQLGlot 行级注入）
"""

import asyncio
import json
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx


@dataclass
class CallRecord:
    """单次 LLM 调用记录"""
    trace_id: str
    agent: str           # 'geomind' / 'welding' / 'rag'
    user_id: str         # 调用者标识，桌面端默认 'local'
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: int
    status: str          # 'ok' / 'error'
    error: str | None = None
    timestamp: float = field(default_factory=time.time)


class RateLimitError(Exception):
    """超过限流阈值"""


class LLMGateway:
    """所有 Agent 共享的 LLM 网关

    用法::

        gw = LLMGateway(base_url=..., api_key=..., model=...)
        async for chunk in gw.stream_chat(
            messages, agent='geomind', user_id='u001', tools=TOOL_SPECS
        ):
            delta = chunk['choices'][0]['delta']
            # 解析逻辑与原来 httpx.stream 完全一致
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        max_concurrent: int = 5,
        rate_limit_per_min: int = 60,
        user_rate_limit_per_min: int = 20,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.max_concurrent = max_concurrent
        self.rate_limit_per_min = rate_limit_per_min
        self.user_rate_limit_per_min = user_rate_limit_per_min
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._call_log: list[CallRecord] = []
        self._rate_window: list[float] = []
        # 按用户限流：user_id → 时间窗口
        self._user_rate_windows: dict[str, list[float]] = defaultdict(list)

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        agent: str = 'geomind',
        user_id: str = 'local',
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = 'auto',
        temperature: float = 0.2,
        trace_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """流式调用 LLM，yield 原始 JSON chunk

        agent: 哪个业务 Agent 在调用（geomind / welding / rag）
        user_id: 哪个用户在调用（桌面端默认 'local'；多用户场景传真实 id）
        网关记录这两个维度，但不做权限决策——权限在业务 Agent 层
        """
        trace_id = trace_id or uuid.uuid4().hex
        start = time.time()

        async with self._semaphore:
            now = time.time()
            # 全局限流
            self._rate_window = [t for t in self._rate_window if now - t < 60]
            if len(self._rate_window) >= self.rate_limit_per_min:
                raise RateLimitError(f'全局限流：{self.rate_limit_per_min}/min')
            # 按用户限流
            uw = self._user_rate_windows[user_id]
            uw[:] = [t for t in uw if now - t < 60]
            if len(uw) >= self.user_rate_limit_per_min:
                raise RateLimitError(
                    f'用户 {user_id} 限流：{self.user_rate_limit_per_min}/min'
                )
            self._rate_window.append(now)
            uw.append(now)

            request_kwargs: dict[str, Any] = {
                'method': 'POST',
                'url': f'{self.base_url}/chat/completions',
                'headers': {'Authorization': f'Bearer {self.api_key}'},
                'json': {
                    'model': self.model,
                    'messages': messages,
                    'temperature': temperature,
                    'stream': True,
                    **({'tools': tools, 'tool_choice': tool_choice} if tools else {}),
                },
            }

            prompt_tokens = 0
            completion_tokens = 0
            status = 'ok'
            error_msg: str | None = None

            try:
                async with httpx.AsyncClient(timeout=180) as client:
                    async with client.stream(**request_kwargs) as resp:
                        if resp.status_code != 200:
                            body = (await resp.aread()).decode()[:300]
                            raise RuntimeError(
                                f'模型接口错误 HTTP {resp.status_code}：{body}'
                            )
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
                            usage = chunk.get('usage')
                            if usage:
                                prompt_tokens = usage.get('prompt_tokens', 0)
                                completion_tokens = usage.get('completion_tokens', 0)
                            yield chunk
            except Exception as exc:
                status = 'error'
                error_msg = f'{type(exc).__name__}: {exc}'
                raise
            finally:
                total = prompt_tokens + completion_tokens
                self._call_log.append(CallRecord(
                    trace_id=trace_id,
                    agent=agent,
                    user_id=user_id,
                    model=self.model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total,
                    latency_ms=int((time.time() - start) * 1000),
                    status=status,
                    error=error_msg,
                ))

    # ---------- 统计与日志（供仪表盘 / 数据中台消费） ----------

    def stats(self) -> dict[str, Any]:
        """聚合统计：按 agent + 按 user 两个维度"""
        if not self._call_log:
            return {'total_calls': 0, 'by_agent': {}, 'by_user': {}}

        by_agent: dict[str, dict[str, Any]] = defaultdict(
            lambda: {'calls': 0, 'tokens': 0, 'errors': 0, 'latency_sum': 0}
        )
        by_user: dict[str, dict[str, Any]] = defaultdict(
            lambda: {'calls': 0, 'tokens': 0, 'errors': 0, 'agents': set()}
        )
        for r in self._call_log:
            a = by_agent[r.agent]
            a['calls'] += 1
            a['tokens'] += r.total_tokens
            a['errors'] += 1 if r.status == 'error' else 0
            a['latency_sum'] += r.latency_ms

            u = by_user[r.user_id]
            u['calls'] += 1
            u['tokens'] += r.total_tokens
            u['errors'] += 1 if r.status == 'error' else 0
            u['agents'].add(r.agent)

        return {
            'total_calls': len(self._call_log),
            'total_tokens': sum(r.total_tokens for r in self._call_log),
            'by_agent': {
                a: {
                    'calls': v['calls'],
                    'tokens': v['tokens'],
                    'errors': v['errors'],
                    'avg_latency_ms': v['latency_sum'] // v['calls'] if v['calls'] else 0,
                }
                for a, v in by_agent.items()
            },
            'by_user': {
                u: {
                    'calls': v['calls'],
                    'tokens': v['tokens'],
                    'errors': v['errors'],
                    'agents': sorted(v['agents']),
                }
                for u, v in by_user.items()
            },
        }

    def recent_calls(self, limit: int = 50) -> list[dict[str, Any]]:
        """最近调用记录"""
        return [
            {
                'trace_id': r.trace_id,
                'agent': r.agent,
                'user_id': r.user_id,
                'model': r.model,
                'prompt_tokens': r.prompt_tokens,
                'completion_tokens': r.completion_tokens,
                'total_tokens': r.total_tokens,
                'latency_ms': r.latency_ms,
                'status': r.status,
                'error': r.error,
                'timestamp': r.timestamp,
            }
            for r in self._call_log[-limit:]
        ]


def create_gateway(settings: Any) -> LLMGateway:
    """从 Settings 创建网关实例

    settings.endpoint() 返回 LLMEndpoint（provider/api_key/base_url/model），
    网关不关心是 deepseek 还是 ollama，统一走 OpenAI 兼容接口。
    """
    ep = settings.endpoint()
    return LLMGateway(
        base_url=ep.base_url,
        api_key=ep.api_key,
        model=ep.model,
    )
