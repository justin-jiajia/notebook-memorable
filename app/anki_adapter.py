from __future__ import annotations

import sys
from pathlib import Path

RESEARCH_DIR = Path(__file__).resolve().parent.parent / "anki-research"
if str(RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(RESEARCH_DIR))

from ankiweb import AnkiWebClient  # noqa: E402


def create_client(email: str, password: str) -> AnkiWebClient:
    client = AnkiWebClient()
    try:
        client.login(email, password)
    except Exception:
        client.close()
        raise
    return client
