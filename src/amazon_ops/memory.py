from __future__ import annotations

from threading import RLock
from typing import Literal, TypedDict


class ConversationMessage(TypedDict):
    role: Literal["user", "assistant"]
    content: str


class InMemoryConversationStore:
    """Small process-local conversation memory with a bounded message window."""

    def __init__(self, *, max_messages: int = 20) -> None:
        if max_messages < 2:
            raise ValueError("max_messages must be at least 2")
        self.max_messages = max_messages
        self._lock = RLock()
        self._conversations: dict[str, list[ConversationMessage]] = {}

    def history(self, conversation_id: str) -> list[ConversationMessage]:
        with self._lock:
            return [dict(item) for item in self._conversations.get(conversation_id, [])]

    def append(self, conversation_id: str, *, role: Literal["user", "assistant"], content: str) -> None:
        text = content.strip()
        if not text:
            return
        with self._lock:
            messages = self._conversations.setdefault(conversation_id, [])
            messages.append({"role": role, "content": text})
            if len(messages) > self.max_messages:
                del messages[: len(messages) - self.max_messages]

    def clear(self, conversation_id: str) -> None:
        with self._lock:
            self._conversations.pop(conversation_id, None)

    def count(self) -> int:
        with self._lock:
            return len(self._conversations)
