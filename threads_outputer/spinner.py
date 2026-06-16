"""終端機旋轉進度指示器。

在 LLM 分析等「長時間、無輸出」的階段顯示一個旋轉字元，讓使用者知道程式仍在執行。

- 只在互動式終端機（TTY）顯示動畫；非互動環境（CI、輸出導向檔案）會退回成逐行輸出，
  避免產生 \\r 控制字元造成的亂碼。
- 寫入 stderr，不影響 stdout 的結果輸出。
"""

from __future__ import annotations

import itertools
import sys
import threading
import time
from typing import Optional, TextIO

_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class Spinner:
    def __init__(
        self,
        text: str = "處理中…",
        *,
        stream: Optional[TextIO] = None,
        enabled: Optional[bool] = None,
        interval: float = 0.1,
    ):
        self.stream = stream or sys.stderr
        self._text = text
        self.interval = interval
        if enabled is None:
            try:
                enabled = self.stream.isatty()
            except Exception:  # noqa: BLE001
                enabled = False
        self.enabled = bool(enabled)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def update(self, text: str) -> None:
        """更新顯示文字（可在執行中由其他階段呼叫）。"""
        with self._lock:
            self._text = text
        if not self.enabled:
            # 非互動環境：直接輸出一行，確保仍看得到進度
            print(text, file=self.stream, flush=True)

    def start(self) -> "Spinner":
        if self.enabled and self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        if self._thread is not None:
            self._stop.set()
            self._thread.join(timeout=1.0)
            self._thread = None
            self._clear()

    def _run(self) -> None:
        for frame in itertools.cycle(_FRAMES):
            if self._stop.is_set():
                break
            with self._lock:
                text = self._text
            self.stream.write(f"\r{frame} {text} ")
            self.stream.flush()
            time.sleep(self.interval)

    def _clear(self) -> None:
        try:
            self.stream.write("\r" + " " * 80 + "\r")
            self.stream.flush()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self) -> "Spinner":
        return self.start()

    def __exit__(self, *exc) -> bool:
        self.stop()
        return False
