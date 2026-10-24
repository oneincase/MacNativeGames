"""``python -m scenecap_compare`` 入口（等价于 ``scenecap diff``）。"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":  # pragma: no cover - 子进程入口
    sys.exit(main())
