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
        self.history = messages[1:][-HISTORY_LIMIT:]

    def reset(self) -> None:
        self.history = []
        self.store.current = None
