"""P3：游戏版本一键安装 / 升级（Fabric，单实例）与 Java 管理。

升级分三步，保证停服窗口短、失败可回滚：

1. **预检**（不改任何东西）：目标 MC 需要的 Java 版本、每个 mod 在目标版本下是否有兼容版本、
   systemd 单元是否由 mc-ops 管理、磁盘空间。
2. **准备**（服务器照常运行）：在暂存目录下载新的 Fabric 启动器、Mojang 原版服务端（sha1 校验）、
   兼容的 mod（sha512 校验），需要时下载 Java（sha256 校验）；再在暂存目录里用新版本**试启动一次**，
   eula=false 让它下载并解压完全部依赖后自行退出 —— 既验证新版本能起来，也让真正停服后的启动无需联网
   （10-08 回档就是升级后联网重建 .fabric 失败）。
3. **应用**（运维任务，停服几分钟）：倒计时广播 → 停服 → 完整冷备份 → 替换 jar / .fabric / libraries /
   versions / mods → 开服等就绪。任何一步失败：停服，把这次替换的文件和世界从冷备份恢复，用旧版本开回去。
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import tarfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from mcops.mods import (
    MAX_DOWNLOAD_BYTES,
    DownloadFn,
    HttpFn,
    _HashCache,
    check_updates,
    parse_server_jar,
    scan_mods,
)

log = logging.getLogger(__name__)

FABRIC_META = "https://meta.fabricmc.net/v2"
MOJANG_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
ADOPTIUM_API = "https://api.adoptium.net/v3"
# 国内直连 GitHub 很慢（实测 37 KB/s），清华镜像与官方文件名一致，用官方 API 给的 sha256 校验
JAVA_MIRRORS = {
    "tuna": "https://mirrors.tuna.tsinghua.edu.cn/Adoptium/{major}/jre/x64/linux/{name}",
    "official": "",  # 用 Adoptium API 返回的原始链接
}
ALLOWED_HOSTS = {
    "meta.fabricmc.net", "maven.fabricmc.net", "piston-meta.mojang.com", "piston-data.mojang.com",
    "launcher.mojang.com", "api.adoptium.net", "github.com", "objects.githubusercontent.com",
    "release-assets.githubusercontent.com", "mirrors.tuna.tsinghua.edu.cn",
}  # fmt: skip
JRE_MAX_BYTES = 150 * 1024 * 1024
JAR_PREFIX = "fabric-server-mc."
SWAP_DIRS = ("libraries", "versions", ".fabric")
PREWARM_TIMEOUT_S = 600.0


class UpgradeError(RuntimeError):
    pass


def _check_url(url: str) -> str:
    u = urlsplit(url)
    if u.scheme != "https" or u.hostname not in ALLOWED_HOSTS:
        raise UpgradeError(f"下载地址不在允许范围：{u.hostname}")
    return url


# ---- 版本列表 ------------------------------------------------------------------


async def list_versions(http: HttpFn) -> dict[str, Any]:
    games = await http("GET", f"{FABRIC_META}/versions/game", None)
    loaders = await http("GET", f"{FABRIC_META}/versions/loader", None)
    installers = await http("GET", f"{FABRIC_META}/versions/installer", None)
    return {
        "games": [
            {"version": g["version"], "stable": bool(g.get("stable"))}
            for g in games
            if isinstance(g, dict)
        ],
        "loaders": [
            {"version": x["version"], "stable": bool(x.get("stable"))}
            for x in loaders
            if isinstance(x, dict)
        ],
        "installer": next(
            (x["version"] for x in installers if isinstance(x, dict) and x.get("stable")), None
        ),
    }


async def mojang_server_info(http: HttpFn, mc: str) -> dict[str, Any]:
    manifest = await http("GET", MOJANG_MANIFEST, None)
    entry = next((v for v in manifest.get("versions", []) if v.get("id") == mc), None)
    if entry is None:
        raise UpgradeError(f"Mojang 版本清单里没有 {mc}")
    meta = await http("GET", _check_url(str(entry["url"])), None)
    server = (meta.get("downloads") or {}).get("server")
    if not isinstance(server, dict):
        raise UpgradeError(f"{mc} 没有官方服务端")
    return {
        "java_major": int((meta.get("javaVersion") or {}).get("majorVersion") or 8),
        "server_url": _check_url(str(server["url"])),
        "server_sha1": str(server["sha1"]),
        "server_size": int(server.get("size") or 0),
    }


# ---- Java ----------------------------------------------------------------------


Runner = Callable[[list[str], Path, float], Awaitable[tuple[int, str]]]


async def _default_runner(argv: list[str], cwd: Path, timeout: float) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=cwd, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )  # fmt: skip
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        return -1, "超时"
    return proc.returncode or 0, out.decode("utf-8", "replace")


_JAVA_VER = re.compile(r'version "(\d+)(?:\.(\d+))?')


async def java_major(java_bin: str, runner: Runner = _default_runner) -> int | None:
    if shutil.which(java_bin) is None and not Path(java_bin).is_file():
        return None
    code, out = await runner([java_bin, "-version"], Path.cwd(), 20)
    m = _JAVA_VER.search(out)
    if code != 0 or not m:
        return None
    major = int(m.group(1))
    return int(m.group(2)) if major == 1 and m.group(2) else major  # 1.8 -> 8


def managed_javas(data_dir: Path) -> list[dict[str, Any]]:
    root = data_dir / "java"
    out = []
    for d in sorted(root.glob("*")) if root.is_dir() else []:
        java = d / "bin" / "java"
        meta = d / ".mcops.json"
        if java.is_file() and meta.is_file():
            info = json.loads(meta.read_text())
            out.append(
                {"major": info.get("major"), "release": info.get("release"), "java_bin": str(java)}
            )
    return out


async def install_java(
    http: HttpFn, download: DownloadFn, data_dir: Path, major: int, mirror: str = "tuna"
) -> str:
    """下载 Temurin JRE 到 <data_dir>/java/<release>/，sha256 校验，返回 java 可执行文件路径。已装过就直接返回。"""
    for j in managed_javas(data_dir):
        if j["major"] == major:
            return str(j["java_bin"])
    q = "os=linux&architecture=x64&image_type=jre&vendor=eclipse"
    assets = await http("GET", f"{ADOPTIUM_API}/assets/latest/{major}/hotspot?{q}", None)
    if not isinstance(assets, list) or not assets:
        raise UpgradeError(f"Adoptium 没有 Java {major} 的 Linux x64 JRE")
    pkg = assets[0]["binary"]["package"]
    name, sha256, size = str(pkg["name"]), str(pkg["checksum"]), int(pkg["size"])
    if not re.fullmatch(r"[A-Za-z0-9._+-]+\.tar\.gz", name):
        raise UpgradeError(f"异常的文件名：{name}")
    tpl = JAVA_MIRRORS.get(mirror) or ""
    url = tpl.format(major=major, name=name) if tpl else _check_url(str(pkg["link"]))
    root = data_dir / "java"
    root.mkdir(parents=True, exist_ok=True)
    tgz = root / f"{name}.part"
    try:
        n = await download(_check_url(url), tgz, JRE_MAX_BYTES)
        if n != size:
            raise UpgradeError(f"Java 安装包大小不符：期望 {size}，实得 {n}")
        if await asyncio.to_thread(_sha256, tgz) != sha256:
            raise UpgradeError("Java 安装包 sha256 校验失败")
        release = str(assets[0].get("release_name") or name)
        dest = root / re.sub(r"[^A-Za-z0-9._+-]", "_", release)
        await asyncio.to_thread(_extract_jre, tgz, dest)
        (dest / ".mcops.json").write_text(
            json.dumps({"major": major, "release": release, "sha256": sha256})
        )
    finally:
        tgz.unlink(missing_ok=True)
    java = dest / "bin" / "java"
    got = await java_major(str(java))
    if got != major:
        raise UpgradeError(f"安装的 Java 无法运行（检测到版本 {got}）")
    return str(java)


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha1(p: Path) -> str:
    h = hashlib.sha1(usedforsecurity=False)  # Mojang 只提供 sha1，用作完整性校验
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _extract_jre(tgz: Path, dest: Path) -> None:
    """JRE 包顶层是一个目录（jdk-21.0.x-jre/），去掉这一层解到 dest。filter='data' 防路径穿越/设备文件。"""
    tmp = dest.with_name(dest.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    with tarfile.open(tgz) as tf:
        tf.extractall(tmp, filter="data")
    tops = [p for p in tmp.iterdir()]
    if len(tops) != 1 or not (tops[0] / "bin" / "java").is_file():
        shutil.rmtree(tmp, ignore_errors=True)
        raise UpgradeError("Java 安装包结构不对")
    shutil.rmtree(dest, ignore_errors=True)
    os.replace(tops[0], dest)
    shutil.rmtree(tmp, ignore_errors=True)
    (dest / "bin" / "java").chmod(0o755)


# ---- 预检 ----------------------------------------------------------------------


@dataclass
class Target:
    mc: str
    loader: str
    installer: str


def jar_name(t: Target) -> str:
    return f"{JAR_PREFIX}{t.mc}-loader.{t.loader}-launcher.{t.installer}.jar"


def current_jar(game_dir: Path) -> str | None:
    jars = sorted(p.name for p in game_dir.glob(f"{JAR_PREFIX}*.jar"))
    return jars[-1] if jars else None


async def precheck(
    http: HttpFn,
    game_dir: Path,
    hashes: _HashCache,
    java_bin: str,
    target: Target,
    *,
    systemd: bool,
    manage_unit: bool,
    runner: Runner = _default_runner,
) -> dict[str, Any]:
    cur = current_jar(game_dir)
    cur_mc, cur_loader = parse_server_jar(cur) if cur else (None, None)
    info = await mojang_server_info(http, target.mc)
    java_now = await java_major(java_bin, runner)
    blockers: list[str] = []
    warnings: list[str] = []
    if systemd and not manage_unit:
        blockers.append("服务端由 systemd 托管，但单元文件不是 mc-ops 生成的：请先在「设置 → 服务器配置 → 服务端 → 高级」"
                        "打开「由 mc-ops 生成 systemd 单元」并把 Java 路径设成实际路径，否则新 jar 名不会写进单元")  # fmt: skip
    if cur_mc and _ver_tuple(target.mc) < _ver_tuple(cur_mc):
        blockers.append(
            f"不支持降级（{cur_mc} → {target.mc}）：旧版本打不开新版本保存过的世界，请从备份恢复"
        )
    free = shutil.disk_usage(game_dir).free
    world = game_dir / "world"
    need = (
        sum(f.stat().st_size for f in world.rglob("*") if f.is_file()) if world.is_dir() else 0
    ) * 2 + 500 * 2**20
    if free < need:
        blockers.append(
            f"磁盘剩余 {free // 2**30} GB，不足以做冷备份和暂存（约需 {need // 2**30 + 1} GB）"
        )

    mods = await asyncio.to_thread(scan_mods, game_dir, hashes)
    enabled = [m for m in mods if m.enabled and m.kind == "fabric"]
    rows: list[dict[str, Any]] = []
    if enabled:
        res = await check_updates(enabled, target.mc, http)
        if res.get("error"):
            warnings.append(str(res["error"]))
        ups = res.get("updates", {})
        for m in enabled:
            u = ups.get(m.file, {})
            if u.get("source") != "modrinth":
                status = "unknown"
            elif u.get("has_update"):
                status = "update"
            else:
                status = "compatible"
            rows.append({
                "file": m.file, "id": m.id, "name": m.name, "version": m.version, "status": status,
                "new_version": u.get("version_number") if status == "update" else None,
                "new_file": u.get("filename") if status == "update" else None,
                "url": u.get("url") if status == "update" else None,
                "sha512": u.get("sha512") if status == "update" else None,
                "size": u.get("size") if status == "update" else None,
            })  # fmt: skip
    unknown = [r for r in rows if r["status"] == "unknown"]
    if unknown and cur_mc != target.mc:
        warnings.append(
            f"{len(unknown)} 个 mod 在 Modrinth 上找不到 {target.mc} 的版本（或不是从 Modrinth 装的），"
            "可以选择升级时先停用它们"
        )
    return {
        "current": {
            "jar": cur,
            "mc": cur_mc,
            "loader": cur_loader,
            "java_major": java_now,
            "java_bin": java_bin,
        },
        "target": {**target.__dict__, "jar": jar_name(target), "java_major": info["java_major"]},
        "java": {
            "required": info["java_major"],
            "current": java_now,
            "needs_install": java_now is None or java_now < info["java_major"],
        },
        "mods": rows,
        "blockers": blockers,
        "warnings": warnings,
        "fresh_install": cur is None,
    }


def _ver_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:4])


# ---- 准备（暂存 + 试启动） ---------------------------------------------------------


@dataclass
class Prepared:
    staging: Path
    target: Target
    jar: str
    java_bin: str
    mod_updates: list[dict[str, Any]] = field(default_factory=list)
    disable: list[str] = field(default_factory=list)
    prewarm_log: str = ""
    prepared_at: float = field(default_factory=time.time)

    def to_json(self) -> dict[str, Any]:
        return {
            "target": self.target.__dict__, "jar": self.jar, "java_bin": self.java_bin,
            "mod_updates": [{"file": m["file"], "new_file": m["new_file"]} for m in self.mod_updates],
            "disable": self.disable, "prewarm_log_tail": self.prewarm_log[-3000:],
            "prepared_at": datetime.fromtimestamp(self.prepared_at, UTC).isoformat(),
        }  # fmt: skip


async def prepare(
    http: HttpFn,
    download: DownloadFn,
    game_dir: Path,
    data_dir: Path,
    plan: dict[str, Any],
    *,
    disable_unknown: bool,
    java_mirror: str = "tuna",
    runner: Runner = _default_runner,
    note: Callable[[str], None] = lambda _m: None,
) -> Prepared:
    if plan["blockers"]:
        raise UpgradeError("预检未通过：" + "；".join(plan["blockers"]))
    t = Target(**{k: plan["target"][k] for k in ("mc", "loader", "installer")})
    staging = data_dir / "staging" / f"{t.mc}-{t.loader}"
    shutil.rmtree(staging, ignore_errors=True)
    # 新 mod 放在 _mods/，不能放 mods/：试启动时 Fabric 会加载 mods/ 里的东西
    (staging / "_mods").mkdir(parents=True)
    java_bin = plan["current"]["java_bin"]
    if plan["java"]["needs_install"]:
        note(f"下载 Java {plan['java']['required']}（Temurin JRE）")
        java_bin = await install_java(
            http, download, data_dir, int(plan["java"]["required"]), java_mirror
        )
        note(f"Java 已安装：{java_bin}")

    jar = jar_name(t)
    note(f"下载 Fabric 启动器 {jar}")
    url = _check_url(
        f"{FABRIC_META}/versions/loader/{quote(t.mc)}/{quote(t.loader)}/{quote(t.installer)}/server/jar"
    )
    n = await download(url, staging / jar, MAX_DOWNLOAD_BYTES)
    if n < 10_000 or not _is_zip(staging / jar):
        raise UpgradeError("Fabric 启动器下载不完整")

    info = await mojang_server_info(http, t.mc)
    vanilla = staging / ".fabric" / "server" / f"{t.mc}-server.jar"
    vanilla.parent.mkdir(parents=True)
    note(f"下载 Minecraft {t.mc} 原版服务端（{info['server_size'] // 2**20} MB）")
    await download(info["server_url"], vanilla, MAX_DOWNLOAD_BYTES)
    if await asyncio.to_thread(_sha1, vanilla) != info["server_sha1"]:
        raise UpgradeError("原版服务端 sha1 校验失败")

    from mcops.modstore import _download_verified

    ups = [m for m in plan["mods"] if m["status"] == "update"]
    for m in ups:
        note(f"下载 mod 新版本 {m['new_file']}")
        await _download_verified(download, _check_mod_url(m["url"]), staging / "_mods" / Path(m["new_file"]).name,
                                 m["sha512"], m["size"], MAX_DOWNLOAD_BYTES)  # fmt: skip
    disable = (
        [m["file"] for m in plan["mods"] if m["status"] == "unknown"] if disable_unknown else []
    )

    note("试启动新版本（eula=false，只下载依赖并验证能启动，不加载世界和 mod）")
    (staging / "eula.txt").write_text("eula=false\n")
    code, out = await runner(
        [java_bin, "-Xms256M", "-Xmx1G", "-jar", jar, "nogui"], staging, PREWARM_TIMEOUT_S
    )
    if "EULA" not in out and "eula" not in out:
        raise UpgradeError(f"新版本试启动失败（退出码 {code}）：\n{out[-2000:]}")
    if not (staging / "libraries").is_dir():
        raise UpgradeError("试启动后没有生成 libraries/，依赖可能没下载完整")
    note("试启动通过：依赖已就绪")
    return Prepared(staging, t, jar, java_bin, ups, disable, out)


def _check_mod_url(url: Any) -> str:
    u = urlsplit(str(url))
    if u.scheme != "https" or u.hostname != "cdn.modrinth.com":
        raise UpgradeError(f"mod 下载地址不在允许范围：{u.hostname}")
    return str(url)


def _is_zip(p: Path) -> bool:
    with p.open("rb") as f:
        return f.read(2) == b"PK"


# ---- 应用：替换文件 / 回滚 ---------------------------------------------------------


@dataclass
class SwapRecord:
    backup_dir: Path
    old_jar: str | None
    new_jar: str
    moved_dirs: list[str] = field(default_factory=list)
    new_mods: list[str] = field(default_factory=list)
    replaced_mods: list[str] = field(default_factory=list)


def swap_in(game_dir: Path, prep: Prepared, backup_dir: Path) -> SwapRecord:
    """停服后调用：把旧 jar / libraries / versions / .fabric / 被替换的 mod 移到 backup_dir，暂存的新文件移进来。"""
    backup_dir.mkdir(parents=True, exist_ok=True)
    old = current_jar(game_dir)
    rec = SwapRecord(backup_dir, old, prep.jar)
    for j in game_dir.glob(f"{JAR_PREFIX}*.jar"):
        shutil.move(str(j), str(backup_dir / j.name))
    for d in SWAP_DIRS:
        if (game_dir / d).exists():
            shutil.move(str(game_dir / d), str(backup_dir / d))
            rec.moved_dirs.append(d)
        if (prep.staging / d).exists():
            shutil.move(str(prep.staging / d), str(game_dir / d))
    shutil.move(str(prep.staging / prep.jar), str(game_dir / prep.jar))
    mods = game_dir / "mods"
    (backup_dir / "mods").mkdir(exist_ok=True)
    for m in prep.mod_updates:
        if (mods / m["file"]).exists():
            shutil.move(str(mods / m["file"]), str(backup_dir / "mods" / m["file"]))
            rec.replaced_mods.append(m["file"])
        name = Path(m["new_file"]).name
        shutil.move(str(prep.staging / "_mods" / name), str(mods / name))
        rec.new_mods.append(name)
    for f in prep.disable:
        if (mods / f).exists():
            shutil.move(str(mods / f), str(backup_dir / "mods" / f))
            rec.replaced_mods.append(f)
    (backup_dir / "swap.json").write_text(
        json.dumps(rec.__dict__, default=str, ensure_ascii=False, indent=1)
    )
    return rec


def swap_out(game_dir: Path, rec: SwapRecord) -> None:
    """回滚文件层面的替换（世界由冷备份恢复）。"""
    mods = game_dir / "mods"
    for name in rec.new_mods:
        (mods / name).unlink(missing_ok=True)
    for name in rec.replaced_mods:
        src = rec.backup_dir / "mods" / name
        if src.exists():
            shutil.move(str(src), str(mods / name))
    (game_dir / rec.new_jar).unlink(missing_ok=True)
    for j in rec.backup_dir.glob(f"{JAR_PREFIX}*.jar"):
        shutil.move(str(j), str(game_dir / j.name))
    for d in SWAP_DIRS:
        shutil.rmtree(game_dir / d, ignore_errors=True)
        if (rec.backup_dir / d).exists():
            shutil.move(str(rec.backup_dir / d), str(game_dir / d))


def restore_world(game_dir: Path, backup_tar: Path, quarantine: Path) -> None:
    """用冷备份恢复 world/（新版本可能已经转换过区块）。当前 world 移到 quarantine 留作排查，不删除。"""
    quarantine.mkdir(parents=True, exist_ok=True)
    if (game_dir / "world").exists():
        shutil.move(str(game_dir / "world"), str(quarantine / "world"))
    with tarfile.open(backup_tar) as tf:
        members = [m for m in tf.getmembers() if m.name == "world" or m.name.startswith("world/")]
        tf.extractall(game_dir, members=members, filter="data")
