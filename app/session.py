from __future__ import annotations

import secrets
import time
from dataclasses import dataclass


@dataclass
class UserSession:
    id: str
    anki_user: str
    client: object
    expires_at: float


class SessionStore:
    def __init__(self, ttl_seconds: int = 60 * 60 * 24):
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[str, UserSession] = {}

    def create(self, anki_user: str, client: object) -> UserSession:
        session = UserSession(
            id=secrets.token_urlsafe(32),
            anki_user=anki_user,
            client=client,
            expires_at=time.time() + self.ttl_seconds,
        )
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str | None) -> UserSession | None:
        if not session_id:
            return None
        session = self._sessions.get(session_id)
        if not session:
            return None
        if session.expires_at <= time.time():
            self.delete(session_id)
            return None
        return session

    def delete(self, session_id: str | None) -> None:
        if not session_id:
            return
        session = self._sessions.pop(session_id, None)
        if session:
            close = getattr(session.client, "close", None)
            if close:
                close()
