import json
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.gzip import GZipMiddleware

from .agent import APPROVALS, agent_events, run_agent
from .export_html import generate_export_html
from .config import load_settings
from .registry import DatasetRegistry
from .session import Session

app = FastAPI(title='GeoMind Sidecar', version='0.2.0')

# Electron 渲染端在 file:// / dev http://localhost 下访问，放开本地跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=False,
    allow_methods=['*'],
    allow_headers=['*'],
)
# Web 版 Cesium chunk 约 9MB，gzip 后约 2.5MB
app.add_middleware(GZipMiddleware, minimum_size=1000)

settings = load_settings()
registry = DatasetRegistry()
# 桌面端单用户：一个会话承载多轮历史与当前场景
session = Session()


class ChatBody(BaseModel):
    message: str


class ApprovalBody(BaseModel):
    runId: str
    approved: bool
    reason: str | None = None


@app.get('/health')
def health() -> dict[str, str]:
    return {'ok': 'true', 'service': 'geomind-sidecar'}


@app.get('/api/datasets')
def datasets() -> dict:
    return {'datasets': registry.list_datasets(), 'models': registry.list_models()}


@app.post('/api/session/reset')
def session_reset() -> dict:
    session.reset()
    return {'ok': True}


@app.post('/api/approval')
def approval(body: ApprovalBody) -> dict:
    future = APPROVALS.get(body.runId)
    if future is None or future.done():
        return {'ok': False, 'error': '该审批不存在或已结束'}
    future.set_result({'approved': body.approved, 'reason': body.reason})
    return {'ok': True}


@app.post('/api/chat')
async def chat(body: ChatBody) -> dict:
    reply, traces = await run_agent(body.message, settings, registry, session)
    return {
        'reply': reply,
        'traces': traces,
        'scene': session.store.current.model_dump() if session.store.current else None,
    }


@app.post('/api/chat/stream')
async def chat_stream(body: ChatBody):
    async def event_source():
        async for event in agent_events(body.message, settings, registry, session):
            yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_source(),
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@app.get('/api/scene/export')
def scene_export(download: int = 0) -> HTMLResponse:
    if session.store.current is None:
        return HTMLResponse(
            '<html><body style="font-family:sans-serif;padding:40px">'
            '<h2>暂无场景</h2><p>请先生成一个三维场景再导出。</p></body></html>',
            status_code=400,
        )
    html = generate_export_html(session.store.current.model_dump())
    headers = {}
    if download:
        headers['Content-Disposition'] = 'attachment; filename="geomind-scene.html"'
    return HTMLResponse(content=html, headers=headers)


# ---------- Web 版：同源托管渲染端静态产物（必须在所有 API 路由之后挂载） ----------

_default_web_dir = Path(__file__).resolve().parents[2] / 'out' / 'renderer'
_web_dir = Path(os.getenv('GEOMIND_WEB_DIR', _default_web_dir))
if _web_dir.exists():
    app.mount('/', StaticFiles(directory=str(_web_dir), html=True), name='web')
