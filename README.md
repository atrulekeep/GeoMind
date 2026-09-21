# GeoMind — 自然语言驱动的三维 GIS 智能体

用一句话完成"空间分析 → 三维场景"：LLM 规划工具链，GeoPandas 做真实空间计算，
输出声明式 SceneSpec，Cesium 实时渲染。桌面端（Electron）数据全程不出本机；
同一套后端也可构建为 Web 版（Docker，同源单端口部署）。

> 示例：`统计北京每个区的医院数量，用三维柱状图展示，底图换成卫星影像`

## 特性

- **LangGraph Agent**：显式 `plan → execute → plan` 状态机（上限 12 步），流式 SSE
  输出思考状态、工具调用/返回、逐 token 回复；工具错误以结构化结果回喂，模型自纠
- **六类 GIS 工具**：数据集列表、字段探测、点落面空间聚合（GeoPandas `sjoin`）、
  声明式三维渲染（三维柱/填色/点位/glTF 模型）、视角飞行、知识库检索
- **多轮场景 patch**：默认 merge（同 id 图层替换、新图层追加、底图/地形继承），
  `replaceScene` 全量重建
- **HITL 人工审批**：`replaceScene` 等破坏性操作经 LangGraph `interrupt` 暂停，
  前端批准/拒绝后恢复；拒绝时模型自主降级为增量方案；180 秒超时自动拒绝
- **本地 RAG**：24 条手写 GIS 知识，hybrid 检索（本地 bge-large-zh 向量 + BM25 融合，
  Ollama 不可用时自动降级）；embedding 带版本缓存
- **MCP Server**：同一份 Pydantic 工具契约以 stdio MCP 暴露给 Claude Desktop / Cursor
- **评测体系**：30 条端到端用例、确定性断言（不用 LLM 打分）、JSON 报告落盘；
  五轮全量评测 pass@1 为 93%~100%（均值 97.3%），平均 4.4 个工具步
- **双模型通道**：DeepSeek 云端 / Ollama 本地（支持 tools 的模型），`.env` 切换

## 架构

```
Electron (Vue3 + TS + Cesium)  ──SSE──▶  FastAPI sidecar (127.0.0.1:8765)
  对话流 / 工具过程 / 审批卡片              │
  SceneSpec → Cesium 渲染                  ├─ LangGraph 状态机（plan/execute + interrupt）
                                           ├─ 工具层（Pydantic 契约，strict 校验）
                                           │    ├─ GeoPandas（sjoin/CRS 归一化）
                                           │    ├─ SceneStore（merge/replaceScene）
                                           │    └─ hybrid RAG（bge-large-zh + BM25）
                                           ├─ stdio MCP Server（复用同一 TOOL_SPECS）
                                           └─ DeepSeek / Ollama（OpenAI 兼容协议）
```

## 快速开始（Electron 桌面端）

```bash
# 1) 前端依赖
pnpm install

# 2) Python sidecar（Python 3.11）
cd server
python -m venv .venv
./.venv/bin/pip install -r requirements.txt
cp .env.example .env        # 填入 DEEPSEEK_API_KEY，或 GEOMIND_PROVIDER=ollama
./.venv/bin/python -m uvicorn app.main:app --port 8765

# 3) 另开终端启动桌面端（Electron 主进程会自动拉起 sidecar，手动启动用于调试）
pnpm dev
```

Ollama 本地通道：拉取支持 tools 的模型（如 `qwen3.5:9b`），知识库嵌入用
`ollama pull bge-large`，设置 `GEOMIND_PROVIDER=ollama` 即可全离线运行。

## Web 版（Docker，同源单端口）

FastAPI 检测到静态产物时自动托管，API 与页面同端口，无需额外反代：

```bash
docker build -t geomind .
docker run --rm -p 8765:8765 \
  -e GEOMIND_PROVIDER=deepseek \
  -e DEEPSEEK_API_KEY=sk-xxx \
  geomind
# 打开 http://localhost:8765
```

镜像内不含 `.env`，所有配置通过环境变量注入。Cesium chunk gzip 后传输约 1.6MB。

## MCP Server

```bash
cd server
./.venv/bin/python -m app.mcp_server     # stdio，挂到 Claude Desktop / Cursor
```

`tools/list` 与 sidecar 完全一致（同一份 `TOOL_SPECS` 生成 inputSchema，契约零漂移）。

## 评测

```bash
cd server
./.venv/bin/python evals/run_eval.py              # 全量 30 条（3 并发）
./.venv/bin/python evals/run_eval.py --smoke      # 每类抽 1 条
./.venv/bin/python evals/run_eval.py --ids eval-07,eval-29
```

断言基于可观测事实：工具调用序列、`render_scene` 入参、最终场景图层/底图/地形、
回复关键词。报告写入 `server/evals/reports/`（已 gitignore）。

## 数据说明

- `beijing_districts`：DataV 官方行政区划边界（可信）
- `beijing_hospitals`：36 个手工近似坐标的演示数据（`sample=true`，不可用于真实结论）
- `beijing_landmarks`：公开知名地标坐标
- 底图/地形：高德、OSM、ArcGIS World_Imagery / WorldElevation3D（均免 token）
- 模型：Cesium 官方与 Khronos 公开 glb；所有几何服务端解析，LLM 不接触坐标

## 目录

```
src/renderer/          Vue3 + Cesium 渲染端（SSE 消费 / 审批卡片 / SceneSpec 渲染）
src/main               Electron 主进程（开发期自动拉起 sidecar）
server/app/
  agent.py             LangGraph 状态机 + 流式事件 + HITL
  tools.py             GIS 工具层与 OpenAI function-calling 契约
  knowledge.py         hybrid 检索（bge-large-zh 向量 + BM25）
  knowledge/chunks.json 24 条 GIS 知识库
  schemas.py           SceneSpec 与工具入参（Pydantic strict）
  mcp_server.py        stdio MCP Server
server/evals/          30 条评测集与确定性 runner
```
