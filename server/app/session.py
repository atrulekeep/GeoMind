"""桌面端单用户会话：跨轮消息历史 + 当前场景"""

from typing import Any

from .tools import SceneStore

# 只保留最近 N 条非 system 消息，控制 token 用量
HISTORY_LIMIT = 20


class Session:
    def __init__(self) -> None:
        self.store = SceneStore()
        # 对话历史不含 system（system 每轮由 agent 前置），保证策略可即时更新
        self.history: list[dict[str, Any]] = []

    def commit(self, messages: list[dict[str, Any]]) -> None:
        hist = messages[1:][-HISTORY_LIMIT:]
        # 盲取窗口可能把 assistant(tool_calls) 与其 tool 响应切开，
        # 产生孤儿 tool 消息会让后续每轮请求 400（tool must follow tool_calls）
        start = 0
        while start < len(hist) and hist[start].get('role') == 'tool':
            start += 1
        hist = hist[start:]
        # 步数用尽/异常中断时末尾可能是未被响应的 assistant.tool_calls，同样会导致 400
        while hist and hist[-1].get('role') == 'assistant' and hist[-1].get('tool_calls'):
            hist.pop()
        self.history = hist

    def reset(self) -> None:
        self.history = []
        self.store.current = None
