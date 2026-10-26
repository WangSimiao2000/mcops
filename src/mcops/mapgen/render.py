"""区域 -> 等轴测图层。

投影：2:1 像素等轴测，从东南往西北看（视线方向 (-1,-1,-1)）。缩放 s 时一个方块的顶面是
4s x 2s 的菱形，竖直每格 2s 像素：

    SX = 2s (x - z)          SY = s (x + z) - 2s (y + 1)

可见性：只取「上 / 南 / 东」任一面贴着空气的方块（水只看是否贴着空气），按深度 x+y+z
做 z-buffer（深度大的离观察者近）。区域和区块边界外当作空气：多画出的面一定会被前面
的区域盖住（前面区域的方块轮廓覆盖它背面的投影），所以合成后是对的。

内存：区块按行流式处理，只保留两行解码结果（约 13MB）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from mcops.mapgen.anvil import MIN_Y, Chunk, Palette, read_region
from mcops.mapgen.biome import BiomeTable
from mcops.mapgen.blocks import (
    AIR,
    SOLID,
    T_FOLIAGE,
    T_GRASS,
    T_WATER,
    WATER,
    BlockTable,
)

SHADE_LEFT = 0.80  # 南面（画面左下），朝光
SHADE_RIGHT = 0.62  # 东面（画面右下），背光
WATER_MAX_DEPTH = 12


@dataclass(slots=True)
class Layer:
    rx: int
    rz: int
    ox: int  # 图层左上角在全局画面坐标里的位置
    oy: int
    rgba: np.ndarray  # uint8 [H, W, 4]
    hot_tiles: set[tuple[int, int]]  # 玩家常待区块覆盖到的高清（第 0 级）瓦片


class Tables:
    """一次渲染会话共享的调色板和查找表（多个区域复用，方块 id 保持一致）。"""

    def __init__(self, blocks: BlockTable, biomes: BiomeTable) -> None:
        self.bpal = Palette("minecraft:air")
        self.mpal = Palette("minecraft:plains")
        self.blocks = blocks
        self.biomes = biomes
        self.hot_ticks = 72000  # InhabitedTime 超过它（默认 1 小时）的区块出高清瓦片

    def sync(self) -> None:
        self.blocks.sync(self.bpal.names)
        self.biomes.sync(self.mpal.names)


def sprite(s: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """方块精灵的像素偏移 (ox, oy) 和所属面（0 顶 / 1 南 / 2 东），以顶面西北角为锚点。"""
    oxs, oys, faces = [], [], []
    for oy in range(4 * s):
        for ox in range(-2 * s, 2 * s):
            x, y = ox + 0.5, oy + 0.5
            if abs(x) / (2 * s) + abs(y - s) / s <= 1:
                f = 0
            elif x < 0 and 2 * s + x / 2 <= y <= 4 * s + x / 2:
                f = 1
            elif x > 0 and 2 * s - x / 2 <= y <= 4 * s - x / 2:
                f = 2
            else:
                continue
            oxs.append(ox)
            oys.append(oy)
            faces.append(f)
    return np.array(oxs), np.array(oys), np.array(faces)


def _hash3(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    h = (x * 73856093) ^ (y * 19349663) ^ (z * 83492791)
    h = (h ^ (h >> 13)) * 1274126177
    out: np.ndarray = ((h >> 16) & 0xFF).astype(np.float32) / 255.0
    return out


class _Collect:
    def __init__(self) -> None:
        self.x: list[np.ndarray] = []
        self.y: list[np.ndarray] = []
        self.z: list[np.ndarray] = []
        self.top: list[np.ndarray] = []
        self.side: list[np.ndarray] = []
        self.hot: list[np.ndarray] = []


def _visible(ch: Chunk, east: Chunk | None, south: Chunk | None, t: Tables, out: _Collect) -> None:
    kind = t.blocks.kind
    k = kind[ch.blocks]  # [384, 16(z), 16(x)]
    pad = np.zeros((k.shape[0] + 1, 17, 17), dtype=np.uint8)  # 默认空气
    pad[:-1, :16, :16] = k
    if east is not None:
        pad[:-1, :16, 16] = kind[east.blocks[:, :, 0]]
    if south is not None:
        pad[:-1, 16, :16] = kind[south.blocks[:, 0, :]]
    up = pad[1:, :16, :16]
    sth = pad[:-1, 1:, :16]
    est = pad[:-1, :16, 1:]
    open_ = (up == AIR) | (sth == AIR) | (est == AIR)
    vis = ((k == SOLID) | (k == WATER)) & open_
    ys, zs, xs = np.nonzero(vis)
    if ys.size == 0:
        return
    ids = ch.blocks[ys, zs, xs]
    bio = ch.biomes[ys >> 2, zs >> 2, xs >> 2]
    top = t.blocks.top[ids].copy()
    side = t.blocks.side[ids].copy()
    tint = t.blocks.tint[ids]

    g = tint == T_GRASS
    top[g] *= t.biomes.grass[bio[g]] / 255.0
    f = tint == T_FOLIAGE
    top[f] *= t.biomes.foliage[bio[f]] / 255.0
    side[f] *= t.biomes.foliage[bio[f]] / 255.0

    w = tint == T_WATER
    if w.any():
        wy, wz, wx = ys[w], zs[w], xs[w]
        water = t.biomes.water[bio[w]]
        # 往下数水深，找到水底方块的颜色，按深度混合：浅水透出水底，深水是水色
        depth = np.zeros(wy.size, dtype=np.int32)
        floor = np.zeros((wy.size, 3), dtype=np.float32)
        found = np.zeros(wy.size, dtype=bool)
        for d in range(1, WATER_MAX_DEPTH + 1):
            yy = wy - d
            ok = ~found & (yy >= 0)
            if not ok.any():
                break
            bid = np.zeros(wy.size, dtype=np.uint16)
            bid[ok] = ch.blocks[yy[ok], wz[ok], wx[ok]]
            hit = ok & (kind[bid] != WATER) & (kind[bid] != AIR)
            floor[hit] = t.blocks.top[bid[hit]]
            gt = hit & (t.blocks.tint[bid] == T_GRASS)
            floor[gt] *= t.biomes.grass[bio[w][gt]] / 255.0
            depth[hit] = d
            found |= hit
        depth[~found] = WATER_MAX_DEPTH
        # 平滑的深度曲线加一点抖动，避免一圈圈明显的色带
        dith = (_hash3(wx + ch.cx * 16, wy, wz + ch.cz * 16) - 0.5) * 0.06
        a = np.clip(0.40 + 0.53 * (1 - np.exp(-depth / 3.5)) + dith, 0.38, 0.95)[:, None]
        mixed = floor * (1 - a) + water * a
        mixed[~found] = water[~found] * 0.85
        top[w] = mixed
        side[w] = water * 0.9

    # 每个方块一点亮度抖动，平地不至于一片死色；越高的地方略亮，增强起伏感
    gx = xs + ch.cx * 16
    gz = zs + ch.cz * 16
    gy = ys + MIN_Y
    jitter = 0.94 + 0.12 * _hash3(gx, gy, gz)
    lift = np.clip(0.94 + (gy - 62) * 0.0012, 0.88, 1.06)
    m = (jitter * lift)[:, None]
    m[w] = 1.0
    out.x.append(gx.astype(np.int32))
    out.y.append(gy.astype(np.int32))
    out.z.append(gz.astype(np.int32))
    out.top.append(top * m)
    out.side.append(side * m)
    out.hot.append(np.full(gx.size, ch.inhabited >= t.hot_ticks))


def render_region(path: Path, s: int, t: Tables, tile: int = 512) -> Layer | None:
    rx, rz = (int(v) for v in path.name.split(".")[1:3])
    out = _Collect()
    rows: dict[int, dict[int, Chunk]] = {}

    def flush(lz: int) -> None:
        row = rows.pop(lz)
        below = rows.get(lz + 1, {})
        for lx, ch in row.items():
            _visible(ch, row.get(lx + 1), below.get(lx), t, out)

    # read_region 按行产出（lz 外层）：读到第 lz 行时，第 lz-1 行已完整，
    # 于是第 lz-2 行（南邻是 lz-1）可以画了。始终只留两行在内存里。
    for ch in read_region(path, t.bpal, t.mpal):
        t.sync()
        lz = ch.cz - rz * 32
        if lz not in rows:
            for old in sorted(r for r in rows if r < lz - 1):
                flush(old)
            rows[lz] = {}
        rows[lz][ch.cx - rx * 32] = ch
    for lz in sorted(rows):
        flush(lz)
    return None if not out.x else _raster(rx, rz, s, out, tile)


HI_SCALE = 2  # 第 0 级（高清）瓦片的缩放


def hot_tiles(x: np.ndarray, y: np.ndarray, z: np.ndarray, tile: int) -> set[tuple[int, int]]:
    """这些方块在高清级别（s=2）画面上覆盖到的瓦片。"""
    s = HI_SCALE
    bx = 2 * s * (x - z)
    by = s * (x + z) - 2 * s * (y + 1)
    out: set[tuple[int, int]] = set()
    for dx in (-2 * s, 2 * s - 1):
        for dy in (0, 4 * s - 1):
            k = np.unique(np.stack([(bx + dx) // tile, (by + dy) // tile], axis=1), axis=0)
            out.update((int(a), int(b)) for a, b in k.tolist())
    return out


def _raster(rx: int, rz: int, s: int, c: _Collect, tile: int) -> Layer:
    x = np.concatenate(c.x)
    y = np.concatenate(c.y)
    z = np.concatenate(c.z)
    hot = np.concatenate(c.hot)
    hot_set = hot_tiles(x[hot], y[hot], z[hot], tile) if hot.any() else set()
    top = np.clip(np.concatenate(c.top), 0, 255)
    side = np.concatenate(c.side)
    cols = [
        top.astype(np.uint8),
        np.clip(side * SHADE_LEFT, 0, 255).astype(np.uint8),
        np.clip(side * SHADE_RIGHT, 0, 255).astype(np.uint8),
    ]
    depth = x + y + z
    order = np.argsort(depth, kind="stable")
    x, y, z, depth = x[order], y[order], z[order], depth[order]
    cols = [cc[order] for cc in cols]

    bx = 2 * s * (x - z)
    by = s * (x + z) - 2 * s * (y + 1)
    ox0 = int(bx.min()) - 2 * s
    oy0 = int(by.min())
    w = int(bx.max()) + 2 * s - ox0
    h = int(by.max()) + 4 * s - oy0
    zbuf = np.full(w * h, np.iinfo(np.int32).min, dtype=np.int32)
    img = np.zeros((w * h, 4), dtype=np.uint8)
    base = (by - oy0) * w + (bx - ox0)
    for ox, oy, face in zip(*sprite(s), strict=True):
        idx = base + (oy * w + ox)
        keep = depth >= zbuf[idx]
        i2 = idx[keep]
        zbuf[i2] = depth[keep]
        img[i2, :3] = cols[face][keep]
        img[i2, 3] = 255
    return Layer(rx, rz, ox0, oy0, img.reshape(h, w, 4), hot_set)
