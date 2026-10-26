"""群系着色：草 / 树叶查原版色图（温度、降水），水用群系水色，特殊群系用固定色。"""

from __future__ import annotations

import numpy as np

# 群系 -> (温度, 降水)，取原版数值
CLIMATE: dict[str, tuple[float, float]] = {
    "plains": (0.8, 0.4),
    "sunflower_plains": (0.8, 0.4),
    "snowy_plains": (0.0, 0.5),
    "ice_spikes": (0.0, 0.5),
    "desert": (2.0, 0.0),
    "swamp": (0.8, 0.9),
    "mangrove_swamp": (0.8, 0.9),
    "forest": (0.7, 0.8),
    "flower_forest": (0.7, 0.8),
    "birch_forest": (0.6, 0.6),
    "old_growth_birch_forest": (0.6, 0.6),
    "dark_forest": (0.7, 0.8),
    "pale_garden": (0.7, 0.8),
    "old_growth_pine_taiga": (0.3, 0.8),
    "old_growth_spruce_taiga": (0.25, 0.8),
    "taiga": (0.25, 0.8),
    "snowy_taiga": (-0.5, 0.4),
    "savanna": (2.0, 0.0),
    "savanna_plateau": (2.0, 0.0),
    "windswept_savanna": (2.0, 0.0),
    "windswept_hills": (0.2, 0.3),
    "windswept_gravelly_hills": (0.2, 0.3),
    "windswept_forest": (0.2, 0.3),
    "jungle": (0.95, 0.9),
    "sparse_jungle": (0.95, 0.8),
    "bamboo_jungle": (0.95, 0.9),
    "badlands": (2.0, 0.0),
    "eroded_badlands": (2.0, 0.0),
    "wooded_badlands": (2.0, 0.0),
    "meadow": (0.5, 0.8),
    "cherry_grove": (0.5, 0.8),
    "grove": (-0.2, 0.8),
    "snowy_slopes": (-0.3, 0.9),
    "frozen_peaks": (-0.7, 0.9),
    "jagged_peaks": (-0.7, 0.9),
    "stony_peaks": (1.0, 0.3),
    "river": (0.5, 0.5),
    "frozen_river": (0.0, 0.5),
    "beach": (0.8, 0.4),
    "snowy_beach": (0.05, 0.3),
    "stony_shore": (0.2, 0.3),
    "warm_ocean": (0.5, 0.5),
    "lukewarm_ocean": (0.5, 0.5),
    "deep_lukewarm_ocean": (0.5, 0.5),
    "ocean": (0.5, 0.5),
    "deep_ocean": (0.5, 0.5),
    "cold_ocean": (0.5, 0.5),
    "deep_cold_ocean": (0.5, 0.5),
    "frozen_ocean": (0.0, 0.5),
    "deep_frozen_ocean": (0.5, 0.5),
    "mushroom_fields": (0.9, 1.0),
    "dripstone_caves": (0.8, 0.4),
    "lush_caves": (0.5, 0.5),
    "deep_dark": (0.8, 0.4),
}

_GRASS_FIXED = {
    "swamp": 0x6A7039,
    "mangrove_swamp": 0x6A7039,
    "badlands": 0x90814D,
    "eroded_badlands": 0x90814D,
    "wooded_badlands": 0x90814D,
    "cherry_grove": 0xB6DB61,
    "pale_garden": 0x778272,
}
_FOLIAGE_FIXED = {
    "swamp": 0x6A7039,
    "mangrove_swamp": 0x8DB127,
    "badlands": 0x9E814D,
    "eroded_badlands": 0x9E814D,
    "wooded_badlands": 0x9E814D,
    "cherry_grove": 0xB6DB61,
    "pale_garden": 0x878D76,
}
_WATER = {
    "warm_ocean": 0x43D5EE,
    "lukewarm_ocean": 0x45ADF2,
    "deep_lukewarm_ocean": 0x45ADF2,
    "cold_ocean": 0x3D57D6,
    "deep_cold_ocean": 0x3D57D6,
    "frozen_ocean": 0x3938C9,
    "deep_frozen_ocean": 0x3938C9,
    "frozen_river": 0x3938C9,
    "swamp": 0x617B64,
    "mangrove_swamp": 0x3A7A6A,
    "cherry_grove": 0x5DB7EF,
    "meadow": 0x0E4ECF,
    "pale_garden": 0x76889D,
}
_WATER_DEFAULT = 0x3F76E4


def _rgb(v: int) -> tuple[int, int, int]:
    return (v >> 16) & 255, (v >> 8) & 255, v & 255


def _lookup(cmap: np.ndarray | None, temp: float, rain: float) -> tuple[int, int, int]:
    if cmap is None:
        return (0x79, 0xC0, 0x5A)
    t = min(max(temp, 0.0), 1.0)
    r = min(max(rain, 0.0), 1.0) * t
    x = int((1.0 - t) * 255)
    y = int((1.0 - r) * 255)
    c = cmap[y, x]
    return int(c[0]), int(c[1]), int(c[2])


class BiomeTable:
    """全局群系 id -> 草色 / 叶色 / 水色（float32 [n, 3]）。"""

    def __init__(self, colormaps: dict[str, np.ndarray]) -> None:
        self.cm = colormaps
        self.grass = np.zeros((0, 3), dtype=np.float32)
        self.foliage = np.zeros((0, 3), dtype=np.float32)
        self.water = np.zeros((0, 3), dtype=np.float32)

    def sync(self, names: list[str]) -> None:
        n0 = len(self.grass)
        if n0 == len(names):
            return
        g, f, w = [], [], []
        for full in names[n0:]:
            n = full.split(":", 1)[-1]
            temp, rain = CLIMATE.get(n, (0.8, 0.4))
            gc = _GRASS_FIXED.get(n)
            g.append(_rgb(gc) if gc else _lookup(self.cm.get("grass"), temp, rain))
            fc = _FOLIAGE_FIXED.get(n)
            f.append(_rgb(fc) if fc else _lookup(self.cm.get("foliage"), temp, rain))
            w.append(_rgb(_WATER.get(n, _WATER_DEFAULT)))
            if n == "dark_forest":  # 原版：草色再和 0x28340A 混一半
                r, gg, b = g[-1]
                g[-1] = ((r + 0x28) // 2, (gg + 0x34) // 2, (b + 0x0A) // 2)
        self.grass = np.concatenate([self.grass, np.asarray(g, dtype=np.float32)])
        self.foliage = np.concatenate([self.foliage, np.asarray(f, dtype=np.float32)])
        self.water = np.concatenate([self.water, np.asarray(w, dtype=np.float32)])
