"""安全整改回归测试：登录节流、会话吊销、封禁理由清洗。"""

from __future__ import annotations

from mcops.auth import LoginThrottle
from mcops.network import sanitize_ban_reason


def test_login_throttle_escalates_then_resets() -> None:
    t = LoginThrottle(base_s=0.5, cap_s=8.0)
    assert t.delay_s() == 0.0
    t.failure()
    assert t.delay_s() == 0.5
    t.failure()
    assert t.delay_s() == 1.0
    for _ in range(10):
        t.failure()
    assert t.delay_s() == 8.0  # 封顶，不会无限增长，也从不"拉黑"
    t.success()
    assert t.delay_s() == 0.0  # 成功即清零


def test_login_throttle_window_prunes_old_failures(monkeypatch: object) -> None:
    import mcops.auth as auth_mod

    clock = {"t": 1000.0}
    monkeypatch.setattr(auth_mod.time, "monotonic", lambda: clock["t"])  # type: ignore[attr-defined]
    t = LoginThrottle(base_s=0.5, window_s=300.0)
    t.failure()
    t.failure()
    assert t.delay_s() == 1.0
    clock["t"] += 301  # 过了窗口
    assert t.delay_s() == 0.0


def test_sanitize_ban_reason() -> None:
    assert sanitize_ban_reason(None) is None
    assert sanitize_ban_reason("扫描器") == "扫描器"
    assert sanitize_ban_reason("line1\nline2") == "line1 line2"  # 换行被压成空格
    assert sanitize_ban_reason("a\x00\x07b") == "a b"  # 控制字符换成空格，不把词粘在一起
    assert sanitize_ban_reason("   ") is None  # 只剩空白则视为无
    assert sanitize_ban_reason("  多个   空格  ") == "多个 空格"
