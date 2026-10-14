"""API：面板配置 / 能力探测 / server.properties / 账号与机密（P1，docs/03-config-api.md）。"""

# 注意：本模块特意不启用 `from __future__ import annotations`（同 api_mods.py 的原因）。

import asyncio
import logging
import os
import shutil
from typing import Annotated, Any

import pyotp
from fastapi import FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)


class ConfigBody(BaseModel):
    values: dict[str, Any]
    note: str = Field(default="", max_length=200)


class ResetBody(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=200)


class PropsBody(BaseModel):
    changes: dict[str, Any]


class PasswordBody(BaseModel):
    old_password: str = Field(max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class TotpConfirmBody(BaseModel):
    code: str = Field(min_length=6, max_length=8)


class TotpDisableBody(BaseModel):
    password: str = Field(max_length=256)


SECRET_ITEMS: list[dict[str, Any]] = [
    {"key": "qq_appid", "title": "QQ 机器人 AppID", "editable": True, "apply": "agent"},
    {"key": "qq_secret", "title": "QQ 机器人 AppSecret", "editable": True, "apply": "agent"},
    {"key": "rcon_password", "title": "RCON 密码", "editable": False,
     "note": "由 mc-ops 生成，并同时写在 server.properties 里；面板暂不支持轮换"},
    {"key": "restic_password", "title": "restic 仓库密码", "editable": False,
     "note": "改密要用 restic key passwd 重新加密仓库，面板不支持；丢失后仓库无法解开，请另外保存"},
    {"key": "offsite_s3_access_key", "title": "异地副本：对象存储 SecretId / AccessKey", "editable": True,
     "apply": "agent", "note": "建议用只授权这一个存储桶读写的子账号密钥"},
    {"key": "offsite_s3_secret_key", "title": "异地副本：对象存储 SecretKey", "editable": True, "apply": "agent"},
    {"key": "offsite_smb_user", "title": "异地副本：NAS 用户名", "editable": True, "apply": "agent",
     "note": "NAS 网络邻居允许匿名访问时可不填"},
    {"key": "offsite_smb_password", "title": "异地副本：NAS 密码", "editable": True, "apply": "agent"},
]  # fmt: skip
EDITABLE_SECRETS = {it["key"] for it in SECRET_ITEMS if it["editable"]}


def register(app: FastAPI) -> None:
    from mcops import capabilities as capmod
    from mcops import gameprops
    from mcops.api import Services, SessionDep, SvcDep
    from mcops.auth import (
        SESSION_COOKIE,
        hash_password,
    )
    from mcops.config import update_secrets
    from mcops.configstore import ConfigValidationError

    def _store(s: Services) -> Any:
        if s.config is None:
            raise HTTPException(503, "config_store_unavailable")
        return s.config

    async def _result(s: Services, changed: list[dict[str, str]]) -> dict[str, Any]:
        v = await _store(s).view()
        return {"ok": True, "changed": changed, "pending": v["pending"],
                "values": v["values"], "overridden": v["overridden"]}  # fmt: skip

    # ---- 配置 ----

    @app.get("/api/config")
    async def config_view(_: SessionDep, s: SvcDep) -> dict[str, Any]:
        return await _store(s).view()  # type: ignore[no-any-return]

    @app.put("/api/config")
    async def config_save(body: ConfigBody, sess: SessionDep, s: SvcDep) -> dict[str, Any]:
        try:
            changed = await _store(s).save(body.values, sess.user, body.note)
        except ConfigValidationError as e:
            raise HTTPException(422, str(e)) from None
        if changed:
            await s.audit(
                sess.user, "config.save", changed=[c["path"] for c in changed], note=body.note
            )
        return await _result(s, changed)

    @app.post("/api/config/reset")
    async def config_reset(body: ResetBody, sess: SessionDep, s: SvcDep) -> dict[str, Any]:
        try:
            changed = await _store(s).reset(body.paths, sess.user)
        except ConfigValidationError as e:
            raise HTTPException(422, str(e)) from None
        if changed:
            await s.audit(sess.user, "config.reset", changed=[c["path"] for c in changed])
        return await _result(s, changed)

    @app.get("/api/config/history")
    async def config_history(
        _: SessionDep, s: SvcDep, limit: Annotated[int, Query(ge=1, le=200)] = 50
    ) -> dict[str, Any]:
        return {"items": await _store(s).history(limit)}

    @app.post("/api/config/history/{hid}/restore")
    async def config_restore(hid: int, sess: SessionDep, s: SvcDep) -> dict[str, Any]:
        try:
            changed = await _store(s).restore(hid, sess.user)
        except KeyError:
            raise HTTPException(404, "history_not_found") from None
        except ConfigValidationError as e:
            raise HTTPException(422, f"这个版本在当前环境下无效：{e}") from None
        await s.audit(sess.user, "config.restore", target=str(hid))
        return await _result(s, changed)

    @app.post("/api/system/restart-agent", status_code=202)
    async def restart_agent(sess: SessionDep, s: SvcDep) -> dict[str, bool]:
        unit = os.environ.get("MCOPS_SYSTEMD_UNIT", "mcops-agent.service")
        if not os.environ.get("INVOCATION_ID") or shutil.which("systemd-run") is None:
            raise HTTPException(409, "agent 不是由 systemd 启动的（开发模式），请手动重启")
        await s.audit(sess.user, "agent.restart")
        # 交给一个独立的临时单元 2 秒后执行，本请求先正常返回；重启 agent 不影响 MC 进程
        proc = await asyncio.create_subprocess_exec(
            "systemd-run", "--user", "--collect", "--quiet", "--on-active=2",
            "systemctl", "--user", "restart", unit,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )  # fmt: skip
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise HTTPException(500, f"无法安排重启：{err.decode().strip()}")
        return {"accepted": True}

    # ---- 能力 ----

    @app.get("/api/capabilities")
    async def capabilities(_: SessionDep, s: SvcDep) -> dict[str, Any]:
        cfg = s.server.cfg
        caps = await asyncio.to_thread(
            capmod.detect, cfg.game_dir, cfg.jar, s.caps.features if s.caps else {}
        )
        s.caps = caps
        return caps.to_json()

    # ---- server.properties ----

    def _props_path(s: Services) -> Any:
        return s.server.cfg.game_dir / "server.properties"

    def _motd_sync(s: Services) -> bool:
        cfg = s.config.running if s.config is not None else None
        return bool(cfg and cfg.ops.motd and cfg.ops.motd.server_list is not None)

    @app.get("/api/gamecfg/properties")
    async def props_view(_: SessionDep, s: SvcDep) -> dict[str, Any]:
        p = _props_path(s)
        if not p.is_file():
            raise HTTPException(404, "server.properties 不存在（服务端还没生成过）")
        return await asyncio.to_thread(gameprops.view, p, _motd_sync(s))

    @app.put("/api/gamecfg/properties")
    async def props_save(body: PropsBody, sess: SessionDep, s: SvcDep) -> dict[str, Any]:
        if s.server.observe:
            raise HTTPException(409, "观测模式：不修改服务端文件")
        try:
            r = await asyncio.to_thread(
                gameprops.update, _props_path(s), body.changes, _motd_sync(s)
            )
        except gameprops.PropsError as e:
            raise HTTPException(422, str(e)) from None
        if r["changed"]:
            await s.audit(sess.user, "gamecfg.properties", changed=r["changed"], backup=r["backup"])
        return r

    # ---- 账号与机密 ----

    def _secrets_file(s: Services) -> Any:
        if s.secrets_file is None:
            raise HTTPException(503, "secrets_unavailable")
        return s.secrets_file

    @app.post("/api/auth/password", status_code=204)
    async def change_password(
        body: PasswordBody, request: Request, response: Response, sess: SessionDep, s: SvcDep
    ) -> None:
        if not s.auth.check_password(body.old_password):
            await s.audit(sess.user, "auth.password", result="bad_old_password")
            raise HTTPException(403, "原密码不正确")
        new_hash = await asyncio.to_thread(hash_password, body.new_password)
        await asyncio.to_thread(update_secrets, _secrets_file(s), {"admin_password_hash": new_hash})
        s.auth.set_password_hash(new_hash)
        n = await s.auth.revoke_all_sessions()
        response.delete_cookie(SESSION_COOKIE, path="/")
        await s.audit(sess.user, "auth.password", revoked_sessions=n)

    pending_totp: dict[str, str] = {}

    @app.get("/api/auth/totp")
    async def totp_status(_: SessionDep, s: SvcDep) -> dict[str, bool]:
        return {"enabled": s.auth.totp_enabled}

    @app.post("/api/auth/totp/setup")
    async def totp_setup(sess: SessionDep, s: SvcDep) -> dict[str, str]:
        secret = pyotp.random_base32()
        pending_totp[sess.user] = secret
        uri = pyotp.TOTP(secret).provisioning_uri(name=sess.user, issuer_name="mc-ops")
        return {"secret": secret, "otpauth_uri": uri}

    @app.post("/api/auth/totp/confirm")
    async def totp_confirm(body: TotpConfirmBody, sess: SessionDep, s: SvcDep) -> dict[str, bool]:
        secret = pending_totp.get(sess.user)
        if secret is None:
            raise HTTPException(409, "请先生成二维码")
        if not pyotp.TOTP(secret).verify(body.code.strip(), valid_window=1):
            raise HTTPException(422, "验证码不正确，请确认手机时间准确后重试")
        await asyncio.to_thread(update_secrets, _secrets_file(s), {"totp_secret": secret})
        s.auth.set_totp(secret)
        pending_totp.pop(sess.user, None)
        await s.audit(sess.user, "auth.totp.enable")
        return {"enabled": True}

    @app.post("/api/auth/totp/disable")
    async def totp_disable(body: TotpDisableBody, sess: SessionDep, s: SvcDep) -> dict[str, bool]:
        if not s.auth.check_password(body.password):
            raise HTTPException(403, "密码不正确")
        await asyncio.to_thread(update_secrets, _secrets_file(s), {"totp_secret": None})
        s.auth.set_totp(None)
        await s.audit(sess.user, "auth.totp.disable")
        return {"enabled": False}

    @app.get("/api/secrets")
    async def secrets_view(_: SessionDep, s: SvcDep) -> dict[str, Any]:
        from mcops.config import load_secrets

        sec = await asyncio.to_thread(load_secrets, _secrets_file(s))
        return {
            "items": [{**it, "set": getattr(sec, it["key"]) is not None} for it in SECRET_ITEMS]
        }

    @app.put("/api/secrets")
    async def secrets_save(body: dict[str, Any], sess: SessionDep, s: SvcDep) -> dict[str, Any]:
        changes: dict[str, str | None] = {}
        for k, v in body.items():
            if k not in EDITABLE_SECRETS:
                raise HTTPException(422, f"{k}：不能在面板修改")
            if not isinstance(v, str) or "\n" in v or len(v) > 512:
                raise HTTPException(422, f"{k}：必须是单行文本")
            if v.strip():  # 空字符串 = 不改
                changes[k] = v.strip()
        if changes:
            await asyncio.to_thread(update_secrets, _secrets_file(s), changes)
            await s.audit(sess.user, "secrets.update", keys=sorted(changes))
        pend = [{"path": f"secrets.{k}", "apply": "agent"} for k in sorted(changes)]
        return {"ok": True, "pending": pend}
