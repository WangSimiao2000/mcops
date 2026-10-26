"""方块分类与颜色：从客户端 jar 的贴图算平均色（只保存算好的颜色，不分发贴图）。

每种方块取顶面和侧面两个颜色；草、树叶、水按群系着色。非完整方块（草丛、花、火把、
告示牌……）当作空气，地图上看起来更干净。
"""

from __future__ import annotations

import io
import json
import zipfile
import zlib
from pathlib import Path

import numpy as np
from PIL import Image

AIR, SOLID, WATER = 0, 1, 2
# 群系着色方式
T_NONE, T_GRASS, T_FOLIAGE, T_WATER = 0, 1, 2, 3

_TEX = "assets/minecraft/textures/block/"

# 画成空气的（不占体积、太小或纯装饰）
_AIR_EXACT = {
    "air",
    "cave_air",
    "void_air",
    "light",
    "barrier",
    "structure_void",
    "short_grass",
    "tall_grass",
    "fern",
    "large_fern",
    "dead_bush",
    "bush",
    "short_dry_grass",
    "tall_dry_grass",
    "firefly_bush",
    "leaf_litter",
    "wildflowers",
    "pink_petals",
    "cactus_flower",
    "dandelion",
    "poppy",
    "blue_orchid",
    "allium",
    "azure_bluet",
    "oxeye_daisy",
    "cornflower",
    "lily_of_the_valley",
    "torchflower",
    "closed_eyeblossom",
    "open_eyeblossom",
    "wither_rose",
    "sunflower",
    "lilac",
    "rose_bush",
    "peony",
    "pitcher_plant",
    "spore_blossom",
    "hanging_roots",
    "torch",
    "wall_torch",
    "soul_torch",
    "soul_wall_torch",
    "redstone_torch",
    "redstone_wall_torch",
    "copper_torch",
    "copper_wall_torch",
    "redstone_wire",
    "lever",
    "tripwire",
    "tripwire_hook",
    "string",
    "cobweb",
    "vine",
    "glow_lichen",
    "ladder",
    "fire",
    "soul_fire",
    "scaffolding",
    "chain",
    "iron_chain",
    "end_rod",
    "lightning_rod",
    "brown_mushroom",
    "red_mushroom",
    "crimson_fungus",
    "warped_fungus",
    "crimson_roots",
    "warped_roots",
    "nether_sprouts",
    "twisting_vines",
    "twisting_vines_plant",
    "weeping_vines",
    "weeping_vines_plant",
    "cave_vines",
    "cave_vines_plant",
    "big_dripleaf_stem",
    "small_dripleaf",
    "sweet_berry_bush",
    "nether_wart",
    "attached_melon_stem",
    "attached_pumpkin_stem",
    "melon_stem",
    "pumpkin_stem",
    "flower_pot",
    "pale_hanging_moss",
    "resin_clump",
    "sculk_vein",
    "pointed_dripstone",
    "amethyst_cluster",
    "small_amethyst_bud",
    "medium_amethyst_bud",
    "large_amethyst_bud",
    "frogspawn",
    "turtle_egg",
    "sniffer_egg",
    "candle",
    "player_head",
    "player_wall_head",
    "skeleton_skull",
    "creeper_head",
    "zombie_head",
    "dragon_head",
    "piglin_head",
    "wither_skeleton_skull",
}
_AIR_SUFFIX = (
    "_sapling",
    "_button",
    "_pressure_plate",
    "_sign",
    "_banner",
    "_tulip",
    "_rail",
    "_coral",
    "_coral_fan",
    "_coral_wall_fan",
    "_candle",
    "_head",
    "_skull",
    "_torch",
    "_propagule",
)
_AIR_PREFIX = ("potted_",)
_RAILS = {"rail", "powered_rail", "detector_rail", "activator_rail"}
_WATER = {"water", "bubble_column", "seagrass", "tall_seagrass", "kelp", "kelp_plant"}

_FOLIAGE_FIXED = {
    "spruce_leaves": (0x61, 0x99, 0x61),
    "birch_leaves": (0x80, 0xA7, 0x55),
    "mangrove_leaves": (0x92, 0xC6, 0x48),
}
_FOLIAGE_UNTINTED = {
    "cherry_leaves",
    "azalea_leaves",
    "flowering_azalea_leaves",
    "pale_oak_leaves",
}

# 方块名 -> (顶面贴图, 侧面贴图)；没列出的按通用规则猜
_SPECIAL: dict[str, tuple[str, str]] = {
    "grass_block": ("grass_block_top", "grass_block_side"),
    "water": ("water_still", "water_still"),
    "lava": ("lava_still", "lava_still"),
    "dirt_path": ("dirt_path_top", "dirt_path_side"),
    "farmland": ("farmland_moist", "dirt"),
    "snow": ("snow", "snow"),
    "powder_snow": ("powder_snow", "powder_snow"),
    "chest": ("oak_planks", "oak_planks"),
    "trapped_chest": ("oak_planks", "oak_planks"),
    "ender_chest": ("obsidian", "obsidian"),
    "wheat": ("wheat_stage7", "wheat_stage7"),
    "carrots": ("carrots_stage3", "carrots_stage3"),
    "potatoes": ("potatoes_stage3", "potatoes_stage3"),
    "beetroots": ("beetroots_stage3", "beetroots_stage3"),
    "sugar_cane": ("sugar_cane", "sugar_cane"),
    "bamboo": ("bamboo_stalk", "bamboo_stalk"),
    "cactus": ("cactus_top", "cactus_side"),
    "lily_pad": ("lily_pad", "lily_pad"),
    "quartz_block": ("quartz_block_top", "quartz_block_side"),
    "smooth_quartz": ("quartz_block_bottom", "quartz_block_bottom"),
    "smooth_stone": ("smooth_stone", "smooth_stone"),
    "smooth_sandstone": ("sandstone_top", "sandstone_top"),
    "smooth_red_sandstone": ("red_sandstone_top", "red_sandstone_top"),
    "sandstone": ("sandstone_top", "sandstone"),
    "red_sandstone": ("red_sandstone_top", "red_sandstone"),
    "bookshelf": ("oak_planks", "bookshelf"),
    "crafting_table": ("crafting_table_top", "crafting_table_front"),
    "furnace": ("furnace_top", "furnace_front"),
    "hay_block": ("hay_block_top", "hay_block_side"),
    "moss_carpet": ("moss_block", "moss_block"),
    "bell": ("gold_block", "gold_block"),
    "campfire": ("campfire_log_lit", "campfire_log_lit"),
    "soul_campfire": ("soul_campfire_log_lit", "soul_campfire_log_lit"),
    "lantern": ("lantern", "lantern"),
    "frosted_ice": ("frosted_ice_0", "frosted_ice_0"),
    "big_dripleaf": ("big_dripleaf_top", "big_dripleaf_side"),
    "decorated_pot": ("terracotta", "terracotta"),
}

_MATERIAL_SUFFIX = (
    "_stairs",
    "_slab",
    "_wall",
    "_fence_gate",
    "_fence",
    "_carpet",
    "_pane",
)


def _short(name: str) -> str:
    return name.split(":", 1)[-1]


def classify(name: str) -> int:
    n = _short(name)
    if n in _WATER:
        return WATER
    if n in _AIR_EXACT or n in _RAILS or n.startswith(_AIR_PREFIX) or n.endswith(_AIR_SUFFIX):
        return AIR
    return SOLID


def tint_of(name: str) -> int:
    n = _short(name)
    if n == "grass_block":
        return T_GRASS
    if n in _WATER:
        return T_WATER
    if n.endswith("_leaves") and n not in _FOLIAGE_FIXED and n not in _FOLIAGE_UNTINTED:
        return T_FOLIAGE
    return T_NONE


class Textures:
    """客户端 jar 里的方块贴图平均色（结果缓存为 JSON，jar 只需要第一次）。"""

    def __init__(self, jar: Path | None, cache: Path) -> None:
        self.cache = cache
        self.avg: dict[str, list[int] | None] = {}
        if cache.exists():
            self.avg = json.loads(cache.read_text())
        self._zip = zipfile.ZipFile(jar) if jar else None
        self.colormaps: dict[str, np.ndarray] = {}
        for cm in ("grass", "foliage"):
            p = cache.with_name(f"colormap_{cm}.png")
            if not p.exists() and self._zip:
                p.write_bytes(self._zip.read(f"assets/minecraft/textures/colormap/{cm}.png"))
            if p.exists():
                self.colormaps[cm] = np.asarray(Image.open(p).convert("RGB"))
        self._dirty = False

    def color(self, tex: str) -> tuple[int, int, int] | None:
        if tex not in self.avg:
            self.avg[tex] = self._compute(tex)
            self._dirty = True
        v = self.avg[tex]
        return (v[0], v[1], v[2]) if v else None

    def _compute(self, tex: str) -> list[int] | None:
        if not self._zip:
            return None
        try:
            raw = self._zip.read(_TEX + tex + ".png")
        except KeyError:
            return None
        im = Image.open(io.BytesIO(raw)).convert("RGBA")
        w, _h = im.size
        a = np.asarray(im.crop((0, 0, w, w)), dtype=np.float64)  # 动画贴图只取第一帧
        alpha = a[..., 3] / 255.0
        if alpha.sum() < 1:
            return None
        rgb = (a[..., :3] * alpha[..., None]).sum((0, 1)) / alpha.sum()
        return [round(c) for c in rgb]

    def save(self) -> None:
        if self._dirty:
            self.cache.write_text(json.dumps(self.avg, sort_keys=True))
            self._dirty = False


def _candidates(n: str) -> tuple[list[str], list[str]]:
    if n in _SPECIAL:
        t, s = _SPECIAL[n]
        return [t], [s]
    for pre in ("waxed_", "infested_"):
        if n.startswith(pre):
            return _candidates(n.removeprefix(pre))
    if n.endswith(("_log", "_stem")) or n in ("basalt", "polished_basalt", "bone_block"):
        return [n + "_top"], [n, n + "_side"]
    if n.endswith(("_wood", "_hyphae")):
        base = n.replace("_wood", "_log").replace("_hyphae", "_stem")
        return [base], [base]
    if n.endswith(("_door",)):
        return [n + "_top"], [n + "_bottom"]
    for suf in _MATERIAL_SUFFIX:
        if n.endswith(suf):
            base = n.removesuffix(suf)
            if suf == "_carpet" and base.endswith(
                (
                    "white",
                    "orange",
                    "magenta",
                    "light_blue",
                    "yellow",
                    "lime",
                    "pink",
                    "gray",
                    "cyan",
                    "purple",
                    "blue",
                    "brown",
                    "green",
                    "red",
                    "black",
                )
            ):
                return [base + "_wool"], [base + "_wool"]
            if suf == "_pane":
                return [base], [base]
            opts = [base, base + "s", base + "_planks", base + "_block", base + "_top"]
            if base.endswith("_brick"):
                opts.insert(0, base + "s")
            if base.endswith("_tile"):
                opts.insert(0, base + "s")
            if base in ("smooth_quartz", "quartz"):
                opts.insert(0, "quartz_block_top")
            if base.endswith("sandstone"):
                opts.insert(0, base + "_top")
            return opts, opts
    top = [n + "_top", n, n + "_side", n + "_front"]
    side = [n + "_side", n, n + "_front", n + "_top"]
    return top, side


def _fallback(n: str) -> tuple[int, int, int]:
    h = zlib.crc32(n.encode())
    return (90 + h % 120, 90 + (h >> 8) % 120, 90 + (h >> 16) % 120)


def block_colors(name: str, tex: Textures) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    n = _short(name)
    if n in _FOLIAGE_FIXED:
        c = _FOLIAGE_FIXED[n]
        return c, c
    tops, sides = _candidates(n)
    t0 = next((col for t in tops if (col := tex.color(t))), None)
    s0 = next((col for t in sides if (col := tex.color(t))), None)
    top = t0 or s0 or _fallback(n)
    side = s0 or top
    return top, side


class BlockTable:
    """把全局方块调色板展开成 numpy 查找表：kind / 顶色 / 侧色 / 着色方式。"""

    def __init__(self, tex: Textures) -> None:
        self.tex = tex
        self.kind = np.zeros(0, dtype=np.uint8)
        self.top = np.zeros((0, 3), dtype=np.float32)
        self.side = np.zeros((0, 3), dtype=np.float32)
        self.tint = np.zeros(0, dtype=np.uint8)

    def sync(self, names: list[str]) -> None:
        n0 = len(self.kind)
        if n0 == len(names):
            return
        kind, top, side, tint = [], [], [], []
        for name in names[n0:]:
            kind.append(classify(name))
            t, s = block_colors(name, self.tex)
            top.append(t)
            side.append(s)
            tint.append(tint_of(name))
        self.kind = np.concatenate([self.kind, np.asarray(kind, dtype=np.uint8)])
        self.top = np.concatenate([self.top, np.asarray(top, dtype=np.float32)])
        self.side = np.concatenate([self.side, np.asarray(side, dtype=np.float32)])
        self.tint = np.concatenate([self.tint, np.asarray(tint, dtype=np.uint8)])
        self.tex.save()
