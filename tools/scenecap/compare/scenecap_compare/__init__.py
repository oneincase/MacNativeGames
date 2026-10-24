"""scenecap 重放结果像素比较与渲染回归判定（纯标准库实现）。

对外主要入口：

* :func:`load_replay_dir` —— 读取/校验 t3 的重放输出目录契约；
* :func:`compare_dirs` —— 逐 pass 比较两个目录；
* :func:`compare_rasters` —— 直接比较两张 RGBA8 光栅；
* :func:`main` —— ``scenecap diff`` 命令行。

设计要点见 ``README.md``：误差以 8-bit 量化单位表达、按 encoding 解码到线性光、
阈值判定用严格大于、输入错误与渲染回归用不同退出码区分。
"""

from __future__ import annotations

__version__ = "0.1.0"

from .baseline import (
    BASELINE_MARKER,
    BASELINE_SCHEMA,
    baseline_exists,
    load_baseline,
    verify_baseline,
    write_baseline,
)
from .cli import main
from .diffimg import build_diff_raster
from .metrics import (
    LINEAR_TO_LINEAR,
    SRGB_TO_LINEAR,
    InputError,
    MetricError,
    PassMetrics,
    Thresholds,
    compare_float_buffers,
    compare_rasters,
    normalize_encoding,
)
from .png_codec import PngError, Raster, decode_png, encode_png, read_png, write_png
from .replay_index import REPLAY_SCHEMA, PassEntry, ReplayDir, load_replay_dir
from .report import render_json, render_report, result_to_dict
from .results import DiffResult, PassResult, compare_dirs

__all__ = [
    "__version__",
    "BASELINE_MARKER",
    "BASELINE_SCHEMA",
    "DiffResult",
    "InputError",
    "LINEAR_TO_LINEAR",
    "MetricError",
    "PassEntry",
    "PassMetrics",
    "PassResult",
    "PngError",
    "REPLAY_SCHEMA",
    "Raster",
    "ReplayDir",
    "SRGB_TO_LINEAR",
    "Thresholds",
    "baseline_exists",
    "build_diff_raster",
    "compare_dirs",
    "compare_float_buffers",
    "compare_rasters",
    "decode_png",
    "encode_png",
    "load_baseline",
    "load_replay_dir",
    "main",
    "normalize_encoding",
    "read_png",
    "render_json",
    "render_report",
    "result_to_dict",
    "verify_baseline",
    "write_baseline",
    "write_png",
]
