"""差异图生成：把每个 pass 的像素差异“放大标注”成一张可肉眼复核的 PNG。

配色约定（写进报告，便于人工对照）：

* 未超过阈值的像素 -> 25% 灰度背景（线性 0.25 经 sRGB 编码后约 137/255），
  只显示基线亮度，不参与差异判断；
* 超过阈值的像素 -> 热力色：黄（刚超阈值）-> 红（远超阈值），
  绿色通道按 ``e = clamp(log2(d8 / max(阈值, 1/255)) / 6, 0, 1)`` 衰减，
  即差异每放大 2^6≈64 倍到达纯红。这样微小的“刚好超标”也能被看见。

差异图仅用于人工复核，不参与自动判定。
"""

from __future__ import annotations

import math

from .metrics import Thresholds, _LUT_INT, normalize_encoding
from .png_codec import Raster, write_png

__all__ = ["build_diff_raster", "write_diff_png", "BACKGROUND_GRAY"]

# sRGB 编码(0.25) ≈ 0.537 -> 137/255；作为未差异像素的背景灰。
BACKGROUND_GRAY = 137


def _srgb_encode(value: float) -> float:
    if value <= 0.0031308:
        return value * 12.92
    return 1.055 * (value ** (1.0 / 2.4)) - 0.055


def build_diff_raster(
    base: Raster,
    cand: Raster,
    thresholds: Thresholds,
    encoding: str = "srgb",
) -> Raster:
    """生成差异图光栅。要求两图尺寸一致（由调用方先校验）。"""
    if base.width != cand.width or base.height != cand.height:
        raise ValueError(
            f"无法生成差异图：尺寸不一致（{base.width}×{base.height} vs "
            f"{cand.width}×{cand.height}）"
        )
    encoding = normalize_encoding(encoding)
    lut = _LUT_INT[encoding]
    threshold_int = thresholds.channel_delta_int
    # 对数放大的基准：阈值至少按 1/255 计，避免阈值为 0 时 log 发散。
    reference = max(threshold_int, 1.0)
    base_data = base.data
    cand_data = cand.data
    out = bytearray(base.width * base.height * 4)
    for pixel in range(base.width * base.height):
        offset = pixel * 4
        dr = lut[base_data[offset]] - lut[cand_data[offset]]
        dg = lut[base_data[offset + 1]] - lut[cand_data[offset + 1]]
        db = lut[base_data[offset + 2]] - lut[cand_data[offset + 2]]
        da = lut[base_data[offset + 3]] - lut[cand_data[offset + 3]]
        pixel_max = max(abs(dr), abs(dg), abs(db), abs(da))
        if pixel_max <= threshold_int:
            out[offset] = BACKGROUND_GRAY
            out[offset + 1] = BACKGROUND_GRAY
            out[offset + 2] = BACKGROUND_GRAY
            out[offset + 3] = 255
            continue
        ratio = pixel_max / reference
        exaggeration = math.log2(ratio) / 6.0 if ratio > 1.0 else 0.0
        exaggeration = min(max(exaggeration, 0.0), 1.0)
        out[offset] = 255
        out[offset + 1] = int(round(255.0 * (1.0 - exaggeration)))
        out[offset + 2] = 0
        out[offset + 3] = 255
    return Raster(base.width, base.height, bytes(out))


def write_diff_png(path, base: Raster, cand: Raster, thresholds: Thresholds, encoding: str = "srgb") -> None:
    write_png(path, build_diff_raster(base, cand, thresholds, encoding))
