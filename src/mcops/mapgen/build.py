"""增量构建瓦片：区域 -> 按瓦片切好的图层碎片 -> 瓦片 -> 缩放金字塔 -> manifest.json。

级别（z 越大越缩小）：

- z=0：高清（每方块 8px），只在玩家常待区块（InhabitedTime 达到阈值）覆盖到的瓦片上有；
- z=1：全图（每方块 4px），单独渲染而不是从 z=0 缩出来，像素依然锐利；
- z>=2：由下一级 2x2 张盒式平均缩小。

缓存布局（cache/）：

    regions/r.X.Z.mca            agent 复制来的存档快照（只读）
    frag/{0,1}/TX_TY/r.X.Z.png   每个区域落在每张瓦片上的那一块
    meta/{0,1}/r.X.Z.json        源文件 mtime/大小、碎片所在瓦片、常待瓦片
    pyr/Z/TX_TY.png              z>=1 的无损瓦片（生成上一级用）
    state.json                   上次的常待瓦片和级数

只重画源文件变过的区域，只重新合成碎片变过的瓦片，再逐级往上传。
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import shutil
import time
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image

from mcops.mapgen import nbt
from mcops.mapgen.biome import BiomeTable
from mcops.mapgen.blocks import BlockTable, Textures
from mcops.mapgen.render import HI_SCALE, Tables, render_region

log = logging.getLogger(__name__)

TILE = 512
SCALES = {0: HI_SCALE, 1: 1}  # 级别 -> 每方块半宽像素
WEBP_QUALITY = 88

Key = tuple[int, int]


def _key(name: str) -> Key:
    a, b = name.split("_")
    return int(a), int(b)


def _name(k: Key) -> str:
    return f"{k[0]}_{k[1]}"


def _rkey(region: str) -> tuple[int, int]:
    _, x, z, _ = region.split(".")
    return int(x), int(z)


def _painter(region: str) -> tuple[int, int]:
    rx, rz = _rkey(region)
    return rx + rz, rx  # x+z 小的在后面，先画


def _write_json(p: Path, data: Any) -> None:
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    tmp.replace(p)


def _read_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---- 渲染（子进程） ----------------------------------------------------------------

_TABLES: dict[str, Tables] = {}


def _tables(jar: str | None, cache: str, hot_ticks: int) -> Tables:
    t = _TABLES.get(cache)
    if t is None:
        tex = Textures(Path(jar) if jar else None, Path(cache) / "texavg.json")
        t = Tables(BlockTable(tex), BiomeTable(tex.colormaps))
        _TABLES[cache] = t
    t.hot_ticks = hot_ticks
    return t


def _render_job(
    cache: str, jar: str | None, hot_ticks: int, level: int, region: str
) -> tuple[str, list[Key], list[Key], list[Key]]:
    """渲染一个区域的一个级别，切成碎片落盘。返回 (区域, 旧瓦片, 新瓦片, 常待瓦片)。"""
    c = Path(cache)
    src = c / "regions" / region
    meta_p = c / "meta" / str(level) / (region + ".json")
    old = _read_json(meta_p) or {}
    old_tiles = [tuple(k) for k in old.get("tiles", [])]
    st = src.stat()
    layer = render_region(src, SCALES[level], _tables(jar, cache, hot_ticks), TILE)
    for k in old_tiles:
        (c / "frag" / str(level) / _name(k) / (region + ".png")).unlink(missing_ok=True)
    new_tiles: list[Key] = []
    hot: list[Key] = []
    if layer is not None:
        h, w = layer.rgba.shape[:2]
        im = Image.fromarray(layer.rgba, "RGBA")
        for tx in range(layer.ox // TILE, (layer.ox + w - 1) // TILE + 1):
            for ty in range(layer.oy // TILE, (layer.oy + h - 1) // TILE + 1):
                box = (tx * TILE - layer.ox, ty * TILE - layer.oy)
                crop = im.crop((box[0], box[1], box[0] + TILE, box[1] + TILE))
                if crop.getbbox() is None:
                    continue
                d = c / "frag" / str(level) / _name((tx, ty))
                d.mkdir(parents=True, exist_ok=True)
                crop.save(d / (region + ".png"), compress_level=1)
                new_tiles.append((tx, ty))
        hot = sorted(layer.hot_tiles)
    meta_p.parent.mkdir(parents=True, exist_ok=True)
    _write_json(meta_p, {"src": [st.st_mtime_ns, st.st_size], "tiles": new_tiles, "hot": hot})
    _tables(jar, cache, hot_ticks).blocks.tex.save()
    return region, old_tiles, new_tiles, hot


# ---- 合成 ----------------------------------------------------------------------


def _compose_frags(cache: Path, level: int, k: Key) -> Image.Image | None:
    d = cache / "frag" / str(level) / _name(k)
    try:
        names = sorted(os.listdir(d), key=lambda n: _painter(n.removesuffix(".png")))
    except FileNotFoundError:
        return None
    out: Image.Image | None = None
    for n in names:
        im = Image.open(d / n).convert("RGBA")
        out = im if out is None else Image.alpha_composite(out, im)
    return out


def _reduce(cache: Path, z: int, k: Key) -> Image.Image | None:
    big = Image.new("RGBA", (TILE * 2, TILE * 2))
    any_ = False
    for dx in (0, 1):
        for dy in (0, 1):
            p = cache / "pyr" / str(z - 1) / f"{k[0] * 2 + dx}_{k[1] * 2 + dy}.png"
            if p.exists():
                big.paste(Image.open(p).convert("RGBA"), (dx * TILE, dy * TILE))
                any_ = True
    return big.reduce(2) if any_ else None


def _emit(cache: Path, out: Path, z: int, k: Key, im: Image.Image | None) -> None:
    webp = out / "tiles" / str(z) / f"{_name(k)}.webp"
    png = cache / "pyr" / str(z) / f"{_name(k)}.png"
    if im is None or im.getbbox() is None:
        webp.unlink(missing_ok=True)
        png.unlink(missing_ok=True)
        return
    webp.parent.mkdir(parents=True, exist_ok=True)
    im.save(webp, "WEBP", quality=WEBP_QUALITY, method=4, alpha_quality=100)
    if z >= 1:
        png.parent.mkdir(parents=True, exist_ok=True)
        im.save(png, compress_level=1)


# ---- 入口 ----------------------------------------------------------------------


@dataclass
class BuildStats:
    regions: int = 0
    rendered: dict[int, int] = field(default_factory=dict)
    tiles_written: int = 0
    levels: int = 0
    hot_tiles: int = 0
    tiles: dict[str, int] = field(default_factory=dict)  # 每级瓦片数
    bytes: int = 0  # 发布目录里瓦片总大小
    seconds: float = 0.0


def read_spawn(level_dat: Path) -> tuple[int, int, int] | None:
    try:
        root = nbt.loads(gzip.decompress(level_dat.read_bytes()))
    except (OSError, ValueError, nbt.NBTError):
        return None
    data = root.get("Data") or {}
    sp = data.get("spawn")
    if isinstance(sp, dict) and sp.get("pos") is not None and len(sp["pos"]) == 3:
        x, y, z = (int(v) for v in sp["pos"])
        return x, y, z
    if "SpawnX" in data:
        return int(data["SpawnX"]), int(data.get("SpawnY", 64)), int(data["SpawnZ"])
    return None


def _render_all(
    cache: Path, jar: Path | None, hot_ticks: int, level: int, regions: Iterable[str], workers: int
) -> list[tuple[str, list[Key], list[Key], list[Key]]]:
    todo = sorted(regions)
    if not todo:
        return []
    args = (str(cache), str(jar) if jar else None, hot_ticks, level)
    if workers <= 1:
        return [_render_job(*args, r) for r in todo]
    with ProcessPoolExecutor(workers) as ex:
        return list(ex.map(_render_job, *zip(*[(*args, r) for r in todo], strict=True)))


def build(
    cache: Path,
    out: Path,
    jar: Path | None,
    hot_ticks: int = 72000,
    workers: int = 2,
    world_version: str | None = None,
) -> BuildStats:
    t0 = time.monotonic()
    stats = BuildStats()
    regions = {
        p.name: [p.stat().st_mtime_ns, p.stat().st_size]
        for p in (cache / "regions").glob("r.*.*.mca")
        if p.stat().st_size >= 8192
    }
    stats.regions = len(regions)
    dirty: dict[int, set[Key]] = {0: set(), 1: set()}

    def metas(level: int) -> dict[str, dict[str, Any]]:
        d = cache / "meta" / str(level)
        res = {}
        for p in d.glob("*.json") if d.exists() else []:
            m = _read_json(p)
            if m is not None:
                res[p.name.removesuffix(".json")] = m
        return res

    def drop(level: int, region: str, meta: dict[str, Any]) -> None:
        for k in meta.get("tiles", []):
            (cache / "frag" / str(level) / _name(tuple(k)) / (region + ".png")).unlink(
                missing_ok=True
            )
            dirty[level].add(tuple(k))
        (cache / "meta" / str(level) / (region + ".json")).unlink(missing_ok=True)

    # 1) 全图级别：每个变过的区域都重画
    m1 = metas(1)
    for r, m in m1.items():
        if r not in regions:
            drop(1, r, m)
    stale1 = [r for r in regions if (m1.get(r) or {}).get("src") != regions[r]]
    for _r, old, new, _hot in _render_all(cache, jar, hot_ticks, 1, stale1, workers):
        dirty[1].update(old, new)
    stats.rendered[1] = len(stale1)
    m1 = metas(1)

    # 2) 高清级别：只给覆盖到常待瓦片的区域画
    hot: set[Key] = {tuple(k) for m in m1.values() for k in m.get("hot", [])}
    stats.hot_tiles = len(hot)
    need2 = set()
    for r, m in m1.items():
        for tx, ty in m.get("tiles", []):
            if any((2 * tx + dx, 2 * ty + dy) in hot for dx in (0, 1) for dy in (0, 1)):
                need2.add(r)
                break
    m0 = metas(0)
    for r, m in m0.items():
        if r not in need2:
            drop(0, r, m)
    stale0 = [r for r in need2 if (m0.get(r) or {}).get("src") != regions[r]]
    for _r, old, new, _hot in _render_all(cache, jar, hot_ticks, 0, stale0, workers):
        dirty[0].update(old, new)
    stats.rendered[0] = len(stale0)

    state = _read_json(cache / "state.json") or {}
    prev_hot = {tuple(k) for k in state.get("hot", [])}
    dirty[0] |= hot ^ prev_hot

    # 3) 合成第 0、1 级
    for k in dirty[0]:
        _emit(cache, out, 0, k, _compose_frags(cache, 0, k) if k in hot else None)
    for k in dirty[1]:
        _emit(cache, out, 1, k, _compose_frags(cache, 1, k))
    stats.tiles_written = len(dirty[0]) + len(dirty[1])

    # 4) 往上逐级缩小，直到整张图不超过 2x2 张瓦片
    def present(z: int) -> set[Key]:
        d = cache / "pyr" / str(z)
        return {_key(p.stem) for p in d.glob("*.png")} if d.exists() else set()

    prev_levels = int(state.get("levels", 1))
    z, cur = 1, dirty[1]
    while True:
        have = present(z)
        if not have:
            break
        xs = [k[0] for k in have]
        ys = [k[1] for k in have]
        if max(xs) - min(xs) < 2 and max(ys) - min(ys) < 2:
            break
        z += 1
        todo = {(tx // 2, ty // 2) for tx, ty in (have if z > prev_levels else cur)}
        for k in todo:
            _emit(cache, out, z, k, _reduce(cache, z, k))
        stats.tiles_written += len(todo)
        cur = todo
    stats.levels = z + 1
    for extra in range(z + 1, max(prev_levels, z) + 2):  # 图变小了：删掉多出来的级别
        shutil.rmtree(out / "tiles" / str(extra), ignore_errors=True)
        shutil.rmtree(cache / "pyr" / str(extra), ignore_errors=True)

    # 5) manifest：查看器据此只请求存在的瓦片
    tiles: dict[str, list[list[int]]] = {}
    for lv in range(stats.levels):
        d = out / "tiles" / str(lv)
        tiles[str(lv)] = (
            sorted([list(_key(p.stem)) for p in d.glob("*.webp")]) if d.exists() else []
        )
    stats.tiles = {k: len(v) for k, v in tiles.items()}
    stats.bytes = (
        sum(p.stat().st_size for p in (out / "tiles").rglob("*.webp"))
        if (out / "tiles").exists()
        else 0
    )
    if stats.tiles_written or not (cache / "preview.webp").exists():
        write_preview(out, tiles, cache / "preview.webp")
    spawn = read_spawn(cache / "level.dat")
    manifest = {
        "version": 1,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "world_version": world_version,
        "tile": TILE,
        "levels": stats.levels,
        "full_level": 1,
        "hi_scale": HI_SCALE,
        # 第 0 级画面坐标：SX = 4(x - z)，SY = 2(x + z) - 4(y + 1)；第 n 级再除以 2^n
        "projection": "iso-2:1-se",
        "spawn": list(spawn) if spawn else None,
        "tiles": tiles,
    }
    out.mkdir(parents=True, exist_ok=True)
    _write_json(out / "manifest.json", manifest)
    _write_json(cache / "state.json", {"hot": sorted(list(k) for k in hot), "levels": stats.levels})
    stats.seconds = round(time.monotonic() - t0, 1)
    return stats


def write_preview(
    out: Path, tiles: dict[str, list[list[int]]], dest: Path, width: int = 1400
) -> None:
    """面板里的全图缩略图：取拼起来宽度刚超过 width 的那一级，缩到 width 宽。"""
    best = None
    for z in sorted((int(k) for k in tiles), reverse=True):
        keys = tiles[str(z)]
        if not keys:
            continue
        best = z
        if (max(k[0] for k in keys) - min(k[0] for k in keys) + 1) * TILE >= width:
            break
    if best is None:
        return
    tmp = dest.with_name("preview.tmp.png")
    preview(out, best, tmp, bg=None)
    im = Image.open(tmp)
    im.thumbnail((width, width))
    im.save(dest, "WEBP", quality=82, method=4)
    tmp.unlink(missing_ok=True)


def preview(
    out: Path, level: int, dest: Path, bg: tuple[int, int, int] | None = (236, 239, 243)
) -> None:
    """把某一级的瓦片拼成一张图（调试用）。"""
    files = list((out / "tiles" / str(level)).glob("*.webp"))
    keys = [_key(f.stem) for f in files]
    x0, y0 = min(k[0] for k in keys), min(k[1] for k in keys)
    x1, y1 = max(k[0] for k in keys), max(k[1] for k in keys)
    fill = (*bg, 255) if bg else (0, 0, 0, 0)
    im = Image.new("RGBA", ((x1 - x0 + 1) * TILE, (y1 - y0 + 1) * TILE), fill)
    for f, (tx, ty) in zip(files, keys, strict=True):
        im.alpha_composite(Image.open(f).convert("RGBA"), ((tx - x0) * TILE, (ty - y0) * TILE))
    (im.convert("RGB") if bg else im.crop(im.getbbox() or (0, 0, 1, 1))).save(dest)
