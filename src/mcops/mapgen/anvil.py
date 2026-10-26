"""Anvil 区域文件（r.X.Z.mca）读取，区块解码为方块 id 体素数组。"""

from __future__ import annotations

import gzip
import struct
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from mcops.mapgen import nbt

MIN_Y = -64
HEIGHT = 384  # -64..319
SECTIONS = HEIGHT // 16

_ROOT_KEYS = frozenset({"Status", "sections", "InhabitedTime"})


class Palette:
    """全局调色板：方块名 / 群系名 -> 小整数 id（0 号固定为空气 / 未知群系）。"""

    def __init__(self, first: str) -> None:
        self.names: list[str] = [first]
        self.ids: dict[str, int] = {first: 0}

    def id(self, name: str) -> int:
        i = self.ids.get(name)
        if i is None:
            i = len(self.names)
            self.names.append(name)
            self.ids[name] = i
        return i


@dataclass(slots=True)
class Chunk:
    cx: int
    cz: int
    blocks: np.ndarray  # uint16 [384, 16(z), 16(x)]，全局方块 id
    biomes: np.ndarray  # uint16 [96, 4(z), 4(x)]，全局群系 id
    inhabited: int = 0  # 玩家在附近累计待过的游戏刻（InhabitedTime）


def unpack(data: np.ndarray, bits: int, count: int) -> np.ndarray:
    """1.16+ 的紧凑长整型数组：每个 long 放 64//bits 个值，不跨 long。"""
    longs = data.astype(np.int64).view(np.uint64)
    per = 64 // bits
    shifts = (np.arange(per, dtype=np.uint64) * np.uint64(bits))[None, :]
    vals = (longs[:, None] >> shifts) & np.uint64((1 << bits) - 1)
    return vals.reshape(-1)[:count]


def _read_chunk_bytes(f: bytes, index: int, region_path: Path, cx: int, cz: int) -> bytes | None:
    loc = int.from_bytes(f[index * 4 : index * 4 + 3], "big")
    if loc == 0:
        return None
    start = loc * 4096
    (length,) = struct.unpack_from(">I", f, start)
    comp = f[start + 4]
    if comp & 0x80:  # 大区块存在外部 .mcc 文件
        ext = region_path.with_name(f"c.{cx}.{cz}.mcc")
        payload = ext.read_bytes()
        comp &= 0x7F
    else:
        payload = f[start + 5 : start + 4 + length]
    if comp == 2:
        return zlib.decompress(payload)
    if comp == 1:
        return gzip.decompress(payload)
    if comp == 3:
        return bytes(payload)
    raise ValueError(f"{region_path.name} 区块 ({cx},{cz}) 使用了不支持的压缩格式 {comp}")


def _decode(raw: bytes, cx: int, cz: int, blocks: Palette, biomes: Palette) -> Chunk | None:
    root = nbt.loads(raw, _ROOT_KEYS)
    status = str(root.get("Status", ""))
    if status.removeprefix("minecraft:") != "full":
        return None  # 未生成完的区块（世界边缘）不画
    out_b = np.zeros((HEIGHT, 16, 16), dtype=np.uint16)
    out_m = np.zeros((HEIGHT // 4, 4, 4), dtype=np.uint16)
    for sec in root.get("sections", []):
        sy = int(sec.get("Y", 0)) - MIN_Y // 16
        if not 0 <= sy < SECTIONS:
            continue
        bs = sec.get("block_states")
        if bs:
            pal = [blocks.id(p["Name"]) for p in bs.get("palette", [])]
            if len(pal) == 1 or "data" not in bs:
                if pal[0]:
                    out_b[sy * 16 : sy * 16 + 16] = pal[0]
            else:
                bits = max(4, (len(pal) - 1).bit_length())
                idx = unpack(bs["data"], bits, 4096)
                lut = np.asarray(pal, dtype=np.uint16)
                out_b[sy * 16 : sy * 16 + 16] = lut[idx.astype(np.intp)].reshape(16, 16, 16)
        bm = sec.get("biomes")
        if bm:
            pal = [biomes.id(p) for p in bm.get("palette", [])]
            if len(pal) == 1 or "data" not in bm:
                out_m[sy * 4 : sy * 4 + 4] = pal[0]
            else:
                bits = max(1, (len(pal) - 1).bit_length())
                idx = unpack(bm["data"], bits, 64)
                lut = np.asarray(pal, dtype=np.uint16)
                out_m[sy * 4 : sy * 4 + 4] = lut[idx.astype(np.intp)].reshape(4, 4, 4)
    return Chunk(cx, cz, out_b, out_m, int(root.get("InhabitedTime", 0)))


def read_region(path: Path, blocks: Palette, biomes: Palette) -> Iterator[Chunk]:
    """依次产出区域里已生成完的区块（绝对区块坐标）。"""
    rx, rz = (int(v) for v in path.name.split(".")[1:3])
    data = path.read_bytes()
    if len(data) < 8192:
        return
    for lz in range(32):
        for lx in range(32):
            cx, cz = rx * 32 + lx, rz * 32 + lz
            raw = _read_chunk_bytes(data, lz * 32 + lx, path, cx, cz)
            if raw is None:
                continue
            ch = _decode(raw, cx, cz, blocks, biomes)
            if ch is not None:
                yield ch
