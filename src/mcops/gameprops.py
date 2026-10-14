"""server.properties 可视化编辑（P1）：按已知键给类型/范围/中文说明，保留原文件的顺序与注释。

写回前备份为 server.properties.bak-YYYYmmdd-HHMMSS；改动在下次开服生效。
mc-ops 自己管理的键（RCON、端口、MOTD 同步）只读，机密键不返回值。
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

Kind = Literal["bool", "int", "enum", "string"]


@dataclass(frozen=True)
class Prop:
    kind: Kind
    group: str
    title: str
    description: str = ""
    options: tuple[str, ...] = ()
    min: int | None = None
    max: int | None = None


G_PLAY, G_WORLD, G_PLAYER, G_NET, G_OTHER = "玩法", "世界", "玩家与权限", "网络与性能", "其它"
B: Kind = "bool"
INT: Kind = "int"
E: Kind = "enum"
S: Kind = "string"

KNOWN: dict[str, Prop] = {
    "difficulty": Prop(E, G_PLAY, "难度", options=("peaceful", "easy", "normal", "hard")),
    "gamemode": Prop(E, G_PLAY, "默认游戏模式", options=("survival", "creative", "adventure", "spectator")),
    "force-gamemode": Prop(B, G_PLAY, "强制默认游戏模式", "玩家每次进服都切回默认模式"),
    "hardcore": Prop(B, G_PLAY, "极限模式", "死亡后变为旁观者"),
    "pvp": Prop(B, G_PLAY, "允许 PVP"),
    "allow-flight": Prop(B, G_PLAY, "允许飞行", "关闭时悬空过久会被踢；mc-ops 的悬空检测只告警不踢人"),
    "spawn-monsters": Prop(B, G_PLAY, "生成怪物"),
    "enable-command-block": Prop(B, G_PLAY, "启用命令方块"),
    "allow-nether": Prop(B, G_WORLD, "允许下界"),
    "generate-structures": Prop(B, G_WORLD, "生成结构", "只影响新生成的区块"),
    "level-name": Prop(S, G_WORLD, "世界目录名", "改了会加载（或新建）另一个世界"),
    "level-seed": Prop(S, G_WORLD, "世界种子", "只对新世界生效"),
    "level-type": Prop(S, G_WORLD, "世界类型", "只对新世界生效"),
    "max-world-size": Prop(INT, G_WORLD, "世界边界半径", min=1, max=29999984),
    "spawn-protection": Prop(INT, G_WORLD, "出生点保护半径", "0 表示不保护（OP 不受限）", min=0, max=1000),
    "white-list": Prop(B, G_PLAYER, "启用白名单"),
    "enforce-whitelist": Prop(B, G_PLAYER, "重载白名单时踢出不在名单的玩家"),
    "online-mode": Prop(B, G_PLAYER, "正版验证", "关闭即离线模式；离线模式务必配合白名单与 EasyAuth"),
    "max-players": Prop(INT, G_PLAYER, "最大玩家数", min=1, max=1000),
    "op-permission-level": Prop(INT, G_PLAYER, "OP 权限等级", min=0, max=4),
    "function-permission-level": Prop(INT, G_PLAYER, "函数权限等级", min=1, max=4),
    "player-idle-timeout": Prop(INT, G_PLAYER, "挂机踢出（分钟）", "0 表示不踢", min=0, max=10080),
    "enforce-secure-profile": Prop(B, G_PLAYER, "强制安全聊天签名", "离线模式下应关闭"),
    "hide-online-players": Prop(B, G_PLAYER, "在服务器列表隐藏在线玩家"),
    "log-ips": Prop(B, G_PLAYER, "日志记录玩家 IP", "mc-ops 的会话与安全规则依赖它"),
    "view-distance": Prop(INT, G_NET, "视距（区块）", "越大越吃内存和带宽", min=3, max=32),
    "simulation-distance": Prop(INT, G_NET, "模拟距离（区块）", "实体与红石运行的范围，影响 CPU", min=3, max=32),
    "entity-broadcast-range-percentage": Prop(INT, G_NET, "实体可见范围（%）", min=10, max=1000),
    "network-compression-threshold": Prop(INT, G_NET, "网络压缩阈值（字节）", "-1 关闭压缩", min=-1, max=65535),
    "max-tick-time": Prop(INT, G_NET, "单 tick 超时（毫秒）", "超过即判定卡死并关服；-1 关闭", min=-1, max=600000),
    "pause-when-empty-seconds": Prop(INT, G_NET, "无人时暂停（秒）", "0 表示不暂停", min=0, max=86400),
    "rate-limit": Prop(INT, G_NET, "每秒包数上限", "0 表示不限", min=0, max=100000),
    "sync-chunk-writes": Prop(B, G_NET, "同步写区块", "更安全但更慢"),
    "use-native-transport": Prop(B, G_NET, "使用原生网络传输（Linux epoll）"),
    "prevent-proxy-connections": Prop(B, G_NET, "拒绝代理/VPN 连接", "仅正版模式有效"),
    "accepts-transfers": Prop(B, G_NET, "接受其它服务器转移过来的玩家"),
    "require-resource-pack": Prop(B, G_OTHER, "强制资源包"),
    "resource-pack": Prop(S, G_OTHER, "资源包 URL"),
    "resource-pack-sha1": Prop(S, G_OTHER, "资源包 SHA1"),
    "resource-pack-prompt": Prop(S, G_OTHER, "资源包提示语"),
    "enable-status": Prop(B, G_OTHER, "在服务器列表显示状态", "关闭后 mc-ops 与 QQ 机器人的在线检测会失效"),
    "broadcast-console-to-ops": Prop(B, G_OTHER, "控制台输出广播给 OP"),
    "broadcast-rcon-to-ops": Prop(B, G_OTHER, "RCON 输出广播给 OP"),
    "enable-code-of-conduct": Prop(B, G_OTHER, "显示行为准则"),
}  # fmt: skip

# 机密：不返回值、不可编辑
SENSITIVE = {"rcon.password", "management-server-secret"}
# mc-ops 依赖的键：只读，附原因
MANAGED_BASE = {
    "enable-rcon": "mc-ops 通过 RCON 控制服务器，必须开启",
    "rcon.port": "与 mc-ops 配置的 RCON 端口一致，改端口请在「服务器配置」里改",
    "rcon.password": "由 mc-ops 管理",
    "server-port": "与 mc-ops 配置的游戏端口一致，请在「服务器配置」里改",
    "management-server-secret": "机密",
}
MOTD_MANAGED = "已开启 MOTD 同步（服务器配置 → 运维任务），每次开服会被覆盖"


class PropsError(ValueError):
    pass


_LINE = re.compile(r"^([^#!=:\s][^=:]*?)\s*[=:]\s?(.*)$")


def _unescape(v: str) -> str:
    return v.replace("\\:", ":").replace("\\=", "=").replace("\\\\", "\\")


def _escape(v: str) -> str:
    return v.replace("\\", "\\\\").replace(":", "\\:").replace("=", "\\=")


def parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = _LINE.match(line)
        if m:
            out[m.group(1)] = _unescape(m.group(2))
    return out


def managed_keys(motd_sync: bool) -> dict[str, str]:
    m = dict(MANAGED_BASE)
    if motd_sync:
        m["motd"] = MOTD_MANAGED
    return m


def view(path: Path, motd_sync: bool) -> dict[str, Any]:
    values = parse(path.read_text(encoding="utf-8"))
    managed = managed_keys(motd_sync)
    items: list[dict[str, Any]] = []
    for key, value in values.items():
        p = KNOWN.get(key)
        item: dict[str, Any] = {
            "key": key,
            "value": None if key in SENSITIVE else _typed(p, value),
            "known": p is not None,
            "type": p.kind if p else "string",
            "group": p.group if p else G_OTHER,
            "title": p.title if p else key,
            "description": p.description if p else "",
            "managed": key in managed,
        }
        if key in managed:
            item["managed_reason"] = managed[key]
        if key in SENSITIVE:
            item["sensitive"] = True
        if p and p.options:
            item["options"] = list(p.options)
        if p and p.min is not None:
            item["min"] = p.min
        if p and p.max is not None:
            item["max"] = p.max
        items.append(item)
    order = list(KNOWN)
    items.sort(
        key=lambda it: (not it["known"], order.index(it["key"]) if it["known"] else 0, it["key"])
    )
    return {"path": str(path), "items": items}


def _typed(p: Prop | None, raw: str) -> Any:
    if p is None:
        return raw
    if p.kind == "bool":
        return raw.strip().lower() == "true"
    if p.kind == "int":
        try:
            return int(raw.strip())
        except ValueError:
            return raw
    return raw


def _coerce(key: str, value: Any) -> str:
    p = KNOWN.get(key)
    if p is None or p.kind == "string":
        if not isinstance(value, str) or "\n" in value or "\r" in value:
            raise PropsError(f"{key}：必须是单行文本")
        return value
    if p.kind == "bool":
        if not isinstance(value, bool):
            raise PropsError(f"{key}：必须是开/关")
        return "true" if value else "false"
    if p.kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise PropsError(f"{key}：必须是整数")
        if (p.min is not None and value < p.min) or (p.max is not None and value > p.max):
            raise PropsError(f"{key}：必须在 {p.min} 到 {p.max} 之间")
        return str(value)
    if value not in p.options:
        raise PropsError(f"{key}：只能是 {' / '.join(p.options)}")
    return str(value)


def update(path: Path, changes: dict[str, Any], motd_sync: bool) -> dict[str, Any]:
    """校验并写回；只改动指定键的那一行，其余原样保留。未知键（不在文件里）不会新增。"""
    if not changes:
        return {"ok": True, "changed": [], "backup": None}
    text = path.read_text(encoding="utf-8")
    current = parse(text)
    managed = managed_keys(motd_sync)
    rendered: dict[str, str] = {}
    for key, value in changes.items():
        if key not in current:
            raise PropsError(f"{key}：文件里没有这个键")
        if key in managed or key in SENSITIVE:
            raise PropsError(f"{key}：{managed.get(key, '机密')}，不能在这里修改")
        new = _coerce(key, value)
        if new != current[key]:
            rendered[key] = new
    if not rendered:
        return {"ok": True, "changed": [], "backup": None}
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        m = _LINE.match(line.rstrip("\r\n"))
        if m and m.group(1) in rendered:
            nl = line[len(line.rstrip("\r\n")) :]
            lines[i] = f"{m.group(1)}={_escape(rendered[m.group(1)])}{nl or os.linesep}"
    backup = path.with_name(f"{path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(path, backup)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(lines), encoding="utf-8")
    shutil.copymode(path, tmp)
    os.replace(tmp, path)
    _prune_backups(path)
    return {"ok": True, "changed": sorted(rendered), "backup": backup.name}


def _prune_backups(path: Path, keep: int = 10) -> None:
    baks = sorted(path.parent.glob(f"{path.name}.bak-*"), reverse=True)
    for old in baks[keep:]:
        old.unlink(missing_ok=True)
