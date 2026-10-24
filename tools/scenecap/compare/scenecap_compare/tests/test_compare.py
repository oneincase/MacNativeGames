"""scenecap_compare 的单元测试套件（由 tests/test_scenecap_compare.py 薄入口接入 make check）。

全部用例用本模块自造的 PNG + index.json，不依赖 capture/replay 等其他任务的产物。
"""

from __future__ import annotations

import io
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scenecap_compare import (
    BASELINE_SCHEMA,
    REPLAY_SCHEMA,
    PngError,
    Raster,
    Thresholds,
    compare_dirs,
    compare_float_buffers,
    compare_rasters,
    decode_png,
    encode_png,
    load_replay_dir,
    main,
    read_png,
    write_png,
)
from scenecap_compare import png_codec

TOOL_ROOT = Path(__file__).resolve().parents[2]  # tools/scenecap/compare
ENTRY = TOOL_ROOT / "scenecap_diff.py"


# --------------------------------------------------------------------------
# 测试辅助：构造光栅与重放目录
# --------------------------------------------------------------------------


def solid(width: int, height: int, rgba: tuple[int, int, int, int]) -> Raster:
    return Raster(width, height, bytes(rgba) * (width * height))


def gradient(width: int, height: int) -> Raster:
    data = bytearray()
    for y in range(height):
        for x in range(width):
            data += bytes(
                (
                    (x * 7 + y * 3) % 256,
                    (x * 11 + y * 5) % 256,
                    (x * 13 + y * 17) % 256,
                    255,
                )
            )
    return Raster(width, height, bytes(data))


def with_pixel(raster: Raster, x: int, y: int, rgba: tuple[int, int, int, int]) -> Raster:
    data = bytearray(raster.data)
    offset = (y * raster.width + x) * 4
    data[offset : offset + 4] = bytes(rgba)
    return Raster(raster.width, raster.height, bytes(data))


def build_replay(
    directory: Path,
    passes: list[dict],
    *,
    schema: str = REPLAY_SCHEMA,
) -> Path:
    """写一个符合 t3 契约的重放输出目录。passes 里每项给 index/slug/raster。"""
    directory.mkdir(parents=True, exist_ok=True)
    entries = []
    for spec in passes:
        raster: Raster = spec["raster"]
        name = f"pass_{spec['index']:03d}_{spec['slug']}.png"
        write_png(directory / name, raster)
        entries.append(
            {
                "index": spec["index"],
                "slug": spec["slug"],
                "kind": spec.get("kind", "render"),
                "width": spec.get("width", raster.width),
                "height": spec.get("height", raster.height),
                "pixel_format": spec.get("pixel_format", "RGBA8Unorm"),
                "encoding": spec.get("encoding", "linear"),
                "png": spec.get("png", name),
                "draws": spec.get("draws", 3),
                "status": spec.get("status", "ok"),
            }
        )
    index = {
        "schema": schema,
        "package": {
            "path": "synthetic-scene.nvs",
            "sha256": "0123456789abcdef" * 4,
            "captured_frame": 42,
        },
        "device": {"name": "synthetic-metal", "metal_version": "3.1"},
        "passes": entries,
        "unsupported": [],
        "errors": [],
    }
    (directory / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return directory


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def srgb_code_for_linear(linear_code: int) -> int:
    """给定线性 8-bit 码值，返回经 sRGB 编码后的 8-bit 码值。"""
    value = linear_code / 255.0
    if value <= 0.0031308:
        encoded = value * 12.92
    else:
        encoded = 1.055 * (value ** (1 / 2.4)) - 0.055
    return int(round(255 * encoded))


# --------------------------------------------------------------------------
# 1. PNG 编解码（纯标准库自洽 + 可选 Pillow 交叉验证）
# --------------------------------------------------------------------------


class PngCodecTests(unittest.TestCase):
    def test_roundtrip_rgba(self) -> None:
        raster = gradient(23, 17)
        decoded = decode_png(encode_png(raster))
        self.assertEqual(decoded, raster)

    def test_solid_color_roundtrip(self) -> None:
        raster = solid(3, 2, (12, 34, 56, 78))
        self.assertEqual(decode_png(encode_png(raster)), raster)

    def test_crc_corruption_rejected(self) -> None:
        payload = bytearray(encode_png(gradient(8, 8)))
        # 破坏 IDAT 数据区里的一个字节（保持在块内），CRC 必须报错。
        payload[50] ^= 0xFF
        with self.assertRaises(PngError):
            decode_png(bytes(payload))

    def test_rejects_16bit(self) -> None:
        ihdr = (2, 2, 16, 6, 0, 0, 0)
        import struct

        data = (
            png_codec.PNG_SIGNATURE
            + png_codec._chunk(b"IHDR", struct.pack(">IIBBBBB", *ihdr))
            + png_codec._chunk(b"IDAT", png_codec.zlib.compress(b"\x00" * 16))
            + png_codec._chunk(b"IEND", b"")
        )
        with self.assertRaises(PngError):
            decode_png(data)

    def test_rejects_interlaced(self) -> None:
        import struct

        ihdr = (2, 2, 8, 6, 0, 0, 1)
        data = (
            png_codec.PNG_SIGNATURE
            + png_codec._chunk(b"IHDR", struct.pack(">IIBBBBB", *ihdr))
            + png_codec._chunk(b"IDAT", png_codec.zlib.compress(b"\x00" * 20))
            + png_codec._chunk(b"IEND", b"")
        )
        with self.assertRaises(PngError):
            decode_png(data)

    def test_gray_and_rgb_expand_to_rgba(self) -> None:
        import struct

        # 2x1 灰度图：像素 0 与 200
        gray_raw = b"\x00" + bytes([0, 200])
        gray = (
            png_codec.PNG_SIGNATURE
            + png_codec._chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 1, 8, 0, 0, 0, 0))
            + png_codec._chunk(b"IDAT", png_codec.zlib.compress(gray_raw))
            + png_codec._chunk(b"IEND", b"")
        )
        raster = decode_png(gray)
        self.assertEqual(raster.data, bytes([0, 0, 0, 255, 200, 200, 200, 255]))

        rgb_raw = b"\x00" + bytes([1, 2, 3, 4, 5, 6])
        rgb = (
            png_codec.PNG_SIGNATURE
            + png_codec._chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 1, 8, 2, 0, 0, 0))
            + png_codec._chunk(b"IDAT", png_codec.zlib.compress(rgb_raw))
            + png_codec._chunk(b"IEND", b"")
        )
        raster = decode_png(rgb)
        self.assertEqual(raster.data, bytes([1, 2, 3, 255, 4, 5, 6, 255]))

    def test_pillow_cross_check_when_available(self) -> None:
        try:
            from PIL import Image  # type: ignore
        except ImportError:  # pragma: no cover - 可选依赖
            self.skipTest("Pillow 未安装，跳过交叉验证")
        raster = gradient(19, 11)
        # 我们的编码 -> Pillow 解码
        import io as _io

        image = Image.open(_io.BytesIO(encode_png(raster)))
        reference = image.convert("RGBA").tobytes()
        self.assertEqual(reference, raster.data)
        # Pillow 编码（filter 多种）-> 我们的解码
        buffer = _io.BytesIO()
        Image.frombytes("RGBA", (raster.width, raster.height), raster.data).save(
            buffer, format="PNG", optimize=True
        )
        self.assertEqual(decode_png(buffer.getvalue()), raster)


# --------------------------------------------------------------------------
# 2. 比较度量与判定（任务要求的 6 类核心用例）
# --------------------------------------------------------------------------


class CompareTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _pair(self, base_passes, cand_passes) -> tuple[Path, Path]:
        return (
            build_replay(self.root / "base", base_passes),
            build_replay(self.root / "cand", cand_passes),
        )

    def test_identical_images_pass(self) -> None:
        raster = gradient(32, 24)
        base, cand = self._pair(
            [{"index": 0, "slug": "main", "raster": raster}],
            [{"index": 0, "slug": "main", "raster": raster}],
        )
        result = compare_dirs(base, cand)
        self.assertTrue(result.passed)
        self.assertIsNone(result.first_failure)
        metrics = result.passes[0].metrics
        self.assertEqual(metrics.max_channel_error, 0.0)
        self.assertEqual(metrics.psnr_db, math.inf)
        self.assertEqual(metrics.ssim, 1.0)

    def test_single_pixel_small_change_within_threshold(self) -> None:
        raster = solid(64, 64, (10, 20, 30, 255))
        changed = with_pixel(raster, 5, 7, (11, 20, 30, 255))  # +1 码值
        base, cand = self._pair(
            [{"index": 0, "slug": "main", "raster": raster}],
            [{"index": 0, "slug": "main", "raster": changed}],
        )
        result = compare_dirs(base, cand, Thresholds(2.0, 0.001))
        self.assertTrue(result.passed, msg=str(result.passes[0].reasons))
        metrics = result.passes[0].metrics
        self.assertEqual(metrics.max_channel_error, 1.0)
        self.assertEqual(metrics.diff_pixels, 0)  # 严格大于阈值才算差异像素
        self.assertGreater(metrics.psnr_db, 40)

    def test_change_beyond_channel_threshold_fails(self) -> None:
        raster = solid(32, 32, (100, 100, 100, 255))
        changed = with_pixel(raster, 3, 3, (140, 100, 100, 255))  # +40 码值
        base, cand = self._pair(
            [{"index": 0, "slug": "main", "raster": raster}],
            [{"index": 0, "slug": "main", "raster": changed}],
        )
        result = compare_dirs(base, cand)
        self.assertFalse(result.passed)
        self.assertEqual(result.first_failure.index, 0)
        reasons = "；".join(result.passes[0].reasons)
        self.assertIn("最大通道误差", reasons)
        self.assertEqual(result.passes[0].metrics.max_channel_error, 40.0)

    def test_many_small_changes_fail_on_diff_ratio(self) -> None:
        raster = solid(16, 16, (60, 60, 60, 255))
        data = bytearray(raster.data)
        changed_pixels = 26  # 26/256 ≈ 10.2% >> 0.1%
        for pixel in range(changed_pixels):
            data[pixel * 4] = 61  # +1 码值，低于通道阈值
        changed = Raster(16, 16, bytes(data))
        base, cand = self._pair(
            [{"index": 0, "slug": "main", "raster": raster}],
            [{"index": 0, "slug": "main", "raster": changed}],
        )
        # 先收紧通道阈值，确保差异像素被计入
        result = compare_dirs(base, cand, Thresholds(0.5, 0.001))
        self.assertFalse(result.passed)
        reasons = "；".join(result.passes[0].reasons)
        self.assertIn("差异像素比例", reasons)
        metrics = result.passes[0].metrics
        self.assertAlmostEqual(metrics.diff_pixel_ratio, changed_pixels / 256.0, places=6)

    def test_size_mismatch_fails(self) -> None:
        base, cand = self._pair(
            [{"index": 0, "slug": "main", "raster": gradient(8, 8)}],
            [{"index": 0, "slug": "main", "raster": gradient(8, 7)}],
        )
        result = compare_dirs(base, cand)
        self.assertFalse(result.passed)
        self.assertIn("尺寸不一致", "；".join(result.passes[0].reasons))

    def test_declared_size_mismatch_fails(self) -> None:
        raster = gradient(8, 8)
        base = build_replay(
            self.root / "base",
            [{"index": 0, "slug": "main", "raster": raster}],
        )
        cand = build_replay(
            self.root / "cand",
            [{"index": 0, "slug": "main", "raster": raster, "width": 9, "height": 8}],
        )
        result = compare_dirs(base, cand)
        self.assertFalse(result.passed)
        self.assertIn("声明宽度", "；".join(result.passes[0].reasons))

    def test_missing_pass_fails(self) -> None:
        base, cand = self._pair(
            [
                {"index": 0, "slug": "main", "raster": gradient(8, 8)},
                {"index": 1, "slug": "ui", "raster": gradient(4, 4)},
            ],
            [{"index": 0, "slug": "main", "raster": gradient(8, 8)}],
        )
        result = compare_dirs(base, cand)
        self.assertFalse(result.passed)
        self.assertEqual(result.first_failure.index, 1)
        self.assertIn("候选缺少 pass #1", "；".join(result.passes[1].reasons))

    def test_extra_pass_in_candidate_fails(self) -> None:
        base, cand = self._pair(
            [{"index": 0, "slug": "main", "raster": gradient(8, 8)}],
            [
                {"index": 0, "slug": "main", "raster": gradient(8, 8)},
                {"index": 1, "slug": "water", "raster": gradient(4, 4)},
            ],
        )
        result = compare_dirs(base, cand)
        self.assertFalse(result.passed)
        self.assertIn("基线缺少 pass #1", "；".join(result.passes[1].reasons))

    def test_non_ok_status_fails(self) -> None:
        base, cand = self._pair(
            [{"index": 0, "slug": "main", "raster": gradient(8, 8)}],
            [
                {
                    "index": 0,
                    "slug": "main",
                    "raster": gradient(8, 8),
                    "status": "unsupported",
                }
            ],
        )
        result = compare_dirs(base, cand)
        self.assertFalse(result.passed)
        self.assertIn("状态", "；".join(result.passes[0].reasons))

    def test_pixel_format_mismatch_fails(self) -> None:
        raster = gradient(8, 8)
        base, cand = self._pair(
            [{"index": 0, "slug": "main", "raster": raster, "pixel_format": "RGBA8Unorm"}],
            [{"index": 0, "slug": "main", "raster": raster, "pixel_format": "BGRA8Unorm"}],
        )
        result = compare_dirs(base, cand)
        self.assertFalse(result.passed)
        self.assertIn("pixel_format", "；".join(result.passes[0].reasons))

    def test_srgb_and_linear_mixed_decode_to_same_linear_values(self) -> None:
        """同一线性场景：基线写 sRGB 编码，候选写 linear 编码 -> 必须判通过。"""
        linear_codes = [0, 1, 32, 64, 128, 217, 255]
        base_data = bytearray()
        cand_data = bytearray()
        for code in linear_codes:
            encoded = srgb_code_for_linear(code)
            base_data += bytes((encoded, encoded, encoded, 255))
            cand_data += bytes((code, code, code, 255))
        base_raster = Raster(len(linear_codes), 1, bytes(base_data))
        cand_raster = Raster(len(linear_codes), 1, bytes(cand_data))
        base, cand = self._pair(
            [{"index": 0, "slug": "main", "raster": base_raster, "encoding": "srgb"}],
            [{"index": 0, "slug": "main", "raster": cand_raster, "encoding": "linear"}],
        )
        result = compare_dirs(base, cand, Thresholds(2.0, 0.001))
        self.assertTrue(result.passed, msg=str(result.passes[0].reasons))
        metrics = result.passes[0].metrics
        self.assertLessEqual(metrics.max_channel_error, 1.1)
        self.assertEqual(metrics.diff_pixels, 0)
        self.assertIn("两侧 encoding 不同", "；".join(result.passes[0].notes))

    def test_same_bytes_different_encoding_must_differ(self) -> None:
        """相同字节若按不同 encoding 解释，线性值差异巨大，必须判失败。"""
        raster = solid(4, 1, (128, 128, 128, 255))
        base, cand = self._pair(
            [{"index": 0, "slug": "main", "raster": raster, "encoding": "srgb"}],
            [{"index": 0, "slug": "main", "raster": raster, "encoding": "linear"}],
        )
        result = compare_dirs(base, cand)
        self.assertFalse(result.passed)
        self.assertGreater(result.passes[0].metrics.max_channel_error, 70)

    def test_threshold_equals_delta_passes_strictly(self) -> None:
        raster = solid(8, 8, (50, 50, 50, 255))
        changed = with_pixel(raster, 0, 0, (52, 50, 50, 255))  # 恰好 2
        base, cand = self._pair(
            [{"index": 0, "slug": "main", "raster": raster}],
            [{"index": 0, "slug": "main", "raster": changed}],
        )
        result = compare_dirs(base, cand, Thresholds(2.0, 0.001))
        self.assertTrue(result.passed)
        self.assertEqual(result.passes[0].metrics.diff_pixels, 0)


class MetricsUnitTests(unittest.TestCase):
    def test_psnr_and_ssim_on_known_values(self) -> None:
        a = solid(8, 8, (0, 0, 0, 255))
        b = solid(8, 8, (0, 0, 0, 255))
        metrics = compare_rasters(a, b, Thresholds())
        self.assertEqual(metrics.psnr_db, math.inf)
        self.assertEqual(metrics.ssim, 1.0)

    def test_float_buffer_nan_inf_counting(self) -> None:
        base = [0.5, 0.5, 0.5, 1.0, 0.25, 0.25, 0.25, 1.0]
        cand = [0.5, float("nan"), 0.5, 1.0, float("inf"), 0.25, 0.25, 1.0]
        metrics = compare_float_buffers(base, cand, Thresholds(), width=2, height=1)
        self.assertEqual(metrics.nan_pixels, 1)
        self.assertEqual(metrics.inf_pixels, 1)
        self.assertEqual(metrics.diff_pixels, 2)
        reasons = "；".join(metrics.failures(Thresholds()))
        self.assertIn("NaN", reasons)
        self.assertIn("Inf", reasons)

    def test_float_buffer_equal_passes(self) -> None:
        values = [0.1, 0.2, 0.3, 1.0] * 4
        metrics = compare_float_buffers(values, list(values), Thresholds(), width=2, height=2)
        self.assertEqual(metrics.max_channel_error, 0.0)
        self.assertEqual(metrics.nan_pixels, 0)

    def test_threshold_validation(self) -> None:
        from scenecap_compare import InputError

        with self.assertRaises(InputError):
            Thresholds(-1.0, 0.001)
        with self.assertRaises(InputError):
            Thresholds(2.0, 1.5)


# --------------------------------------------------------------------------
# 3. 命令行、报告、差异图与退出码
# --------------------------------------------------------------------------


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.base = build_replay(
            self.root / "base",
            [
                {"index": 0, "slug": "main", "raster": gradient(16, 12)},
                {"index": 1, "slug": "ui", "raster": solid(8, 8, (40, 50, 60, 255))},
            ],
        )
        self.cand = build_replay(
            self.root / "cand",
            [
                {"index": 0, "slug": "main", "raster": gradient(16, 12)},
                {"index": 1, "slug": "ui", "raster": solid(8, 8, (40, 50, 60, 255))},
            ],
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_pass_exit_zero_and_report(self) -> None:
        report = self.root / "report.md"
        code, out, err = run_cli([str(self.base), str(self.cand), "-o", str(report)])
        self.assertEqual(code, 0, msg=err)
        self.assertIn("通过", out)
        text = report.read_text(encoding="utf-8")
        self.assertIn(str(self.base), text)
        self.assertIn(str(self.cand), text)
        self.assertIn("阈值", text)
        self.assertIn("✅ 通过", text)
        self.assertIn("main", text)
        self.assertIn("ui", text)
        self.assertTrue((self.root / "report.json").is_file())
        payload = json.loads((self.root / "report.json").read_text(encoding="utf-8"))
        self.assertTrue(payload["passed"])
        self.assertEqual(len(payload["passes"]), 2)

    def test_fail_exit_one_and_first_failure_in_report(self) -> None:
        changed = build_replay(
            self.root / "cand_bad",
            [
                {"index": 0, "slug": "main", "raster": gradient(16, 12)},
                {
                    "index": 1,
                    "slug": "ui",
                    "raster": solid(8, 8, (140, 50, 60, 255)),
                },
            ],
        )
        report = self.root / "fail.md"
        code, out, err = run_cli([str(self.base), str(changed), "-o", str(report)])
        self.assertEqual(code, 1, msg=err)
        self.assertIn("失败", out)
        text = report.read_text(encoding="utf-8")
        self.assertIn("首个失败 pass", text)
        self.assertIn("#1 ui", text)
        self.assertIn("❌ 失败", text)

    def test_diff_images_written_and_marked(self) -> None:
        changed = build_replay(
            self.root / "cand_bad2",
            [
                {"index": 0, "slug": "main", "raster": gradient(16, 12)},
                {"index": 1, "slug": "ui", "raster": solid(8, 8, (200, 50, 60, 255))},
            ],
        )
        report = self.root / "diff.md"
        code, _, _ = run_cli([str(self.base), str(changed), "-o", str(report), "--all-diffs"])
        self.assertEqual(code, 1)
        diff_dir = self.root / "diff.diff"
        images = sorted(diff_dir.glob("diff_*.png"))
        self.assertEqual(len(images), 2)
        marked = read_png(diff_dir / "diff_001_ui.png")
        self.assertEqual((marked.width, marked.height), (8, 8))
        data = marked.data
        hot = sum(1 for i in range(0, len(data), 4) if data[i] == 255 and data[i + 2] == 0)
        self.assertEqual(hot, 64)  # 8x8 全为超阈值像素
        text = report.read_text(encoding="utf-8")
        self.assertIn("diff_001_ui.png", text)

    def test_cli_threshold_override(self) -> None:
        small = with_pixel(gradient(8, 8), 0, 0, (1, 1, 1, 255))
        cand = build_replay(
            self.root / "cand_small",
            [{"index": 0, "slug": "main", "raster": small}],
        )
        base = build_replay(
            self.root / "base_one",
            [{"index": 0, "slug": "main", "raster": gradient(8, 8)}],
        )
        report = self.root / "override.md"
        code, _, _ = run_cli(
            [
                str(base),
                str(cand),
                "-o",
                str(report),
                "--max-channel-delta",
                "0",
                "--max-diff-pixel-ratio",
                "0.001",
            ]
        )
        self.assertEqual(code, 1)
        payload = json.loads((self.root / "override.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["thresholds"]["max_channel_delta"], 0.0)
        self.assertFalse(payload["passed"])

    def test_input_error_exit_two(self) -> None:
        report = self.root / "bad.md"
        code, _, err = run_cli([str(self.root / "nope"), str(self.cand), "-o", str(report)])
        self.assertEqual(code, 2)
        self.assertIn("错误", err)

    def test_bad_schema_exit_two(self) -> None:
        bad = self.root / "bad_schema"
        bad.mkdir()
        (bad / "index.json").write_text(json.dumps({"schema": "other/1"}), encoding="utf-8")
        code, _, err = run_cli([str(bad), str(self.cand), "-o", str(self.root / "x.md")])
        self.assertEqual(code, 2)
        self.assertIn("schema", err)

    def test_wrong_arg_count_exit_two(self) -> None:
        code, _, err = run_cli([str(self.base)])
        self.assertEqual(code, 2)
        self.assertIn("两个目录", err)

    def test_missing_index_json_exit_two(self) -> None:
        empty = self.root / "empty"
        empty.mkdir()
        code, _, err = run_cli([str(empty), str(self.cand), "-o", str(self.root / "y.md")])
        self.assertEqual(code, 2)
        self.assertIn("index.json", err)

    def test_entry_script_runs_standalone(self) -> None:
        report = self.root / "scripted.md"
        proc = subprocess.run(
            [sys.executable, str(ENTRY), str(self.base), str(self.cand), "-o", str(report)],
            capture_output=True,
            text=True,
            cwd=str(self.root),
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertTrue(report.is_file())
        self.assertIn("通过", proc.stdout)

    def test_module_entry_runs(self) -> None:
        report = self.root / "mod.md"
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "scenecap_compare",
                str(self.base),
                str(self.cand),
                "-o",
                str(report),
            ],
            capture_output=True,
            text=True,
            cwd=str(TOOL_ROOT),
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertTrue(report.is_file())

    def test_load_replay_dir_rejects_duplicate_index(self) -> None:
        duplicate = self.root / "dup"
        duplicate.mkdir()
        entry = {
            "index": 0,
            "slug": "main",
            "width": 1,
            "height": 1,
            "pixel_format": "RGBA8Unorm",
            "encoding": "linear",
            "png": "pass_000_main.png",
            "status": "ok",
        }
        write_png(duplicate / "pass_000_main.png", solid(1, 1, (0, 0, 0, 255)))
        (duplicate / "index.json").write_text(
            json.dumps({"schema": REPLAY_SCHEMA, "passes": [entry, dict(entry)]}),
            encoding="utf-8",
        )
        with self.assertRaises(Exception) as ctx:
            load_replay_dir(duplicate)
        self.assertIn("重复", str(ctx.exception))

    def test_png_path_traversal_rejected(self) -> None:
        evil = build_replay(
            self.root / "evil",
            [{"index": 0, "slug": "main", "raster": gradient(4, 4), "png": "../outside.png"}],
        )
        write_png(self.root / "outside.png", gradient(4, 4))
        code, _, err = run_cli([str(self.base), str(evil), "-o", str(self.root / "t.md")])
        self.assertEqual(code, 2)
        self.assertIn("越出目录", err)

    def test_missing_png_judged_as_failure(self) -> None:
        broken = build_replay(
            self.root / "broken",
            [{"index": 0, "slug": "main", "raster": gradient(8, 8)}],
        )
        (broken / "pass_000_main.png").unlink()
        code, out, _ = run_cli([str(broken), str(broken), "-o", str(self.root / "m.md")])
        self.assertEqual(code, 1)
        self.assertIn("缺少 PNG", out)

    def test_no_json_and_no_diff_images(self) -> None:
        report = self.root / "plain.md"
        code, _, err = run_cli(
            [
                str(self.base),
                str(self.cand),
                "-o",
                str(report),
                "--no-json",
                "--no-diff-images",
            ]
        )
        self.assertEqual(code, 0, msg=err)
        self.assertTrue(report.is_file())
        self.assertFalse((self.root / "plain.json").exists())
        self.assertFalse((self.root / "plain.diff").exists())

    def test_custom_diff_dir(self) -> None:
        custom = self.root / "custom-diffs"
        report = self.root / "c.md"
        code, _, _ = run_cli(
            [str(self.base), str(self.cand), "-o", str(report), "--diff-dir", str(custom), "--all-diffs"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(list(custom.glob("diff_*.png"))), 2)


# --------------------------------------------------------------------------
# 4. 基线模式
# --------------------------------------------------------------------------


class BaselineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.good = build_replay(
            self.root / "good",
            [{"index": 0, "slug": "main", "raster": gradient(12, 10)}],
        )
        self.baseline = self.root / "baseline"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_write_then_regress(self) -> None:
        report = self.root / "b1.md"
        code, out, err = run_cli(
            ["--baseline", str(self.baseline), str(self.good), "-o", str(report)]
        )
        self.assertEqual(code, 0, msg=err)
        self.assertIn("已写入基线", out)
        marker = json.loads((self.baseline / "baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(marker["schema"], BASELINE_SCHEMA)
        self.assertIn("index.json", marker["snapshot"])
        self.assertTrue((self.baseline / "index.json").is_file())

        # 相同候选 -> 通过
        code, _, err = run_cli(
            ["--baseline", str(self.baseline), str(self.good), "-o", str(self.root / "b2.md")]
        )
        self.assertEqual(code, 0, msg=err)

        # 有差异 -> 失败且退出码 1
        bad = build_replay(
            self.root / "bad",
            [{"index": 0, "slug": "main", "raster": solid(12, 10, (255, 255, 255, 255))}],
        )
        code, out, _ = run_cli(
            ["--baseline", str(self.baseline), str(bad), "-o", str(self.root / "b3.md")]
        )
        self.assertEqual(code, 1)
        self.assertIn("失败", out)

    def test_corrupted_baseline_exit_two(self) -> None:
        run_cli(["--baseline", str(self.baseline), str(self.good), "-o", str(self.root / "c1.md")])
        target = self.baseline / "pass_000_main.png"
        data = bytearray(target.read_bytes())
        data[-5] ^= 0xFF
        target.write_bytes(bytes(data))
        code, _, err = run_cli(
            ["--baseline", str(self.baseline), str(self.good), "-o", str(self.root / "c2.md")]
        )
        self.assertEqual(code, 2)
        self.assertIn("SHA-256", err)

    def test_update_baseline(self) -> None:
        run_cli(["--baseline", str(self.baseline), str(self.good), "-o", str(self.root / "u1.md")])
        other = build_replay(
            self.root / "other",
            [{"index": 0, "slug": "main", "raster": solid(12, 10, (9, 9, 9, 255))}],
        )
        code, out, _ = run_cli(
            [
                "--baseline",
                str(self.baseline),
                str(other),
                "--update-baseline",
                "-o",
                str(self.root / "u2.md"),
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("已更新基线", out)
        code, _, _ = run_cli(
            ["--baseline", str(self.baseline), str(other), "-o", str(self.root / "u3.md")]
        )
        self.assertEqual(code, 0)

    def test_refuse_outputs_inside_baseline(self) -> None:
        # 报告写进基线目录会破坏“基线自包含 + 清单校验”，必须在写入前拒绝。
        report = self.baseline / "report.md"
        code, _, err = run_cli(
            ["--baseline", str(self.baseline), str(self.good), "-o", str(report)]
        )
        self.assertEqual(code, 2)
        self.assertIn("基线目录", err)
        self.assertFalse(self.baseline.exists())

    def test_refuse_non_baseline_occupied_dir(self) -> None:
        self.baseline.mkdir()
        (self.baseline / "keep.txt").write_text("not a baseline", encoding="utf-8")
        code, _, err = run_cli(
            ["--baseline", str(self.baseline), str(self.good), "-o", str(self.root / "o1.md")]
        )
        self.assertEqual(code, 2)
        self.assertIn("非空", err)

    def test_baseline_layout_is_self_contained(self) -> None:
        run_cli(["--baseline", str(self.baseline), str(self.good), "-o", str(self.root / "o2.md")])
        names = sorted(path.name for path in self.baseline.iterdir())
        self.assertIn("baseline.json", names)
        self.assertIn("index.json", names)
        self.assertIn("pass_000_main.png", names)
        # 基线目录本身就是一个合法的重放目录（t3 契约）
        loaded = load_replay_dir(self.baseline)
        self.assertEqual(sorted(loaded.passes), [0])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
