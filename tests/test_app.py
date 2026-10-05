from fastapi.testclient import TestClient

from app.main import app, sessions


class FakeClient:
    def __init__(self, reversed_type=True, chinese_types=False):
        self.reversed_type = reversed_type
        self.chinese_types = chinese_types
        self.calls = []

    def flatten_decks(self):
        return [{"deck_id": 7, "name": "Default"}]

    def get_add_info(self):
        types = [{"id": 1, "name": "问答题" if self.chinese_types else "Basic"}]
        if self.reversed_type:
            types.append({"id": 2, "name": "问答题（附翻转卡片）" if self.chinese_types else "Basic (and reversed card)"})
        return {"decks": [{"id": 7, "name": "Default"}], "notetypes": types}

    def add_note(self, **kwargs):
        self.calls.append(kwargs)
        return {"noteId": len(self.calls)}

    def close(self):
        pass


def logged_in(client):
    session = sessions.create("test@example.com", client)
    client = TestClient(app)
    client.cookies.set("notebook_session", session.id)
    return client


def test_protected_routes_require_session():
    client = TestClient(app)
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/decks").status_code == 401


def test_write_reversed_and_tags():
    fake = FakeClient()
    client = logged_in(fake)
    response = client.post("/api/write", json={"entries": [{"id": "a", "zh": "苹果", "en": "apple", "direction": "both"}], "deckName": "Default", "tags": "one, two one"})
    assert response.status_code == 200
    assert response.json()["success"][0]["noteId"] == 1
    assert fake.calls[0]["notetype_id"] == 2
    assert fake.calls[0]["tags"] == "one two"


def test_write_keeps_individual_failures():
    fake = FakeClient()
    client = logged_in(fake)
    response = client.post("/api/write", json={"entries": [{"id": "bad", "zh": "", "en": "x", "direction": "zh2en"}, {"id": "ok", "zh": "中", "en": "en", "direction": "en2zh"}], "deckName": "Default", "tags": ""})
    assert response.json()["failed"][0]["entryId"] == "bad"
    assert response.json()["success"][0]["entryId"] == "ok"


def test_write_supports_chinese_note_type_names():
    fake = FakeClient(chinese_types=True)
    client = logged_in(fake)
    response = client.post("/api/write", json={"entries": [{"id": "a", "zh": "苹果", "en": "apple", "direction": "both"}], "deckName": "Default", "tags": ""})
    assert response.status_code == 200
    assert response.json()["success"][0]["ok"] is True
    assert fake.calls[0]["notetype_id"] == 2
