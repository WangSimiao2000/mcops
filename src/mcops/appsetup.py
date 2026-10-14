"""P4：首次安装向导（空目录 → 可运行的 Fabric 服务端）与 mc-ops 自身升级/回滚（上传离线安装包）。"""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import secrets
import shutil
import tarfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcops import gameversions as gv
from mcops.config import update_secrets

# ---- 首次安装 --------------------------------------------------------------------


@dataclass
class FreshSettings:
    motd: str = "A Minecraft Server"
    max_players: int = 20
    online_mode: bool = False
    white_list: bool = True
    difficulty: str = "normal"
    view_distance: int = 10
    port: int = 25565


DIFFICULTIES = {"peaceful", "easy", "normal", "hard"}


def _props_text(fs: FreshSettings, rcon_port: int, rcon_password: str) -> str:
    vals = {
        "motd": fs.motd, "server-port": str(fs.port), "max-players": str(fs.max_players),
        "online-mode": "true" if fs.online_mode else "false",
        "white-list": "true" if fs.white_list else "false", "enforce-whitelist": "true" if fs.white_list else "false",
        "difficulty": fs.difficulty, "view-distance": str(fs.view_distance),
        "enable-rcon": "true", "rcon.port": str(rcon_port), "rcon.password": rcon_password,
        "broadcast-rcon-to-ops": "false", "log-ips": "true", "enable-status": "true",
        # 离线模式下聊天签名无意义且会刷警告
        "enforce-secure-profile": "true" if fs.online_mode else "false",
    }  # fmt: skip
    head = (
        f"#Minecraft server properties\n#由 mc-ops 首次安装生成 {datetime.now():%Y-%m-%d %H:%M}\n"
    )
    return head + "".join(f"{k}={gameprops_escape(v)}\n" for k, v in vals.items())


def gameprops_escape(v: str) -> str:
    from mcops.gameprops import _escape

    return _escape(v)


def write_fresh_files(game_dir: Path, fs: FreshSettings, rcon_port: int, secrets_file: Path) -> str:
    """写 eula.txt（调用方必须已拿到用户同意）、server.properties，并把随机 RCON 密码存进 secrets.yaml。"""
    if (
        fs.difficulty not in DIFFICULTIES
        or not (1 <= fs.port <= 65535)
        or not (1 <= fs.max_players <= 1000)
    ):
        raise gv.UpgradeError("安装参数不合法")
    if "\n" in fs.motd or len(fs.motd) > 120:
        raise gv.UpgradeError("MOTD 必须是一行，且不超过 120 字")
    game_dir.mkdir(parents=True, exist_ok=True)
    props = game_dir / "server.properties"
    if props.exists():
        raise gv.UpgradeError("server.properties 已存在：这不是一个空目录，请改用「游戏版本」升级")
    pw = secrets.token_urlsafe(24)
    update_secrets(secrets_file, {"rcon_password": pw})
    fd = os.open(props, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(_props_text(fs, rcon_port, pw))
    (game_dir / "eula.txt").write_text(
        f"#同意 Minecraft EULA（https://aka.ms/MinecraftEULA），由 mc-ops 安装向导在 {datetime.now(UTC).isoformat()} 记录\n"
        "eula=true\n",
        encoding="utf-8",
    )
    (game_dir / "mods").mkdir(exist_ok=True)
    return pw


# ---- 自身升级 --------------------------------------------------------------------

BUNDLE_MAX_BYTES = 300 * 1024 * 1024
# git 短哈希，或 git describe 的标签形式（v0.1.0、v0.1.0-3-gabc1234）；不能含 / 和 ..
REV_RE = re.compile(r"^(?:[0-9a-f]{7,40}|v\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?)$")
REQUIRED = ("REVISION", "requirements.txt", "deploy/install.sh", "web-dist/index.html")


def bundles_dir() -> Path:
    return Path.home() / ".cache" / "mcops-bundles"


def list_bundles(current: str | None) -> list[dict[str, Any]]:
    out = []
    root = bundles_dir()
    for d in (
        sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        if root.is_dir()
        else []
    ):
        if not (d.is_dir() and REV_RE.match(d.name) and all((d / r).exists() for r in REQUIRED)):
            continue
        out.append({
            "revision": d.name, "current": d.name == current,
            "installed_at": datetime.fromtimestamp(d.stat().st_mtime, UTC).isoformat(),
        })  # fmt: skip
    return out[:10]


def unpack_bundle(data: bytes) -> str:
    """校验并解开上传的安装包（make-bundle.sh --tar 生成的 .tar.gz），返回版本号。"""
    if len(data) > BUNDLE_MAX_BYTES:
        raise gv.UpgradeError("安装包超过 300 MB")
    try:
        tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
    except tarfile.TarError as e:
        raise gv.UpgradeError(f"不是有效的 .tar.gz：{e}") from None
    with tf:
        names = set(tf.getnames())
        missing = [r for r in REQUIRED if r not in names]
        if missing:
            raise gv.UpgradeError(f"不是 mc-ops 安装包，缺少：{', '.join(missing)}")
        rev_f = tf.extractfile("REVISION")
        rev = rev_f.read().decode().strip() if rev_f else ""
        if not REV_RE.match(rev):
            raise gv.UpgradeError(f"安装包版本号不合法：{rev!r}")
        root = bundles_dir()
        root.mkdir(parents=True, exist_ok=True)
        tmp = root / f".{rev}.tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        tf.extractall(tmp, filter="data")  # 拒绝绝对路径、..、设备文件、越界符号链接
    dest = root / rev
    shutil.rmtree(dest, ignore_errors=True)
    os.replace(tmp, dest)
    return rev


async def schedule_install(rev: str, unit: str, runner: Callable[..., Any] | None = None) -> None:
    """交给一个独立的临时 systemd 单元：2 秒后执行 install.sh 并重启 agent（本请求先正常返回）。"""
    d = bundles_dir() / rev
    if not REV_RE.match(rev) or not (d / "deploy" / "install.sh").is_file():
        raise gv.UpgradeError(f"找不到版本 {rev} 的安装包")
    log = d / "install.log"
    script = (
        f'bash "{d}/deploy/install.sh" "{d}" > "{log}" 2>&1 && systemctl --user restart "{unit}"'
    )
    argv = ["systemd-run", "--user", "--collect", "--quiet", "--on-active=2",
            f"--unit=mcops-self-update-{rev}", "/bin/bash", "-c", script]  # fmt: skip
    if runner is not None:
        await runner(argv)
        return
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise gv.UpgradeError(f"无法安排升级：{err.decode().strip()}")
    (d / "scheduled.json").write_text(json.dumps({"at": datetime.now(UTC).isoformat()}))
