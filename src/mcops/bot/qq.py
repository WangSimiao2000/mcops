"""botpy 适配层：把 QQ 开放平台事件转给 BotCore，并提供主动推送。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import botpy
from botpy.manage import GroupManageEvent
from botpy.message import GroupMessage

from mcops.bot.core import BotCore, Message
from mcops.bot.texts import mask_openid

log = logging.getLogger(__name__)


class QQClient(botpy.Client):  # type: ignore[misc]
    def __init__(self, core: BotCore) -> None:
        super().__init__(intents=botpy.Intents(public_messages=True))
        self.core = core
        self.connected = False
        self.last_error: str | None = None
        self._seq: dict[str, int] = {}
        core.pusher = self.push

    def _next_seq(self, openid: str) -> int:
        self._seq[openid] = self._seq.get(openid, 0) + 1
        return self._seq[openid]

    async def push(self, content: str, groups: set[str]) -> int:
        sent = 0
        for openid in groups:
            try:
                await self.api.post_group_message(
                    group_openid=openid, msg_type=0, content=content, msg_seq=self._next_seq(openid)
                )
                sent += 1
            except Exception as exc:
                code = getattr(exc, "code", None) or ""
                self.last_error = f"推送失败：{exc} {code}".strip()
                log.error("向群 %s 推送失败：%s", mask_openid(openid), exc)
        return sent

    async def on_ready(self) -> None:
        self.connected, self.last_error = True, None
        log.info("QQ 机器人已上线：%s", getattr(self.robot, "name", "?"))

    async def on_group_add_robot(self, event: GroupManageEvent) -> None:
        await self.core.add_group(event.group_openid)

    async def on_group_del_robot(self, event: GroupManageEvent) -> None:
        await self.core.remove_group(event.group_openid)

    async def on_group_at_message_create(self, message: GroupMessage) -> None:
        openid = getattr(getattr(message, "author", None), "member_openid", None)
        msg = Message(
            content=message.content or "",
            group_openid=message.group_openid,
            actor_openid=(str(openid).strip() or None) if openid else None,
        )
        try:
            _cmd, reply = await self.core.handle_message(msg)
        except Exception:
            log.exception("处理群消息失败")
            reply = "⚠️ 操作未完成，请联系管理员查看后台日志。"
        await message.reply(content=reply)


async def run_qq(client: QQClient, appid: str, secret: str) -> None:
    """由 Supervisor 托管；断线由 botpy 自身重连，彻底失败时抛出由 Supervisor 退避重启。"""
    try:
        await client.start(appid=appid, secret=secret)
    except asyncio.CancelledError:
        await client.close()
        raise
    except Exception as e:
        client.connected, client.last_error = False, f"{type(e).__name__}: {e}"
        raise
    finally:
        client.connected = False


def status_json(
    client: QQClient | None, core: BotCore, groups: list[dict[str, Any]], enabled: bool
) -> dict[str, Any]:
    alert = core.s.alert_groups
    return {
        "enabled": enabled,
        "connected": bool(client and client.connected),
        "robot_name": getattr(getattr(client, "robot", None), "name", None) if client else None,
        "last_error": client.last_error if client else None,
        "groups": [
            {
                "openid_masked": mask_openid(g["openid"]),
                "added_at": g["added_at"].isoformat() if g.get("added_at") else None,
                "alert_group": g["openid"] in alert,
            }
            for g in groups
        ],
        **core.status(),
    }
