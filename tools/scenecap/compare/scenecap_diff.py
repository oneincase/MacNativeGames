#!/usr/bin/env python3
"""scenecap diff 独立入口：``python3 tools/scenecap/compare/scenecap_diff.py ...``。

t5 会把 ``scenecap diff`` 子命令转发到这里；也可以直接运行本脚本完成
基线写入与回归判定。只依赖 Python 标准库。
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from scenecap_compare.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
