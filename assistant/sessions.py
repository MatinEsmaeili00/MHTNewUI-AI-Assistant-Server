"""
A short conversation history per session_id, kept in memory for the LLM ("and the other one?").

Capped in every direction: turns per session, characters per turn, number of sessions.
A restart forgets everything, which is fine: the history only helps follow-up questions.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict, deque

from . import config

MAX_TURN_CHARS = 600


class SessionStore:
    def __init__(self) -> None:
        self._sessions: OrderedDict[str, deque] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _key(session_id: object) -> str:
        return session_id.strip()[:64] if isinstance(session_id, str) else ""

    def history(self, session_id: object) -> list[dict]:
        """The last turns as chat messages: [{"role": "user", ...}, {"role": "assistant", ...}, ...]."""
        key = self._key(session_id)
        with self._lock:
            return list(self._sessions.get(key, ())) if key else []

    def add(self, session_id: object, text: str, reply: str, actions: list[dict]) -> None:
        key = self._key(session_id)
        if not key or not text:
            return
        answer = json.dumps({"reply": reply, "actions": actions}, ensure_ascii=False)
        with self._lock:
            turns = self._sessions.pop(key, None)
            if turns is None:
                turns = deque(maxlen=2 * max(1, config.settings.session_turns))
            turns.append({"role": "user", "content": text[:MAX_TURN_CHARS]})
            turns.append({"role": "assistant", "content": answer[:MAX_TURN_CHARS]})
            self._sessions[key] = turns                      # most recently used last
            while len(self._sessions) > config.settings.max_sessions:
                self._sessions.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()


sessions = SessionStore()
