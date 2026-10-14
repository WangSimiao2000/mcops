"""白名单：离线 UUID 计算与 whitelist.json 原子读写（移植自 mc-qq-bot/whitelist_manager.py）。

## 为什么不调用服务端原生的 `whitelist add`

离线模式下，对**从未进过服**的玩家名，服务端会退化为用**小写**名生成离线 profile，
而离线 UUID 由名字**区分大小写**推导：

    uuid3(md5("OfflinePlayer:" + name))
    ZzNeverSeen42 -> 32869268-9827-3c46-a566-114043a91d59
    zzneverseen42 -> 402cc456-bedc-3ec6-8e41-72a65ed40203

白名单按 UUID 校验，于是 `whitelist add ZzNeverSeen42` 表面成功、实际无效，还会污染
usercache.json（1.21.11 实测）。因此这里自行按原始大小写计算 UUID 直接写文件，
再让服务端 `whitelist reload` 并回读确认。
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# 与旧 mc.sh validate_player、EasyAuth username-regexp 一致
NAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")


def validate_name(name: str | None) -> bool:
    return bool(name) and bool(NAME_RE.match(name or ""))


def offline_uuid(name: str) -> str:
    digest = hashlib.md5(("OfflinePlayer:" + name).encode("utf-8")).digest()  # noqa: S324 - 协议规定
    return str(uuid.UUID(bytes=digest, version=3))


class WhitelistError(Exception):
    """预期内失败。message 会原样回复到群里（全群可见），不得含内部细节；细节放 detail。"""

    def __init__(self, message: str, detail: str | None = None) -> None:
        super().__init__(message)
        self.detail = detail


def _atomic_write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class WhitelistFile:
    """whitelist.json 读写。写操作持有同目录 .lock 的排他锁，避免多个写入方竞争
    （旧系统里机器人、备份脚本、原生命令之间没有统一锁）。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        lock = self.path.with_name(".whitelist.mcops.lock")
        with open(lock, "a") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8") or "[]")
        except json.JSONDecodeError as exc:
            raise WhitelistError(
                "白名单文件异常，请联系管理员处理。", f"JSONDecodeError: {exc}"
            ) from exc
        if not isinstance(data, list):
            raise WhitelistError("白名单文件异常，请联系管理员处理。", "顶层不是数组")
        return data

    def names(self) -> list[str]:
        return [e.get("name", "") for e in self.load() if e.get("name")]

    def add(self, name: str) -> str:
        if not validate_name(name):
            raise WhitelistError(f"玩家名 `{name}` 不合法：只允许字母、数字、下划线，长度 3-16")
        with self._locked():
            entries = self.load()
            for e in entries:
                existing = e.get("name", "")
                if existing == name:
                    raise WhitelistError(f"`{name}` 已在白名单中")
                if existing.lower() == name.lower():
                    raise WhitelistError(
                        f"白名单中已存在大小写不同的条目 `{existing}`。\n"
                        f"离线模式下两者 UUID 不同，请先移除 `{existing}` 再添加 `{name}`，"
                        f"否则会产生无效条目。"
                    )
            uid = offline_uuid(name)
            entries.append({"uuid": uid, "name": name})
            _atomic_write_json(self.path, entries)
            return uid

    def remove(self, name: str) -> str:
        if not validate_name(name):
            raise WhitelistError(f"玩家名 `{name}` 不合法")
        with self._locked():
            entries = self.load()
            hit = next((e for e in entries if e.get("name", "").lower() == name.lower()), None)
            if hit is None:
                raise WhitelistError(f"`{name}` 不在白名单中")
            _atomic_write_json(self.path, [e for e in entries if e is not hit])
            return str(hit.get("name", name))


_LIST_RE = re.compile(r"whitelisted player\(s\):\s*(?P<names>.+?)\s*$")
_EMPTY_RE = re.compile(r"(?:no whitelisted players|There are 0 whitelisted)")


def parse_whitelist_list(text: str) -> set[str] | None:
    """解析服务端 `whitelist list` 输出（RCON 返回或日志片段）；无法识别返回 None。"""
    found: set[str] | None = None
    for line in text.splitlines():
        if _EMPTY_RE.search(line):
            found = set()
            continue
        m = _LIST_RE.search(line)
        if m:
            found = {n.strip() for n in m.group("names").split(",") if n.strip()}
    return found
