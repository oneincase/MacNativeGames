"""把比较结果渲染成中文 markdown 报告与机器可读 JSON。

报告的目标读者是“要复核这次回归判定的人”：因此必须写清输入目录、阈值、
每个 pass 的度量、失败原因、差异图位置，以及“判定规则”本身，保证结论可复算。
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

from .metrics import PassMetrics
from .results import DiffResult, PassResult

__all__ = [
    "render_report",
    "render_json",
    "render_baseline_report",
    "write_report",
    "result_to_dict",
]

DIFF_SCHEMA = "scenecap.diff/1"


def _fmt(value: float | None, digits: int = 4, unit: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, float) and math.isinf(value):
        return "∞"
    if isinstance(value, float) and math.isnan(value):
        return "NaN"
    text = f"{value:.{digits}g}" if value != 0 else "0"
    return f"{text}{unit}"


def _fmt_psnr(value: float) -> str:
    if math.isinf(value):
        return "∞"
    return f"{value:.2f}"


def _fmt_ratio(value: float) -> str:
    return f"{value * 100:.4f}%"


def _fmt_ssim(value: float) -> str:
    return f"{value:.6f}"


def _pass_metric_cells(item: PassResult) -> list[str]:
    if item.metrics is None:
        return ["—", "—", "—", "—", "—"]
    m = item.metrics
    return [
        _fmt(m.max_channel_error, 4),
        _fmt(m.mean_abs_error, 3),
        _fmt_ratio(m.diff_pixel_ratio),
        _fmt_psnr(m.psnr_db),
        _fmt_ssim(m.ssim),
    ]


def _size_label(item: PassResult) -> str:
    entry = item.cand or item.base
    if item.base_raster is not None and item.cand_raster is not None:
        if (
            item.base_raster.width == item.cand_raster.width
            and item.base_raster.height == item.cand_raster.height
        ):
            return f"{item.base_raster.width}×{item.base_raster.height}"
    if entry is not None and entry.width and entry.height:
        return f"{entry.width}×{entry.height}"
    if item.base_raster is not None:
        return f"{item.base_raster.width}×{item.base_raster.height}"
    return "—"


def _encoding_label(item: PassResult) -> str:
    base = item.base.encoding if item.base else "—"
    cand = item.cand.encoding if item.cand else "—"
    if base == cand:
        return base
    return f"{base} → {cand}"


def _format_package(index: dict) -> str:
    package = index.get("package") or {}
    if not isinstance(package, dict):
        return "—"
    sha = package.get("sha256")
    frame = package.get("captured_frame")
    pieces = []
    if sha:
        pieces.append(f"sha256 `{sha}`")
    if frame is not None:
        pieces.append(f"捕获帧 {frame}")
    for key in ("game", "build"):
        if package.get(key):
            pieces.append(f"{key}={package[key]}")
    return "，".join(pieces) if pieces else "—"


def _diff_label(diff_image: Path | None, report_path: Path | None) -> str:
    if diff_image is None:
        return "—"
    if report_path is not None:
        try:
            return str(diff_image.resolve().relative_to(report_path.parent.resolve()))
        except ValueError:
            return str(diff_image)
    return str(diff_image)


def render_report(
    result: DiffResult,
    *,
    command: str,
    tool_version: str,
    generated_at: datetime | None = None,
    report_path: Path | None = None,
) -> str:
    """渲染 markdown 报告（中文）。"""
    generated_at = generated_at or datetime.now(timezone.utc).astimezone()
    totals = result.totals()
    verdict = "✅ 通过" if result.passed else "❌ 失败"
    first = result.first_failure
    header = f"# scenecap 渲染回归报告 — {verdict}"
    if first is not None:
        header += f"（首个失败 pass：#{first.index} {first.slug}）"

    lines: list[str] = [header, ""]
    lines.append(f"- 生成时间：{generated_at.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    lines.append(f"- 命令：`{command}`")
    lines.append(f"- 工具版本：scenecap-compare {tool_version}")
    lines.append(f"- 基线目录：`{result.base.path}`")
    lines.append(f"- 候选目录：`{result.cand.path}`")
    lines.append(f"- 阈值：{result.thresholds.describe()}")
    lines.append("")

    lines.append("## 输入包")
    lines.append("")
    lines.append("| 侧 | 目录 | schema | 包信息 |")
    lines.append("| --- | --- | --- | --- |")
    lines.append(
        f"| 基线 | `{result.base.path}` | `{result.base.schema}` | "
        f"{_format_package(result.base.index)} |"
    )
    lines.append(
        f"| 候选 | `{result.cand.path}` | `{result.cand.schema}` | "
        f"{_format_package(result.cand.index)} |"
    )
    lines.append("")

    lines.append("## 结论")
    lines.append("")
    if result.passed:
        lines.append(
            f"**结论：PASS** —— {len(result.passes)} 个 pass 全部满足阈值"
            f"（{result.thresholds.describe()}）。"
        )
    else:
        assert first is not None
        lines.append(
            f"**结论：FAIL** —— {len(result.passes)} 个 pass 中 "
            f"{result.failed_count} 个未通过。"
        )
        lines.append("")
        lines.append(
            f"- 首个失败 pass：**#{first.index} {first.slug}** —— "
            f"{'；'.join(first.reasons)}"
        )
    lines.append("")

    lines.append("## 整体统计")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("| --- | --- |")
    lines.append(f"| pass 总数 / 实际比较 / 失败 | {totals['passes']} / {totals['measured']} / {totals['failed']} |")
    lines.append(f"| 总像素数 | {totals['total_pixels']} |")
    lines.append(f"| 差异像素数 / 比例 | {totals['diff_pixels']} / {_fmt_ratio(totals['diff_pixel_ratio'])} |")
    worst_pass = totals["max_error_pass"]
    worst_suffix = f"（pass #{worst_pass}）" if worst_pass is not None else ""
    lines.append(
        f"| 最大通道误差 | {_fmt(totals['max_channel_error'], 4)}/255{worst_suffix} |"
    )
    lines.append(f"| 最小 PSNR | {_fmt_psnr(totals['min_psnr_db'])} dB |")
    lines.append(f"| 最小 SSIM | {_fmt_ssim(totals['min_ssim'])} |")
    lines.append(
        f"| NaN / Inf 像素 | {totals['nan_pixels']} / {totals['inf_pixels']} |"
    )
    lines.append("")

    lines.append("## pass 明细")
    lines.append("")
    lines.append(
        "| pass | slug | 尺寸 | encoding | 最大通道误差 | 平均绝对误差 | 差异像素比例 "
        "| PSNR (dB) | SSIM | 判定 | 差异图 |"
    )
    lines.append(
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"
    )
    for item in result.passes:
        cells = _pass_metric_cells(item)
        verdict_cell = "通过" if item.passed else "失败"
        lines.append(
            "| {index} | {slug} | {size} | {enc} | {err} | {mae} | {ratio} | {psnr} "
            "| {ssim} | {verdict} | {diff} |".format(
                index=item.index,
                slug=item.slug,
                size=_size_label(item),
                enc=_encoding_label(item),
                err=cells[0],
                mae=cells[1],
                ratio=cells[2],
                psnr=cells[3],
                ssim=cells[4],
                verdict=verdict_cell,
                diff=_diff_label(item.diff_image, report_path),
            )
        )
    lines.append("")

    failures = [item for item in result.passes if not item.passed]
    if failures:
        lines.append("## 失败详情")
        lines.append("")
        for item in failures:
            lines.append(f"### pass #{item.index} {item.slug}")
            lines.append("")
            for reason in item.reasons:
                lines.append(f"- ❌ {reason}")
            if item.metrics is not None:
                m = item.metrics
                lines.append(
                    f"- 度量：最大通道误差 {_fmt(m.max_channel_error, 4)}/255"
                    f"（通道 {m.max_error_channel}），平均绝对误差 "
                    f"{_fmt(m.mean_abs_error, 3)}/255，差异像素 {m.diff_pixels}/"
                    f"{m.total_pixels}（{_fmt_ratio(m.diff_pixel_ratio)}），"
                    f"PSNR {_fmt_psnr(m.psnr_db)} dB，SSIM {_fmt_ssim(m.ssim)}"
                )
            for note in item.notes:
                lines.append(f"- 备注：{note}")
            lines.append("")

    notes_all = [(item, note) for item in result.passes for note in item.notes]
    if notes_all:
        lines.append("## 备注")
        lines.append("")
        for item, note in notes_all:
            lines.append(f"- pass #{item.index} {item.slug}：{note}")
        lines.append("")

    diff_images = [item for item in result.passes if item.diff_image is not None]
    lines.append("## 差异图")
    lines.append("")
    if not diff_images:
        lines.append("（未生成差异图）")
    else:
        lines.append(
            "差异图中：未超阈值的像素为 25% 灰度背景；超阈值像素按黄→红热力色显示，"
            "对数放大（差异每增大 2^6≈64 倍到达纯红），便于定位“刚好超标”的像素。"
        )
        lines.append("")
        for item in diff_images:
            lines.append(
                f"- pass #{item.index} {item.slug}：`{_diff_label(item.diff_image, report_path)}`"
            )
    lines.append("")

    lines.append("## 度量与判定定义")
    lines.append("")
    lines.append(
        "- 解码：按各侧 index.json 的 `encoding` 把 8-bit 像素转为线性光"
        "（`srgb` 走反 sRGB 传递函数，`linear` 直接 `value/255`）。"
    )
    lines.append(
        "- 通道误差：两线性值之差 ×255，即“8-bit 量化单位”；默认阈值 2 允许编码"
        "往返的舍入误差。"
    )
    lines.append(
        "- 差异像素：任一通道（R/G/B/A）误差严格大于阈值；每 pass 差异像素比例"
        "为差异像素数 / 总像素数。"
    )
    lines.append("- PSNR：峰值 1.0（线性归一化），`-10·log10(MSE)`。")
    lines.append(
        "- SSIM：简化版，全局单窗口，Rec.709 亮度，K1=0.01、K2=0.03（非分块均值）。"
    )
    lines.append(
        "- NaN/Inf：8-bit PNG 不携带非有限值，故本报告该计数为 0；浮点缓冲比较"
        "接口（`compare_float_buffers`）会真实统计并判失败。"
    )
    lines.append(
        "- 判定：任一侧缺少该 pass、`status != ok`、尺寸或 pixel_format 不一致，"
        "该 pass 直接失败；全部 pass 通过则整体通过。"
    )
    lines.append("")
    return "\n".join(lines)


def _metrics_to_dict(metrics: PassMetrics | None) -> dict | None:
    if metrics is None:
        return None
    return {
        "width": metrics.width,
        "height": metrics.height,
        "total_pixels": metrics.total_pixels,
        "max_channel_error": metrics.max_channel_error,
        "max_error_channel": metrics.max_error_channel,
        "mean_abs_error": metrics.mean_abs_error,
        "diff_pixels": metrics.diff_pixels,
        "diff_pixel_ratio": metrics.diff_pixel_ratio,
        "psnr_db": None if math.isinf(metrics.psnr_db) else metrics.psnr_db,
        "ssim": metrics.ssim,
        "nan_pixels": metrics.nan_pixels,
        "inf_pixels": metrics.inf_pixels,
    }


def result_to_dict(result: DiffResult) -> dict:
    """机器可读结果（供 CI / t6 联调断言使用）。"""
    return {
        "schema": DIFF_SCHEMA,
        "passed": result.passed,
        "first_failure": (
            None
            if result.first_failure is None
            else {
                "index": result.first_failure.index,
                "slug": result.first_failure.slug,
                "reasons": list(result.first_failure.reasons),
            }
        ),
        "thresholds": {
            "max_channel_delta": result.thresholds.max_channel_delta,
            "max_diff_pixel_ratio": result.thresholds.max_diff_pixel_ratio,
        },
        "base": {
            "path": str(result.base.path),
            "schema": result.base.schema,
            "package": result.base.index.get("package"),
        },
        "cand": {
            "path": str(result.cand.path),
            "schema": result.cand.schema,
            "package": result.cand.index.get("package"),
        },
        "passes": [
            {
                "index": item.index,
                "slug": item.slug,
                "verdict": item.verdict,
                "encoding": item.base.encoding if item.base else None,
                "cand_encoding": item.cand.encoding if item.cand else None,
                "metrics": _metrics_to_dict(item.metrics),
                "reasons": list(item.reasons),
                "notes": list(item.notes),
                "diff_image": str(item.diff_image) if item.diff_image else None,
            }
            for item in result.passes
        ],
        "totals": result.totals(),
    }


def render_json(result: DiffResult) -> str:
    return json.dumps(result_to_dict(result), ensure_ascii=False, indent=2) + "\n"


def render_baseline_report(
    *,
    baseline_dir: Path,
    replay_dir: Path,
    command: str,
    tool_version: str,
    pass_count: int,
    generated_at: datetime | None = None,
    updated: bool = False,
) -> str:
    generated_at = generated_at or datetime.now(timezone.utc).astimezone()
    action = "更新" if updated else "写入"
    lines = [
        f"# scenecap 基线报告 — 已{action}基线",
        "",
        f"- 生成时间：{generated_at.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- 命令：`{command}`",
        f"- 工具版本：scenecap-compare {tool_version}",
        f"- 基线目录：`{baseline_dir}`",
        f"- 基线来源重放目录：`{replay_dir}`",
        f"- pass 数：{pass_count}",
        "",
        "## 说明",
        "",
        "- 基线快照复制了来源目录的 `index.json` 与被引用的全部 PNG，并记录 SHA-256；",
        "  之后每次回归都会先校验快照完整性，避免“基线被悄悄改掉”。",
        "- 后续运行 `scenecap diff --baseline <dir> <候选目录>` 会与本次快照比较；",
        "  失败时进程以退出码 1 结束。",
        "- 若确实需要更换基线，使用 `--update-baseline` 覆盖。",
        "",
    ]
    return "\n".join(lines)


def write_report(path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
