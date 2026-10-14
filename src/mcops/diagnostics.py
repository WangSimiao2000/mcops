"""P4：自检诊断。每项给出状态与修复建议，面板「设置 → 系统」展示。只读，不改任何东西。"""

from __future__ import annotations

import asyncio
import getpass
import os
import shutil
import socket
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from mcops import gameprops

Status = Literal["ok", "warn", "fail", "info"]


@dataclass
class Check:
    id: str
    group: str
    title: str
    status: Status
    detail: str
    fix: str = ""


def _mem_available() -> int | None:
    try:
        for ln in Path("/proc/meminfo").read_text().splitlines():
            if ln.startswith("MemAvailable:"):
                return int(ln.split()[1]) * 1024
    except OSError:
        pass
    return None


def _xmx_bytes(opts: list[str]) -> int | None:
    for o in opts:
        if o.startswith("-Xmx"):
            v = o[4:].upper()
            mult = {"G": 2**30, "M": 2**20, "K": 2**10}.get(v[-1:], 1)
            try:
                return int(v.rstrip("GMK")) * mult
            except ValueError:
                return None
    return None


async def _run(*argv: str, timeout: float = 10) -> tuple[int, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except (OSError, TimeoutError) as e:
        return -1, str(e)
    return proc.returncode or 0, out.decode("utf-8", "replace").strip()


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return True
    except OSError:
        return False


def _age(ts: float) -> str:
    h = (datetime.now(UTC).timestamp() - ts) / 3600
    return f"{h:.0f} 小时前" if h < 48 else f"{h / 24:.0f} 天前"


async def run_checks(s: Any) -> list[dict[str, Any]]:
    from mcops import gameversions as gv
    from mcops.backup import list_backups

    cfg = s.config.running if s.config is not None else None
    srv = s.server
    sc = srv.cfg
    game = sc.game_dir
    out: list[Check] = []

    def add(*a: Any, **k: Any) -> None:
        out.append(Check(*a, **k))

    # ---- 服务端 ----
    jar = gv.current_jar(game)
    if jar is None:
        add("jar", "服务端", "服务端 jar", "fail", f"{game} 里没有 Fabric 服务端",
            "到「Mod 与升级 → 游戏版本」或总览页的安装向导安装")  # fmt: skip
    else:
        add("jar", "服务端", "服务端 jar", "ok", jar)
    major = await gv.java_major(sc.java_bin)
    if major is None:
        add("java", "服务端", "Java", "fail", f"找不到可用的 Java：{sc.java_bin}",
            "在「游戏版本」里检查兼容性时会自动安装所需的 Java，或在服务器配置里改 Java 路径")  # fmt: skip
    else:
        add("java", "服务端", "Java", "ok", f"Java {major}（{sc.java_bin}）")
    eula = game / "eula.txt"
    if jar and not (
        eula.is_file() and "eula=true" in eula.read_text(encoding="utf-8", errors="replace")
    ):
        add(
            "eula",
            "服务端",
            "EULA",
            "fail",
            "eula.txt 不存在或未同意",
            "需要同意 Minecraft EULA 才能开服（安装向导里勾选）",
        )
    props = game / "server.properties"
    if props.is_file():
        p = gameprops.parse(props.read_text(encoding="utf-8", errors="replace"))
        if p.get("enable-rcon") != "true":
            add(
                "rcon_cfg",
                "服务端",
                "RCON",
                "fail",
                "server.properties 没有开启 RCON",
                "mc-ops 依赖 RCON 发命令；设 enable-rcon=true 后重启",
            )
        elif p.get("rcon.port") and int(p["rcon.port"]) != sc.rcon_port:
            add("rcon_cfg", "服务端", "RCON", "fail", f"端口不一致：server.properties 是 {p['rcon.port']}，mc-ops 配置是 {sc.rcon_port}",
                "在「服务器配置」把 RCON 端口改成一致")  # fmt: skip
        elif srv.state.value == "running":
            try:
                await srv.rcon.command("list")
                add("rcon", "服务端", "RCON", "ok", f"127.0.0.1:{sc.rcon_port} 可用")
            except Exception as e:
                add(
                    "rcon",
                    "服务端",
                    "RCON",
                    "fail",
                    f"连接失败：{e}",
                    "检查 secrets.yaml 的 rcon_password 是否与 server.properties 一致",
                )
        if p.get("online-mode") == "false":
            wl = p.get("white-list") == "true"
            auth = bool(s.caps and s.caps.mods.get("easyauth"))
            if not wl or not auth:
                miss = "、".join(x for x, ok in (("白名单", wl), ("EasyAuth 登录", auth)) if not ok)
                add("offline", "安全", "离线模式防护", "warn", f"离线模式下缺少：{miss}，任何人都能冒用别人的名字进服",
                    "开启白名单并安装 EasyAuth（「Mod 与升级 → 安装 Mod」搜 easyauth）")  # fmt: skip
            else:
                add("offline", "安全", "离线模式防护", "ok", "白名单 + EasyAuth")
        if p.get("log-ips") == "false":
            add(
                "logips",
                "安全",
                "记录玩家 IP",
                "warn",
                "log-ips=false，安全规则拿不到玩家 IP",
                "在「游戏规则」里打开",
            )
    st = srv.status_snapshot()
    add(
        "state",
        "服务端",
        "运行状态",
        "ok" if st["state"] == "running" else "info",
        f"{st['state']}",
    )
    if st["state"] == "running":
        reach = await asyncio.to_thread(_port_open, "127.0.0.1", sc.port)
        add(
            "port",
            "服务端",
            "游戏端口",
            "ok" if reach else "fail",
            f"127.0.0.1:{sc.port} {'可连接' if reach else '连不上'}",
        )

    # ---- 主机 ----
    mem = _mem_available()
    xmx = _xmx_bytes(sc.java_opts)
    if mem is not None:
        if xmx and st["state"] != "running" and mem < xmx * 1.1:
            add(
                "mem",
                "主机",
                "内存",
                "warn",
                f"可用 {mem / 2**30:.1f} GB，小于服务端最大内存 {xmx / 2**30:.0f} GB",
                "调低 JVM 内存或关掉其它程序",
            )
        else:
            add(
                "mem",
                "主机",
                "内存",
                "ok",
                f"可用 {mem / 2**30:.1f} GB"
                + (f"，服务端上限 {xmx / 2**30:.0f} GB" if xmx else ""),
            )
    du = shutil.disk_usage(game if game.exists() else Path.home())
    free_gb = du.free / 2**30
    add("disk", "主机", "磁盘", "ok" if free_gb > 20 else ("warn" if free_gb > 5 else "fail"),
        f"剩余 {free_gb:.0f} GB / 共 {du.total / 2**30:.0f} GB", "" if free_gb > 20 else "清理旧备份或扩容")  # fmt: skip
    if sc.backend == "systemd":
        code, linger = await _run(
            "loginctl", "show-user", getpass.getuser(), "-p", "Linger", "--value"
        )
        if code == 0 and linger.strip() == "yes":
            add("linger", "主机", "开机自启（linger）", "ok", "已开启：未登录时服务也会运行")
        else:
            add("linger", "主机", "开机自启（linger）", "fail", "未开启：退出 SSH 或重启主机后服务会停",
                f"执行一次：sudo loginctl enable-linger {getpass.getuser()}")  # fmt: skip
        add("unit", "主机", "systemd 单元", "ok" if sc.manage_unit else "warn",
            "由 mc-ops 生成" if sc.manage_unit else "手工维护（升级游戏版本前需要改为由 mc-ops 生成）",
            "" if sc.manage_unit else "设置 → 服务器配置 → 服务端 → 高级 → 由 mc-ops 生成 systemd 单元")  # fmt: skip

    # ---- 备份 ----
    if cfg is not None:
        bks = (
            list_backups(cfg.ops.backup_dir)
            if cfg.ops.backup_dir and cfg.ops.backup_dir.is_dir()
            else []
        )
        if not bks:
            add(
                "backup",
                "备份",
                "冷备份",
                "warn" if jar else "info",
                "还没有冷备份",
                "到「备份」页点立即备份，或等每日定时重启",
            )
        else:
            age_h = (datetime.now(UTC).timestamp() - bks[0].stat().st_mtime) / 3600
            add("backup", "备份", "冷备份", "ok" if age_h < 50 else "warn", f"最近一次 {_age(bks[0].stat().st_mtime)}：{bks[0].name}",
                "" if age_h < 50 else "检查每日定时重启是否在跑")  # fmt: skip
        if not cfg.ops.restic.enabled:
            add(
                "restic",
                "备份",
                "增量快照",
                "info",
                "未开启",
                "在「服务器配置 → 运维任务 → 增量快照」开启",
            )
        else:
            last = await s.ops._last_job_start("restic_backup") if s.ops is not None else None
            if last is None:
                add("restic", "备份", "增量快照", "warn", "已开启但还没有成功的快照")
            else:
                h = (datetime.now(UTC) - last).total_seconds() / 3600
                lim = cfg.ops.restic.interval_minutes / 60 * 2 + 1
                add(
                    "restic",
                    "备份",
                    "增量快照",
                    "ok" if h < lim else "warn",
                    f"最近一次 {h:.0f} 小时前",
                )
            off = cfg.ops.restic.offsite
            if not (off.s3_repo or off.smb_host):
                add(
                    "offsite",
                    "备份",
                    "异地副本",
                    "warn",
                    "未配置：快照和世界在同一块盘上，硬盘损坏会一起丢",
                    "在「服务器配置 → 运维任务 → 增量快照 → 异地副本」填对象存储或 NAS",
                )
            else:
                last = await s.ops._last_job_start("offsite_copy") if s.ops is not None else None
                if last is None:
                    add("offsite", "备份", "异地副本", "warn", "已配置但还没有成功复制过",
                        "在「运维任务」页手动运行一次「异地备份」")  # fmt: skip
                else:
                    h = (datetime.now(UTC) - last).total_seconds() / 3600
                    lim = cfg.ops.restic.interval_minutes / 60 * 2 + 1
                    add("offsite", "备份", "异地副本", "ok" if h < lim else "warn",
                        f"最近一次成功 {h:.0f} 小时前")  # fmt: skip

    # ---- 面板与安全 ----
    pw_file = cfg.secrets_file.parent / "admin-password.txt" if cfg is not None else None
    if pw_file is not None and pw_file.is_file():
        initial = pw_file.read_text(encoding="utf-8").strip()
        if initial and s.auth.check_password(initial):
            add(
                "pw",
                "安全",
                "面板密码",
                "fail",
                "仍在使用安装时生成的初始密码",
                "设置 → 账号与安全 → 修改密码",
            )
        else:
            add(
                "pw",
                "安全",
                "面板密码",
                "warn",
                f"已改密码，但初始密码文件还在：{pw_file}",
                f"可以删除：rm {pw_file}",
            )
    else:
        add("pw", "安全", "面板密码", "ok", "已更换初始密码")
    add("totp", "安全", "两步验证", "ok" if s.auth.totp_enabled else "warn",
        "已开启" if s.auth.totp_enabled else "未开启", "" if s.auth.totp_enabled else "设置 → 账号与安全 → 开启两步验证")  # fmt: skip
    if cfg is not None and cfg.secrets_file.exists():
        mode = cfg.secrets_file.stat().st_mode & 0o777
        add("secrets", "安全", "机密文件权限", "ok" if mode == 0o600 else "fail", f"{oct(mode)}（{cfg.secrets_file}）",
            "" if mode == 0o600 else f"chmod 600 {cfg.secrets_file}")  # fmt: skip

    # ---- 组件 ----
    failed = [k for k, m in s.supervisor.modules.items() if m.state == "failed"]
    add("modules", "组件", "后台模块", "fail" if failed else "ok",
        f"异常：{'、'.join(failed)}" if failed else f"{len(s.supervisor.modules)} 个模块正常", "到「日志」页查看 agent 日志" if failed else "")  # fmt: skip
    if s.edge is not None:
        es = s.edge.status()
        add("edge", "组件", "云主机日志采集", "ok" if es.get("connected") else "warn",
            "已连接" if es.get("connected") else f"未连接：{es.get('error') or ''}")  # fmt: skip
    if s.bot is not None and s.bot.enabled:
        c = s.bot.client
        ok = bool(c is not None and c.connected)
        add(
            "bot",
            "组件",
            "QQ 机器人",
            "ok" if ok else "warn",
            "已连接" if ok else f"未连接：{getattr(c, 'last_error', '') or ''}",
        )
    db_path = cfg.db_path if cfg is not None else None
    if db_path is not None and db_path.exists():
        size = db_path.stat().st_size + (db_path.with_name(db_path.name + "-wal").stat().st_size
                                         if db_path.with_name(db_path.name + "-wal").exists() else 0)  # fmt: skip
        add("db", "组件", "数据库", "ok" if size < 2**30 else "warn", f"{size / 2**20:.1f} MB")
    rev = app_revision()
    add("version", "组件", "mc-ops 版本", "info", rev or "未知（开发模式）")
    return [asdict(c) for c in out]


def app_dir() -> Path:
    """agent 安装目录（venv 的上一级），可用 MCOPS_APP_DIR 覆盖。"""
    import sys

    env = os.environ.get("MCOPS_APP_DIR")
    return Path(env) if env else Path(sys.prefix).resolve().parent


def app_revision() -> str | None:
    p = app_dir() / "REVISION"
    try:
        return p.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None
