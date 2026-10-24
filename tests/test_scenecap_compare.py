"""把 tools/scenecap/compare 的测试套件接入顶层 ``make check`` / CI 的 unittest 发现。

真正的用例在 ``tools/scenecap/compare/scenecap_compare/tests/test_compare.py``，
与代码放在一起；这里只做路径引导，避免顶层 tests/ 变成第二个实现目录。

注意：宁可让本模块导入失败也不要静默跳过——scenecap diff 是回归判定工具，
它的测试消失必须被看见。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPARE_ROOT = ROOT / "tools" / "scenecap" / "compare"
if str(COMPARE_ROOT) not in sys.path:
    sys.path.insert(0, str(COMPARE_ROOT))

from scenecap_compare.tests import test_compare as _compare_suite  # noqa: E402


def load_tests(loader, tests, pattern):  # noqa: ARG001 - unittest 协议签名
    """让 unittest 发现机制加载 scenecap 对比工具的完整套件。"""
    return loader.loadTestsFromModule(_compare_suite)
