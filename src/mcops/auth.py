"""Web 鉴权：单管理员，argon2 密码 + 可选 TOTP，数据库会话，CSRF 双提交，登录限流。

即使面板只监听 127.0.0.1（经 SSH 隧道访问），仍做完整鉴权：本机任何进程、
以及浏览器里的恶意页面（DNS rebinding / CSRF）都不应能直接操作服务器。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from mcops import db

SESSION_COOKIE = "mcops_session"
CSRF_COOKIE = "mcops_csrf"
CSRF_HEADER = "X-CSRF-Token"

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(slots=True)
class Session:
    user: str
    csrf: str
    expires_at: datetime


class LoginThrottle:
    """登录失败后对下一次尝试施加递增延迟，而不是硬性拉黑。

    面板只经 SSH 隧道访问，所有请求来源都是 127.0.0.1，按 IP 限流没有意义，而硬拉黑反而
    会被本机任何进程用来把管理员锁在外面。改为：失败累计越多，verify 前 sleep 越久
    （0→0.5→1→2→4…，上限 cap），成功即清零。既拖慢离线不了的爆破，又不会把人锁死。
    """

    def __init__(self, base_s: float = 0.5, cap_s: float = 8.0, window_s: float = 300.0) -> None:
        self.base_s, self.cap_s, self.window_s = base_s, cap_s, window_s
        self._fails: deque[float] = deque()

    def _prune(self, now: float) -> None:
        while self._fails and now - self._fails[0] > self.window_s:
            self._fails.popleft()

    def delay_s(self) -> float:
        self._prune(time.monotonic())
        n = len(self._fails)
        if n == 0:
            return 0.0
        return float(min(self.base_s * (2 ** (n - 1)), self.cap_s))

    def failure(self) -> None:
        self._prune(time.monotonic())
        self._fails.append(time.monotonic())

    def success(self) -> None:
        self._fails.clear()


class LoginError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code  # invalid_credentials / totp_required / totp_invalid


class Authenticator:
    def __init__(
        self,
        engine: AsyncEngine,
        password_hash: str,
        totp_secret: str | None,
        session_ttl_s: int,
    ) -> None:
        self._engine = engine
        self._hash = password_hash
        self._totp = pyotp.TOTP(totp_secret) if totp_secret else None
        self._ttl = timedelta(seconds=session_ttl_s)
        self.limiter = LoginThrottle()

    @property
    def totp_enabled(self) -> bool:
        return self._totp is not None

    def check_password(self, password: str) -> bool:
        try:
            return bool(_hasher.verify(self._hash, password))
        except (VerificationError, InvalidHashError):
            return False

    def set_password_hash(self, password_hash: str) -> None:
        self._hash = password_hash

    def set_totp(self, secret: str | None) -> None:
        self._totp = pyotp.TOTP(secret) if secret else None

    def verify(self, password: str, totp: str | None) -> None:
        try:
            _hasher.verify(self._hash, password)
        except (VerificationError, InvalidHashError):
            raise LoginError("invalid_credentials") from None
        if self._totp is not None:
            if not totp:
                raise LoginError("totp_required")
            if not self._totp.verify(totp, valid_window=1):
                raise LoginError("totp_invalid")

    async def create_session(self, client: str) -> tuple[str, Session]:
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        sess = Session(user="admin", csrf=secrets.token_urlsafe(24), expires_at=now + self._ttl)
        async with self._engine.begin() as conn:
            await conn.execute(delete(db.auth_sessions).where(db.auth_sessions.c.expires_at < now))
            await conn.execute(
                insert(db.auth_sessions).values(
                    token_hash=_token_hash(token),
                    csrf=sess.csrf,
                    user=sess.user,
                    created_at=now,
                    expires_at=sess.expires_at,
                    client=client[:128],
                )
            )
        return token, sess

    async def get_session(self, token: str | None) -> Session | None:
        if not token:
            return None
        async with self._engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        select(db.auth_sessions).where(
                            db.auth_sessions.c.token_hash == _token_hash(token)
                        )
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        exp: datetime = row["expires_at"]
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        if exp < datetime.now(UTC):
            return None
        return Session(user=row["user"], csrf=row["csrf"], expires_at=exp)

    async def delete_session(self, token: str | None) -> None:
        if not token:
            return
        async with self._engine.begin() as conn:
            await conn.execute(
                delete(db.auth_sessions).where(db.auth_sessions.c.token_hash == _token_hash(token))
            )

    async def revoke_all_sessions(self) -> int:
        """吊销全部会话（改密码时调用）：已签发的 cookie 立即失效。"""
        async with self._engine.begin() as conn:
            res = await conn.execute(delete(db.auth_sessions))
        return int(res.rowcount or 0)


def csrf_ok(session: Session, header_value: str | None) -> bool:
    return header_value is not None and hmac.compare_digest(session.csrf, header_value)
