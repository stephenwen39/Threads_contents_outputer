"""spinner 測試（不依賴真實終端機）。"""

from __future__ import annotations

import io
import time

from threads_outputer.spinner import Spinner


def test_disabled_spinner_prints_updates_as_lines():
    buf = io.StringIO()
    sp = Spinner("開始", stream=buf, enabled=False)
    with sp:
        sp.update("第一步")
        sp.update("第二步")
    out = buf.getvalue()
    assert "第一步" in out
    assert "第二步" in out
    # 非互動模式不應輸出回車控制字元
    assert "\r" not in out


def test_stringio_defaults_to_disabled():
    # StringIO 非 TTY，預設 enabled 應為 False
    sp = Spinner(stream=io.StringIO())
    assert sp.enabled is False


def test_enabled_spinner_animates_and_clears():
    buf = io.StringIO()
    sp = Spinner("跑步中", stream=buf, enabled=True, interval=0.01)
    with sp:
        time.sleep(0.05)
        sp.update("換個訊息")
        time.sleep(0.05)
    out = buf.getvalue()
    # 互動模式會用 \r 重畫同一行
    assert "\r" in out
    assert "跑步中" in out or "換個訊息" in out


def test_context_manager_returns_spinner():
    sp = Spinner(stream=io.StringIO(), enabled=False)
    with sp as s:
        assert s is sp
