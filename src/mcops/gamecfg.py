"""开服前后的游戏配置同步（移植自 fabric-server-manager：sync_motd.py 与 cmd_start 里的 setworldspawn）。"""

from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)


def jar_version(jar_name: str) -> str:
    m = re.search(r"mc\.([0-9]+\.[0-9]+(?:\.[0-9]+)?)", jar_name)
    return m.group(1) if m else ""


def sync_motd(
    game_dir: Path, server_list: str | None, line1: str | None, line2: str | None, version: str
) -> list[str]:
    """写 server.properties 的 motd= 和 MiniMOTD main.conf 的 line1/line2；返回改动过的文件。只在内容变化时写。"""
    changed: list[str] = []
    if server_list is not None:
        sp = game_dir / "server.properties"
        text = sp.read_text(encoding="utf-8")
        # server.properties 是 Java properties，非 ASCII 需转义；旧脚本原样写入，这里保持一致
        new = re.sub(r"(?m)^motd=.*$", lambda _m: "motd=" + server_list, text, count=1)
        if new != text:
            sp.write_text(new, encoding="utf-8")
            changed.append(sp.name)
    mc = game_dir / "config" / "MiniMOTD" / "main.conf"
    if mc.exists() and (line1 is not None or line2 is not None):
        text = mc.read_text(encoding="utf-8")
        new = text
        if line1 is not None:
            new = re.sub(r'line1="[^"]*"', lambda _m: f'line1="{line1}"', new)
        if line2 is not None:
            l2 = line2.replace("{version}", version)
            new = re.sub(r'line2="[^"]*"', lambda _m: f'line2="{l2}"', new)
        if new != text:
            mc.write_text(new, encoding="utf-8")
            changed.append("MiniMOTD/main.conf")
    if changed:
        log.info("MOTD 已同步：%s", ", ".join(changed))
    return changed
