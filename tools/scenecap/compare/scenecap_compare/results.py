"""逐 pass 配对比较两个重放输出目录，产出结构化的判定结果。

配对规则（与报告/退出码一致）：

1. 两侧 pass 按 ``index`` 配对（不按文件名）。
2. 任一侧缺少该 index、或任一侧 ``status != "ok"`` -> 该 pass 直接判失败并给出原因。
3. 尺寸或 pixel_format 不一致 -> 判失败（这是契约违背，不是“像素差异小”）。
4. 通过检查后，两侧分别按各自 index.json 的 ``encoding`` 解码到线性光再比较，
   因此 srgb/linear 混用且表示同一场景时可以通过。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from .diffimg import write_diff_png
from .metrics import InputError, MetricError, PassMetrics, Thresholds, compare_rasters
from .png_codec import PngError, Raster, read_png
from .replay_index import PassEntry, ReplayDir, load_replay_dir

__all__ = ["PassResult", "DiffResult", "compare_dirs", "safe_slug"]

_SAFE = re.compile(r"[^0-9A-Za-z_.-]+")


def safe_slug(slug: str) -> str:
    cleaned = _SAFE.sub("_", slug).strip("._")
    return cleaned or "pass"


@dataclass(frozen=True)
class PassResult:
    """单个 pass 的比较结论。"""

    index: int
    slug: str
    verdict: str  # "pass" / "fail"
    base: PassEntry | None
    cand: PassEntry | None
    metrics: PassMetrics | None = None
    reasons: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    diff_image: Path | None = None
    base_raster: Raster | None = None
    cand_raster: Raster | None = None

    @property
    def passed(self) -> bool:
        return self.verdict == "pass"


@dataclass(frozen=True)
class DiffResult:
    """整体比较结果。"""

    base: ReplayDir
    cand: ReplayDir
    thresholds: Thresholds
    passes: tuple[PassResult, ...]
    diff_dir: Path | None = None

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.passes)

    @property
    def first_failure(self) -> PassResult | None:
        for item in self.passes:
            if not item.passed:
                return item
        return None

    @property
    def failed_count(self) -> int:
        return sum(1 for item in self.passes if not item.passed)

    @property
    def measured(self) -> tuple[PassResult, ...]:
        return tuple(item for item in self.passes if item.metrics is not None)

    def totals(self) -> dict:
        """整体汇总（只统计真正比较过的 pass）。"""
        total_pixels = sum(item.metrics.total_pixels for item in self.measured)
        diff_pixels = sum(item.metrics.diff_pixels for item in self.measured)
        nan_pixels = sum(item.metrics.nan_pixels for item in self.measured)
        inf_pixels = sum(item.metrics.inf_pixels for item in self.measured)
        worst = max(
            (item.metrics.max_channel_error for item in self.measured), default=0.0
        )
        worst_item = None
        for item in self.measured:
            if item.metrics.max_channel_error == worst:
                worst_item = item
                break
        psnrs = [item.metrics.psnr_db for item in self.measured]
        ssims = [item.metrics.ssim for item in self.measured]
        return {
            "passes": len(self.passes),
            "measured": len(self.measured),
            "failed": self.failed_count,
            "total_pixels": total_pixels,
            "diff_pixels": diff_pixels,
            "diff_pixel_ratio": (diff_pixels / total_pixels) if total_pixels else 0.0,
            "max_channel_error": worst,
            "max_error_pass": worst_item.index if worst_item else None,
            "min_psnr_db": min(psnrs) if psnrs else math.inf,
            "min_ssim": min(ssims) if ssims else 1.0,
            "nan_pixels": nan_pixels,
            "inf_pixels": inf_pixels,
        }


def _read_raster(path: Path, side: str, reasons: list[str]) -> Raster | None:
    if not path.is_file():
        reasons.append(f"{side}缺少 PNG 文件 {path.name}")
        return None
    try:
        return read_png(path)
    except PngError as exc:
        reasons.append(f"{side}PNG 无法解码（{path.name}）：{exc}")
    except OSError as exc:
        reasons.append(f"{side}PNG 无法读取（{path.name}）：{exc}")
    return None


def _check_declared_size(
    entry: PassEntry, raster: Raster, side: str, reasons: list[str]
) -> None:
    if entry.width is not None and entry.width != raster.width:
        reasons.append(
            f"{side}index.json 声明宽度 {entry.width} 与 PNG 实际宽度 {raster.width} 不一致"
        )
    if entry.height is not None and entry.height != raster.height:
        reasons.append(
            f"{side}index.json 声明高度 {entry.height} 与 PNG 实际高度 {raster.height} 不一致"
        )


def compare_dirs(
    base_dir,
    cand_dir,
    thresholds: Thresholds | None = None,
    *,
    diff_dir=None,
    write_diff_images: bool = True,
) -> DiffResult:
    """比较两个重放目录。返回结构化结果（不抛输入错误以外的异常）。"""
    thresholds = thresholds or Thresholds()
    base = base_dir if isinstance(base_dir, ReplayDir) else load_replay_dir(base_dir)
    cand = cand_dir if isinstance(cand_dir, ReplayDir) else load_replay_dir(cand_dir)
    if not base.passes:
        raise InputError(f"基线目录 {base.path} 的 index.json 没有任何 pass")
    diff_path = Path(diff_dir) if diff_dir else None
    if diff_path is not None and write_diff_images:
        diff_path.mkdir(parents=True, exist_ok=True)

    results: list[PassResult] = []
    for index in sorted(set(base.passes) | set(cand.passes)):
        base_entry = base.passes.get(index)
        cand_entry = cand.passes.get(index)
        slug = (cand_entry or base_entry).slug  # type: ignore[union-attr]
        reasons: list[str] = []
        notes: list[str] = []
        metrics: PassMetrics | None = None
        base_raster: Raster | None = None
        cand_raster: Raster | None = None
        diff_image: Path | None = None

        if base_entry is None:
            reasons.append(
                f"基线缺少 pass #{index}（候选多出该 pass，无法配对）"
            )
        if cand_entry is None:
            reasons.append(f"候选缺少 pass #{index}（基线段为 {base_entry.slug!r}）")
        for side, entry in (("基线", base_entry), ("候选", cand_entry)):
            if entry is not None and entry.status != "ok":
                reasons.append(f"{side} pass #{index} 状态为 {entry.status!r}，不是 ok")

        both_ok = (
            base_entry is not None
            and cand_entry is not None
            and base_entry.status == "ok"
            and cand_entry.status == "ok"
        )
        if both_ok:
            base_raster = _read_raster(base.png_path(base_entry), "基线", reasons)
            cand_raster = _read_raster(cand.png_path(cand_entry), "候选", reasons)
            if base_raster is not None:
                _check_declared_size(base_entry, base_raster, "基线", reasons)
            if cand_raster is not None:
                _check_declared_size(cand_entry, cand_raster, "候选", reasons)
            if (
                base_entry.pixel_format
                and cand_entry.pixel_format
                and base_entry.pixel_format.lower() != cand_entry.pixel_format.lower()
            ):
                reasons.append(
                    f"pixel_format 不一致：基线 {base_entry.pixel_format}，"
                    f"候选 {cand_entry.pixel_format}"
                )
            if base_entry.encoding != cand_entry.encoding:
                notes.append(
                    f"两侧 encoding 不同（基线 {base_entry.encoding}，候选 "
                    f"{cand_entry.encoding}），已分别按各自 encoding 解码后比较"
                )
            if base_raster is not None and cand_raster is not None:
                if (
                    base_raster.width != cand_raster.width
                    or base_raster.height != cand_raster.height
                ):
                    reasons.append(
                        f"尺寸不一致：基线 {base_raster.width}×{base_raster.height}，"
                        f"候选 {cand_raster.width}×{cand_raster.height}"
                    )
                else:
                    try:
                        metrics = compare_rasters(
                            base_raster,
                            cand_raster,
                            thresholds,
                            encoding=base_entry.encoding,
                            cand_encoding=cand_entry.encoding,
                        )
                    except MetricError as exc:
                        reasons.append(f"无法比较：{exc}")
                    else:
                        reasons.extend(metrics.failures(thresholds))
                    if write_diff_images and diff_path is not None:
                        target = (
                            diff_path
                            / f"diff_{index:03d}_{safe_slug(slug)}.png"
                        )
                        try:
                            write_diff_png(
                                target,
                                base_raster,
                                cand_raster,
                                thresholds,
                                encoding=base_entry.encoding,
                            )
                        except OSError as exc:
                            notes.append(f"差异图写入失败：{exc}")
                        else:
                            diff_image = target

        verdict = "pass" if not reasons else "fail"
        results.append(
            PassResult(
                index=index,
                slug=slug,
                verdict=verdict,
                base=base_entry,
                cand=cand_entry,
                metrics=metrics,
                reasons=tuple(reasons),
                notes=tuple(notes),
                diff_image=diff_image,
                base_raster=base_raster,
                cand_raster=cand_raster,
            )
        )
    return DiffResult(
        base=base,
        cand=cand,
        thresholds=thresholds,
        passes=tuple(results),
        diff_dir=diff_path if write_diff_images else None,
    )
