"""最小 PNG 编解码器（纯标准库，不依赖 numpy/Pillow）。

scenecap 重放器的输出契约（见 t3 / docs/scenecap.md）是：

    RGBA8、非预乘 alpha、无隔行、左上原点、线性值经 sRGB 编码写出。

本模块只实现该契约需要的范围：8-bit、非隔行 PNG 的读取（颜色类型 0/2/3/4/6，
即灰度、RGB、调色板、灰度+alpha、RGBA）与写出（RGBA8、filter 0）。
超出范围的输入一律显式抛 `PngError`，绝不猜测或静默截断——diff 工具必须把
“读不懂的图”判为失败，而不是给出虚假的通过结论。

为什么自己写：仓库 CI 只安装 games/totk/requirements.txt（cryptography/lz4/
capstone/nsz），没有 Pillow；`make check` 必须离线可跑。
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

__all__ = ["PngError", "Raster", "decode_png", "encode_png", "read_png", "write_png"]

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# 颜色类型 -> 每像素通道数（8-bit 时即每像素字节数）
_COLOR_CHANNELS = {
    0: 1,  # 灰度
    2: 3,  # RGB
    3: 1,  # 调色板索引
    4: 2,  # 灰度 + alpha
    6: 4,  # RGBA
}

# 关键块：不认识的必须报错；辅助块可以跳过。
_KNOWN_CRITICAL = {b"IHDR", b"PLTE", b"IDAT", b"IEND"}


class PngError(ValueError):
    """PNG 结构错误或不受支持的 PNG 变体。"""


@dataclass(frozen=True)
class Raster:
    """8-bit RGBA 光栅，行主序，非预乘 alpha，左上原点。"""

    width: int
    height: int
    data: bytes  # 长度必须为 width*height*4

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise PngError(f"图像尺寸必须为正：{self.width}×{self.height}")
        expected = self.width * self.height * 4
        if len(self.data) != expected:
            raise PngError(
                f"RGBA 数据长度 {len(self.data)} 与 {self.width}×{self.height}×4={expected} 不一致"
            )


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _unfilter(raw: bytes, height: int, stride: int, bpp: int) -> bytearray:
    """把 PNG 扫描线的 filter 还原成原始字节（PNG 规范 9.2 节）。"""
    out = bytearray(height * stride)
    previous = bytearray(stride)
    pos = 0
    for y in range(height):
        if pos >= len(raw):
            raise PngError(f"PNG 像素数据在扫描线 {y} 处截断")
        filter_type = raw[pos]
        pos += 1
        line = bytearray(raw[pos : pos + stride])
        if len(line) != stride:
            raise PngError(f"PNG 像素数据在扫描线 {y} 处截断")
        pos += stride
        if filter_type == 0:
            pass
        elif filter_type == 1:  # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif filter_type == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 0xFF
        elif filter_type == 3:  # Average
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif filter_type == 4:  # Paeth
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                upper_left = previous[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(left, previous[i], upper_left)) & 0xFF
        else:
            raise PngError(f"未知的 PNG filter 类型 {filter_type}（扫描线 {y}）")
        out[y * stride : (y + 1) * stride] = line
        previous = line
    return out


def _to_rgba(
    raw: bytearray,
    width: int,
    height: int,
    color_type: int,
    palette: bytes | None,
    transparency: bytes | None,
) -> bytes:
    """把解滤波后的样本转换成 RGBA8（非预乘）。"""
    pixels = width * height
    if color_type == 6:
        return bytes(raw)
    if color_type == 2:
        # RGB -> RGBA；tRNS 的 RGB 透明色在重放输出里不会出现，遇到就明确拒绝。
        if transparency:
            raise PngError("不支持带 tRNS 的 RGB PNG（重放器输出为 RGBA8）")
        out = bytearray(pixels * 4)
        out[0::4] = raw[0::3]
        out[1::4] = raw[1::3]
        out[2::4] = raw[2::3]
        out[3::4] = b"\xff" * pixels
        return bytes(out)
    if color_type == 0:
        if transparency:
            raise PngError("不支持带 tRNS 的灰度 PNG（重放器输出为 RGBA8）")
        out = bytearray(pixels * 4)
        out[0::4] = raw
        out[1::4] = raw
        out[2::4] = raw
        out[3::4] = b"\xff" * pixels
        return bytes(out)
    if color_type == 4:
        out = bytearray(pixels * 4)
        out[0::4] = raw[0::2]
        out[1::4] = raw[0::2]
        out[2::4] = raw[0::2]
        out[3::4] = raw[1::2]
        return bytes(out)
    if color_type == 3:
        if not palette:
            raise PngError("调色板 PNG 缺少 PLTE 块")
        if len(palette) % 3 != 0:
            raise PngError("PLTE 块长度不是 3 的倍数")
        entries = len(palette) // 3
        out = bytearray(pixels * 4)
        for i, index in enumerate(raw):
            if index >= entries:
                raise PngError(f"调色板索引 {index} 越界（共 {entries} 个颜色）")
            alpha = transparency[index] if transparency and index < len(transparency) else 0xFF
            base = i * 4
            out[base] = palette[index * 3]
            out[base + 1] = palette[index * 3 + 1]
            out[base + 2] = palette[index * 3 + 2]
            out[base + 3] = alpha
        return bytes(out)
    raise PngError(f"不支持的颜色类型 {color_type}")


def decode_png(data: bytes) -> Raster:
    """解码 PNG 字节串为 RGBA8 光栅。"""
    if not isinstance(data, (bytes, bytearray)):
        raise PngError("PNG 输入必须是字节串")
    if not data.startswith(PNG_SIGNATURE):
        raise PngError("不是 PNG 文件（magic 不匹配）")
    pos = len(PNG_SIGNATURE)
    idat = bytearray()
    palette: bytes | None = None
    transparency: bytes | None = None
    ihdr: tuple[int, int, int, int, int, int, int] | None = None
    saw_iend = False
    while pos < len(data):
        if pos + 8 > len(data):
            raise PngError("PNG 截断：块头不完整")
        length = int.from_bytes(data[pos : pos + 4], "big")
        chunk_type = data[pos + 4 : pos + 8]
        payload_start = pos + 8
        payload_end = payload_start + length
        crc_end = payload_end + 4
        if crc_end > len(data):
            raise PngError(f"PNG 截断：块 {chunk_type!r} 声明长度 {length} 超出文件")
        payload = data[payload_start:payload_end]
        expected_crc = int.from_bytes(data[payload_end:crc_end], "big")
        actual_crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            raise PngError(f"PNG 块 {chunk_type.decode('latin-1')} CRC 校验失败（数据损坏）")
        pos = crc_end
        if chunk_type == b"IHDR":
            if length != 13:
                raise PngError(f"IHDR 长度应为 13，实际 {length}")
            ihdr = struct.unpack(">IIBBBBB", payload)
        elif chunk_type == b"PLTE":
            palette = payload
        elif chunk_type == b"tRNS":
            transparency = payload
        elif chunk_type == b"IDAT":
            idat += payload
        elif chunk_type == b"IEND":
            saw_iend = True
            break
        elif chunk_type not in _KNOWN_CRITICAL and 65 <= chunk_type[0] <= 90:
            raise PngError(f"未知的关键块 {chunk_type.decode('latin-1')}")
    if ihdr is None:
        raise PngError("PNG 缺少 IHDR 块")
    if not saw_iend:
        raise PngError("PNG 缺少 IEND 块（文件被截断）")
    if not idat:
        raise PngError("PNG 缺少 IDAT 块（没有像素数据）")
    width, height, bit_depth, color_type, compression, filter_method, interlace = ihdr
    if width == 0 or height == 0:
        raise PngError(f"PNG 尺寸非法：{width}×{height}")
    if compression != 0:
        raise PngError(f"不支持的 PNG 压缩方式 {compression}")
    if filter_method != 0:
        raise PngError(f"不支持的 PNG 过滤方式 {filter_method}")
    if interlace != 0:
        raise PngError("不支持隔行（Adam7）PNG；重放器输出应为非隔行")
    if color_type not in _COLOR_CHANNELS:
        raise PngError(f"不支持的颜色类型 {color_type}")
    if bit_depth != 8:
        raise PngError(
            f"仅支持 8-bit PNG，实际 bit_depth={bit_depth}（color_type={color_type}）"
        )
    channels = _COLOR_CHANNELS[color_type]
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise PngError(f"PNG 像素数据 zlib 解压失败：{exc}") from exc
    stride = width * channels
    expected = height * (1 + stride)
    if len(raw) != expected:
        raise PngError(
            f"PNG 像素数据长度 {len(raw)} 与预期 {expected}（{width}×{height}, "
            f"{channels} 通道）不一致"
        )
    unfiltered = _unfilter(raw, height, stride, channels)
    rgba = _to_rgba(unfiltered, width, height, color_type, palette, transparency)
    return Raster(width, height, rgba)


def read_png(path) -> Raster:
    """从文件读取 PNG。IO 错误直接抛 OSError，由调用方转成可读的失败原因。"""
    with open(path, "rb") as handle:
        return decode_png(handle.read())


def _chunk(chunk_type: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
    return (
        len(payload).to_bytes(4, "big")
        + chunk_type
        + payload
        + crc.to_bytes(4, "big")
    )


def encode_png(raster: Raster, *, compress_level: int = 6) -> bytes:
    """把 RGBA8 光栅编码成 PNG（filter 0，单 IDAT）。"""
    if not isinstance(raster, Raster):
        raise PngError("encode_png 需要 Raster")
    raw = bytearray()
    stride = raster.width * 4
    for y in range(raster.height):
        raw.append(0)  # filter type 0 (None)
        raw += raster.data[y * stride : (y + 1) * stride]
    ihdr = struct.pack(
        ">IIBBBBB", raster.width, raster.height, 8, 6, 0, 0, 0
    )
    return b"".join(
        (
            PNG_SIGNATURE,
            _chunk(b"IHDR", ihdr),
            _chunk(b"IDAT", zlib.compress(bytes(raw), compress_level)),
            _chunk(b"IEND", b""),
        )
    )


def write_png(path, raster: Raster, *, compress_level: int = 6) -> None:
    path.write_bytes(encode_png(raster, compress_level=compress_level))
