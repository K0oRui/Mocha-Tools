from __future__ import annotations

import pathlib
import time


def write_debug_log(msg: str) -> None:
    try:
        with pathlib.Path("mochatools.log").open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except (AttributeError, TypeError, RuntimeError, OSError) as e:
        write_debug_log(f"[Silenced] write_debug_log: {e}")
