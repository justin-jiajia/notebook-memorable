from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Annotated

from fastapi import Cookie, Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from .anki_adapter import create_client
from .extractor import ExtractionError, extract_entries
from .session import SessionStore, UserSession

BASE = Path(__file__).resolve().parent
load_dotenv(BASE.parent / ".env")
app = FastAPI(title="NoteBloom", version="0.1.0")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
sessions = SessionStore()
COOKIE_NAME = "notebook_session"


class LoginRequest(BaseModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=1)


class Entry(BaseModel):
    id: str = Field(min_length=1)
    zh: str = ""
    en: str = ""
    direction: str = Field(default="both", pattern="^(zh2en|en2zh|both)$")


class WriteRequest(BaseModel):
    entries: list[Entry] = Field(min_length=1)
    deckName: str = Field(min_length=1)
    tags: str = ""


def current_session(cookie: str | None = Cookie(default=None, alias=COOKIE_NAME)) -> UserSession:
    session = sessions.get(cookie)
    if not session:
        raise HTTPException(status_code=401, detail="未登录或会话已过期")
    return session


def _tags(raw: str) -> str:
    return " ".join(dict.fromkeys(part for part in raw.replace(",", " ").split() if part))


@app.get("/")
def home() -> FileResponse:
    return FileResponse(BASE / "static" / "index.html")


@app.post("/api/auth/login")
def login(payload: LoginRequest):
    try:
        client = create_client(payload.email, payload.password)
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"AnkiWeb 登录失败：{exc}") from exc
    session = sessions.create(payload.email, client)
    response = {"ankiUser": session.anki_user, "expiresAt": session.expires_at}
    from fastapi.responses import JSONResponse
    result = JSONResponse(response)
    result.set_cookie(COOKIE_NAME, session.id, httponly=True, samesite="lax", secure=False, max_age=sessions.ttl_seconds)
    return result


@app.post("/api/auth/logout", status_code=204)
def logout(session: UserSession = Depends(current_session), cookie: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    sessions.delete(cookie)
    from fastapi.responses import Response
    result = Response(status_code=204)
    result.delete_cookie(COOKIE_NAME)
    return result


@app.get("/api/auth/me")
def me(session: UserSession = Depends(current_session)):
    return {"ankiUser": session.anki_user, "expiresAt": session.expires_at}


@app.get("/api/decks")
def decks(session: UserSession = Depends(current_session)):
    try:
        return {"decks": session.client.flatten_decks()}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"读取 deck 失败：{exc}") from exc


@app.post("/api/extract")
def extract(image: Annotated[UploadFile, File(...)], session: UserSession = Depends(current_session)):
    del session
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="请上传图片文件")
    content = image.file.read()
    if not content:
        raise HTTPException(status_code=400, detail="图片为空")
    try:
        return {"entries": [{"id": f"entry-{i + 1}", **entry} for i, entry in enumerate(extract_entries(content, image.content_type))]}
    except ExtractionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _find_notetype(info: dict, name: str) -> int | None:
    aliases = {
        "Basic": ("Basic", "问答题"),
        "Basic (and reversed card)": (
            "Basic (and reversed card)",
            "问答题（附翻转卡片）",
            "问答题 (附翻转卡片)",
        ),
    }
    accepted_names = aliases.get(name, (name,))
    for item in info.get("notetypes", []):
        if item.get("name") in accepted_names:
            return item.get("id")
    return None


def _find_deck(info: dict, name: str) -> int | None:
    for item in info.get("decks", []):
        if item.get("name") == name:
            return item.get("id")
    return None


def _add(client: object, deck_id: int, notetype_id: int, front: str, back: str, tags: str):
    return client.add_note(deck_id=deck_id, notetype_id=notetype_id, fields=[front, back], tags=tags)


@app.post("/api/write")
def write(payload: WriteRequest, session: UserSession = Depends(current_session)):
    client = session.client
    try:
        info = client.get_add_info()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"读取 Anki 配置失败：{exc}") from exc
    deck_id = _find_deck(info, payload.deckName)
    if deck_id is None:
        raise HTTPException(status_code=400, detail="目标 deck 不存在，请重新加载列表")
    basic_id = _find_notetype(info, "Basic")
    reversed_id = _find_notetype(info, "Basic (and reversed card)")
    if basic_id is None:
        raise HTTPException(status_code=400, detail="当前账号没有 Basic note type")
    tags = _tags(payload.tags)
    success: list[dict] = []
    failed: list[dict] = []
    for entry in payload.entries:
        try:
            if not entry.zh.strip() or not entry.en.strip():
                raise ValueError("中文和英文都不能为空")
            if entry.direction == "zh2en":
                result = _add(client, deck_id, basic_id, entry.zh, entry.en, tags)
            elif entry.direction == "en2zh":
                result = _add(client, deck_id, basic_id, entry.en, entry.zh, tags)
            elif reversed_id is not None:
                result = _add(client, deck_id, reversed_id, entry.zh, entry.en, tags)
            else:
                _add(client, deck_id, basic_id, entry.zh, entry.en, tags)
                result = _add(client, deck_id, basic_id, entry.en, entry.zh, tags)
            item = {"entryId": entry.id, "ok": True}
            if isinstance(result, dict) and result.get("noteId") is not None:
                item["noteId"] = result["noteId"]
            success.append(item)
        except Exception as exc:
            failed.append({"entryId": entry.id, "ok": False, "error": str(exc)})
    return {"success": success, "failed": failed}
