"""GeoMind MCP Server（stdio）

把 GeoMind 的 GIS 工具层以 MCP 协议暴露给 Claude Desktop / Cursor / Dify 等外部宿主。
关键设计：工具定义直接复用 tools.TOOL_SPECS（FastAPI sidecar 的同一份契约），
Pydantic 生成的 inputSchema 零漂移；执行走统一 dispatch，异常语义与 sidecar 一致。

启动（在 server/ 目录）：
    ./.venv/bin/python -m app.mcp_server
"""

import asyncio
import json
from typing import Any

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from .registry import DatasetRegistry
# 别名导入：避免与下方 call_tool 处理函数同名遮蔽导致递归
from .tools import SceneStore, TOOL_SPECS, dispatch as _dispatch_tool

server: Server = Server('geomind-gis')
registry = DatasetRegistry()
store = SceneStore()


async def list_tools_handler(
    _ctx: Any, _params: types.PaginatedRequestParams
) -> types.ListToolsResult:
    return types.ListToolsResult(
        tools=[
            types.Tool(
                name=spec['function']['name'],
                description=spec['function']['description'],
                inputSchema=spec['function']['parameters'],
            )
            for spec in TOOL_SPECS
        ]
    )


async def call_tool_handler(
    _ctx: Any, params: types.CallToolRequestParams
) -> types.CallToolResult:
    # dispatch 内部已捕获工具异常并返回 {'error': ...}，交还给 LLM 自纠
    result = await _dispatch_tool(params.name, params.arguments or {}, registry, store)
    return types.CallToolResult(
        content=[
            types.TextContent(
                type='text',
                text=json.dumps(result, ensure_ascii=False),
            )
        ]
    )


server.add_request_handler('tools/list', types.PaginatedRequestParams, list_tools_handler)
server.add_request_handler('tools/call', types.CallToolRequestParams, call_tool_handler)


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == '__main__':
    asyncio.run(main())
