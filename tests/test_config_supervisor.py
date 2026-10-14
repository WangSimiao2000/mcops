from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from mcops.config import ConfigError, load_config
from mcops.supervisor import Supervisor


def _write(tmp: Path, host: str = "127.0.0.1", secret_mode: int = 0o600) -> Path:
    (tmp / "secrets.yaml").write_text("rcon_password: s3cr3t-rcon\nadmin_password_hash: h\n")
    os.chmod(tmp / "secrets.yaml", secret_mode)
    cfg = tmp / "mcops.yaml"
    cfg.write_text(
        "data_dir: data\nsecrets_file: secrets.yaml\n"
        "server:\n  game_dir: game\n  jar: server.jar\n"
        f"api:\n  host: '{host}'\n"
    )
    return cfg


def test_relative_paths_resolve_against_config_dir(tmp_path: Path) -> None:
    lc = load_config(_write(tmp_path))
    assert lc.config.data_dir == tmp_path / "data"
    assert lc.config.server.game_dir == tmp_path / "game"
    assert lc.secrets.rcon_password is not None
    assert lc.secrets.rcon_password.get_secret_value() == "s3cr3t-rcon"
    assert "s3cr3t-rcon" not in repr(lc.secrets)  # SecretStr 不应泄露到日志


def test_refuses_non_loopback_bind(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="回环"):
        load_config(_write(tmp_path, host="0.0.0.0"))


def test_refuses_world_readable_secrets(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="600"):
        load_config(_write(tmp_path, secret_mode=0o644))


async def test_supervisor_restarts_crashed_module() -> None:
    calls = 0
    done = asyncio.Event()

    async def flaky() -> None:
        nonlocal calls
        calls += 1
        if calls < 2:
            raise RuntimeError("boom")
        done.set()
        await asyncio.sleep(3600)

    sup = Supervisor()
    sup.spawn("flaky", flaky)
    await asyncio.wait_for(done.wait(), 5)
    st = sup.modules["flaky"]
    assert st.restarts == 1 and st.state == "running"
    await sup.stop_all()
    assert st.state == "stopped"
