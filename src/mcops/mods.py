"""Mod 与升级（M4）：扫描 Fabric mods 目录、健康检查、Modrinth 更新检查与升级/回退。

目录约定（沿用旧脚本）：
- game_dir/mods/            已启用的 *.jar
- game_dir/mods/disabled/   已停用的 *.jar（停用=移到此目录；启用=移回）
- 升级时旧 jar 以 `<旧名>.replaced-YYYYmmddHHMMSS` 存入 disabled/，用于回退。

网络相关函数（Modrinth / Fabric Meta / 下载）全部可注入，测试不触网。
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import re
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from mcops import db

log = logging.getLogger(__name__)

USER_AGENT = "mc-ops/0.1 (github.com/WangSimiao2000/mc-ops)"
MODRINTH_UPDATE_URL = "https://api.modrinth.com/v2/version_files/update"
FABRIC_META_LOADER_URL = "https://meta.fabricmc.net/v2/versions/loader"
HTTP_TIMEOUT_S = 20.0
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024  # 100 MB
KV_UPDATES = "mods.updates"
KV_PENDING = "mods.pending_restart"

# fabric-api 装上后视为已提供所有 fabric- 开头的模块
_FABRIC_API_IDS = {"fabric-api", "fabric"}
# 依赖解析里始终视为"已提供"的内置 id
_BUILTIN_IDS = {"minecraft", "java", "fabricloader", "fabric"}


# ---- HTTP 抽象（可注入，便于测试） -----------------------------------------

# 一个 HTTP POST/GET 函数：接收 (method, url, json_body) 返回解析后的 JSON。
HttpFn = Callable[[str, str, dict[str, Any] | None], Awaitable[Any]]
# 下载函数：接收 (url, dest, max_bytes) 返回写入的字节数。
DownloadFn = Callable[[str, Path, int], Awaitable[int]]


async def _default_http(method: str, url: str, body: dict[str, Any] | None) -> Any:
    """默认 HTTP 实现：延迟导入 httpx（它在 dev 依赖组里），生产 run 时也可用。"""
    import httpx

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S, headers=headers) as c:
        if method == "POST":
            resp = await c.post(url, json=body)
        else:
            resp = await c.get(url)
        resp.raise_for_status()
        return resp.json()


async def _default_download(url: str, dest: Path, max_bytes: int) -> int:
    import httpx

    written = 0
    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(
        timeout=HTTP_TIMEOUT_S, headers=headers, follow_redirects=True
    ) as c:
        async with c.stream("GET", url) as resp:
            resp.raise_for_status()
            with dest.open("wb") as f:
                async for chunk in resp.aiter_bytes():
                    written += len(chunk)
                    if written > max_bytes:
                        raise ValueError(f"下载超过大小上限（{max_bytes} 字节）")
                    f.write(chunk)
    return written


# ---- 数据模型 --------------------------------------------------------------


@dataclass(slots=True)
class ModInfo:
    file: str
    enabled: bool
    size: int
    mtime: float
    sha1: str
    sha512: str
    id: str
    name: str
    version: str
    description: str
    authors: list[str]
    homepage: str | None
    depends: dict[str, str]
    environment: str
    provides: list[str]
    kind: str = "fabric"  # fabric / unknown

    def to_json(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "enabled": self.enabled,
            "size": self.size,
            "mtime": datetime.fromtimestamp(self.mtime, UTC).isoformat(),
            "sha1": self.sha1,
            "sha512": self.sha512,
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "authors": self.authors,
            "homepage": self.homepage,
            "depends": self.depends,
            "environment": self.environment,
            "provides": self.provides,
            "kind": self.kind,
        }


# ---- 哈希缓存 --------------------------------------------------------------


class _HashCache:
    """按 (path, size, mtime) 缓存 sha1/sha512，避免对未变动的 jar 重复计算。"""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, int, float], tuple[str, str]] = {}

    def get(self, path: Path, size: int, mtime: float) -> tuple[str, str]:
        key = (str(path), size, mtime)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        sha1 = hashlib.sha1(usedforsecurity=False)  # 仅作文件标识，非安全用途
        sha512 = hashlib.sha512()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                sha1.update(chunk)
                sha512.update(chunk)
        result = (sha1.hexdigest(), sha512.hexdigest())
        self._cache[key] = result
        return result


# ---- jar 元数据解析 --------------------------------------------------------


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _authors(raw: Any) -> list[str]:
    out: list[str] = []
    for a in raw if isinstance(raw, list) else []:
        if isinstance(a, str):
            out.append(a)
        elif isinstance(a, dict) and isinstance(a.get("name"), str):
            out.append(a["name"])
    return out


def _homepage(contact: Any) -> str | None:
    if not isinstance(contact, dict):
        return None
    for key in ("homepage", "sources"):
        v = contact.get(key)
        if isinstance(v, str) and v:
            return v
    return None


def _nested_provides(zf: zipfile.ZipFile, meta: dict[str, Any]) -> list[str]:
    """往下读一层内嵌 jar（jars[].file）的 fabric.mod.json，收集它们声明的 id。"""
    ids: list[str] = []
    for entry in meta.get("jars", []) if isinstance(meta.get("jars"), list) else []:
        inner = entry.get("file") if isinstance(entry, dict) else None
        if not isinstance(inner, str):
            continue
        try:
            # 内嵌 jar 本身是一个 zip，需作为 zip 打开再读它的 fabric.mod.json。
            with zf.open(inner) as fh:
                with zipfile.ZipFile(io.BytesIO(fh.read())) as inner_zf:
                    data = json.loads(inner_zf.read("fabric.mod.json").decode("utf-8"))
            inner_id = data.get("id")
            if isinstance(inner_id, str) and inner_id:
                ids.append(inner_id)
        except (KeyError, zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError, OSError):
            continue
    return ids


def _parse_jar(path: Path, enabled: bool, hashes: _HashCache) -> ModInfo:
    """读取一个 jar 的元数据；坏 jar / 非 Fabric 的 jar 标记 kind='unknown'。"""
    stat = path.stat()
    size, mtime = stat.st_size, stat.st_mtime
    sha1, sha512 = hashes.get(path, size, mtime)
    base = ModInfo(
        file=path.name,
        enabled=enabled,
        size=size,
        mtime=mtime,
        sha1=sha1,
        sha512=sha512,
        id="",
        name=path.name,
        version="",
        description="",
        authors=[],
        homepage=None,
        depends={},
        environment="*",
        provides=[],
        kind="unknown",
    )
    if size == 0:
        return base
    try:
        with zipfile.ZipFile(path) as zf:
            try:
                raw = zf.read("fabric.mod.json").decode("utf-8")
            except KeyError:
                return base  # 非 Fabric 的 jar
            meta = json.loads(raw)
            if not isinstance(meta, dict):
                return base
            provides_raw = meta.get("provides")
            provides = (
                [p for p in provides_raw if isinstance(p, str)]
                if isinstance(provides_raw, list)
                else []
            )
            provides += _nested_provides(zf, meta)
    except (zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError, OSError):
        return base

    depends_raw = meta.get("depends")
    depends: dict[str, str] = {}
    if isinstance(depends_raw, dict):
        for k, v in depends_raw.items():
            depends[str(k)] = _dep_str(v)

    mod_id = meta.get("id")
    base.kind = "fabric"
    base.id = str(mod_id) if isinstance(mod_id, str) else ""
    base.name = str(meta.get("name") or base.id or path.name)
    base.version = str(meta.get("version") or "")
    base.description = _truncate(str(meta.get("description") or ""), 300)
    base.authors = _authors(meta.get("authors"))
    base.homepage = _homepage(meta.get("contact"))
    base.depends = depends
    env = meta.get("environment")
    base.environment = str(env) if isinstance(env, str) else "*"
    base.provides = list(dict.fromkeys(provides))  # 去重，保序
    return base


def _dep_str(v: Any) -> str:
    """depends 的值可能是字符串或字符串数组（表示"或"）。统一成展示用字符串。"""
    if isinstance(v, list):
        return " || ".join(str(x) for x in v)
    return str(v)


def scan_mods(game_dir: Path, hashes: _HashCache) -> list[ModInfo]:
    """扫描 mods/ 与 mods/disabled/ 下的 *.jar（子目录如 luckperms/ 里的非 jar 忽略）。"""
    mods_dir = game_dir / "mods"
    disabled_dir = mods_dir / "disabled"
    out: list[ModInfo] = []
    for directory, enabled in ((mods_dir, True), (disabled_dir, False)):
        if not directory.is_dir():
            continue
        for p in sorted(directory.iterdir()):
            if p.is_file() and p.suffix == ".jar":
                out.append(_parse_jar(p, enabled, hashes))
    return out


# ---- MC 版本范围匹配 -------------------------------------------------------


def _parse_version(v: str) -> tuple[int, ...]:
    parts: list[int] = []
    for seg in v.split("."):
        m = re.match(r"(\d+)", seg)
        parts.append(int(m.group(1)) if m else 0)
    return tuple(parts)


def _cmp(a: str, b: str) -> int:
    ta, tb = _parse_version(a), _parse_version(b)
    n = max(len(ta), len(tb))
    ta += (0,) * (n - len(ta))
    tb += (0,) * (n - len(tb))
    return (ta > tb) - (ta < tb)


def version_matches(spec: str, mc: str) -> bool | None:
    """判断 MC 版本 `mc` 是否满足范围 `spec`。无法解析的范围返回 None（调用方跳过，不报错）。

    支持：精确、>= <= > <、~1.21.11、^1.21.11、1.21.x、空格分隔（且）、数组→'||'（或）。
    """
    spec = spec.strip()
    if not spec or spec in ("*", "any"):
        return True
    # 数组展开成 '||'
    if "||" in spec:
        results = [version_matches(part, mc) for part in spec.split("||")]
        if any(r is True for r in results):
            return True
        if all(r is False for r in results):
            return False
        return None
    # 空格分隔表示"且"
    tokens = spec.split()
    if len(tokens) > 1:
        results = [version_matches(t, mc) for t in tokens]
        if any(r is None for r in results):
            return None
        return all(results)
    return _match_single(spec, mc)


def _match_single(spec: str, mc: str) -> bool | None:
    spec = spec.strip()
    m = re.match(r"^(>=|<=|>|<|=|~|\^)?\s*(.+)$", spec)
    if not m:
        return None
    op, ver = m.group(1) or "=", m.group(2).strip()

    if ver.endswith(".x") or ver.endswith(".*"):
        if op not in ("=", "~", "^"):
            return None
        prefix = ver[:-2]
        return mc == prefix or mc.startswith(prefix + ".")
    if not re.match(r"^\d+(\.\d+)*$", ver):
        return None  # 不认识的范围语法，跳过

    c = _cmp(mc, ver)
    if op == "=":
        return c == 0
    if op == ">=":
        return c >= 0
    if op == "<=":
        return c <= 0
    if op == ">":
        return c > 0
    if op == "<":
        return c < 0
    if op == "~":
        # ~1.21.11 => >=1.21.11 且 <1.22.0（锁定次版本）
        tv = _parse_version(ver)
        if c < 0:
            return False
        if len(tv) >= 2:
            upper = f"{tv[0]}.{tv[1] + 1}.0"
            return _cmp(mc, upper) < 0
        return _cmp(mc, f"{tv[0] + 1}.0.0") < 0
    if op == "^":
        # ^1.21.11 => >=1.21.11 且 <2.0.0（锁定主版本）
        tv = _parse_version(ver)
        if c < 0:
            return False
        return _cmp(mc, f"{tv[0] + 1}.0.0") < 0
    return None


# ---- 健康检查 --------------------------------------------------------------


def check_health(mods: list[ModInfo], mc_version: str | None) -> list[dict[str, str]]:
    """移植并增强旧 mods_check。返回 [{level, mod, message}]。"""
    issues: list[dict[str, str]] = []
    enabled = [m for m in mods if m.enabled]

    def err(mod: str, msg: str) -> None:
        issues.append({"level": "error", "mod": mod, "message": msg})

    def warn(mod: str, msg: str) -> None:
        issues.append({"level": "warning", "mod": mod, "message": msg})

    # 空文件
    for m in mods:
        if m.size == 0:
            err(m.file, "空文件（0 字节），无法加载")

    # 重复的 mod id（两个已启用 jar 声明同一 id）
    by_id: dict[str, list[ModInfo]] = {}
    for m in enabled:
        if m.id:
            by_id.setdefault(m.id, []).append(m)
    for mod_id, group in by_id.items():
        if len(group) > 1:
            files = "、".join(sorted(g.file for g in group))
            err(mod_id, f"重复的 mod id「{mod_id}」：{files}")

    # 已提供集合：id ∪ provides ∪ 内置
    provided: set[str] = set(_BUILTIN_IDS)
    for m in enabled:
        if m.id:
            provided.add(m.id)
        provided.update(m.provides)
    has_fabric_api = bool(_FABRIC_API_IDS & provided)

    # 缺少依赖
    for m in enabled:
        for dep_id in m.depends:
            if dep_id in provided:
                continue
            if dep_id.startswith("fabric-") and has_fabric_api:
                continue
            if dep_id in _BUILTIN_IDS:
                continue
            warn(m.file, f"缺少依赖「{dep_id}」（{m.name} 声明依赖它，但未找到已启用的提供方）")

    # MC 版本不匹配
    if mc_version:
        for m in enabled:
            spec = m.depends.get("minecraft")
            if not spec:
                continue
            res = version_matches(spec, mc_version)
            if res is False:
                warn(
                    m.file,
                    f"{m.name} 要求 Minecraft {spec}，当前为 {mc_version}，可能不兼容",
                )

    # 缺少 easyauth（离线模式必需）
    if not any(m.id == "easyauth" or "easyauth" in m.provides for m in enabled):
        warn("easyauth", "未检测到 EasyAuth（离线模式必需的登录保护）")

    return issues


# ---- 服务端 jar 版本解析 ---------------------------------------------------


def parse_server_jar(name: str) -> tuple[str | None, str | None]:
    """从 `fabric-server-mc.1.21.11-loader.0.19.2-launcher.1.0.3.jar` 解析 (mc, loader)。"""
    mc = re.search(r"mc\.([0-9]+(?:\.[0-9]+)+)", name)
    loader = re.search(r"loader\.([0-9]+(?:\.[0-9]+)+)", name)
    return (mc.group(1) if mc else None, loader.group(1) if loader else None)


def _server_jar_name(game_dir: Path, jar_glob: str) -> str | None:
    matches = sorted(p.name for p in game_dir.glob(jar_glob))
    return matches[-1] if matches else None


# ---- 更新检查（Modrinth + Fabric Meta） ------------------------------------


async def check_updates(
    mods: list[ModInfo],
    mc_version: str | None,
    http: HttpFn,
) -> dict[str, Any]:
    """查询 Modrinth 的 version_files/update 与 Fabric Meta 最新 loader。

    返回 {checked_at, updates: {file: {...}}, loader_latest, error}。网络失败不抛异常。
    """
    result: dict[str, Any] = {
        "checked_at": datetime.now(UTC).isoformat(),
        "updates": {},
        "loader_latest": None,
        "error": None,
    }
    enabled = [m for m in mods if m.enabled and m.kind == "fabric" and m.sha512]
    by_sha: dict[str, ModInfo] = {m.sha512: m for m in enabled}

    if by_sha and mc_version:
        body = {
            "hashes": list(by_sha),
            "algorithm": "sha512",
            "loaders": ["fabric"],
            "game_versions": [mc_version],
        }
        try:
            data = await http("POST", MODRINTH_UPDATE_URL, body)
        except Exception as e:  # 网络 / HTTP 错误：返回友好信息，不 500
            result["error"] = f"查询 Modrinth 失败：{type(e).__name__}: {e}"
            data = None
        if isinstance(data, dict):
            for req_sha, mod in by_sha.items():
                latest = data.get(req_sha)
                if not isinstance(latest, dict):
                    result["updates"][mod.file] = {"source": "unknown"}
                    continue
                files = latest.get("files")
                primary = _primary_file(files)
                new_sha = primary.get("hashes", {}).get("sha512") if primary else None
                has_update = bool(new_sha) and new_sha != mod.sha512
                result["updates"][mod.file] = {
                    "source": "modrinth",
                    "has_update": has_update,
                    "project_id": latest.get("project_id"),
                    "version_id": latest.get("id"),
                    "version_number": latest.get("version_number"),
                    "date_published": latest.get("date_published"),
                    "changelog": _truncate(str(latest.get("changelog") or ""), 2000),
                    "filename": primary.get("filename") if primary else None,
                    "url": primary.get("url") if primary else None,
                    "sha512": new_sha,
                    "size": primary.get("size") if primary else None,
                }
            # Modrinth 没返回的（启用的）mod 标记 source=unknown
            for mod in enabled:
                result["updates"].setdefault(mod.file, {"source": "unknown"})

    if mc_version:
        try:
            loader_data = await http("GET", f"{FABRIC_META_LOADER_URL}/{mc_version}", None)
            result["loader_latest"] = _first_stable_loader(loader_data)
        except Exception as e:
            if result["error"] is None:
                result["error"] = f"查询 Fabric Loader 失败：{type(e).__name__}: {e}"
    return result


def _primary_file(files: Any) -> dict[str, Any] | None:
    if not isinstance(files, list):
        return None
    for f in files:
        if isinstance(f, dict) and f.get("primary"):
            return f
    return files[0] if files and isinstance(files[0], dict) else None


def _first_stable_loader(data: Any) -> str | None:
    if not isinstance(data, list):
        return None
    for entry in data:
        loader = entry.get("loader") if isinstance(entry, dict) else None
        if isinstance(loader, dict) and loader.get("stable"):
            v = loader.get("version")
            return str(v) if v else None
    return None


# ---- kv 持久化：更新缓存 & 待重启标记 --------------------------------------


async def _kv_get(engine: AsyncEngine, key: str) -> Any:
    async with engine.connect() as conn:
        v = (await conn.execute(select(db.kv.c.value).where(db.kv.c.key == key))).scalar()
    return v


async def _kv_set(engine: AsyncEngine, key: str, value: Any) -> None:
    stmt = sqlite_insert(db.kv).values(key=key, value=value)
    async with engine.begin() as conn:
        await conn.execute(
            stmt.on_conflict_do_update(index_elements=["key"], set_={"value": value})
        )


async def get_cached_updates(engine: AsyncEngine) -> dict[str, Any] | None:
    v = await _kv_get(engine, KV_UPDATES)
    return v if isinstance(v, dict) else None


async def save_updates(engine: AsyncEngine, updates: dict[str, Any]) -> None:
    await _kv_set(engine, KV_UPDATES, updates)


async def _record_pending(engine: AsyncEngine, change: dict[str, Any]) -> None:
    """追加一条"待重启生效"记录。since 为首次改动时间。"""
    existing = await _kv_get(engine, KV_PENDING)
    if isinstance(existing, dict) and existing.get("since"):
        payload = {
            "since": existing["since"],
            "changes": [*existing.get("changes", []), change],
        }
    else:
        payload = {"since": datetime.now(UTC).isoformat(), "changes": [change]}
    await _kv_set(engine, KV_PENDING, payload)


async def pending_restart(
    engine: AsyncEngine, server_started_at: datetime | None
) -> dict[str, Any] | None:
    """返回待重启标记；若服务端进程启动时间晚于标记的 since，视为已生效并清掉标记。

    实现方式：server_started_at 是进程墙钟启动时间（由 api 层换算传入）。只要它比
    记录的 since 更晚，就说明服务端在这次改动之后重启过，mods 已重新加载，标记作废。
    """
    v = await _kv_get(engine, KV_PENDING)
    if not isinstance(v, dict) or not v.get("since"):
        return None
    since_raw = v["since"]
    try:
        since = datetime.fromisoformat(since_raw)
    except (TypeError, ValueError):
        return v
    if server_started_at is not None and server_started_at > since:
        await _kv_set(engine, KV_PENDING, {})  # 清掉：已生效
        return None
    return v


# ---- 文件操作：启用 / 停用 / 更新 / 回退 ------------------------------------


class ModError(RuntimeError):
    """mod 操作失败（API 映射为 400/409）。"""


def _validate_basename(game_dir: Path, file: str) -> None:
    """只接受当前列表（mods/ 或 mods/disabled/）里出现过的 basename，防路径穿越。"""
    if file != Path(file).name or file in ("", ".", "..") or "/" in file or "\\" in file:
        raise ModError("非法的文件名")
    hashes = _HashCache()
    names = {m.file for m in scan_mods(game_dir, hashes)}
    if file not in names:
        raise ModError(f"未知的 mod 文件：{file}")


def _mods_dir(game_dir: Path) -> Path:
    return game_dir / "mods"


def _disabled_dir(game_dir: Path) -> Path:
    d = game_dir / "mods" / "disabled"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def disable_mod(engine: AsyncEngine, game_dir: Path, file: str) -> dict[str, Any]:
    _validate_basename(game_dir, file)
    src = _mods_dir(game_dir) / file
    if not src.is_file():
        raise ModError(f"{file} 不在已启用目录中")
    dst = _disabled_dir(game_dir) / file
    if dst.exists():
        raise ModError(f"disabled/ 下已存在同名文件：{file}")
    await asyncio.to_thread(src.rename, dst)
    await _record_pending(engine, {"action": "disable", "file": file})
    return {"file": file, "enabled": False}


async def enable_mod(engine: AsyncEngine, game_dir: Path, file: str) -> dict[str, Any]:
    _validate_basename(game_dir, file)
    src = _disabled_dir(game_dir) / file
    if not src.is_file():
        raise ModError(f"{file} 不在已停用目录中")
    dst = _mods_dir(game_dir) / file
    if dst.exists():
        raise ModError(f"mods/ 下已存在同名文件：{file}")
    await asyncio.to_thread(src.rename, dst)
    await _record_pending(engine, {"action": "enable", "file": file})
    return {"file": file, "enabled": True}


async def update_mod(
    engine: AsyncEngine,
    game_dir: Path,
    file: str,
    updates: dict[str, Any],
    download: DownloadFn,
) -> dict[str, Any]:
    """下载新版本到 <name>.part，校验 sha512+size 后原子改名，旧 jar 存入 disabled/。"""
    _validate_basename(game_dir, file)
    info = updates.get("updates", {}).get(file)
    if not isinstance(info, dict) or info.get("source") != "modrinth" or not info.get("has_update"):
        raise ModError(f"{file} 没有可用更新")
    url = info.get("url")
    want_sha = info.get("sha512")
    want_size = info.get("size")
    new_name = info.get("filename")
    if not (url and want_sha and new_name):
        raise ModError(f"{file} 的更新信息不完整")

    mods_dir = _mods_dir(game_dir)
    old = mods_dir / file
    if not old.is_file():
        raise ModError(f"{file} 不在已启用目录中")

    part = mods_dir / f"{new_name}.part"
    try:
        written = await download(url, part, MAX_DOWNLOAD_BYTES)
        if want_size is not None and written != want_size:
            raise ModError(f"大小不符：期望 {want_size}，实得 {written}")
        got_sha = await asyncio.to_thread(_sha512_of, part)
        if got_sha != want_sha:
            raise ModError("sha512 校验失败，已丢弃下载的文件")
    except ModError:
        part.unlink(missing_ok=True)
        raise
    except Exception as e:
        part.unlink(missing_ok=True)
        raise ModError(f"下载失败：{type(e).__name__}: {e}") from e

    # 处理目标同名冲突（新文件名可能已存在于 mods/）
    dest = mods_dir / Path(new_name).name
    if dest.exists() and dest != old:
        part.unlink(missing_ok=True)
        raise ModError(f"目标文件已存在：{dest.name}")

    # 旧 jar 移到 disabled/<旧名>.replaced-时间戳，用于回退
    ts = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    backup = _disabled_dir(game_dir) / f"{file}.replaced-{ts}"
    await asyncio.to_thread(old.rename, backup)
    await asyncio.to_thread(part.rename, dest)
    await _record_pending(
        engine, {"action": "update", "file": file, "to": dest.name, "backup": backup.name}
    )
    return {"file": file, "new_file": dest.name, "backup": backup.name}


def _sha512_of(path: Path) -> str:
    h = hashlib.sha512()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


async def rollback_mod(engine: AsyncEngine, game_dir: Path, file: str) -> dict[str, Any]:
    """把最近一次被 `file` 替换的旧 jar 恢复回 mods/，当前新 jar 移到 disabled/。"""
    _validate_basename(game_dir, file)
    disabled = _disabled_dir(game_dir)
    pattern = re.compile(r"^(.+\.jar)\.replaced-(\d{14})$")
    candidates: list[tuple[str, str, Path]] = []
    for p in disabled.iterdir():
        m = pattern.match(p.name)
        if m:
            candidates.append((m.group(1), m.group(2), p))
    if not candidates:
        raise ModError("没有可回退的历史版本")
    # 取时间戳最新的一条
    candidates.sort(key=lambda c: c[1], reverse=True)
    old_name, _, backup_path = candidates[0]

    mods_dir = _mods_dir(game_dir)
    current = mods_dir / file
    restored = mods_dir / old_name
    if restored.exists():
        raise ModError(f"mods/ 下已存在同名文件：{old_name}")
    # 当前新 jar 移到 disabled（若存在）
    if current.is_file():
        dst = disabled / file
        if dst.exists():
            dst = disabled / f"{file}.rolledback-{datetime.now(UTC):%Y%m%d%H%M%S}"
        await asyncio.to_thread(current.rename, dst)
    await asyncio.to_thread(backup_path.rename, restored)
    await _record_pending(engine, {"action": "rollback", "restored": old_name, "removed": file})
    return {"restored": old_name, "removed": file}


# ---- 顶层聚合 --------------------------------------------------------------


@dataclass
class ModsService:
    """把扫描、检查、更新缓存与文件操作组装起来，供 api_mods 调用。"""

    engine: AsyncEngine
    game_dir: Path
    jar_glob: str
    http: HttpFn = _default_http
    download: DownloadFn = _default_download
    hashes: _HashCache = field(default_factory=_HashCache)

    def mc_and_loader(self) -> tuple[str | None, str | None]:
        name = _server_jar_name(self.game_dir, self.jar_glob)
        if name is None:
            return None, None
        return parse_server_jar(name)

    async def overview(self, server_started_at: datetime | None) -> dict[str, Any]:
        mods = await asyncio.to_thread(scan_mods, self.game_dir, self.hashes)
        mc, loader = self.mc_and_loader()
        issues = check_health(mods, mc)
        cached = await get_cached_updates(self.engine)
        pending = await pending_restart(self.engine, server_started_at)
        return {
            "mc_version": mc,
            "loader_version": loader,
            "loader_latest": cached.get("loader_latest") if cached else None,
            "mods": [m.to_json() for m in mods],
            "issues": issues,
            "updates": cached.get("updates", {}) if cached else {},
            "checked_at": cached.get("checked_at") if cached else None,
            "update_error": cached.get("error") if cached else None,
            "pending_restart": pending,
        }

    async def refresh_updates(self) -> dict[str, Any]:
        mods = await asyncio.to_thread(scan_mods, self.game_dir, self.hashes)
        mc, _ = self.mc_and_loader()
        result = await check_updates(mods, mc, self.http)
        await save_updates(self.engine, result)
        return result

    async def update_all(self) -> list[dict[str, Any]]:
        cached = await get_cached_updates(self.engine) or {"updates": {}}
        results: list[dict[str, Any]] = []
        for file, info in list(cached.get("updates", {}).items()):
            if not (
                isinstance(info, dict)
                and info.get("source") == "modrinth"
                and info.get("has_update")
            ):
                continue
            try:
                r = await update_mod(self.engine, self.game_dir, file, cached, self.download)
                results.append({"file": file, "ok": True, **r})
            except ModError as e:
                results.append({"file": file, "ok": False, "error": str(e)})
        return results
