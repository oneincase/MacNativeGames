"""像素比较度量：以“8-bit 量化单位”表达通道误差，供 diff 判定使用。

关键语义（与报告中的阈值一一对应）：

* 8-bit 输入先按 index.json 的 ``encoding`` 解码成线性光：
  ``srgb`` -> 反 sRGB 传递函数；``linear`` -> ``value/255``。
* 两个线性值之差再乘以 255，得到“8-bit 量化后的通道误差”。因此默认阈值
  “每通道 ≤2” 指的就是这个量：允许编码往返的四舍五入误差，但对真实渲染
  差异敏感。
* 差异像素：任一通道误差 **严格大于** 阈值。等于阈值算通过。
* PSNR 峰值取 1.0（线性归一化），因此 PSNR 与 sRGB/linear 解码方式无关。
* SSIM 为简化版：全局单窗口，Rec.709 亮度，K1=0.01、K2=0.03。
* NaN/Inf：8-bit PNG 不可能携带非有限值，故该计数恒为 0；真正的统计在
  :func:`compare_float_buffers`（f32 线性缓冲）中完成。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .png_codec import Raster

__all__ = [
    "InputError",
    "MetricError",
    "normalize_encoding",
    "SRGB_TO_LINEAR",
    "LINEAR_TO_LINEAR",
    "Thresholds",
    "PassMetrics",
    "compare_rasters",
    "compare_float_buffers",
]

_QUANT = 65535.0
_QUANT_INT = 65535


class InputError(ValueError):
    """输入不可用（契约不符、目录缺失等）。CLI 以退出码 2 报告。"""


class MetricError(ValueError):
    """无法比较（尺寸/通道数不一致等）。"""


def normalize_encoding(value) -> str:
    """把 index.json 的 encoding 归一为 ``srgb`` 或 ``linear``。"""
    if value is None:
        return "srgb"
    if not isinstance(value, str):
        raise InputError(f"encoding 必须是字符串，实际为 {value!r}")
    text = value.strip().lower()
    if text in ("srgb", "s_rgb", "srgb8", "srgb-encoded", "srgb_encoded", "gamma"):
        return "srgb"
    if text in ("linear", "linearrgb", "linear-float", "f32"):
        return "linear"
    raise InputError(f"不支持的 encoding {value!r}（只接受 srgb / linear）")


def _srgb_to_linear(code: int) -> float:
    value = code / 255.0
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


# 256 项查找表：8-bit 码值 -> 线性值（两种 encoding 各一张）。
SRGB_TO_LINEAR: tuple[float, ...] = tuple(_srgb_to_linear(code) for code in range(256))
LINEAR_TO_LINEAR: tuple[float, ...] = tuple(code / 255.0 for code in range(256))

# 量化到 65535 的整数表，用于整数累加（避免浮点求和误差与慢速路径）。
_LUT_INT = {
    "srgb": tuple(round(value * _QUANT_INT) for value in SRGB_TO_LINEAR),
    "linear": tuple(code * 257 for code in range(256)),  # x/255*65535 == x*257
}


@dataclass(frozen=True)
class Thresholds:
    """判定阈值（命令行可覆盖）。"""

    max_channel_delta: float = 2.0  # 8-bit 量化单位
    max_diff_pixel_ratio: float = 0.001  # 0.1%

    def __post_init__(self) -> None:
        if not math.isfinite(self.max_channel_delta) or self.max_channel_delta < 0:
            raise InputError(
                f"--max-channel-delta 必须是非负有限数，实际为 {self.max_channel_delta!r}"
            )
        if self.max_channel_delta > 255:
            raise InputError(
                f"--max-channel-delta 不能超过 255，实际为 {self.max_channel_delta!r}"
            )
        if not math.isfinite(self.max_diff_pixel_ratio) or not (
            0.0 <= self.max_diff_pixel_ratio <= 1.0
        ):
            raise InputError(
                f"--max-diff-pixel-ratio 必须在 [0,1]，实际为 {self.max_diff_pixel_ratio!r}"
            )

    @property
    def channel_delta_int(self) -> float:
        """阈值换算到 65535 整数线性刻度。"""
        return self.max_channel_delta * 257.0

    def describe(self) -> str:
        return (
            f"每通道 ≤{self.max_channel_delta:g}/255（8-bit 量化单位），"
            f"差异像素比例 ≤{self.max_diff_pixel_ratio * 100:g}%"
        )


@dataclass(frozen=True)
class PassMetrics:
    """一个 pass 的像素比较结果。"""

    width: int
    height: int
    total_pixels: int
    max_channel_error: float  # 8-bit 量化单位
    max_error_channel: str  # R/G/B/A
    mean_abs_error: float  # 8-bit 量化单位
    diff_pixels: int
    diff_pixel_ratio: float
    psnr_db: float
    ssim: float
    nan_pixels: int
    inf_pixels: int

    @property
    def identical(self) -> bool:
        return self.max_channel_error == 0.0

    def failures(self, thresholds: Thresholds) -> tuple[str, ...]:
        """返回该 pass 未满足阈值的理由（空元组表示通过）。"""
        reasons: list[str] = []
        if self.max_channel_error > thresholds.max_channel_delta:
            reasons.append(
                f"最大通道误差 {self.max_channel_error:.4g}/255 超过阈值 "
                f"{thresholds.max_channel_delta:g}/255（通道 {self.max_error_channel}）"
            )
        if self.diff_pixel_ratio > thresholds.max_diff_pixel_ratio:
            reasons.append(
                f"差异像素比例 {self.diff_pixel_ratio * 100:.4f}% 超过阈值 "
                f"{thresholds.max_diff_pixel_ratio * 100:g}%（{self.diff_pixels} 像素）"
            )
        if self.nan_pixels:
            reasons.append(f"存在 {self.nan_pixels} 个 NaN 像素")
        if self.inf_pixels:
            reasons.append(f"存在 {self.inf_pixels} 个 Inf 像素")
        return tuple(reasons)


def _channel_slices(data: bytes):
    return data[0::4], data[1::4], data[2::4], data[3::4]


def compare_rasters(
    base: Raster,
    cand: Raster,
    thresholds: Thresholds,
    encoding: str = "srgb",
    cand_encoding: str | None = None,
) -> PassMetrics:
    """比较两张 RGBA8 光栅（各自线性化后再比较）。尺寸不一致抛 ``MetricError``。

    ``cand_encoding`` 用于两侧 encoding 不同的情况（例如基线是 srgb 编码、
    候选是 linear 编码，但二者表示同一个线性场景）：两侧分别按自己的
    encoding 解码，再在线性光空间比较。
    """
    if base.width != cand.width or base.height != cand.height:
        raise MetricError(
            f"尺寸不一致：基线 {base.width}×{base.height}，候选 {cand.width}×{cand.height}"
        )
    encoding = normalize_encoding(encoding)
    cand_encoding = normalize_encoding(cand_encoding if cand_encoding is not None else encoding)
    total = base.width * base.height
    if base.data == cand.data and encoding == cand_encoding:
        # 快速路径：字节完全一致且解码方式一致 -> 线性值也一致。
        return PassMetrics(
            width=base.width,
            height=base.height,
            total_pixels=total,
            max_channel_error=0.0,
            max_error_channel="-",
            mean_abs_error=0.0,
            diff_pixels=0,
            diff_pixel_ratio=0.0,
            psnr_db=math.inf,
            ssim=1.0,
            nan_pixels=0,
            inf_pixels=0,
        )
    lut = _LUT_INT[encoding]
    lut_cand = _LUT_INT[cand_encoding]
    threshold_int = thresholds.channel_delta_int
    br, bg, bb, ba = _channel_slices(base.data)
    cr, cg, cb, ca = _channel_slices(cand.data)
    sum_abs = 0
    max_abs = 0
    max_channel = "-"
    diff_pixels = 0
    sum_y_a = 0.0
    sum_y_b = 0.0
    sum_y2_a = 0.0
    sum_y2_b = 0.0
    sum_y_ab = 0.0
    # 通道名与索引对应：R/G/B/A
    names = ("R", "G", "B", "A")
    for xr, xg, xb, xa, yr, yg, yb_, ya in zip(br, bg, bb, ba, cr, cg, cb, ca):
        dr = lut[xr] - lut_cand[yr]
        dg = lut[xg] - lut_cand[yg]
        db = lut[xb] - lut_cand[yb_]
        da = lut[xa] - lut_cand[ya]
        for name, delta in (
            ("R", dr),
            ("G", dg),
            ("B", db),
            ("A", da),
        ):
            magnitude = delta if delta >= 0 else -delta
            sum_abs += magnitude
            if magnitude > max_abs:
                max_abs = magnitude
                max_channel = name
        pixel_max = max(abs(dr), abs(dg), abs(db), abs(da))
        if pixel_max > threshold_int:
            diff_pixels += 1
        # SSIM 亮度（Rec.709，线性光）
        y_a = 0.2126 * lut[xr] + 0.7152 * lut[xg] + 0.0722 * lut[xb]
        y_b = 0.2126 * lut_cand[yr] + 0.7152 * lut_cand[yg] + 0.0722 * lut_cand[yb_]
        sum_y_a += y_a
        sum_y_b += y_b
        sum_y2_a += y_a * y_a
        sum_y2_b += y_b * y_b
        sum_y_ab += y_a * y_b

    # 65535 整数刻度 -> [0,1] 线性值
    mean_abs_linear = (sum_abs / 4.0) / _QUANT / total
    mse = (sum_abs / 4.0) / (_QUANT * _QUANT) / total
    psnr = math.inf if mse == 0 else -10.0 * math.log10(mse)
    mu_a = sum_y_a / total / _QUANT
    mu_b = sum_y_b / total / _QUANT
    var_a = max(sum_y2_a / total / (_QUANT * _QUANT) - mu_a * mu_a, 0.0)
    var_b = max(sum_y2_b / total / (_QUANT * _QUANT) - mu_b * mu_b, 0.0)
    cov = sum_y_ab / total / (_QUANT * _QUANT) - mu_a * mu_b
    c1 = 0.01**2
    c2 = 0.03**2
    denominator = (mu_a * mu_a + mu_b * mu_b + c1) * (var_a + var_b + c2)
    ssim = (
        ((2 * mu_a * mu_b + c1) * (2 * cov + c2)) / denominator if denominator else 1.0
    )
    return PassMetrics(
        width=base.width,
        height=base.height,
        total_pixels=total,
        max_channel_error=max_abs / 257.0,
        max_error_channel=max_channel,
        mean_abs_error=round(mean_abs_linear * 255.0, 6),
        diff_pixels=diff_pixels,
        diff_pixel_ratio=diff_pixels / total if total else 0.0,
        psnr_db=psnr,
        ssim=max(-1.0, min(1.0, ssim)),
        nan_pixels=0,
        inf_pixels=0,
    )


def compare_float_buffers(
    base: tuple[float, ...] | list[float],
    cand: tuple[float, ...] | list[float],
    thresholds: Thresholds,
    *,
    width: int,
    height: int,
    channels: int = 4,
) -> PassMetrics:
    """比较两份线性 f32 缓冲（例如 .nvs 里未量化的 pass 输出）。

    非有限值（NaN/±Inf）单独计数，并计为一次“超过阈值”的通道误差；
    这让下游能区分“数值爆炸”与“普通像素差异”。
    """
    if channels <= 0:
        raise MetricError(f"channels 必须为正，实际 {channels}")
    if width <= 0 or height <= 0:
        raise MetricError(f"尺寸必须为正：{width}×{height}")
    expected = width * height * channels
    if len(base) != expected or len(cand) != expected:
        raise MetricError(
            f"缓冲元素数不一致：基线 {len(base)}、候选 {len(cand)}、预期 {expected}"
        )
    total = width * height
    sum_abs = 0.0
    max_abs = 0.0
    max_channel = "-"
    diff_pixels = 0
    nan_pixels = 0
    inf_pixels = 0
    names = ("R", "G", "B", "A")
    for pixel in range(total):
        pixel_diff = False
        pixel_nan = False
        pixel_inf = False
        for channel in range(channels):
            offset = pixel * channels + channel
            a = base[offset]
            b = cand[offset]
            bad_a = not math.isfinite(a)
            bad_b = not math.isfinite(b)
            if bad_a or bad_b:
                if (bad_a and math.isnan(a)) or (bad_b and math.isnan(b)):
                    pixel_nan = True
                else:
                    pixel_inf = True
                pixel_diff = True
                delta = 255.0
            else:
                delta = abs(a - b) * 255.0
            sum_abs += delta
            if delta > max_abs:
                max_abs = delta
                max_channel = names[channel] if channel < len(names) else str(channel)
            if delta > thresholds.max_channel_delta:
                pixel_diff = True
        if pixel_nan:
            nan_pixels += 1
        if pixel_inf:
            inf_pixels += 1
        if pixel_diff:
            diff_pixels += 1
    channel_count = total * channels
    mean_abs = sum_abs / channel_count if channel_count else 0.0
    mse = sum_abs / channel_count / (255.0 * 255.0) if channel_count else 0.0
    psnr = math.inf if mse == 0 else -10.0 * math.log10(mse)
    # 简化 SSIM：这里不做亮度投影（缓冲可能不是 RGBA 布局），用整体能量近似。
    mu_a = 0.0
    mu_b = 0.0
    var_a = 0.0
    var_b = 0.0
    cov = 0.0
    if channel_count:
        sum_a = sum(value for value in base if math.isfinite(value))
        sum_b = sum(value for value in cand if math.isfinite(value))
        mu_a = sum_a / channel_count
        mu_b = sum_b / channel_count
        var_a = max(
            sum(value * value for value in base if math.isfinite(value)) / channel_count
            - mu_a * mu_a,
            0.0,
        )
        var_b = max(
            sum(value * value for value in cand if math.isfinite(value)) / channel_count
            - mu_b * mu_b,
            0.0,
        )
        cov = (
            sum(a * b for a, b in zip(base, cand) if math.isfinite(a) and math.isfinite(b))
            / channel_count
            - mu_a * mu_b
        )
    c1 = 0.01**2
    c2 = 0.03**2
    denominator = (mu_a * mu_a + mu_b * mu_b + c1) * (var_a + var_b + c2)
    ssim = ((2 * mu_a * mu_b + c1) * (2 * cov + c2)) / denominator if denominator else 1.0
    return PassMetrics(
        width=width,
        height=height,
        total_pixels=total,
        max_channel_error=max_abs,
        max_error_channel=max_channel,
        mean_abs_error=mean_abs,
        diff_pixels=diff_pixels,
        diff_pixel_ratio=diff_pixels / total if total else 0.0,
        psnr_db=psnr,
        ssim=max(-1.0, min(1.0, ssim)),
        nan_pixels=nan_pixels,
        inf_pixels=inf_pixels,
    )
