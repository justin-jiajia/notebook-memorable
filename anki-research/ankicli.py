#!/usr/bin/env python3
"""
ankiweb_cli.py — 交互式验证 AnkiWeb 内部 /svc/ 协议：登录 / 列 deck / 加笔记。

⚠️  仅用于研究复现 / 个人账号验证。AnkiWeb 条款禁止第三方客户端直连；
    生产用途请使用 AnkiConnect。

用法:
    pip install requests
    python ankiweb_cli.py
    # 或从环境变量读凭据（避免 shell history 泄露）
    ANKIWEB_EMAIL=you@example.com ANKIWEB_PASSWORD=... python ankiweb_cli.py
    # 或命令行
    python ankiweb_cli.py --email you@example.com
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import sys

import requests

from ankiweb import AnkiWebClient, AnkiWebError, LoginError

BANNER = r"""
╔══════════════════════════════════════════════════════════════════╗
║  AnkiWeb /svc/ 协议交互式验证工具                                ║
║  ⚠  仅供研究复现。AnkiWeb 条款禁止第三方客户端直连。             ║
║     生产场景请使用 AnkiConnect:                                  ║
║     https://foosoft.net/projects/anki-connect/                   ║
╚══════════════════════════════════════════════════════════════════╝
"""


# ---------------------------------------------------------------------------
# 交互选择
# ---------------------------------------------------------------------------

def choose(label: str, options: list[tuple[str, int]]) -> tuple[str, int]:
    """options: [(name, id), ...]。支持按序号 / id / 名称（含唯一子串）选择。"""
    if not options:
        raise ValueError(f"没有可选的 {label}")

    print(f"\n请选择 {label}:")
    for i, (name, oid) in enumerate(options):
        print(f"  [{i:>2}] {name}    (id={oid})")

    while True:
        try:
            raw = input(f"输入序号 / id / 名称 [默认 0]: ").strip()
        except EOFError:
            return options[0]

        if not raw:
            return options[0]

        if raw.isdigit():
            n = int(raw)
            if 0 <= n < len(options):
                return options[n]
            for name, oid in options:
                if oid == n:
                    return (name, oid)

        lowered = raw.lower()
        for name, oid in options:
            if name.lower() == lowered:
                return (name, oid)

        matches = [(n, o) for n, o in options if lowered in n.lower()]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            print(f"  ✗ 匹配到 {len(matches)} 个，请更具体。")
            continue
        print("  ✗ 未找到，请重试。")


def refresh_decks(client: AnkiWebClient) -> list[tuple[str, int]]:
    info = client.get_add_info()
    return [(d["name"], d["id"]) for d in info.get("decks", [])]


def refresh_notetypes(client: AnkiWebClient) -> list[tuple[str, int]]:
    info = client.get_add_info()
    return [(n["name"], n["id"]) for n in info.get("notetypes", [])]


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> int:
    print(BANNER)

    p = argparse.ArgumentParser(description="AnkiWeb /svc/ 交互式验证工具")
    p.add_argument("--email", help="AnkiWeb 邮箱（或用 $ANKIWEB_EMAIL）")
    p.add_argument("--password", help="AnkiWeb 密码（或用 $ANKIWEB_PASSWORD）")
    p.add_argument("--timeout", type=float, default=20.0)
    args = p.parse_args()

    email = (
        args.email
        or os.environ.get("ANKIWEB_EMAIL")
        or input("AnkiWeb 邮箱: ").strip()
    )
    if not email:
        print("必须提供邮箱。", file=sys.stderr)
        return 2

    password = args.password or os.environ.get("ANKIWEB_PASSWORD")
    if not password:
        password = getpass.getpass("AnkiWeb 密码: ")

    client = AnkiWebClient(timeout=args.timeout)

    print("\n[1/3] 登录中 …", end="", flush=True)
    try:
        client.login(email, password)
    except LoginError as e:
        print(" 失败")
        print(f"      {e}", file=sys.stderr)
        client.close()
        return 1
    except requests.RequestException as e:
        print(" 失败")
        print(f"      网络错误: {e}", file=sys.stderr)
        client.close()
        return 1
    print(f" OK  (两个域的 ankiweb cookie 均已种下)")

    print("[2/3] 拉取 deck / notetype 列表 …", end="", flush=True)
    try:
        info = client.get_add_info()
    except AnkiWebError as e:
        print(" 失败")
        print(f"      {e}", file=sys.stderr)
        client.close()
        return 1
    print(" OK")

    decks = [(d["name"], d["id"]) for d in info.get("decks", [])]
    notetypes = [(n["name"], n["id"]) for n in info.get("notetypes", [])]
    if not decks:
        print("账号下没有任何 deck。", file=sys.stderr)
        client.close()
        return 1
    if not notetypes:
        print("账号下没有任何 notetype。", file=sys.stderr)
        client.close()
        return 1

    print("[3/3] 选择目标 deck / notetype")
    deck_name, deck_id = choose("目标 deck", decks)
    nt_name, nt_id = choose("notetype", notetypes)

    try:
        field_names = client.get_notetype_field_names(nt_id)
    except AnkiWebError:
        field_names = [f.get("name") or f"field{i}"
                       for i, f in enumerate(info.get("fields", []))]
    if not field_names:
        field_names = ["field_1"]

    def show_state() -> None:
        print(f"\n  当前 deck     : {deck_name}  (id={deck_id})")
        print(f"  当前 notetype : {nt_name}  (id={nt_id})")
        print(f"  字段顺序      : {', '.join(field_names)}")

    show_state()

    print(
        "\n命令：\n"
        "  /add  <字段1> | <字段2> | ...  [--tags t1 t2]   快速添加一条笔记\n"
        "  /addi                                           交互式逐字段输入\n"
        "  /list                                           列出所有 deck\n"
        "  /deck                                           重新选择 deck\n"
        "  /notetype                                       重新选择 notetype\n"
        "  /info                                           显示当前选择\n"
        "  /quit                                           退出"
    )

    while True:
        try:
            line = input("\nanki> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not line:
            continue

        # ---- 退出 --------------------------------------------------------
        if line in ("/quit", "/exit", "/q"):
            break

        # ---- 帮助 --------------------------------------------------------
        if line in ("/help", "/h", "/?"):
            print(
                "  /add  字段1 | 字段2 | ...   [--tags t1 t2]\n"
                "  /addi\n"
                "  /list\n"
                "  /deck  /notetype  /info  /quit"
            )
            continue

        # ---- /info -------------------------------------------------------
        if line == "/info":
            show_state()
            continue

        # ---- /list -------------------------------------------------------
        if line == "/list":
            try:
                flat = client.flatten_decks()
            except AnkiWebError as e:
                print(f"  ✗ {e}")
                continue
            if not flat:
                print("  (无)")
                continue
            for d in flat:
                marker = "→" if d["deck_id"] == deck_id else " "
                print(
                    f"  {marker} {d['name']:<40} id={d['deck_id']} "
                    f"new={d['new_count']} learn={d['learn_count']} "
                    f"review={d['review_count']}"
                )
            continue

        # ---- /deck -------------------------------------------------------
        if line == "/deck":
            try:
                decks = refresh_decks(client)
            except AnkiWebError as e:
                print(f"  ✗ {e}")
                continue
            if not decks:
                print("  ✗ 没有 deck")
                continue
            deck_name, deck_id = choose("目标 deck", decks)
            print(f"  → 已切换 deck: {deck_name} (id={deck_id})")
            continue

        # ---- /notetype ---------------------------------------------------
        if line == "/notetype":
            try:
                notetypes = refresh_notetypes(client)
            except AnkiWebError as e:
                print(f"  ✗ {e}")
                continue
            if not notetypes:
                print("  ✗ 没有 notetype")
                continue
            nt_name, nt_id = choose("notetype", notetypes)
            try:
                field_names = client.get_notetype_field_names(nt_id)
            except AnkiWebError:
                field_names = []
            if not field_names:
                field_names = ["field_1"]
            print(f"  → 已切换 notetype: {nt_name} (id={nt_id})")
            print(f"    字段: {', '.join(field_names)}")
            continue

        # ---- /add（一行式） -----------------------------------------------
        if line.startswith("/add "):
            payload = line[len("/add "):].strip()

            tags = ""
            m = re.search(r"--tags?\s+(.+)$", payload)
            if m:
                tags = m.group(1).strip()
                payload = payload[:m.start()].strip()

            parts = [p.strip() for p in payload.split("|")] if payload else []
            if len(parts) != len(field_names):
                print(
                    f"  ✗ 需要 {len(field_names)} 个字段（用 | 分隔），"
                    f"实际给了 {len(parts)} 个。\n"
                    f"    字段顺序: {', '.join(field_names)}"
                )
                continue

            if any(not p for p in parts[:1]):
                print("  ⚠  第一个字段为空 —— 服务端不会报错，但桌面版 Anki 会拒绝。")

            try:
                client.add_note(deck_id, nt_id, parts, tags)
            except AnkiWebError as e:
                print(f"  ✗ 添加失败: {e}")
                continue
            extra = f"  tags={tags}" if tags else ""
            print(f"  ✓ 已添加到 {deck_name} (deck_id={deck_id}){extra}")
            continue

        # ---- /addi（交互式） ----------------------------------------------
        if line == "/addi":
            values: list[str] = []
            aborted = False
            for i, fname in enumerate(field_names):
                try:
                    v = input(f"  [{i}] {fname}: ")
                except (EOFError, KeyboardInterrupt):
                    print()
                    aborted = True
                    break
                values.append(v)
            if aborted:
                continue
            try:
                tags = input("  tags (空格分隔, 可空): ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                continue

            if not values or not values[0]:
                print("  ⚠  第一个字段为空 —— 服务端不校验，但桌面版 Anki 会拒绝。")

            try:
                client.add_note(deck_id, nt_id, values, tags)
            except AnkiWebError as e:
                print(f"  ✗ 添加失败: {e}")
                continue
            print(f"  ✓ 已添加到 {deck_name} (deck_id={deck_id})")
            continue

        # ---- 未知命令 -----------------------------------------------------
        print(f"  ✗ 未知命令: {line!r}  (输入 /help 查看帮助)")

    print("\n再见。")
    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())