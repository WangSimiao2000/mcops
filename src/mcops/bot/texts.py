"""QQ 机器人对外文案与指令解析。

全部从旧 mc-qq-bot/bot.py 原样移植：群内回复对所有群成员可见，所以文案经过刻意收口，
不含文件路径、配置键名、服务端指令、主机指标、UUID 等内部细节（见 tests/test_bot_exposure.py）。
"""

from __future__ import annotations

import re

HELP_KEYWORDS = ("帮助", "help", "菜单", "指令", "命令", "?", "？")
PERF_KEYWORDS = ("性能", "perf", "负载", "资源", "cpu", "内存", "配置")
MODS_KEYWORDS = ("mod", "模组", "插件")
JOIN_KEYWORDS = ("怎么加入", "怎么进", "如何加入", "如何进", "加入", "地址", "ip", "join", "连接")
PLAYERS_KEYWORDS = ("在线玩家", "玩家", "在线", "列表", "players", "who", "谁在")
STATUS_KEYWORDS = ("状态", "status", "服务器", "mc")

HELP_BASE = (
    "🤖 {name}\n"
    "━━━━━━━━━━━━\n"
    "@我 状态       —— 在线状态 / 人数 / TPS / 隧道延迟\n"
    "@我 在线玩家   —— 当前在线玩家名单\n"
    "@我 mod        —— 已安装 Mod 列表\n"
    "@我 怎么加入   —— 服务器地址与进服指引\n"
    "@我 帮助       —— 显示本帮助\n"
    "━━━━━━━━━━━━\n"
    "🎫 本服已开启白名单，新人需先获批\n"
    "@我 申请白名单 <游戏ID>  —— 提交申请，等管理员审核\n"
    "@我 我的白名单          —— 查看自己的申请/获批状态\n"
    "⚠️ 游戏ID 区分大小写，须与进服时填的完全一致\n"
    "   仅允许字母 / 数字 / 下划线，长度 3-16"
)

HELP_ADMIN = (
    "\n━━━━━━━━━━━━\n"
    "🔑 管理员指令\n"
    "@我 待审                —— 查看待审申请\n"
    "@我 批准 <游戏ID>       —— 批准并加入白名单\n"
    "@我 拒绝 <游戏ID>       —— 驳回申请\n"
    "@我 白名单 添加 <游戏ID> —— 直接添加\n"
    "@我 白名单 移除 <游戏ID>\n"
    "@我 白名单 列表\n"
    "@我 性能                —— 主机资源占用\n"
    "@我 我的id              —— 查看自己的 openid\n"
    "@我 设为告警群 / 取消告警群 —— 本群接收服务器安全告警\n"
    "@我 告警                —— 查看未处理的安全告警（仅告警群）\n"
    "💡 以上回复同样全群可见，涉敏信息请私下处理"
)

HELP_FOOTER = "\n━━━━━━━━━━━━\n📢 服务器宕机 / 恢复 / 计划维护时，我会自动在群内播报。"

MSG_ADMIN_ONLY = "⛔ 该指令仅管理员可用。"
MSG_INTERNAL_ERR = "⚠️ 操作未完成，请联系管理员查看后台日志。"

# 严格指令匹配必须先于关键词子串匹配：
#   1) 子串匹配会误伤（「白名单 列表」含“列表”会被 PLAYERS_KEYWORDS 抢走）；
#   2) 玩家名区分大小写，必须从原文解析。
MENTION_RE = re.compile(r"<@!?[^>]{1,64}>")

CMD_PATTERNS = (
    ("my_wl", re.compile(r"^(?:我的白名单|我的申请|我的状态)$", re.I)),
    ("wl_list", re.compile(r"^(?:白名单\s*(?:列表|查看)|wl\s+list)$", re.I)),
    ("wl_add", re.compile(r"^(?:白名单\s*添加|添加白名单|wl\s+add)\s+(\S+)$", re.I)),
    ("wl_remove", re.compile(r"^(?:白名单\s*(?:移除|删除)|移除白名单|wl\s+remove)\s+(\S+)$", re.I)),
    ("apply", re.compile(r"^(?:申请白名单|白名单\s*申请|wl\s+apply)\s+(\S+)$", re.I)),
    ("pending", re.compile(r"^(?:待审|待审核|申请列表)$", re.I)),
    ("approve", re.compile(r"^(?:批准|通过|同意)\s+(\S+)$", re.I)),
    ("reject", re.compile(r"^(?:拒绝|驳回)\s+(\S+)$", re.I)),
    ("myid", re.compile(r"^(?:我的id|我的openid|myid|openid)$", re.I)),
    (
        "alert_on",
        re.compile(
            r"^(?:设为|设置|设成|开启)?(?:告警|警告|报警)群$|^开启(?:告警|警告|报警)推送$", re.I
        ),
    ),
    (
        "alert_off",
        re.compile(r"^(?:取消|关闭)(?:告警|警告|报警)群$|^关闭(?:告警|警告|报警)推送$", re.I),
    ),
    ("alerts", re.compile(r"^(?:告警|安全告警|查看告警|alerts)$", re.I)),
)

ADMIN_ONLY = {
    "wl_list",
    "wl_add",
    "wl_remove",
    "pending",
    "approve",
    "reject",
    "alert_on",
    "alert_off",
    "alerts",
}


def parse_strict_command(raw: str) -> tuple[str, str | None] | None:
    """从原始消息文本解析严格指令，返回 (cmd, arg) 或 None。保留大小写。"""
    text = MENTION_RE.sub(" ", raw or "").strip()
    text = re.sub(r"^[/!！]\s*", "", text)
    text = re.sub(r"\s+", " ", text)
    for name, pattern in CMD_PATTERNS:
        m = pattern.match(text)
        if m:
            return name, (m.group(1) if m.groups() else None)
    return None


def duration_text(hours: int) -> str:
    return f"{hours // 24} 天" if hours >= 24 and hours % 24 == 0 else f"{hours} 小时"


def activation_rule(hours: int) -> str:
    """申请/帮助里对注册期限的说明；0 = 不限期，不出现这句。"""
    if hours <= 0:
        return ""
    return (
        f"⏰ 获批后须在 {duration_text(hours)}内进服并完成注册（/register），"
        "否则会自动移出白名单，需要重新申请"
    )


def help_text(is_admin: bool, activation_hours: int = 0, name: str = "Minecraft 服务器 bot") -> str:
    rule = activation_rule(activation_hours)
    base = HELP_BASE.replace("{name}", name, 1).replace(
        "⚠️ 游戏ID 区分大小写", (rule + "\n" if rule else "") + "⚠️ 游戏ID 区分大小写", 1
    )
    return base + (HELP_ADMIN if is_admin else "") + HELP_FOOTER


def flatten(text: str, limit: int = 600) -> str:
    """把多行回复压成单行，便于检索。"""
    s = re.sub(r"\s*\n\s*", " / ", (text or "").strip())
    return s if len(s) <= limit else s[:limit] + f"…(+{len(s) - limit})"


def mask_openid(openid: str | None) -> str:
    """面板与日志外显用的掩码：A1B2C3D4…6D7E8F90 → A1B2…8F90。"""
    if not openid:
        return "-"
    return openid if len(openid) <= 8 else f"{openid[:4]}…{openid[-4:]}"
