"""悬空 / 飞行检测（M4）：服务端开 allow-flight=true 后原版不再踢人，由 agent 只做告警。

每隔 interval_s 通过 RCON 对每个在线玩家跑一条 execute：
生存/冒险模式、不在地面、没开鞘翅、不在水/梯子/藤蔓/气泡柱里、没骑乘、没有漂浮/缓降效果时，
返回其 Y 坐标；否则无输出。连续 samples 次都"悬空且没有在下落"才告警（原版 4 秒就踢，这里默认 15 秒）。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, tzinfo
from typing import Any

log = logging.getLogger(__name__)

Command = Callable[[str], Awaitable[str]]
_NAME = re.compile(r"^[A-Za-z0-9_]{1,16}$")


def probe_cmd(name: str) -> str:
    # 不信任 NBT OnGround：它来自客户端上报，飞行挂通常伪造成 true（实测假人悬停时也是 1b）。
    # 改为服务端自己看脚下：碰撞箱四角在脚下 0.2 和 0.6 格处全是空气才算悬空
    # （两层是为了站在栅栏顶 / 悬空台阶上不误报）。
    under = "".join(
        f" if block ~{dx} ~{dy} ~{dz} #minecraft:air"
        for dy in ("-0.2", "-0.6")
        for dx in ("-0.3", "0.3")
        for dz in ("-0.3", "0.3")
    )
    return (
        f"execute as {name} at @s"
        " if entity @s[gamemode=!creative,gamemode=!spectator,nbt={FallFlying:0b}]"
        " unless entity @s[nbt={RootVehicle:{}}]"
        ' unless entity @s[nbt={active_effects:[{id:"minecraft:levitation"}]}]'
        ' unless entity @s[nbt={active_effects:[{id:"minecraft:slow_falling"}]}]'
        " unless block ~ ~ ~ #minecraft:climbable"
        " unless block ~ ~ ~ minecraft:water unless block ~ ~-1 ~ minecraft:water"
        " unless block ~ ~ ~ minecraft:bubble_column unless block ~ ~ ~ minecraft:cobweb"
        " unless block ~ ~ ~ minecraft:powder_snow" + under + " run data get entity @s Pos"
    )


_FALL_FLYING = re.compile(r"has the following entity data: ([01])b")


async def is_fall_flying(command: Command, name: str, timeout_s: float = 3.0) -> bool | None:
    """玩家此刻是否在用鞘翅滑翔。None = 无法判断（离线 / RCON 不可用 / 名字非法）。

    FallFlying 由服务端维护（开始滑翔要服务端确认，落地 / 入水由服务端清除），不是客户端随意上报的。
    """
    if not _NAME.match(name):
        return None
    try:
        out = await asyncio.wait_for(command(f"data get entity {name} FallFlying"), timeout_s)
    except Exception:  # RCON 断线 / 超时都按"无法判断"处理
        return None
    m = _FALL_FLYING.search(out)
    return None if m is None else m.group(1) == "1"


def parse_online(list_output: str) -> list[str]:
    """`list` → 玩家名。例：There are 2 of a max of 25 players online: a, b"""
    _, _, tail = list_output.partition(":")
    return [n.strip() for n in tail.split(",") if _NAME.match(n.strip())]


@dataclass
class _Track:
    count: int = 0
    last_y: float | None = None
    start: datetime | None = None
    alerted: bool = False


@dataclass
class FlightWatch:
    command: Command
    raise_alert: Callable[..., Awaitable[Any]]
    tz: tzinfo
    is_running: Callable[[], bool]
    interval_s: float = 3.0
    samples: int = 5
    night_hours: tuple[int, int] = (1, 6)
    fall_tolerance: float = 0.5
    """一个采样间隔内 Y 下降超过这个值视为正常下落，计数清零。"""
    _tracks: dict[str, _Track] = field(default_factory=dict)

    async def loop(self) -> None:
        while True:
            await asyncio.sleep(self.interval_s)
            if not self.is_running():
                self._tracks.clear()
                continue
            with contextlib.suppress(Exception):  # RCON 暂时不可用就等下一轮
                await self.tick()

    async def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        names = parse_online(await self.command("list"))
        for gone in set(self._tracks) - set(names):
            del self._tracks[gone]
        for name in names:
            out = await self.command(probe_cmd(name))
            await self._sample(name, out, now)

    async def _sample(self, name: str, out: str, now: datetime) -> None:
        t = self._tracks.setdefault(name, _Track())
        m = re.search(
            r"has the following entity data: \[(-?[\d.]+)d, (-?[\d.]+)d, (-?[\d.]+)d\]", out
        )
        if not m:
            self._tracks[name] = _Track()
            return
        x, y, z = (float(v) for v in m.groups())
        if t.last_y is not None and t.last_y - y > self.fall_tolerance:
            t.count, t.start = 0, None  # 正在下落：正常
        else:
            t.count += 1
            t.start = t.start or now
        t.last_y = y
        if t.count >= self.samples and not t.alerted:
            t.alerted = True
            local = now.astimezone(self.tz)
            night = self.night_hours[0] <= local.hour < self.night_hours[1]
            secs = round(self.interval_s * t.count)
            await self.raise_alert(
                "flight_suspect",
                now,
                f"{name} 疑似飞行：悬空约 {secs} 秒",
                f"位置 ({x:.0f}, {y:.0f}, {z:.0f})，生存模式、未用鞘翅、不在水/梯子中且没有下落。"
                "服务端已允许飞行（不踢人），请上线或看回放核实。"
                + ("凌晨发生，已升级。" if night else ""),
                name,
                {
                    "name": name,
                    "x": round(x, 1),
                    "y": round(y, 1),
                    "z": round(z, 1),
                    "seconds": secs,
                },
                severity="critical" if night else None,
            )
        if t.count == 0:
            t.alerted = False
