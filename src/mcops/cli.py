"""运维命令行：mcops hash-password / mcops console / mcops replay。"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import gzip
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

from mcops import logparse
from mcops.auth import hash_password
from mcops.config import load_config
from mcops.rcon import RconClient


def _cmd_hash_password(_: argparse.Namespace) -> int:
    pw = getpass.getpass("新的管理员密码：")
    if len(pw) < 12:
        print("密码至少 12 位", file=sys.stderr)
        return 1
    if getpass.getpass("再输入一次：") != pw:
        print("两次输入不一致", file=sys.stderr)
        return 1
    print(hash_password(pw))
    print(
        "提示：改密码后，把上面的哈希写进 admin-password 配置并重启 agent，"
        "再运行 `mcops revoke-sessions --config <yaml>` 让旧登录立即失效。",
        file=sys.stderr,
    )
    return 0


def _cmd_revoke_sessions(args: argparse.Namespace) -> int:
    import sqlite3

    loaded = load_config(args.config)
    path = loaded.config.db_path
    if not path.exists():
        print(f"数据库不存在：{path}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(path)
    try:
        n = conn.execute("DELETE FROM auth_sessions").rowcount
        conn.commit()
    finally:
        conn.close()
    print(f"已吊销 {n} 个会话，所有设备需重新登录。")
    return 0


def _cmd_console(args: argparse.Namespace) -> int:
    loaded = load_config(args.config)
    s = loaded.config.server
    if loaded.secrets.rcon_password is None:
        print("secrets.yaml 未配置 rcon_password", file=sys.stderr)
        return 1
    client = RconClient(s.rcon_host, s.rcon_port, loaded.secrets.rcon_password.get_secret_value())

    async def repl() -> None:
        loop = asyncio.get_running_loop()
        print(f"已连接 RCON {s.rcon_host}:{s.rcon_port}，输入命令（Ctrl-D 退出）")
        while True:
            line = await loop.run_in_executor(None, lambda: input("mc> "))
            if line.strip():
                print(await client.command(line.strip()))

    try:
        asyncio.run(repl())
    except (EOFError, KeyboardInterrupt):
        print()
    return 0


_GZ_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})-\d+\.log(?:\.gz)?$")


def _cmd_replay(args: argparse.Namespace) -> int:
    """把历史日志（latest.log / YYYY-MM-DD-N.log.gz）解析成事件，输出 JSON Lines。"""
    tz = datetime.now().astimezone().tzinfo
    assert tz is not None
    types = set(args.types.split(",")) if args.types else None
    for p in map(Path, args.files):
        m = _GZ_DATE.search(p.name)
        start = date.fromisoformat(m.group(1)) if m else date.today()
        opener = gzip.open if p.suffix == ".gz" else open
        tracker = logparse.DayTracker(start)
        with opener(p, "rt", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                day = tracker.feed(raw)
                line = logparse.parse_line(raw, day, tz)
                if line is None:
                    continue
                for ev in logparse.classify(line):
                    if types is None or ev.type in types:
                        rec = {"ts": ev.ts.isoformat(), "type": ev.type, "file": p.name, **ev.data}
                        print(json.dumps(rec, ensure_ascii=False))
    return 0


def _cmd_backfill(args: argparse.Namespace) -> int:
    """把历史日志导入数据库（会话、IP、事件），并以复盘模式跑安全规则。"""
    from mcops.db import open_db
    from mcops.pipeline import Pipeline, backfill
    from mcops.players import PlayerTracker
    from mcops.security import SecurityEngine

    loaded = load_config(args.config)
    tz = datetime.now().astimezone().tzinfo
    assert tz is not None

    async def go() -> dict[str, int]:
        engine = await open_db(loaded.config.db_path)
        try:
            pipe = Pipeline(PlayerTracker(engine), SecurityEngine(engine, tz))
            return await backfill(engine, pipe, [Path(f) for f in args.files], tz)
        finally:
            await engine.dispose()

    counts = asyncio.run(go())
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"{k:24} {v}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(prog="mcops")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("hash-password", help="生成管理员密码的 argon2 哈希").set_defaults(
        fn=_cmd_hash_password
    )
    c = sub.add_parser("console", help="RCON 交互控制台（替代 tmux attach）")
    c.add_argument("--config", required=True)
    c.set_defaults(fn=_cmd_console)
    rs = sub.add_parser("revoke-sessions", help="吊销全部面板会话（改密后用）")
    rs.add_argument("--config", required=True)
    rs.set_defaults(fn=_cmd_revoke_sessions)
    r = sub.add_parser("replay", help="解析历史日志为事件（JSON Lines）")
    r.add_argument("files", nargs="+")
    r.add_argument("--types", help="只输出这些事件类型，逗号分隔")
    r.set_defaults(fn=_cmd_replay)
    b = sub.add_parser(
        "backfill", help="导入历史日志（会话 / IP / 复盘告警）；只在首次部署时运行一次"
    )
    b.add_argument("--config", required=True)
    b.add_argument("files", nargs="+")
    b.set_defaults(fn=_cmd_backfill)
    args = ap.parse_args()
    sys.exit(args.fn(args))
