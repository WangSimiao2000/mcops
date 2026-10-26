"""MC 进程后端：开发用直接子进程，生产用 systemd --user 单元。两者接口一致。

进程存活一律按 PID 轮询判定（而不是 wait 子进程），这样 agent 重启后也能接管
仍在运行的服务端，游戏不会因为 agent 升级而掉线。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import signal
from pathlib import Path
from typing import Protocol

from mcops.config import ServerConfig
from mcops.supervisor import spawn_logged

log = logging.getLogger(__name__)


def pid_alive(pid: int, expect_in_cmdline: str | None = None) -> bool:
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return False
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        if ") Z " in stat:  # 僵尸进程视为已退出
            return False
    except OSError:
        return False
    return expect_in_cmdline is None or expect_in_cmdline.encode() in cmdline


class Backend(Protocol):
    async def start(self) -> None: ...
    async def pid(self) -> int | None: ...
    async def terminate(self) -> None:
        """优雅停止（SIGTERM：MC 会保存世界后退出）。RCON 不可用时的后备。"""
        ...

    async def kill(self) -> None: ...


class SubprocessBackend:
    """本地开发：agent 直接以独立会话拉起 java。PID 记录在 data_dir 以便接管。"""

    def __init__(self, cfg: ServerConfig, pidfile: Path) -> None:
        self._cfg = cfg
        self._pidfile = pidfile
        self._proc: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        cfg = self._cfg
        out = (cfg.game_dir / "logs").resolve()
        out.mkdir(parents=True, exist_ok=True)
        # stdout 同时写进 latest.log，单独留一份 console.out 便于排查启动期崩溃
        console = (out / "console.out").open("ab")
        try:
            self._proc = await asyncio.create_subprocess_exec(
                cfg.java_bin,
                *cfg.java_opts,
                "-jar",
                cfg.jar,
                "nogui",
                cwd=cfg.game_dir,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=console,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        finally:
            console.close()
        self._pidfile.parent.mkdir(parents=True, exist_ok=True)
        self._pidfile.write_text(str(self._proc.pid))
        # 回收子进程，避免成为僵尸；退出由 pid() 轮询感知
        spawn_logged(self._reap(self._proc), "reap")

    @staticmethod
    async def _reap(proc: asyncio.subprocess.Process) -> None:
        with contextlib.suppress(Exception):
            await proc.wait()

    async def pid(self) -> int | None:
        try:
            pid = int(self._pidfile.read_text().strip())
        except (FileNotFoundError, ValueError):
            return None
        if self._proc is not None and self._proc.pid == pid and self._proc.returncode is not None:
            return None
        return pid if pid_alive(pid, self._cfg.jar) else None

    async def terminate(self) -> None:
        pid = await self.pid()
        if pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(pid), signal.SIGTERM)

    async def kill(self) -> None:
        pid = await self.pid()
        if pid is not None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(pid), signal.SIGKILL)


_UNIT_SAFE = re.compile(r"^[A-Za-z0-9_./=:+,@%-]+$")


def render_unit(cfg: ServerConfig) -> str:
    """生成 MC 的 systemd 用户单元。与 M3 切换时生成的单元一致：

    stdin 不能是 /dev/null（某 mod 读到 EOF 会自行 stop），所以接一个永不结束的管道。
    """
    for tok in [cfg.java_bin, cfg.jar, *cfg.java_opts]:
        if not _UNIT_SAFE.match(tok):
            raise ValueError(f"不安全的参数（不能含空格、引号、$ 等）：{tok!r}")
    cmd = " ".join([cfg.java_bin, *cfg.java_opts, "-jar", cfg.jar, "nogui"])
    return (
        "# 由 mc-ops 生成（server.manage_unit=true），手动修改会在下次开服前被覆盖。\n"
        "# 额外设置请放在同名 .d/ 目录的 drop-in 里。\n"
        "[Unit]\nDescription=Minecraft server (managed by mc-ops)\nAfter=network-online.target\n\n"
        "[Service]\nType=simple\n"
        f"WorkingDirectory={cfg.game_dir}\n"
        f'ExecStart=/bin/bash -c "exec {cmd} < <(exec sleep infinity)"\n'
        "SuccessExitStatus=0 143\nTimeoutStopSec=180\nRestart=no\nLimitNOFILE=65536\n"
        # 加固只用不建 namespace 的选项：PrivateTmp / ProtectKernel* 在 --user 单元里会隐式
        # 开 PrivateUsers，agent 就读不到 /proc/<pid>/cwd 了。已用 Java 21 实测 JIT、线程、写盘正常。
        "NoNewPrivileges=yes\nRestrictSUIDSGID=yes\nLockPersonality=yes\nRestrictRealtime=yes\n"
        "RestrictNamespaces=yes\nSystemCallArchitectures=native\nUMask=0077\n\n"
        "[Install]\nWantedBy=default.target\n"
    )


def unit_path(cfg: ServerConfig) -> Path:
    return Path.home() / ".config" / "systemd" / "user" / cfg.systemd_unit


def write_unit(cfg: ServerConfig) -> bool:
    """内容有变化才写（原子替换），返回是否写了。"""
    text = render_unit(cfg)
    p = unit_path(cfg)
    if p.is_file() and p.read_text(encoding="utf-8") == text:
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)
    log.info("已按配置更新 %s", p)
    return True


class SystemdBackend:
    """生产：`systemctl [--user] start|kill mc-server.service`。"""

    def __init__(self, cfg: ServerConfig) -> None:
        self._cfg = cfg

    async def _systemctl(self, *args: str) -> str:
        cmd = ["systemctl", *(["--user"] if self._cfg.systemd_user else []), *args]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"{' '.join(cmd)} 失败：{err.decode().strip()}")
        return out.decode().strip()

    async def start(self) -> None:
        if self._cfg.manage_unit and self._cfg.systemd_user:
            if write_unit(self._cfg):
                await self._systemctl("daemon-reload")
        await self._systemctl("start", self._cfg.systemd_unit)

    async def pid(self) -> int | None:
        try:
            out = await self._systemctl("show", "-p", "MainPID", "--value", self._cfg.systemd_unit)
        except RuntimeError:
            return None
        pid = int(out or "0")
        return pid if pid > 0 and pid_alive(pid) else None

    async def terminate(self) -> None:
        await self._systemctl("stop", "--no-block", self._cfg.systemd_unit)

    async def kill(self) -> None:
        try:
            await self._systemctl("kill", "--signal=SIGKILL", self._cfg.systemd_unit)
        except RuntimeError as e:
            # 主进程已被杀、但 cgroup 里的附属进程发信号失败时，systemctl 也返回非 0
            # （"Failed to send signal SIGKILL to auxiliary processes: Invalid argument"，
            # 2026-10-26 05:06）。以主进程是否还在为准，不在就算结束成功。
            for _ in range(10):
                if await self.pid() is None:
                    log.warning("systemctl kill 报错但主进程已结束，按成功处理：%s", e)
                    return
                await asyncio.sleep(0.5)
            raise


class ObserveBackend:
    """观测模式：服务端仍由旧脚本（tmux）管理，agent 只按 cmdline + cwd 找到进程，绝不启停。"""

    def __init__(self, cfg: ServerConfig) -> None:
        self._cfg = cfg
        self._cached: int | None = None

    def _matches(self, pid: int) -> bool:
        if not pid_alive(pid, self._cfg.jar):
            return False
        try:
            return Path(f"/proc/{pid}/cwd").resolve() == self._cfg.game_dir.resolve()
        except OSError:
            return False

    async def start(self) -> None:
        raise RuntimeError("观测模式不启动服务端")

    async def terminate(self) -> None:
        raise RuntimeError("观测模式不结束服务端")

    async def kill(self) -> None:
        raise RuntimeError("观测模式不结束服务端")

    async def pid(self) -> int | None:
        if self._cached is not None and self._matches(self._cached):
            return self._cached
        self._cached = None
        for d in Path("/proc").iterdir():
            if d.name.isdigit() and self._matches(int(d.name)):
                self._cached = int(d.name)
                break
        return self._cached


def make_backend(cfg: ServerConfig, data_dir: Path, observe: bool = False) -> Backend:
    if observe:
        return ObserveBackend(cfg)
    if cfg.backend == "systemd":
        return SystemdBackend(cfg)
    return SubprocessBackend(cfg, data_dir / "server.pid")
