"""P2：Mod 一键安装 —— Modrinth 搜索、依赖解析、安装、上传 jar、mrpack 整合包、config 在线编辑。

所有写操作都由 api_mods 在 mods 互斥锁里调用（与定时重启等运维任务互斥）。
下载一律校验 sha512 + 大小，先写 .part 再原子改名；被替换的旧文件存入 mods/disabled/，可回退。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tomllib
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urlsplit

import yaml

from mcops.mods import (
    MAX_DOWNLOAD_BYTES,
    DownloadFn,
    HttpFn,
    ModError,
    _disabled_dir,
    _HashCache,
    _parse_jar,
    _record_pending,
    _sha512_of,
    scan_mods,
)

log = logging.getLogger(__name__)

API = "https://api.modrinth.com/v2"
SEARCH_LIMIT_MAX = 50
MAX_DEP_DEPTH = 8
# mrpack 规范允许的下载域名（https://support.modrinth.com/en/articles/8802351）
MRPACK_HOSTS = {"cdn.modrinth.com", "github.com", "raw.githubusercontent.com", "gitlab.com"}
MRPACK_MAX_BYTES = 200 * 1024 * 1024
# 整合包覆盖目录里，服务端只接受这些顶层目录（不碰世界、白名单、server.properties）
MRPACK_OVERRIDE_DIRS = {"config", "mods", "defaultconfigs", "kubejs", "scripts"}

CONFIG_EXTS = {".json", ".json5", ".toml", ".yaml", ".yml", ".properties", ".conf", ".cfg", ".txt", ".snbt", ".ini"}  # fmt: skip
CONFIG_MAX_BYTES = 1024 * 1024


def _q(v: Any) -> str:
    return quote(json.dumps(v, separators=(",", ":")), safe="")


# ---- 搜索 / 版本 ----------------------------------------------------------------


async def search(
    http: HttpFn,
    query: str,
    mc: str | None,
    offset: int = 0,
    limit: int = 20,
    index: str = "relevance",
) -> dict[str, Any]:
    facets: list[list[str]] = [
        ["project_type:mod"],
        ["categories:fabric"],
        ["server_side:required", "server_side:optional"],
    ]
    if mc:
        facets.append([f"versions:{mc}"])
    limit = max(1, min(limit, SEARCH_LIMIT_MAX))
    url = (
        f"{API}/search?query={quote(query)}&facets={_q(facets)}"
        f"&limit={limit}&offset={max(0, offset)}&index={quote(index)}"
    )
    data = await http("GET", url, None)
    hits = []
    for h in data.get("hits", []) if isinstance(data, dict) else []:
        hits.append(
            {
                "project_id": h.get("project_id"),
                "slug": h.get("slug"),
                "title": h.get("title"),
                "description": h.get("description"),
                "author": h.get("author"),
                "downloads": h.get("downloads"),
                "icon_url": h.get("icon_url"),
                "categories": h.get("display_categories") or h.get("categories") or [],
                "client_side": h.get("client_side"),
                "server_side": h.get("server_side"),
                "latest_version": h.get("latest_version"),
                "date_modified": h.get("date_modified"),
            }
        )
    return {
        "hits": hits,
        "total": data.get("total_hits", 0) if isinstance(data, dict) else 0,
        "offset": offset,
        "limit": limit,
    }


async def project_versions(http: HttpFn, project: str, mc: str | None) -> list[dict[str, Any]]:
    url = f"{API}/project/{quote(project, safe='')}/version?loaders={_q(['fabric'])}"
    if mc:
        url += f"&game_versions={_q([mc])}"
    data = await http("GET", url, None)
    return [_version_json(v) for v in data if isinstance(v, dict)] if isinstance(data, list) else []


def _primary(files: Any) -> dict[str, Any] | None:
    if not isinstance(files, list) or not files:
        return None
    for f in files:
        if isinstance(f, dict) and f.get("primary"):
            return f
    return files[0] if isinstance(files[0], dict) else None


def _version_json(v: dict[str, Any]) -> dict[str, Any]:
    f = _primary(v.get("files")) or {}
    return {
        "version_id": v.get("id"),
        "project_id": v.get("project_id"),
        "version_number": v.get("version_number"),
        "name": v.get("name"),
        "version_type": v.get("version_type"),
        "date_published": v.get("date_published"),
        "game_versions": v.get("game_versions", []),
        "loaders": v.get("loaders", []),
        "dependencies": [
            {
                "project_id": d.get("project_id"),
                "version_id": d.get("version_id"),
                "type": d.get("dependency_type"),
            }
            for d in v.get("dependencies", [])
            if isinstance(d, dict)
        ],
        "filename": f.get("filename"),
        "url": f.get("url"),
        "sha512": (f.get("hashes") or {}).get("sha512"),
        "size": f.get("size"),
    }


# ---- 已安装：sha512 -> Modrinth project ----------------------------------------


async def installed_projects(http: HttpFn, game_dir: Path, hashes: _HashCache) -> dict[str, str]:
    """{project_id: 文件名}（含已停用的）。查不到 Modrinth 的 jar（手动上传的私有 mod）不在其中。"""
    mods = await asyncio.to_thread(scan_mods, game_dir, hashes)
    by_sha = {m.sha512: m.file for m in mods if m.sha512}
    if not by_sha:
        return {}
    try:
        data = await http(
            "POST", f"{API}/version_files", {"hashes": list(by_sha), "algorithm": "sha512"}
        )
    except Exception as e:
        log.warning("查询已安装 mod 的 Modrinth 项目失败：%s", e)
        return {}
    out: dict[str, str] = {}
    if isinstance(data, dict):
        for sha, v in data.items():
            if isinstance(v, dict) and v.get("project_id") and sha in by_sha:
                out[str(v["project_id"])] = by_sha[sha]
    return out


# ---- 依赖解析 ------------------------------------------------------------------


@dataclass
class PlanItem:
    project_id: str
    title: str
    version_id: str
    version_number: str
    filename: str
    url: str
    sha512: str
    size: int | None
    reason: str  # requested / dependency
    required_by: list[str] = field(default_factory=list)
    status: str = "install"  # install / installed / unavailable / client_only

    def to_json(self) -> dict[str, Any]:
        return self.__dict__.copy()


async def _get(http: HttpFn, path: str) -> Any:
    return await http("GET", f"{API}{path}", None)


async def plan_install(
    http: HttpFn,
    game_dir: Path,
    hashes: _HashCache,
    mc: str | None,
    requests: list[dict[str, str | None]],
) -> dict[str, Any]:
    """requests: [{project_id, version_id?}]。返回 {items, warnings}，items 含要装的与已装的依赖。

    依赖只跟 required；optional 不装。服务端标记为 unsupported 的项目（纯客户端 mod）不装。
    """
    if not mc:
        raise ModError("读不到服务端的 MC 版本，无法筛选兼容的 mod 版本")
    installed = await installed_projects(http, game_dir, hashes)
    plan: dict[str, PlanItem] = {}
    warnings: list[str] = []
    titles: dict[str, str] = {}

    async def project_info(pid: str) -> dict[str, Any]:
        p = await _get(http, f"/project/{quote(pid, safe='')}")
        if not isinstance(p, dict):
            raise ModError(f"找不到项目 {pid}")
        titles[str(p.get("id"))] = str(p.get("title") or pid)
        return p

    async def visit(
        pid: str, version_id: str | None, reason: str, parent: str | None, depth: int
    ) -> None:
        if depth > MAX_DEP_DEPTH:
            warnings.append(f"依赖层级过深，已停止在 {pid}")
            return
        if pid in plan:
            if parent and parent not in plan[pid].required_by:
                plan[pid].required_by.append(parent)
            return
        p = await project_info(pid)
        real_id = str(p.get("id"))
        title = titles[real_id]
        if real_id in installed:
            plan[real_id] = PlanItem(real_id, title, "", "", installed[real_id], "", "", None, reason,
                                     [parent] if parent else [], "installed")  # fmt: skip
            return
        if p.get("server_side") == "unsupported":
            plan[real_id] = PlanItem(real_id, title, "", "", "", "", "", None, reason,
                                     [parent] if parent else [], "client_only")  # fmt: skip
            if reason == "requested":
                warnings.append(f"「{title}」是纯客户端 mod，服务端不需要安装")
            return
        if version_id:
            v = await _get(http, f"/version/{quote(version_id, safe='')}")
            versions = [_version_json(v)] if isinstance(v, dict) else []
            if versions and mc not in versions[0]["game_versions"]:
                warnings.append(f"「{title}」{versions[0]['version_number']} 未声明支持 {mc}")
        else:
            versions = await project_versions(http, real_id, mc)
            # 优先正式版，其次 beta，最后 alpha
            rank = {"release": 0, "beta": 1, "alpha": 2}
            versions.sort(
                key=lambda x: rank.get(str(x["version_type"]), 3)
            )  # 稳定排序，同级保持新→旧
        chosen = next((v for v in versions if v["url"] and v["sha512"] and v["filename"]), None)
        if chosen is None:
            plan[real_id] = PlanItem(real_id, title, "", "", "", "", "", None, reason,
                                     [parent] if parent else [], "unavailable")  # fmt: skip
            warnings.append(f"「{title}」没有适用于 Fabric {mc} 的版本")
            return
        plan[real_id] = PlanItem(
            real_id, title, str(chosen["version_id"]), str(chosen["version_number"]),
            Path(str(chosen["filename"])).name, str(chosen["url"]), str(chosen["sha512"]),
            chosen["size"], reason, [parent] if parent else [],
        )  # fmt: skip
        for d in chosen["dependencies"]:
            if d["type"] != "required":
                continue
            dep_pid = d["project_id"]
            if not dep_pid and d["version_id"]:
                dv = await _get(http, f"/version/{quote(str(d['version_id']), safe='')}")
                dep_pid = dv.get("project_id") if isinstance(dv, dict) else None
            if dep_pid:
                await visit(str(dep_pid), d["version_id"], "dependency", title, depth + 1)

    for r in requests:
        pid = r.get("project_id")
        if not pid:
            raise ModError("缺少 project_id")
        await visit(str(pid), r.get("version_id"), "requested", None, 0)

    items = [i.to_json() for i in plan.values()]
    to_install = [i for i in items if i["status"] == "install"]
    return {
        "mc_version": mc,
        "items": items,
        "install_count": len(to_install),
        "download_bytes": sum(int(i["size"] or 0) for i in to_install),
        "warnings": warnings,
    }


async def _download_verified(
    download: DownloadFn, url: str, dest: Path, sha512: str, size: int | None, max_bytes: int
) -> None:
    part = dest.with_name(dest.name + ".part")
    try:
        written = await download(url, part, max_bytes)
        if size is not None and written != size:
            raise ModError(f"{dest.name} 大小不符：期望 {size}，实得 {written}")
        if await asyncio.to_thread(_sha512_of, part) != sha512:
            raise ModError(f"{dest.name} sha512 校验失败，已丢弃")
        os.replace(part, dest)
    except ModError:
        part.unlink(missing_ok=True)
        raise
    except Exception as e:
        part.unlink(missing_ok=True)
        raise ModError(f"下载 {dest.name} 失败：{type(e).__name__}: {e}") from e


def _safe_jar_name(name: str) -> str:
    base = Path(name).name
    if (
        base != name
        or not base.endswith(".jar")
        or base.startswith(".")
        or "/" in name
        or "\\" in name
    ):
        raise ModError(f"非法的文件名：{name}")
    return base


async def install_items(
    engine: Any, game_dir: Path, download: DownloadFn, items: list[dict[str, Any]]
) -> dict[str, Any]:
    """执行 plan_install 产出的 items（只装 status=install 的）。任何一个失败就把本次已装的全部撤回。"""
    mods_dir = game_dir / "mods"
    mods_dir.mkdir(parents=True, exist_ok=True)
    todo = [i for i in items if i.get("status") == "install"]
    for i in todo:
        name = _safe_jar_name(str(i["filename"]))
        if (mods_dir / name).exists():
            raise ModError(f"mods/ 下已存在 {name}")
        u = urlsplit(str(i.get("url", "")))
        if u.scheme != "https" or u.hostname not in MRPACK_HOSTS:
            raise ModError(f"{name} 的下载地址不在允许范围（{u.hostname}）")
    done: list[Path] = []
    try:
        for i in todo:
            dest = mods_dir / _safe_jar_name(str(i["filename"]))
            await _download_verified(download, str(i["url"]), dest, str(i["sha512"]),
                                     i.get("size"), MAX_DOWNLOAD_BYTES)  # fmt: skip
            done.append(dest)
    except Exception:
        for p in done:
            p.unlink(missing_ok=True)
        raise
    for p in done:
        await _record_pending(engine, {"action": "install", "file": p.name})
    return {"installed": [p.name for p in done]}


# ---- 上传 jar ------------------------------------------------------------------


async def upload_jar(
    engine: Any, game_dir: Path, hashes: _HashCache, filename: str, data: bytes, mc: str | None
) -> dict[str, Any]:
    name = _safe_jar_name(filename)
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise ModError("文件超过 100 MB")
    mods_dir = game_dir / "mods"
    if (mods_dir / name).exists():
        raise ModError(f"mods/ 下已存在 {name}")
    part = mods_dir / f".{name}.upload"
    await asyncio.to_thread(part.write_bytes, data)
    try:
        info = await asyncio.to_thread(_parse_jar, part, True, _HashCache())
        if info.kind != "fabric":
            raise ModError("不是 Fabric mod（jar 里没有 fabric.mod.json）")
        existing = await asyncio.to_thread(scan_mods, game_dir, hashes)
        dup = next((m for m in existing if m.enabled and m.id == info.id), None)
        if dup is not None:
            raise ModError(f"已安装同一个 mod（{info.id}）：{dup.file}，请先停用旧版本")
        warnings = []
        want = info.depends.get("minecraft")
        if want and mc:
            from mcops.mods import version_matches

            if version_matches(want, mc) is False:
                warnings.append(f"该 mod 声明需要 Minecraft {want}，当前是 {mc}")
        os.replace(part, mods_dir / name)
    except Exception:
        part.unlink(missing_ok=True)
        raise
    await _record_pending(engine, {"action": "upload", "file": name})
    return {
        "file": name,
        "id": info.id,
        "name": info.name,
        "version": info.version,
        "warnings": warnings,
    }


# ---- mrpack 整合包 ---------------------------------------------------------------


def _safe_rel(path: str) -> PurePosixPath:
    p = PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts or not p.parts or "\\" in path:
        raise ModError(f"整合包里有非法路径：{path}")
    return p


def read_mrpack(data: bytes, mc: str | None, loader: str | None) -> dict[str, Any]:
    """解析 .mrpack，返回计划（不写任何文件）。"""
    import io

    if len(data) > MRPACK_MAX_BYTES:
        raise ModError("整合包超过 200 MB")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        index = json.loads(zf.read("modrinth.index.json"))
    except (zipfile.BadZipFile, KeyError, ValueError) as e:
        raise ModError(f"不是有效的 .mrpack：{e}") from None
    if index.get("game") != "minecraft":
        raise ModError("整合包不是 Minecraft 的")
    deps = index.get("dependencies", {})
    warnings: list[str] = []
    if "fabric-loader" not in deps:
        raise ModError(
            f"整合包不是 Fabric 的（依赖：{', '.join(deps) or '无'}），当前只支持 Fabric"
        )
    if mc and deps.get("minecraft") and deps["minecraft"] != mc:
        warnings.append(f"整合包要求 Minecraft {deps['minecraft']}，当前服务端是 {mc}")
    if loader and deps.get("fabric-loader") and deps["fabric-loader"] != loader:
        warnings.append(f"整合包要求 Fabric Loader {deps['fabric-loader']}，当前是 {loader}")
    files: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for f in index.get("files", []):
        path = str(f.get("path", ""))
        rel = _safe_rel(path)
        if (f.get("env") or {}).get("server") == "unsupported":
            skipped.append({"path": path, "reason": "仅客户端"})
            continue
        if rel.parts[0] not in MRPACK_OVERRIDE_DIRS:
            skipped.append({"path": path, "reason": "服务端不需要的目录"})
            continue
        urls = [
            u
            for u in f.get("downloads", [])
            if urlsplit(u).scheme == "https" and urlsplit(u).hostname in MRPACK_HOSTS
        ]
        sha = (f.get("hashes") or {}).get("sha512")
        if not urls or not sha:
            skipped.append({"path": path, "reason": "没有允许的下载地址或缺少 sha512"})
            continue
        files.append({"path": str(rel), "url": urls[0], "sha512": sha, "size": f.get("fileSize")})
    overrides: list[str] = []
    for n in zf.namelist():
        for prefix in ("overrides/", "server-overrides/"):
            if n.startswith(prefix) and not n.endswith("/"):
                rel = _safe_rel(n[len(prefix) :])
                if rel.parts[0] in MRPACK_OVERRIDE_DIRS:
                    overrides.append(n)
                else:
                    skipped.append({"path": n, "reason": "覆盖目录只接受 config/、mods/ 等"})
    return {
        "name": index.get("name"),
        "version": index.get("versionId"),
        "summary": index.get("summary"),
        "dependencies": deps,
        "files": files,
        "overrides": overrides,
        "skipped": skipped,
        "warnings": warnings,
        "download_bytes": sum(int(x["size"] or 0) for x in files),
    }


async def apply_mrpack(
    engine: Any, game_dir: Path, download: DownloadFn, data: bytes, plan: dict[str, Any]
) -> dict[str, Any]:
    """按 read_mrpack 的计划落地：同名文件先备份（mods -> mods/disabled/，其它 -> .mcops-backup/），再写入。"""
    import io

    ts = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    backup_root = game_dir / ".mcops-backup" / f"mrpack-{ts}"
    written: list[str] = []
    replaced: list[str] = []

    def backup(dest: Path) -> None:
        if not dest.exists():
            return
        rel = dest.relative_to(game_dir)
        if rel.parts[0] == "mods" and len(rel.parts) == 2:
            target = _disabled_dir(game_dir) / f"{dest.name}.replaced-{ts}"
        else:
            target = backup_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dest), str(target))
        replaced.append(str(rel))

    tmp = game_dir / ".mcops-backup" / f"mrpack-{ts}.dl"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        # 先全部下载到临时目录并校验，任何失败都不改动游戏目录
        staged: list[tuple[Path, Path]] = []
        for f in plan["files"]:
            src = tmp / f"{len(staged)}.part"
            await _download_verified(
                download, f["url"], src, f["sha512"], f.get("size"), MAX_DOWNLOAD_BYTES
            )
            staged.append((src, game_dir / f["path"]))
        zf = zipfile.ZipFile(io.BytesIO(data))
        for f in staged:
            dest = f[1]
            dest.parent.mkdir(parents=True, exist_ok=True)
            backup(dest)
            os.replace(f[0], dest)
            written.append(str(dest.relative_to(game_dir)))
        for n in plan["overrides"]:
            prefix = "server-overrides/" if n.startswith("server-overrides/") else "overrides/"
            dest = game_dir / str(_safe_rel(n[len(prefix) :]))
            if not dest.resolve().is_relative_to(game_dir.resolve()):
                raise ModError(f"非法路径：{n}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            backup(dest)
            with zf.open(n) as src_f, dest.open("wb") as out:
                shutil.copyfileobj(src_f, out)
            written.append(str(dest.relative_to(game_dir)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    await _record_pending(engine, {"action": "mrpack", "file": str(plan.get("name") or "")})
    return {"written": written, "replaced": replaced,
            "backup_dir": str(backup_root.relative_to(game_dir)) if backup_root.exists() else None}  # fmt: skip


# ---- config/ 在线编辑 -------------------------------------------------------------


def _config_root(game_dir: Path) -> Path:
    return (game_dir / "config").resolve()


def _config_path(game_dir: Path, rel: str) -> Path:
    root = _config_root(game_dir)
    _safe_rel(rel)
    p = (root / rel).resolve()
    if not p.is_relative_to(root) or p == root:
        raise ModError("路径必须在 config/ 目录内")
    if p.suffix.lower() not in CONFIG_EXTS:
        raise ModError(f"不支持在线编辑 {p.suffix or '无扩展名'} 文件")
    return p


def list_config_files(game_dir: Path) -> list[dict[str, Any]]:
    root = _config_root(game_dir)
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.rglob("*")):
        if p.is_symlink() or not p.is_file() or p.suffix.lower() not in CONFIG_EXTS:
            continue
        st = p.stat()
        out.append(
            {
                "path": str(p.relative_to(root)),
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime, UTC).isoformat(),
                "editable": st.st_size <= CONFIG_MAX_BYTES,
            }
        )
    return out


def read_config_file(game_dir: Path, rel: str) -> dict[str, Any]:
    p = _config_path(game_dir, rel)
    if p.is_symlink() or not p.is_file():
        raise ModError("文件不存在")
    if p.stat().st_size > CONFIG_MAX_BYTES:
        raise ModError("文件超过 1 MB，不支持在线编辑")
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ModError("不是 UTF-8 文本文件") from None
    return {"path": rel, "content": text, "format": _format_of(p), "mtime": p.stat().st_mtime}


def _format_of(p: Path) -> str:
    ext = p.suffix.lower()
    return {".json": "json", ".toml": "toml", ".yaml": "yaml", ".yml": "yaml"}.get(ext, "text")


def validate_config_text(fmt: str, text: str) -> None:
    try:
        if fmt == "json":
            json.loads(text)
        elif fmt == "toml":
            tomllib.loads(text)
        elif fmt == "yaml":
            yaml.safe_load(text)
    except Exception as e:
        raise ModError(f"{fmt.upper()} 语法错误：{e}") from None


def write_config_file(
    game_dir: Path, backup_dir: Path, rel: str, content: str, expected_mtime: float | None
) -> dict[str, Any]:
    p = _config_path(game_dir, rel)
    if p.is_symlink() or not p.is_file():
        raise ModError("文件不存在（不支持新建）")
    if len(content.encode("utf-8")) > CONFIG_MAX_BYTES:
        raise ModError("内容超过 1 MB")
    if expected_mtime is not None and abs(p.stat().st_mtime - expected_mtime) > 1e-6:
        raise ModError("文件在你编辑期间被修改过（可能是服务端或 mod 写回的），请重新打开后再改")
    validate_config_text(_format_of(p), content)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = backup_dir / "config" / f"{rel}.{ts}"
    bak.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, bak)
    tmp = p.with_name(p.name + ".mcops-tmp")
    tmp.write_text(content, encoding="utf-8")
    shutil.copymode(p, tmp)
    os.replace(tmp, p)
    return {"path": rel, "backup": str(bak), "mtime": p.stat().st_mtime}
