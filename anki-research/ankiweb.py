"""
ankiweb.py — 最小化的 AnkiWeb 内部 /svc/ 协议客户端。

功能：
  - 登录（ankiweb.net 登录取 token → ankiuser.net 换 cookie）
  - 列出 deck（deck-list-info / get-info-for-adding）
  - 向指定 deck 添加笔记（editor/add-or-update）

⚠️  AnkiWeb 服务条款禁止第三方客户端访问。本模块仅供研究复现使用。
    生产场景请用 AnkiConnect: https://foosoft.net/projects/anki-connect/

依赖: pip install requests
"""

from __future__ import annotations

import urllib.parse
from typing import Any, Iterable

import requests


# ---------------------------------------------------------------------------
# 极简 protobuf 编解码（只覆盖本模块用到的 wire type 0 / 2）
# ---------------------------------------------------------------------------

def _varint(n: int) -> bytes:
    if n < 0:
        n &= (1 << 64) - 1
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = 0
    result = 0
    while True:
        if i >= len(buf):
            raise ValueError("truncated varint")
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, i
        shift += 7
        if shift > 63:
            raise ValueError("varint too long")


def pb_str(field: int, value: str) -> bytes:
    data = value.encode("utf-8")
    return _varint((field << 3) | 2) + _varint(len(data)) + data


def pb_int(field: int, value: int) -> bytes:
    return _varint((field << 3) | 0) + _varint(value)


def pb_msg(field: int, payload: bytes) -> bytes:
    return _varint((field << 3) | 2) + _varint(len(payload)) + payload


def pb_parse(buf: bytes) -> Iterable[tuple[int, int, Any]]:
    """产出 (field_no, wire_type, value)。wire_type 0→int, 2→bytes, 1/5→int。"""
    i, n = 0, len(buf)
    while i < n:
        key, i = _read_varint(buf, i)
        field, wt = key >> 3, key & 7
        if wt == 0:
            value, i = _read_varint(buf, i)
            yield field, wt, value
        elif wt == 2:
            length, i = _read_varint(buf, i)
            value = buf[i:i + length]
            i += length
            yield field, wt, value
        elif wt == 5:
            value = int.from_bytes(buf[i:i + 4], "little")
            i += 4
            yield field, wt, value
        elif wt == 1:
            value = int.from_bytes(buf[i:i + 8], "little")
            i += 8
            yield field, wt, value
        else:
            raise ValueError(f"unsupported wire type {wt} (field {field})")


# ---------------------------------------------------------------------------
# 响应解码
# ---------------------------------------------------------------------------

def _decode_id_name(buf: bytes) -> dict:
    out: dict[str, Any] = {}
    for f, wt, v in pb_parse(buf):
        if f == 1 and wt == 0:
            out["id"] = v
        elif f == 2 and wt == 2:
            out["name"] = v.decode("utf-8", "replace")
    return out


def _decode_field(buf: bytes) -> dict:
    """Field 消息：ord（可能是 varint 或 FieldOrd{val=1}）、name=2、config=3。"""
    out: dict[str, Any] = {}
    for f, wt, v in pb_parse(buf):
        if f == 1:
            if wt == 0:
                out["ord"] = v
            elif wt == 2:
                for ff, fwt, fv in pb_parse(v):
                    if ff == 1 and fwt == 0:
                        out["ord"] = fv
        elif f == 2 and wt == 2:
            out["name"] = v.decode("utf-8", "replace")
    return out


def _decode_deck_node(buf: bytes) -> dict:
    out: dict[str, Any] = {"children": []}
    for f, wt, v in pb_parse(buf):
        if f == 1 and wt == 0:
            out["deck_id"] = v
        elif f == 2 and wt == 2:
            out["name"] = v.decode("utf-8", "replace")
        elif f == 3 and wt == 2:
            out["children"].append(_decode_deck_node(v))
        elif f == 4 and wt == 0:
            out["level"] = v
        elif f == 5 and wt == 0:
            out["collapsed"] = bool(v)
        elif f == 6 and wt == 0:
            out["review_count"] = v
        elif f == 7 and wt == 0:
            out["learn_count"] = v
        elif f == 8 and wt == 0:
            out["new_count"] = v
    return out


def _decode_deck_list(buf: bytes) -> dict:
    out: dict[str, Any] = {}
    for f, wt, v in pb_parse(buf):
        if f == 1 and wt == 2:
            out["top_node"] = _decode_deck_node(v)
        elif f == 2 and wt == 0:
            out["current_deck_id"] = v
        elif f == 3 and wt == 0:
            out["collection_size_bytes"] = v
        elif f == 4 and wt == 0:
            out["media_size_bytes"] = v
    return out


def _decode_add_info(buf: bytes) -> dict:
    out: dict[str, Any] = {"notetypes": [], "decks": [], "fields": []}
    for f, wt, v in pb_parse(buf):
        if f == 1 and wt == 2:
            out["notetypes"].append(_decode_id_name(v))
        elif f == 2 and wt == 2:
            out["decks"].append(_decode_id_name(v))
        elif f == 3 and wt == 0:
            out["current_deck_id"] = v
        elif f == 4 and wt == 0:
            out["current_notetype_id"] = v
        elif f == 5 and wt == 2:
            out["fields"].append(_decode_field(v))
    return out


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------

class AnkiWebError(Exception):
    pass


class LoginError(AnkiWebError):
    pass


LOGIN_STATUS = {
    0: "UNKNOWN",
    1: "AUTHENTICATED",
    2: "INVALID_USER",
    3: "INVALID_PASS",
}


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------

ANKIWEB = "https://ankiweb.net"
ANKIUSER = "https://ankiuser.net"


class AnkiWebClient:
    """AnkiWeb 内部 /svc/ 接口的最小化客户端。

    注意：`/svc/account|decks|search|study|shared` 在 ankiweb.net；
          `/svc/editor/*` 在 ankiuser.net。走错域会返回 404。
    """

    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
        })
        self.email: str | None = None

    # 上下文管理 -------------------------------------------------------------
    def __enter__(self) -> "AnkiWebClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self.session.close()

    # 底层 -------------------------------------------------------------------
    def _svc(self, host: str, path: str, body: bytes = b"") -> requests.Response:
        url = f"{host}/svc/{path.lstrip('/')}"
        return self.session.post(
            url,
            data=body,
            headers={"Content-Type": "application/octet-stream"},
            timeout=self.timeout,
            allow_redirects=False,
        )

    def _svc_ok(self, host: str, path: str, body: bytes = b"") -> bytes:
        r = self._svc(host, path, body)
        if r.status_code == 403:
            raise AnkiWebError(f"403 Forbidden — 未登录或会话已过期 ({path})")
        if r.status_code != 200:
            detail = r.text[:200] if r.text else "(空 body)"
            raise AnkiWebError(f"{r.status_code} {r.reason} @ {path}: {detail}")
        return r.content

    # 会话状态 ---------------------------------------------------------------
    def is_logged_in(self) -> bool:
        try:
            r = self._svc(ANKIWEB, "account/get-account-status")
        except requests.RequestException:
            return False
        if r.status_code != 200:
            return False
        for f, wt, v in pb_parse(r.content):
            if f == 1 and wt == 0:
                return bool(v)
        return False

    def _cookies_present(self) -> tuple[bool, bool]:
        def has(sub: str) -> bool:
            return any(
                c.name == "ankiweb" and sub in (c.domain or "")
                for c in self.session.cookies
            )
        return has("ankiweb"), has("ankiuser")

    # 登录 -------------------------------------------------------------------
    def login(self, email: str, password: str) -> None:
        """两步登录：
        [1] POST ankiweb.net/svc/account/login  → {status, token}，并在 ankiweb.net 种 cookie
        [2] GET  ankiuser.net/account/ankiuser-login?t=<token>  → 在 ankiuser.net 种 cookie
        """
        body = pb_str(1, email) + pb_str(2, password)
        r = self._svc(ANKIWEB, "account/login", body)

        if r.status_code != 200:
            raise LoginError(f"login HTTP {r.status_code}: {r.text[:200]}")

        status: int | None = None
        token: str | None = None
        for f, wt, v in pb_parse(r.content):
            if f == 1 and wt == 0:
                status = v
            elif f == 2 and wt == 2:
                token = v.decode("utf-8")

        if status != 1:
            raise LoginError(
                f"登录失败: status={status} "
                f"({LOGIN_STATUS.get(status, '?')})"
            )
        if not token:
            raise LoginError("登录返回 AUTHENTICATED 但没有跨域 token")

        # 用一次性 token 在 ankiuser.net 上种 cookie
        url = f"{ANKIUSER}/account/ankiuser-login?t={urllib.parse.quote(token, safe='')}"
        r2 = self.session.get(url, allow_redirects=False, timeout=self.timeout)
        if r2.status_code not in (200, 302, 303):
            raise LoginError(f"ankiuser-login HTTP {r2.status_code}")

        has_web, has_user = self._cookies_present()
        if not (has_web and has_user):
            raise LoginError(
                f"cookie 未正确种下: ankiweb.net={has_web}, ankiuser.net={has_user}"
            )

        self.email = email

    # deck -------------------------------------------------------------------
    def list_decks(self, minutes_west_of_utc: int = 0) -> dict:
        """POST ankiweb.net/svc/decks/deck-list-info → 原始解码字典（树）。"""
        body = pb_int(1, minutes_west_of_utc)
        return _decode_deck_list(self._svc_ok(ANKIWEB, "decks/deck-list-info", body))

    def flatten_decks(self) -> list[dict]:
        """把 deck 树摊平成 [{deck_id, name(完整::路径), new/learn/review}, ...]。"""
        tree = self.list_decks()
        top = tree.get("top_node") or {}
        out: list[dict] = []

        def walk(node: dict, prefix: str) -> None:
            name = (node.get("name") or "").strip()
            full = f"{prefix}::{name}" if (prefix and name) else (name or prefix)
            if node.get("deck_id") and name:  # 跳过无名虚拟根
                out.append({
                    "deck_id": node["deck_id"],
                    "name": full,
                    "new_count": node.get("new_count", 0),
                    "learn_count": node.get("learn_count", 0),
                    "review_count": node.get("review_count", 0),
                })
            for child in node.get("children", []):
                walk(child, full)

        walk(top, "")
        return out

    # 编辑器 -----------------------------------------------------------------
    def get_add_info(self) -> dict:
        """POST ankiuser.net/svc/editor/get-info-for-adding（空 body）。

        返回 {notetypes: [{id,name}], decks: [{id,name(完整路径)}],
              current_deck_id, current_notetype_id, fields: [...]}
        """
        return _decode_add_info(
            self._svc_ok(ANKIUSER, "editor/get-info-for-adding")
        )

    def get_notetype_field_names(self, notetype_id: int) -> list[str]:
        """POST ankiuser.net/svc/editor/get-notetype-fields {notetype_id}。"""
        body = pb_int(1, notetype_id)
        raw = self._svc_ok(ANKIUSER, "editor/get-notetype-fields", body)
        names: list[str] = []
        for f, wt, v in pb_parse(raw):
            if f == 1 and wt == 2:
                name = None
                for ff, fwt, fv in pb_parse(v):
                    if ff == 2 and fwt == 2:
                        name = fv.decode("utf-8", "replace")
                if name is not None:
                    names.append(name)
        return names

    def add_note(
        self,
        deck_id: int,
        notetype_id: int,
        fields: list[str],
        tags: str = "",
    ) -> None:
        """POST ankiuser.net/svc/editor/add-or-update（新增模式）。

        fields 按 notetype 顺序，值是 HTML 字符串。
        tags  空格分隔。

        服务端不会校验：
          * deck_id 不存在 → 笔记静默落入 Default (id=1)
          * 第一个字段为空 / 重复内容 → 一样 200
        调用方自行保证 deck_id 来自 get_add_info()['decks']。
        """
        body = b"".join(pb_str(1, f) for f in fields)
        if tags:
            body += pb_str(2, tags)
        add_msg = pb_int(1, notetype_id) + pb_int(2, deck_id)
        body += pb_msg(3, add_msg)

        r = self._svc(ANKIUSER, "editor/add-or-update", body)
        if r.status_code == 403:
            raise AnkiWebError("403 — 未登录或会话过期")
        if r.status_code != 200:
            detail = r.text[:200] if r.text else "(空 body)"
            raise AnkiWebError(f"{r.status_code} {r.reason}: {detail}")

    # 便捷：名称 → id ---------------------------------------------------------
    def resolve_deck_id(self, name_or_id: str | int) -> int:
        """把 deck 全名或 id 解析成 deck_id。找不到抛 KeyError。"""
        info = self.get_add_info()
        decks = info.get("decks", [])
        if isinstance(name_or_id, int) or str(name_or_id).isdigit():
            target = int(name_or_id)
            for d in decks:
                if d.get("id") == target:
                    return target
            raise KeyError(f"deck id {target} 不在账号内")
        lowered = str(name_or_id).lower()
        for d in decks:
            if (d.get("name") or "").lower() == lowered:
                return d["id"]
        # 允许部分匹配
        matches = [d for d in decks if lowered in (d.get("name") or "").lower()]
        if len(matches) == 1:
            return matches[0]["id"]
        raise KeyError(f"未找到/歧义 deck: {name_or_id!r}")