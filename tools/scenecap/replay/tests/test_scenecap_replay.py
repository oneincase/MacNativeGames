#!/usr/bin/env python3
"""Tests for scenecap_replay, the offline Metal replayer.

The suite drives the real binary: it generates the synthetic package with scenecap_synth.py, replays it and decodes the
PNGs the replayer wrote.  Pixel expectations are computed here from the documented transfer functions instead of being
copied from the replayer, so a wrong sRGB encode or a swapped BGRA channel fails the test.

Parts that need a Metal device are skipped on hosts without one; package validation, the C reader probe and the
failure paths keep running everywhere.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

REPLAY_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPLAY_DIR))
sys.path.insert(0, str(REPO_ROOT / "tools" / "scenecap" / "python"))

import scenecap_pkg  # noqa: E402
import scenecap_synth  # noqa: E402

REPLAY = os.environ.get("SCENECAP_REPLAY") or str(REPLAY_DIR / "build" / "scenecap_replay")
PROBE = os.environ.get("SCENECAP_PROBE") or str(REPLAY_DIR / "build" / "nvs_reader_probe")
WITH_LIBZSTD = os.environ.get("SCENECAP_WITH_LIBZSTD", "1") != "0"


# ------------------------------------------------------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------------------------------------------------------


def run_replay(package: str, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run([REPLAY, package, *arguments], capture_output=True, text=True, timeout=120)


def run_replay_into(package: str, output: str) -> subprocess.CompletedProcess:
    return run_replay(package, "-o", output)


def _srgb_encode(value: float) -> float:
    if value <= 0.0:
        return 0.0
    if value >= 1.0:
        return 1.0
    if value <= 0.0031308:
        return value * 12.92
    return 1.055 * (value ** (1.0 / 2.4)) - 0.055


def expected_srgb8(linear: tuple[float, ...]) -> tuple[int, int, int, int]:
    """Bytes an sRGB render target holds for these linear channels (alpha stays linear)."""
    result = []
    for index, value in enumerate(linear):
        encoded = value if index == 3 else _srgb_encode(value)
        result.append(max(0, min(255, round(encoded * 255.0))))
    return tuple(result)


def read_png(path: str | Path) -> tuple[int, int, bytes]:
    """Decodes an 8-bit non-interlaced RGBA PNG (the only variant the replayer writes)."""
    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError(f"{path}: not a PNG")
    position = 8
    width = height = None
    idat = bytearray()
    while position < len(data):
        length = struct.unpack(">I", data[position : position + 4])[0]
        kind = data[position + 4 : position + 8]
        payload = data[position + 8 : position + 8 + length]
        if kind == b"IHDR":
            width, height, depth, color_type, _compression, _filter, interlace = struct.unpack(">IIBBBBB", payload)
            if (depth, color_type, interlace) != (8, 6, 0):
                raise AssertionError(f"{path}: unexpected IHDR {(depth, color_type, interlace)}")
        elif kind == b"IDAT":
            idat.extend(payload)
        elif kind == b"IEND":
            break
        position += 12 + length
    if width is None or height is None:
        raise AssertionError(f"{path}: no IHDR")
    raw = zlib.decompress(bytes(idat))
    stride = width * 4
    rows = []
    position = 0
    for _ in range(height):
        filter_type = raw[position]
        position += 1
        row = bytearray(raw[position : position + stride])
        position += stride
        if filter_type != 0:
            raise AssertionError(f"{path}: filter type {filter_type} not written by scenecap_replay")
        rows.append(row)
    return width, height, b"".join(rows)


def png_pixel(rgba: bytes, width: int, x: int, y: int) -> tuple[int, int, int, int]:
    offset = (y * width + x) * 4
    return tuple(rgba[offset : offset + 4])


def raw_manifest_bytes(path: str | Path) -> bytes:
    """Decodes the manifest region of a package independently of the C reader (used to check the probe)."""
    data = Path(path).read_bytes()
    fields = struct.unpack("<8sHHIIIIQQQQQ32s28s", data[: scenecap_pkg.HEADER_SIZE])
    flags = fields[4]
    manifest_offset = fields[8]
    manifest_stored = fields[9]
    manifest_raw = fields[10]
    stored = data[manifest_offset : manifest_offset + manifest_stored]
    if flags & 0x1:
        return scenecap_pkg._zstd_decompress(stored, manifest_raw, "manifest")
    return stored


def write_package(path: str, **options: object) -> None:
    """Writes a synthetic package, forcing verbatim storage on a WITH_LIBZSTD=0 build.

    Such a build cannot decode real zstd frames, and the suite must stay green there: verbatim storage exercises the
    same replay path, while the tests that genuinely need zstd skip themselves.
    """
    if not WITH_LIBZSTD:
        options.setdefault("raw", True)
    scenecap_synth.write_synth_package(path, **options)


def read_index(output: str) -> dict:
    with open(os.path.join(output, "index.json"), "r", encoding="utf-8") as handle:
        return json.load(handle)


@functools.lru_cache(maxsize=1)
def metal_device_available() -> bool:
    """Runs the replayer once on the synthetic package and reports whether this host can render."""
    temporary = tempfile.mkdtemp(prefix="scenecap-gpu-probe-")
    try:
        package = os.path.join(temporary, "probe.nvs")
        write_package(package)
        completed = run_replay_into(package, os.path.join(temporary, "out"))
        if completed.returncode == 0:
            return True
        return "no Metal device" not in completed.stderr
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def require_metal(test_case: unittest.TestCase) -> None:
    if not Path(REPLAY).exists():
        test_case.fail(f"replayer binary {REPLAY} is missing (run make check, which builds it)")
    if not metal_device_available():
        raise unittest.SkipTest("this host exposes no Metal device")


class SynthFixture(unittest.TestCase):
    """Shared temporary directory with the synthetic packages."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.directory = tempfile.mkdtemp(prefix="scenecap-replay-")
        cls.package = os.path.join(cls.directory, "synth.nvs")
        # Without the libzstd bridge only verbatim packages are readable, so the shared fixture degrades to raw.
        write_package(cls.package)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.directory, ignore_errors=True)

    def output(self, name: str) -> str:
        path = os.path.join(self.directory, name)
        shutil.rmtree(path, ignore_errors=True)
        return path


# ------------------------------------------------------------------------------------------------------------------
# the happy path
# ------------------------------------------------------------------------------------------------------------------


class ReplayTests(SynthFixture):
    def test_validate_only_accepts_the_synthetic_package(self) -> None:
        completed = run_replay(self.package, "--validate-only")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("validate-only:", completed.stdout)

    def test_index_json_follows_the_replay_contract(self) -> None:
        require_metal(self)
        output = self.output("contract")
        completed = run_replay_into(self.package, output)
        self.assertEqual(completed.returncode, 0, completed.stderr)

        index = read_index(output)
        self.assertEqual(index["schema"], "scenecap.replay/1")
        self.assertEqual(set(index), {"schema", "package", "device", "passes", "unsupported", "errors"})
        package = index["package"]
        self.assertEqual(package["path"], self.package)
        self.assertRegex(package["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(package["captured_frame"], 0)
        self.assertEqual(package["game"], "scenecap-synth")
        self.assertEqual(package["build"], "synth-1")
        self.assertTrue(index["device"]["name"])
        self.assertTrue(index["device"]["metal_version"])
        self.assertEqual(index["unsupported"], [])
        self.assertEqual(index["errors"], [])

        self.assertEqual(len(index["passes"]), 1)
        entry = index["passes"][0]
        for key in ("index", "kind", "slug", "width", "height", "pixel_format", "encoding", "png", "draws", "status"):
            self.assertIn(key, entry)
        self.assertEqual(entry["index"], 0)
        self.assertEqual(entry["kind"], "render")
        self.assertEqual(entry["slug"], "main")
        self.assertEqual(entry["width"], scenecap_synth.WIDTH)
        self.assertEqual(entry["height"], scenecap_synth.HEIGHT)
        self.assertEqual(entry["pixel_format"], scenecap_synth.COLOR_FORMAT)
        self.assertEqual(entry["encoding"], "srgb")
        self.assertEqual(entry["draws"], 1)
        self.assertEqual(entry["status"], "ok")
        self.assertEqual(entry["png"], "pass_000_main.png")

        width, height, rgba = read_png(os.path.join(output, entry["png"]))
        self.assertEqual((width, height), (entry["width"], entry["height"]))

        # Per-pass metrics describe the very bytes the PNG stores, so a reviewer can sanity check a pass cheaply.
        metrics = entry["metrics"]
        self.assertEqual(set(metrics), {"pixels", "min", "max", "mean"})
        self.assertEqual(metrics["pixels"], width * height)
        for key in ("min", "max", "mean"):
            self.assertEqual(len(metrics[key]), 4)
        for channel in range(4):
            self.assertLessEqual(metrics["min"][channel], metrics["mean"][channel])
            self.assertLessEqual(metrics["mean"][channel], metrics["max"][channel])
        self.assertEqual(metrics["min"], [min(rgba[c::4]) for c in range(4)])
        self.assertEqual(metrics["max"], [max(rgba[c::4]) for c in range(4)])

    def test_pixels_match_the_synthetic_scene(self) -> None:
        require_metal(self)
        output = self.output("pixels")
        completed = run_replay_into(self.package, output)
        self.assertEqual(completed.returncode, 0, completed.stderr)

        index = read_index(output)
        width, height, rgba = read_png(os.path.join(output, index["passes"][0]["png"]))
        self.assertEqual((width, height), (scenecap_synth.WIDTH, scenecap_synth.HEIGHT))

        clear = expected_srgb8(scenecap_synth.CLEAR_LINEAR)
        for x, y in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
            self.assertEqual(png_pixel(rgba, width, x, y), clear, f"corner ({x}, {y}) must keep the clear colour")

        texel = expected_srgb8(tuple(channel / 255.0 for channel in scenecap_synth.TEXEL_LINEAR))
        centre = png_pixel(rgba, width, width // 2, height // 2)
        for observed, expected in zip(centre, texel):
            self.assertLessEqual(abs(observed - expected), 1, f"centre {centre} != {texel}")
        self.assertEqual(centre[3], 255, "the synthetic scene is opaque")

    def test_two_runs_are_byte_identical(self) -> None:
        require_metal(self)
        first = self.output("run1")
        second = self.output("run2")
        self.assertEqual(run_replay_into(self.package, first).returncode, 0)
        self.assertEqual(run_replay_into(self.package, second).returncode, 0)
        for name in ("index.json", "pass_000_main.png"):
            self.assertEqual(
                Path(first, name).read_bytes(),
                Path(second, name).read_bytes(),
                f"{name} changed between two replays of the same package",
            )

    def test_pipeline_cache_is_written_and_reused(self) -> None:
        require_metal(self)
        cache_dir = Path(self.package[: -len(".nvs")] + ".scenecap-cache")
        shutil.rmtree(cache_dir, ignore_errors=True)
        try:
            self.assertEqual(run_replay_into(self.package, self.output("cache1")).returncode, 0)
            self.assertTrue((cache_dir / "pipelines.metallib").is_file(), "the metallib archive was not written")
            metadata = json.loads((cache_dir / "cache.json").read_text())
            self.assertEqual(metadata["schema"], "scenecap.replay.cache/1")
            self.assertRegex(metadata["package_sha256"], r"^[0-9a-f]{64}$")
            self.assertGreaterEqual(metadata["pipelines"], 1)

            self.assertEqual(run_replay_into(self.package, self.output("cache2")).returncode, 0)
            reused = json.loads((cache_dir / "cache.json").read_text())
            self.assertEqual(reused["package_sha256"], metadata["package_sha256"])
            self.assertGreaterEqual(reused["pipelines"], 1)
        finally:
            shutil.rmtree(cache_dir, ignore_errors=True)

    def test_raw_storage_replays_identically(self) -> None:
        require_metal(self)
        if not WITH_LIBZSTD:
            self.skipTest("built with WITH_LIBZSTD=0: the zstd package cannot be read")
        raw_package = os.path.join(self.directory, "synth-raw.nvs")
        write_package(raw_package, raw=True)
        zstd_output = self.output("zstd")
        raw_output = self.output("raw")
        self.assertEqual(run_replay_into(self.package, zstd_output).returncode, 0)
        completed = run_replay_into(raw_package, raw_output)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            Path(zstd_output, "pass_000_main.png").read_bytes(),
            Path(raw_output, "pass_000_main.png").read_bytes(),
        )

    def test_indexed_draw_matches_a_plain_draw(self) -> None:
        require_metal(self)
        indexed = os.path.join(self.directory, "synth-indexed.nvs")
        write_package(indexed, indexed=True)
        plain_output = self.output("plain")
        indexed_output = self.output("indexed")
        self.assertEqual(run_replay_into(self.package, plain_output).returncode, 0)
        completed = run_replay_into(indexed, indexed_output)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            Path(plain_output, "pass_000_main.png").read_bytes(),
            Path(indexed_output, "pass_000_main.png").read_bytes(),
        )


# ------------------------------------------------------------------------------------------------------------------
# explicit failures
# ------------------------------------------------------------------------------------------------------------------


class FailureTests(SynthFixture):
    def test_compute_pass_is_reported_as_unsupported(self) -> None:
        require_metal(self)
        package = os.path.join(self.directory, "compute.nvs")
        write_package(package, with_compute_pass=True)
        output = self.output("compute")
        completed = run_replay_into(package, output)
        self.assertEqual(completed.returncode, 3, completed.stderr)
        index = read_index(output)
        self.assertEqual(len(index["unsupported"]), 1)
        entry = index["unsupported"][0]
        self.assertEqual(entry["pass"], 1)
        self.assertIn("compute", entry["reason"])
        self.assertEqual([item["index"] for item in index["passes"]], [0])

    def test_missing_blob_fails_and_names_the_resource(self) -> None:
        package = os.path.join(self.directory, "missing.nvs")
        write_package(package, omit=("blob.checker",))
        output = self.output("missing")
        completed = run_replay_into(package, output)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("blob.checker", completed.stderr)

        validate = run_replay(package, "--validate-only")
        self.assertNotEqual(validate.returncode, 0)
        self.assertIn("blob.checker", validate.stderr)

    def test_tampered_payload_fails_the_hash_check(self) -> None:
        tampered = os.path.join(self.directory, "tampered.nvs")
        shutil.copyfile(self.package, tampered)
        with scenecap_pkg.read_package(self.package) as package:
            texture = next(chunk for chunk in package.chunks if chunk.kind == "texture")
            offset = texture.offset
        data = bytearray(Path(tampered).read_bytes())
        data[offset + 3] ^= 0xFF
        Path(tampered).write_bytes(data)

        completed = run_replay_into(tampered, self.output("tampered"))
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("SHA-256", completed.stderr)
        self.assertIn(texture.id, completed.stderr)

    def test_unknown_pixel_format_fails_and_names_the_pass(self) -> None:
        package = os.path.join(self.directory, "bad-format.nvs")
        write_package(package, pixel_format="float64_exotic")
        completed = run_replay_into(package, self.output("bad-format"))
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("float64_exotic", completed.stderr)
        self.assertIn('pass "main"', completed.stderr)

    def test_shader_compile_failure_names_the_pipeline(self) -> None:
        require_metal(self)
        package = os.path.join(self.directory, "synth-broken-shader.nvs")
        write_package(package, broken_shader=True)
        completed = run_replay_into(package, self.output("broken-shader"))
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn('pipeline "pipeline.synth"', completed.stderr)
        self.assertIn("failed to compile", completed.stderr)
        self.assertIn("msl.synth", completed.stderr)

    def test_stage_without_reflection_is_refused_not_guessed(self) -> None:
        """A package may legally drop shader reflection, but then the replayer must say so instead of binding."""
        package = os.path.join(self.directory, "no-reflection.nvs")
        manifest = scenecap_synth.build_manifest()
        for pipeline in manifest["pipelines"]:
            for stage in pipeline["stages"]:
                stage.pop("reflection_blob", None)
        blobs = [blob for blob in scenecap_synth.build_blobs() if blob.kind != "reflection"]
        scenecap_pkg.write_package(
            package, manifest, blobs, generator="scenecap-test", compress=WITH_LIBZSTD
        )

        output = self.output("no-reflection")
        completed = run_replay_into(package, output)
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertIn("reflection_blob", completed.stderr)
        index = read_index(output)
        self.assertTrue(index["errors"])
        for entry in index["errors"]:
            self.assertEqual(entry["draw_id"], "draw.triangle")
            self.assertEqual(entry["pipeline_id"], "pipeline.synth")
            self.assertIn("reflection_blob", entry["message"])
        self.assertEqual(index["passes"], [], "refusing to bind must not render passes")

        validate = run_replay(package, "--validate-only")
        self.assertEqual(validate.returncode, 1)
        self.assertIn("reflection_blob", validate.stderr)

    def test_usage_errors_exit_two(self) -> None:
        self.assertEqual(subprocess.run([REPLAY], capture_output=True).returncode, 2)
        completed = subprocess.run([REPLAY, self.package], capture_output=True)
        self.assertEqual(completed.returncode, 2, "a replay without -o must be a usage error")
        completed = subprocess.run([REPLAY, self.package, "-o", self.output("x"), "--bogus"], capture_output=True)
        self.assertEqual(completed.returncode, 2)


# ------------------------------------------------------------------------------------------------------------------
# the C read side, driven directly
# ------------------------------------------------------------------------------------------------------------------


class ReaderProbeTests(SynthFixture):
    """Proves the reader inside scenecap_replay decodes Python-written packages byte for byte."""

    def probe(self, package: str) -> str:
        if not Path(PROBE).exists():
            self.fail(f"reader probe {PROBE} is missing (run make check, which builds it)")
        completed = subprocess.run([PROBE, package], capture_output=True, text=True, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed.stdout

    def parse(self, output: str) -> tuple[dict[str, dict], dict]:
        header = {}
        chunks = {}
        manifest = {}
        for line in output.splitlines():
            fields = line.split()
            if not fields:
                continue
            if fields[0] == "format=scenecap":
                header = dict(item.split("=", 1) for item in fields)
            elif fields[0] == "chunk":
                values = dict(item.split("=", 1) for item in fields[1:])
                chunks[values["id"]] = values
            elif fields[0] == "manifest":
                manifest = dict(item.split("=", 1) for item in fields[1:])
        return {"header": header, "chunks": chunks}, manifest

    def test_probe_reads_raw_and_zstd_packages(self) -> None:
        if not WITH_LIBZSTD:
            self.skipTest("built with WITH_LIBZSTD=0: real zstd frames cannot be decoded")
        zstd_package = os.path.join(self.directory, "probe-zstd.nvs")
        raw_package = os.path.join(self.directory, "probe-raw.nvs")
        scenecap_synth.write_synth_package(zstd_package)
        scenecap_synth.write_synth_package(raw_package, raw=True)

        zstd = self.parse(self.probe(zstd_package))
        raw = self.parse(self.probe(raw_package))
        self.assertEqual(zstd[0]["header"]["chunks"], raw[0]["header"]["chunks"])
        for label, package_path, parsed in (("zstd", zstd_package, zstd), ("raw", raw_package, raw)):
            manifest = raw_manifest_bytes(package_path)
            self.assertEqual(int(parsed[1]["bytes"]), len(manifest), f"{label}: manifest length")
            self.assertEqual(parsed[1]["sha256"], hashlib.sha256(manifest).hexdigest(), f"{label}: manifest digest")

        with scenecap_pkg.read_package(zstd_package) as package:
            expected = {chunk.id: (chunk.raw_size, hashlib.sha256(package.payload(chunk)).hexdigest()) for chunk in package.chunks}
        for chunk_id, (raw_size, digest) in expected.items():
            reported = zstd[0]["chunks"][chunk_id]
            self.assertEqual(int(reported["raw"]), raw_size, chunk_id)
            self.assertEqual(int(reported["bytes"]), raw_size, chunk_id)
            self.assertEqual(reported["sha256"], digest, f"chunk {chunk_id} decoded differently in C")

        # At least one chunk must actually have been stored as a zstd frame, otherwise this proves nothing.
        self.assertTrue(
            any(chunk["zstd"] == "1" for chunk in zstd[0]["chunks"].values()),
            "the synthetic zstd package stored no compressed chunk",
        )


if __name__ == "__main__":
    unittest.main()
