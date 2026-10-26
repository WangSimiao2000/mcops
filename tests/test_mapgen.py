"""mapgen：NBT / Anvil 解码、等轴测光栅、增量构建、发布前的存档同步。"""

from __future__ import annotations

import asyncio
import gzip
import json
import os
import struct
import zlib
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from mcops.mapgen import nbt
from mcops.mapgen.anvil import Palette, read_region, unpack
from mcops.mapgen.biome import BiomeTable
from mcops.mapgen.blocks import AIR, SOLID, WATER, BlockTable, Textures, classify
from mcops.mapgen.build import build, read_spawn
from mcops.mapgen.publish import MapPublisher, sync_regions, world_dir
from mcops.mapgen.render import Tables, render_region, sprite

# ---- 测试用的最小 NBT 编码器 ------------------------------------------------------


class Long(int):
    pass


class Byte(int):
    pass


def _tag(v: Any) -> int:
    if isinstance(v, Byte):
        return nbt.BYTE
    if isinstance(v, Long):
        return nbt.LONG
    if isinstance(v, int):
        return nbt.INT
    if isinstance(v, str):
        return nbt.STRING
    if isinstance(v, dict):
        return nbt.COMPOUND
    if isinstance(v, np.ndarray):
        return nbt.LONG_ARRAY if v.dtype.itemsize == 8 else nbt.INT_ARRAY
    if isinstance(v, list):
        return nbt.LIST
    raise TypeError(v)


def _str(s: str) -> bytes:
    b = s.encode()
    return struct.pack(">H", len(b)) + b


def _payload(v: Any) -> bytes:
    t = _tag(v)
    if t == nbt.BYTE:
        return struct.pack(">b", v)
    if t == nbt.INT:
        return struct.pack(">i", v)
    if t == nbt.LONG:
        return struct.pack(">q", v)
    if t == nbt.STRING:
        return _str(v)
    if t in (nbt.LONG_ARRAY, nbt.INT_ARRAY):
        dt = ">i8" if t == nbt.LONG_ARRAY else ">i4"
        return struct.pack(">i", len(v)) + bytes(v.astype(dt).tobytes())
    if t == nbt.LIST:
        sub = _tag(v[0]) if v else nbt.END
        return bytes([sub]) + struct.pack(">i", len(v)) + b"".join(_payload(x) for x in v)
    out = b""
    for k, x in v.items():
        out += bytes([_tag(x)]) + _str(k) + _payload(x)
    return out + b"\x00"


def dumps(root: dict[str, Any]) -> bytes:
    return bytes([nbt.COMPOUND]) + _str("") + _payload(root)


def pack(values: list[int], bits: int) -> np.ndarray:
    per = 64 // bits
    longs = []
    for i in range(0, len(values), per):
        v = 0
        for j, x in enumerate(values[i : i + per]):
            v |= x << (j * bits)
        longs.append(v - (1 << 64) if v >= 1 << 63 else v)
    return np.array(longs, dtype=np.int64)


def chunk_nbt(layers: dict[int, str], inhabited: int = 0, biome: str = "minecraft:plains") -> bytes:
    """一个区块：layers = {世界 y: 方块名}，整层铺满。"""
    sections: dict[int, dict[int, str]] = {}
    for y, name in layers.items():
        sections.setdefault(y // 16, {})[y % 16] = name
    secs = []
    for sy, rows in sorted(sections.items()):
        names = ["minecraft:air", *sorted(set(rows.values()))]
        vals = [0] * 4096
        for ly, name in rows.items():
            for i in range(256):
                vals[ly * 256 + i] = names.index(name)
        secs.append(
            {
                "Y": Byte(sy),
                "block_states": {"palette": [{"Name": n} for n in names], "data": pack(vals, 4)},
                "biomes": {"palette": [biome]},
            }
        )
    return dumps(
        {
            "DataVersion": 4671,
            "Status": "minecraft:full",
            "InhabitedTime": Long(inhabited),
            "sections": secs,
            "block_entities": [{"id": "minecraft:chest", "Items": [{"Count": Byte(1)}]}],
        }
    )


def write_region(path: Path, chunks: dict[tuple[int, int], bytes]) -> None:
    header = bytearray(8192)
    body = bytearray()
    sector = 2
    for (lx, lz), raw in chunks.items():
        data = zlib.compress(raw)
        blob = struct.pack(">IB", len(data) + 1, 2) + data
        n = (len(blob) + 4095) // 4096
        blob += b"\0" * (n * 4096 - len(blob))
        i = lz * 32 + lx
        header[i * 4 : i * 4 + 4] = (sector << 8 | n).to_bytes(4, "big")
        body += blob
        sector += n
    path.write_bytes(bytes(header) + bytes(body))


def tables(tmp: Path) -> Tables:
    tex = Textures(None, tmp / "texavg.json")
    return Tables(BlockTable(tex), BiomeTable(tex.colormaps))


# ---- NBT / Anvil --------------------------------------------------------------


def test_nbt_roundtrip_and_skip() -> None:
    raw = dumps({"a": 1, "b": "x", "c": [{"d": Long(5)}], "e": np.arange(3, dtype=np.int64)})
    full = nbt.loads(raw)
    assert full["a"] == 1 and full["b"] == "x" and full["c"][0]["d"] == 5
    assert list(full["e"]) == [0, 1, 2]
    assert nbt.loads(raw, frozenset({"b"})) == {"b": "x"}


@pytest.mark.parametrize("bits", [1, 4, 5, 7, 15])
def test_unpack_matches_packing(bits: int) -> None:
    rng = np.random.default_rng(bits)
    vals = rng.integers(0, 1 << bits, 4096).tolist()
    assert unpack(pack(vals, bits), bits, 4096).tolist() == vals


def test_read_region_decodes_blocks_and_skips_unfinished(tmp_path: Path) -> None:
    p = tmp_path / "r.0.0.mca"
    unfinished = dumps({"Status": "minecraft:features", "sections": []})
    write_region(p, {(0, 0): chunk_nbt({64: "minecraft:stone"}, 99), (1, 0): unfinished})
    bp, mp = Palette("minecraft:air"), Palette("minecraft:plains")
    chunks = list(read_region(p, bp, mp))
    assert len(chunks) == 1
    ch = chunks[0]
    assert ch.inhabited == 99
    stone = bp.ids["minecraft:stone"]
    assert (ch.blocks[64 + 64] == stone).all()  # 数组下标 = y - (-64)
    assert (ch.blocks[64 + 63] == 0).all()


def test_classify() -> None:
    assert classify("minecraft:stone") == SOLID
    assert classify("minecraft:short_grass") == AIR
    assert classify("minecraft:oak_sapling") == AIR
    assert classify("minecraft:kelp") == WATER


# ---- 光栅 ------------------------------------------------------------------------


@pytest.mark.parametrize("s", [1, 2])
def test_sprite_faces(s: int) -> None:
    _, _, faces = sprite(s)
    assert [int((faces == f).sum()) for f in (0, 1, 2)] == [4 * s * s] * 3


def test_render_flat_plain(tmp_path: Path) -> None:
    p = tmp_path / "r.0.0.mca"
    write_region(p, {(0, 0): chunk_nbt({64: "minecraft:stone"})})
    layer = render_region(p, 1, tables(tmp_path))
    assert layer is not None
    a = layer.rgba[..., 3]
    # 16x16 的平台：顶面 16*16*4 像素 + 南、东两条边各 16 块 * 4 像素
    assert int((a == 255).sum()) == 16 * 16 * 4 + 2 * 16 * 4
    assert layer.hot_tiles == set()


def test_render_hot_chunk_marks_tiles(tmp_path: Path) -> None:
    p = tmp_path / "r.0.0.mca"
    write_region(p, {(0, 0): chunk_nbt({64: "minecraft:stone"}, inhabited=10**6)})
    layer = render_region(p, 1, tables(tmp_path))
    assert layer is not None and layer.hot_tiles


# ---- 增量构建 ----------------------------------------------------------------------


def _world(cache: Path, hot: bool = False) -> None:
    (cache / "regions").mkdir(parents=True, exist_ok=True)
    write_region(
        cache / "regions" / "r.0.0.mca",
        {
            (0, 0): chunk_nbt(
                {63: "minecraft:dirt", 64: "minecraft:grass_block"}, 10**6 if hot else 0
            )
        },
    )
    write_region(cache / "regions" / "r.-3.0.mca", {(0, 0): chunk_nbt({62: "minecraft:water"})})
    level = dumps({"Data": {"spawn": {"pos": np.array([1, 65, 2], dtype=np.int32)}}})
    (cache / "level.dat").write_bytes(gzip.compress(level))


def test_build_incremental(tmp_path: Path) -> None:
    cache, out = tmp_path / "cache", tmp_path / "site"
    _world(cache, hot=True)
    st = build(cache, out, None, workers=1)
    assert st.rendered == {1: 2, 0: 1}  # 只有覆盖到常待瓦片的区域才出高清
    man = json.loads((out / "manifest.json").read_text())
    assert man["spawn"] == [1, 65, 2]
    assert st.tiles["1"] == len(man["tiles"]["1"]) and st.bytes > 0
    assert (cache / "preview.webp").is_file()
    assert man["tiles"]["0"] and man["tiles"]["1"]
    for z, keys in man["tiles"].items():
        for tx, ty in keys:
            assert (out / "tiles" / z / f"{tx}_{ty}.webp").is_file()

    again = build(cache, out, None, workers=1)
    assert again.rendered == {1: 0, 0: 0} and again.tiles_written == 0

    # 改一个区域：只重画它
    p = cache / "regions" / "r.-3.0.mca"
    os.utime(p, ns=(p.stat().st_atime_ns, p.stat().st_mtime_ns + 10**9))
    assert build(cache, out, None, workers=1).rendered[1] == 1

    # 删掉常待区域：高清瓦片随之消失
    (cache / "regions" / "r.0.0.mca").unlink()
    build(cache, out, None, workers=1)
    man = json.loads((out / "manifest.json").read_text())
    assert man["tiles"]["0"] == []
    assert not list((out / "tiles" / "0").glob("*.webp"))


def test_neighbor_of_hot_area_also_rendered_hi(tmp_path: Path) -> None:
    """高清瓦片跨区域时，相邻区域也要出高清，否则合成出来的瓦片会缺一块。"""
    cache = tmp_path / "cache"
    _world(cache, hot=True)
    write_region(cache / "regions" / "r.-1.0.mca", {(31, 0): chunk_nbt({62: "minecraft:stone"})})
    assert build(cache, tmp_path / "site", None, workers=1).rendered[0] == 2


def test_read_spawn_legacy_keys(tmp_path: Path) -> None:
    p = tmp_path / "level.dat"
    p.write_bytes(gzip.compress(dumps({"Data": {"SpawnX": 5, "SpawnY": 70, "SpawnZ": -3}})))
    assert read_spawn(p) == (5, 70, -3)


# ---- 发布：存档同步与调度 ------------------------------------------------------------


def test_sync_regions_copies_only_changes(tmp_path: Path) -> None:
    world = tmp_path / "world"
    (world / "region").mkdir(parents=True)
    a = world / "region" / "r.0.0.mca"
    a.write_bytes(b"x" * 9000)
    (world / "level.dat").write_bytes(b"lvl")
    dest = tmp_path / "cache"
    assert sync_regions(world, dest) == {"regions": 1, "copied": 1, "removed": 0}
    assert sync_regions(world, dest)["copied"] == 0
    a.write_bytes(b"y" * 9001)
    assert sync_regions(world, dest)["copied"] == 1
    a.unlink()
    assert sync_regions(world, dest)["removed"] == 1
    assert (dest / "level.dat").read_bytes() == b"lvl"


def test_world_dir_reads_level_name(tmp_path: Path) -> None:
    (tmp_path / "server.properties").write_text("level-name=survival\n")
    assert world_dir(tmp_path) == tmp_path / "survival"
    assert world_dir(tmp_path / "missing") == tmp_path / "missing" / "world"


def _pub(tmp_path: Path) -> MapPublisher:
    async def cmd(_c: str) -> str:
        return ""

    return MapPublisher(
        game_dir=tmp_path,
        data_dir=tmp_path,
        remote="git@example.com:x/y.git",
        ssh_key=tmp_path / "k",
        branch="main",
        run_at="06:30",
        tz=timezone(timedelta(hours=8)),
        hot_minutes=60,
        workers=1,
        snapshot=lambda: {},
        command=cmd,
    )


def test_next_run_catches_up_once_per_day(tmp_path: Path) -> None:
    pub = _pub(tmp_path)
    tz = timezone(timedelta(hours=8))
    before = datetime(2026, 10, 17, 5, 0, tzinfo=tz)
    assert pub._next_run(before.astimezone(UTC)) == datetime(2026, 10, 17, 6, 30, tzinfo=tz)
    after = datetime(2026, 10, 17, 9, 0, tzinfo=tz)
    assert pub._next_run(after.astimezone(UTC)) == after  # 今天还没跑：马上补跑
    pub._save_state(last_date="2026-10-17")
    assert pub._next_run(after.astimezone(UTC)) == datetime(2026, 10, 18, 6, 30, tzinfo=tz)


def test_restart_mode_uses_fallback_time(tmp_path: Path) -> None:
    pub = _pub(tmp_path)
    pub.run_at = None
    pub.fallback_at = "06:30"
    tz = timezone(timedelta(hours=8))
    pub._save_state(last_date="2026-10-17")
    after = datetime(2026, 10, 17, 9, 0, tzinfo=tz)
    assert pub._next_run(after.astimezone(UTC)) == datetime(2026, 10, 18, 6, 30, tzinfo=tz)


def test_restart_run_before_fallback_skips_today(tmp_path: Path) -> None:
    # 10-20：05:14 跟随重启生成过，06:20~06:30 醒来时兜底不能再排今天 06:30
    pub = _pub(tmp_path)
    pub.run_at = None
    pub.fallback_at = "06:30"
    tz = timezone(timedelta(hours=8))
    pub._save_state(last_date="2026-10-20")
    for hh, mm, ss in ((5, 20, 0), (6, 21, 7), (6, 29, 59)):
        now = datetime(2026, 10, 20, hh, mm, ss, tzinfo=tz)
        assert pub._next_run(now.astimezone(UTC)) == datetime(2026, 10, 21, 6, 30, tzinfo=tz)


def test_loop_does_not_run_twice_when_waking_just_before_target(tmp_path: Path) -> None:
    # asyncio 的定时器可能比目标早一点点醒，原来 _next_run 和算 delay 各取一次 now，
    # 在 06:29:59.999 返回今天的 06:30、到算 delay 时已过 06:30 => delay<=0 直接跑
    pub = _pub(tmp_path)
    pub.run_at = None
    pub.fallback_at = "06:30"
    tz = timezone(timedelta(hours=8))
    pub._save_state(last_date="2026-10-20")
    now = datetime(2026, 10, 20, 6, 29, 59, 999000, tzinfo=tz)
    assert pub._due(now.astimezone(UTC)) is False
    pub._save_state(last_date="2026-10-19")
    assert pub._due(datetime(2026, 10, 20, 6, 30, 0, 1, tzinfo=tz).astimezone(UTC)) is True


async def test_loop_skips_fallback_when_restart_already_ran(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mcops.mapgen.publish as mod

    tz = timezone(timedelta(hours=8))
    clock = [datetime(2026, 10, 17, 18, 0, tzinfo=tz)]
    restart_done = datetime(2026, 10, 18, 5, 10, tzinfo=tz)
    stop_at = datetime(2026, 10, 19, 7, 0, tzinfo=tz)

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> Any:
            return clock[0].astimezone(tz) if tz else clock[0]

    pub = _pub(tmp_path)
    pub.run_at = None
    pub.fallback_at = "06:30"
    pub._save_state(last_date="2026-10-17")
    runs: list[datetime] = []

    async def run_once(trigger: str = "schedule") -> dict[str, Any]:
        runs.append(clock[0])
        pub._save_state(last_date=clock[0].date().isoformat())
        return {}

    async def fake_sleep(s: float) -> None:
        before = clock[0]
        clock[0] += timedelta(seconds=s)
        if before < restart_done <= clock[0]:  # 05:00 重启那一路生成完成
            pub._save_state(last_date="2026-10-18")
        if clock[0] > stop_at:
            raise asyncio.CancelledError

    monkeypatch.setattr(mod, "datetime", FakeDT)
    monkeypatch.setattr(pub, "run_once", run_once)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)  # mod 里的 asyncio 就是这个模块
    with pytest.raises(asyncio.CancelledError):
        await pub.loop()
    # 10-18 重启已生成，兜底不再跑；10-19 没有重启记录，06:30 兜底跑一次
    assert [r.astimezone(tz).date().isoformat() for r in runs] == ["2026-10-19"]


async def test_restart_hook_copies_then_renders_after_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = tmp_path / "world"
    (world / "region").mkdir(parents=True)
    (world / "region" / "r.0.0.mca").write_bytes(b"x" * 9000)
    said: list[str] = []

    async def announce(text: str) -> None:
        said.append(text)

    pub = _pub(tmp_path)
    pub.announce = announce
    pub.snapshot = lambda: {"state": "running", "mc_version": "1.21.11"}
    calls: list[str] = []

    async def jar(_v: str) -> Path:
        calls.append("jar")
        return tmp_path / "client.jar"

    async def render(_jar: Path, _v: str | None) -> dict[str, Any]:
        calls.append("render")
        return {"rendered": {"1": 3, "0": 1}}

    async def push(_stamp: str) -> None:
        calls.append("push")

    async def nosleep(_s: float) -> None:
        return None

    monkeypatch.setattr(pub, "ensure_jar", jar)
    monkeypatch.setattr(pub, "render", render)
    monkeypatch.setattr(pub, "push", push)
    monkeypatch.setattr(asyncio, "sleep", nosleep)
    notes: list[str] = []
    await pub.on_restart_stopped(notes.append)
    assert "复制了 1 个变化的区域" in notes[0]
    assert pub.running and pub.phase == "等待开服"
    assert pub._bg is not None
    await pub._bg
    assert calls == ["jar", "render", "push"]
    assert not pub.running
    st = pub.status()
    assert st["history"][0]["ok"] and st["history"][0]["trigger"] == "restart"
    assert st["last"]["result"]["render"]["rendered"]["1"] == 3
    assert said and "3 个区域" in said[0]

    # 失败也记进历史，且不播报
    async def bad(_jar: Path, _v: str | None) -> dict[str, Any]:
        raise RuntimeError("渲染炸了")

    monkeypatch.setattr(pub, "render", bad)
    with pytest.raises(RuntimeError):
        await pub.run_once("test")
    st = pub.status()
    assert not st["history"][0]["ok"] and "渲染炸了" in st["history"][0]["error"]
    assert len(said) == 1 and not pub.running


async def test_no_announce_when_nothing_changed(tmp_path: Path) -> None:
    said: list[str] = []

    async def announce(text: str) -> None:
        said.append(text)

    pub = _pub(tmp_path)
    pub.announce = announce
    await pub._announce({"render": {"rendered": {"1": 0, "0": 0}}})
    assert said == []


async def test_announce_includes_public_url(tmp_path: Path) -> None:
    said: list[str] = []

    async def announce(text: str) -> None:
        said.append(text)

    pub = _pub(tmp_path)
    pub.announce = announce
    pub.public_url = "https://blog.example.com/server/map/"
    await pub._announce({"render": {"rendered": {"1": 3}}})
    assert len(said) == 1
    assert "3 个区域" in said[0] and "https://blog.example.com/server/map/" in said[0]
