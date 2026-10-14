#!/usr/bin/env python3
"""准备本地开发用的 Fabric 测试服与 agent 配置（幂等，可重复运行）。

    uv run python dev/setup_server.py

产物全部在 dev/.local/（已 gitignore）：
    dev/.local/server/        Fabric 1.21.11 服务端 + 关键 Mod（与生产一致的认证/代理链）
    dev/.local/secrets.yaml   RCON 密码、管理员密码哈希（600 权限）
    dev/.local/admin-password.txt  本地面板登录密码（仅开发用，600 权限）

测试服只监听 127.0.0.1:25566（RCON 127.0.0.1:25576），不会和生产冲突，也不对外。
运行即表示你同意 Minecraft EULA（https://aka.ms/MinecraftEULA），脚本会写入 eula=true。
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

MC_VERSION = "1.21.11"
# 与生产保持一致的 Mod（见生产 GameFile/mods）：认证、真实 IP、carpet 假人
MODS = ["fabric-api", "easyauth", "proxy-protocol-support", "carpet"]
UA = "mc-ops-dev-setup/0.1 (github.com/WangSimiao2000/mc-ops)"

ROOT = Path(__file__).resolve().parent
LOCAL = ROOT / ".local"
SERVER = LOCAL / "server"


def get_json(url: str) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def download(url: str, dest: Path, sha512: str | None = None) -> None:
    if dest.exists() and sha512 and hashlib.sha512(dest.read_bytes()).hexdigest() == sha512:
        print(f"  已存在且校验通过：{dest.name}")
        return
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    if sha512 and hashlib.sha512(data).hexdigest() != sha512:
        raise SystemExit(f"sha512 校验失败：{url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(dest)
    print(f"  下载完成：{dest.name}（{len(data) // 1024} KiB）")


def fabric_server_jar() -> str:
    loaders = get_json(f"https://meta.fabricmc.net/v2/versions/loader/{MC_VERSION}")
    assert isinstance(loaders, list)
    loader = next(x["loader"]["version"] for x in loaders if x["loader"].get("stable"))
    installers = get_json("https://meta.fabricmc.net/v2/versions/installer")
    assert isinstance(installers, list)
    installer = next(x["version"] for x in installers if x.get("stable"))
    name = f"fabric-server-mc.{MC_VERSION}-loader.{loader}-launcher.{installer}.jar"
    dest = SERVER / name
    if not dest.exists():
        url = (
            f"https://meta.fabricmc.net/v2/versions/loader/{MC_VERSION}/{loader}/{installer}"
            "/server/jar"
        )
        download(url, dest)
        # Fabric Meta 不提供哈希：至少校验是合法 jar（沿用旧 upgrade.sh 的 zip 校验）
        if not zipfile.is_zipfile(dest):
            dest.unlink()
            raise SystemExit("下载的 Fabric 服务端不是合法的 jar")
    print(f"Fabric 服务端：{name}")
    return name


def install_mods() -> None:
    mods = SERVER / "mods"
    mods.mkdir(parents=True, exist_ok=True)
    wanted: set[str] = set()
    for slug in MODS:
        q = urllib.parse.urlencode(
            {"loaders": json.dumps(["fabric"]), "game_versions": json.dumps([MC_VERSION])}
        )
        versions = get_json(f"https://api.modrinth.com/v2/project/{slug}/version?{q}")
        assert isinstance(versions, list)
        if not versions:
            raise SystemExit(f"Modrinth 上没有 {slug} 的 {MC_VERSION} 版本")
        v = next((x for x in versions if x["version_type"] == "release"), versions[0])
        f = next((x for x in v["files"] if x.get("primary")), v["files"][0])
        print(f"Mod {slug} {v['version_number']}")
        download(f["url"], mods / f["filename"], f["hashes"]["sha512"])
        wanted.add(f["filename"])
    for stale in mods.glob("*.jar"):
        if stale.name not in wanted:
            stale.unlink()
            print(f"  移除旧版本：{stale.name}")


def write_server_files(rcon_password: str) -> None:
    (SERVER / "eula.txt").write_text("eula=true\n")
    props = {
        "server-ip": "127.0.0.1",
        "server-port": "25566",
        "online-mode": "false",
        "white-list": "true",
        "enforce-whitelist": "true",
        "enable-rcon": "true",
        "rcon.port": "25576",
        "rcon.password": rcon_password,
        "broadcast-rcon-to-ops": "false",
        "level-name": "world",
        "level-type": "minecraft\\:flat",
        "generate-structures": "false",
        "spawn-protection": "0",
        "view-distance": "4",
        "simulation-distance": "4",
        "max-players": "10",
        "motd": "mc-ops dev server",
        "enable-query": "false",
    }
    path = SERVER / "server.properties"
    existing: dict[str, str] = {}
    if path.exists():
        for ln in path.read_text().splitlines():
            if "=" in ln and not ln.startswith("#"):
                k, _, v = ln.partition("=")
                existing[k] = v
    existing.update(props)
    path.write_text("".join(f"{k}={v}\n" for k, v in sorted(existing.items())))
    os.chmod(path, 0o600)  # 含 RCON 密码
    cfg = SERVER / "config"
    cfg.mkdir(exist_ok=True)
    (cfg / "proxy_protocol_support.json").write_text(
        json.dumps(
            {
                "enableProxyProtocol": True,
                "proxyServerIPs": ["10.88.88.1"],
                "directAccessIPs": ["127.0.0.1"],
                "whitelistTCPShieldServers": False,
            },
            indent=2,
        )
        + "\n"
    )


def write_secrets() -> str:
    sys.path.insert(0, str(ROOT.parent / "src"))
    from mcops.auth import hash_password

    LOCAL.mkdir(parents=True, exist_ok=True)
    sec = LOCAL / "secrets.yaml"
    pw_file = LOCAL / "admin-password.txt"
    if sec.exists() and pw_file.exists():
        import yaml

        data = yaml.safe_load(sec.read_text())
        print("复用已有 dev/.local/secrets.yaml")
        return str(data["rcon_password"])
    rcon = secrets.token_urlsafe(24)
    admin = secrets.token_urlsafe(12)
    old = os.umask(0o077)
    try:
        sec.write_text(
            f"rcon_password: {json.dumps(rcon)}\n"
            f"admin_password_hash: {json.dumps(hash_password(admin))}\n"
        )
        pw_file.write_text(admin + "\n")
    finally:
        os.umask(old)
    print(f"已生成本地面板密码，见 {pw_file}")
    return rcon


def main() -> int:
    SERVER.mkdir(parents=True, exist_ok=True)
    rcon = write_secrets()
    jar = fabric_server_jar()
    install_mods()
    write_server_files(rcon)
    (LOCAL / "jar-name.txt").write_text(jar + "\n")
    print("\n完成。启动 agent：")
    print("  uv run mcops-agent --config dev/mcops.dev.yaml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
