"""能力探测：看服务端装了哪些关键 mod、配了哪些可选组件，面板和规则据此开关功能。"""

from __future__ import annotations

import json
import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcops.mods import parse_server_jar

log = logging.getLogger(__name__)

# mod id -> 能力名
KNOWN_MODS: dict[str, str] = {
    "easyauth": "easyauth",
    "carpet": "carpet",
    "proxy_protocol_support": "proxy_protocol",
    "minimotd-fabric": "minimotd",
}


@dataclass
class Capabilities:
    mc_version: str | None = None
    loader: str = "fabric"
    loader_version: str | None = None
    mods: dict[str, bool] = field(default_factory=lambda: dict.fromkeys(KNOWN_MODS.values(), False))
    proxy_sources: list[str] = field(default_factory=list)
    features: dict[str, bool] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "mc_version": self.mc_version,
            "loader": self.loader,
            "loader_version": self.loader_version,
            "mods": self.mods,
            "proxy_source": self.proxy_sources[0] if self.proxy_sources else None,
            "proxy_sources": self.proxy_sources,
            "features": self.features,
        }


def _mod_id(jar: Path) -> str | None:
    try:
        with zipfile.ZipFile(jar) as zf:
            meta = json.loads(zf.read("fabric.mod.json").decode("utf-8"), strict=False)
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None
    v = meta.get("id")
    return v if isinstance(v, str) else None


def easyauth_allows(game_dir: Path, packet_id: str) -> bool:
    """EasyAuth 在玩家 /login 之前是否放行这个自定义数据包（按 extended.conf 判断，读不到当作不放行）。"""
    try:
        text = (game_dir / "config" / "EasyAuth" / "extended.conf").read_text(encoding="utf-8")
    except OSError:
        return False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip().replace(" ", "")
        if line in ("allow-custom-packets=true", "allowCustomPackets=true"):
            return True
        if line.startswith(("allowed-custom-packets=", "allowedCustomPackets=")) and (
            f'"{packet_id}"' in line
        ):
            return True
    return False


def unavailable_rules(caps: Capabilities, game_dir: Path) -> dict[str, str]:
    """依赖的信号在当前环境里根本收不到的规则 → 不可用原因。这些规则强制停用，面板显示原因。"""
    out: dict[str, str] = {}
    if not caps.mods.get("carpet"):
        out["fingerprint_mismatch"] = "没装 carpet，收不到客户端握手，规则会把每个人都判成可疑"
    elif caps.mods.get("easyauth") and not easyauth_allows(game_dir, "carpet:hello"):
        out["fingerprint_mismatch"] = (
            "EasyAuth 在玩家登录前丢弃 carpet 客户端握手（carpet:hello），规则收不到信号。"
            "不要为它放行 carpet:hello：同一通道能以该玩家身份执行命令，等于未登录就能执行命令"
        )
    return out


def proxy_sources(game_dir: Path) -> list[str]:
    """proxy-protocol-support 信任的代理源地址（frpc 的 localIP）。封禁它会误伤所有玩家。"""
    p = game_dir / "config" / "proxy_protocol_support.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not data.get("enableProxyProtocol", False):
        return []
    ips = data.get("proxyServerIPs", [])
    return [str(x) for x in ips if isinstance(x, str)]


def detect(game_dir: Path, jar: str, features: dict[str, bool] | None = None) -> Capabilities:
    caps = Capabilities(features=dict(features or {}))
    caps.mc_version, caps.loader_version = parse_server_jar(jar)
    mods_dir = game_dir / "mods"
    if mods_dir.is_dir():
        for j in mods_dir.glob("*.jar"):  # 只看启用的 mod（disabled/ 子目录不算）
            name = KNOWN_MODS.get(_mod_id(j) or "")
            if name:
                caps.mods[name] = True
    if caps.mods["proxy_protocol"]:
        caps.proxy_sources = proxy_sources(game_dir)
    return caps
