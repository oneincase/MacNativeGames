"""Tests for the scenecap .nvs container, its C module and the scenecap CLI.

Run directly (``python -m unittest discover -s tools/scenecap/tests``) or through the top-level ``make check``.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = TOOL_DIR.parents[1]
FORMAT_DIR = TOOL_DIR / "format"
PYTHON_DIR = TOOL_DIR / "python"
CLI = TOOL_DIR / "scenecap"

sys.path.insert(0, str(PYTHON_DIR))

import scenecap_pkg as sp  # noqa: E402

CC = shutil.which("cc")
CFLAGS = ["-std=c11", "-Wall", "-Wextra", "-Werror", "-fsanitize=undefined", "-fno-sanitize-recover=all"]


def _state() -> dict:
    return {
        "blend": {
            "enabled": True,
            "attachments": [
                {
                    "index": 0,
                    "enabled": True,
                    "rgb_operation": "add",
                    "rgb_source": "source_alpha",
                    "rgb_destination": "one_minus_source_alpha",
                    "alpha_operation": "add",
                    "alpha_source": "one",
                    "alpha_destination": "one_minus_source_alpha",
                }
            ],
        },
        "depth_stencil": {"depth_test": True, "depth_write": True, "depth_compare": "less", "stencil": None},
        "raster": {"cull": "back", "fill": "fill", "depth_bias": 0, "depth_slope_scale": 1.0, "depth_clamp": 0.0},
        "viewport": [0, 0, 64, 64],
        "scissor": [0, 0, 64, 64],
        "frontface": "ccw",
        "primitive_type": "triangle",
    }


def scene_manifest() -> dict:
    """A complete synthetic scene: render, compute and blit passes over two frames."""
    passes = [
        "pass/render",
        "pass/compute",
        "pass/blit",
    ]
    return {
        "capture": {
            "game": "synthetic",
            "title_id": "0100000000000001",
            "game_version": "1.0.0",
            "git_commit": "0123456789abcdef0123456789abcdef01234567",
            "port_version": "0.2.0",
            "build_version": "release-7-gdeadbeef",
            "gpu": "Apple M2",
            "macos": "15.2",
            "metal": "3.2",
            "frame_first": 0,
            "frame_count": 2,
            "captured_at": "2026-02-03T04:05:06Z",
        },
        "frames": [
            {
                "index": 0,
                "passes": passes,
                "presented": False,
                "drawable": {"width": 64, "height": 64, "pixel_format": "bgra8unorm"},
            },
            {
                "index": 1,
                "passes": passes,
                "presented": True,
                "drawable": {"width": 64, "height": 64, "pixel_format": "bgra8unorm"},
            },
        ],
        "passes": [
            {
                "id": "pass/render",
                "frame": 0,
                "kind": "render",
                "width": 64,
                "height": 64,
                "pixel_format": "bgra8unorm",
                "attachments": [
                    {
                        "slot": 0,
                        "texture": "tex/color",
                        "level": 0,
                        "layer": 0,
                        "load": "clear",
                        "store": "store",
                        "clear": [0.0, 0.0, 0.0, 1.0],
                    },
                    {
                        "slot": 1,
                        "texture": "tex/depth",
                        "level": 0,
                        "layer": 0,
                        "load": "clear",
                        "store": "dontcare",
                        "clear": [1.0],
                    },
                ],
                "pipelines": ["pipe/render"],
                "draws": ["draw/tri"],
            },
            {
                "id": "pass/compute",
                "frame": 0,
                "kind": "compute",
                "width": 64,
                "height": 64,
                "pixel_format": "r32float",
                "attachments": [{"slot": 0, "texture": "tex/depth", "level": 0, "layer": 0, "load": "load",
                                 "store": "store"}],
                "pipelines": ["pipe/compute"],
                "draws": [],
            },
            {
                "id": "pass/blit",
                "frame": 1,
                "kind": "blit",
                "width": 64,
                "height": 64,
                "pixel_format": "bgra8unorm",
                "attachments": [{"slot": 0, "texture": "tex/color", "level": 0, "layer": 0, "load": "load",
                                 "store": "store"}],
                "pipelines": [],
                "draws": [],
            },
        ],
        "pipelines": [
            {
                "id": "pipe/render",
                "label": "synthetic/render",
                "stages": [
                    {"stage": "vertex", "msl_blob": "msl/render.vs", "reflection_blob": "refl/render.vs"},
                    {"stage": "fragment", "msl_blob": "msl/render.fs", "reflection_blob": "refl/render.fs"},
                ],
                "state": _state(),
            },
            {
                "id": "pipe/compute",
                "label": "synthetic/compute",
                "stages": [{"stage": "kernel", "msl_blob": "msl/kernel", "reflection_blob": "refl/kernel"}],
                "state": _state(),
            },
        ],
        "draws": [
            {
                "id": "draw/tri",
                "pass": "pass/render",
                "pipeline": "pipe/render",
                "vertex": {"start": 0, "count": 6},
                "index": {"type": "uint16", "start": 0, "count": 6},
                "instances": 1,
                "primitive_type": "triangle",
                "argument_table": {
                    "vertex_buffers": [{"index": 0, "buffer": "buf/vbuf", "offset": 0, "length": 48}],
                    "textures": [{"index": 0, "texture": "tex/color"}],
                    "samplers": [
                        {
                            "index": 0,
                            "min_filter": "linear",
                            "mag_filter": "linear",
                            "address_u": "clamp_to_edge",
                            "address_v": "clamp_to_edge",
                            "address_w": "clamp_to_edge",
                        }
                    ],
                    "uniform_buffers": [{"index": 0, "buffer": "buf/ubuf", "offset": 0, "length": 64}],
                },
            }
        ],
        "resources": [
            {
                "id": "tex/color",
                "type": "texture",
                "blob": "tex/color",
                "pixel_format": "rgba8unorm",
                "width": 4,
                "height": 4,
                "depth": 1,
                "mip_levels": 1,
                "array_layers": 1,
                "sample_count": 1,
                "texture_type": "2d",
                "usage": ["render_target", "shader_read"],
                "bytes": {"offset": 0, "size": 64},
                "mip_range": {"first": 0, "count": 1},
                "layer_range": {"first": 0, "count": 1},
            },
            {
                "id": "tex/depth",
                "type": "texture",
                "blob": "tex/depth",
                "pixel_format": "depth32float",
                "width": 4,
                "height": 4,
                "depth": 1,
                "mip_levels": 1,
                "array_layers": 1,
                "sample_count": 1,
                "texture_type": "2d",
                "usage": ["render_target"],
                "bytes": {"offset": 0, "size": 64},
                "mip_range": {"first": 0, "count": 1},
                "layer_range": {"first": 0, "count": 1},
            },
            {
                "id": "buf/vbuf",
                "type": "buffer",
                "blob": "buf/vbuf",
                "usage": ["vertex"],
                "bytes": {"offset": 0, "size": 48},
            },
            {
                "id": "buf/ibuf",
                "type": "buffer",
                "blob": "buf/ibuf",
                "usage": ["index"],
                "bytes": {"offset": 0, "size": 12},
            },
            {
                "id": "buf/ubuf",
                "type": "buffer",
                "blob": "buf/ubuf",
                "usage": ["uniform"],
                "bytes": {"offset": 0, "size": 64},
            },
            {
                "id": "buf/storage",
                "type": "buffer",
                "blob": "buf/storage",
                "usage": ["shader_write"],
                "bytes": {"offset": 0, "size": 256},
            },
        ],
    }


def scene_blobs() -> list[sp.Blob]:
    return [
        sp.Blob("msl/render.vs", "msl", b"#include <metal_stdlib>\n// synthetic vertex shader\n"),
        sp.Blob("msl/render.fs", "msl", b"#include <metal_stdlib>\n// synthetic fragment shader\n"),
        sp.Blob("msl/kernel", "msl", b"#include <metal_stdlib>\n// synthetic kernel\n"),
        sp.Blob("refl/render.vs", "reflection", b'{"stage":"vertex","function":"vmain"}'),
        sp.Blob("refl/render.fs", "reflection", b'{"stage":"fragment","function":"fmain"}'),
        sp.Blob("refl/kernel", "reflection", b'{"stage":"kernel","function":"kmain"}'),
        sp.Blob("tex/color", "texture", bytes(range(64))),
        sp.Blob("tex/depth", "texture", bytes(64)),
        sp.Blob("buf/vbuf", "buffer", struct.pack("<12f", *[float(i) for i in range(12)])),
        sp.Blob("buf/ibuf", "buffer", struct.pack("<6H", 0, 1, 2, 0, 2, 3)),
        sp.Blob("buf/ubuf", "buffer", bytes(64)),
        sp.Blob("buf/storage", "buffer", bytes(range(256))),
    ]


class Builds:
    """Compiled C artifacts, shared by every test in this module."""

    directory: str
    tool: str
    c_test: str

    @classmethod
    def build(cls) -> None:
        cls.directory = tempfile.mkdtemp(prefix="scenecap-tests-")
        cls.tool = os.path.join(cls.directory, "nvs_tool")
        cls.c_test = os.path.join(cls.directory, "test_nvs_format")
        source = str(FORMAT_DIR / "nvs_format.c")
        for output, extra in ((cls.tool, [str(FORMAT_DIR / "nvs_tool.c")]),
                              (cls.c_test, [str(TOOL_DIR / "tests" / "test_nvs_format.c")])):
            subprocess.run(
                [CC, *CFLAGS, "-I", str(FORMAT_DIR), source, *extra, "-o", output],
                check=True,
                capture_output=True,
                text=True,
            )


@unittest.skipUnless(CC, "a C compiler is needed to build the .nvs module")
class ScenecapFormatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        Builds.build()
        cls.tempdir = tempfile.mkdtemp(prefix="scenecap-nvs-")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(Builds.directory, ignore_errors=True)
        shutil.rmtree(cls.tempdir, ignore_errors=True)

    # -- helpers ----------------------------------------------------------------------------------------------------
    def path(self, name: str) -> str:
        return os.path.join(self.tempdir, name)

    def write_scene(self, name: str = "scene.nvs", manifest: dict | None = None) -> str:
        target = self.path(name)
        sp.write_package(target, scene_manifest() if manifest is None else manifest, scene_blobs())
        return target

    def c_verify(self, package: str) -> subprocess.CompletedProcess:
        return subprocess.run([Builds.tool, "verify", package], capture_output=True, text=True)

    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(CLI), *arguments], capture_output=True, text=True)

    def assert_verify_fails(self, package: str, *needles: str) -> sp.VerifyReport:
        report = sp.verify_package(package)
        self.assertFalse(report.ok, f"verification passed but should have failed ({needles})")
        text = "\n".join(report.errors)
        for needle in needles:
            self.assertIn(needle, text)
        return report

    # -- the C test binary -------------------------------------------------------------------------------------------
    def test_c_module_self_test(self) -> None:
        result = subprocess.run([Builds.c_test], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("all checks passed", result.stdout)

    def test_c_tool_writes_a_package_python_can_verify(self) -> None:
        """Cross-language: the C writer's fixture must satisfy the Python reader and manifest verifier."""
        package = self.path("c-fixture.nvs")
        written = subprocess.run([Builds.tool, "selftest-write", package], capture_output=True, text=True)
        self.assertEqual(written.returncode, 0, written.stderr)
        parsed = sp.read_package(package)
        self.assertEqual(parsed.major, sp.MAJOR)
        self.assertEqual(
            [chunk.id for chunk in parsed.chunks],
            [
                "msl/main.vert",
                "msl/main.frag",
                "refl/main.vert",
                "refl/main.frag",
                "tex/color0",
                "buf/vtx",
                "buf/cst",
                "buf/zeros",
            ],
        )
        # 64 KiB of zeros must reach the file as a frame the built-in encoder can shrink, and decode back.
        zeros = parsed.chunk("buf/zeros")
        self.assertTrue(zeros.compressed, "the C writer should have compressed 64 KiB of zeros")
        self.assertEqual(parsed.payload("buf/zeros"), bytes(65536))
        report = sp.verify_package(package)
        self.assertTrue(report.ok, report.render())
        self.assertEqual(report.capture["game"], "synthetic")

    def test_python_writer_package_is_verified_by_c(self) -> None:
        package = self.write_scene("py-scene.nvs")
        result = self.c_verify(package)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ok:", result.stdout)
        info = subprocess.run([Builds.tool, "info", package], capture_output=True, text=True)
        self.assertEqual(info.returncode, 0, info.stderr)
        self.assertIn("format          1.0", info.stdout)

    # -- round trip ---------------------------------------------------------------------------------------------------
    def test_round_trip_is_self_consistent(self) -> None:
        package_path = self.write_scene()
        package = sp.read_package(package_path)
        self.assertEqual(package.major, sp.MAJOR)
        self.assertEqual(package.minor, sp.MINOR)
        self.assertEqual(package.chunks[0].kind, "msl")
        self.assertEqual(len(package.chunks), len(scene_blobs()))
        self.assertEqual(package.manifest, scene_manifest())
        self.assertEqual(package.payload("buf/ibuf"), struct.pack("<6H", 0, 1, 2, 0, 2, 3))
        self.assertEqual(package.payload("tex/color"), bytes(range(64)))
        for chunk in package.chunks:
            self.assertEqual(hashlib.sha256(package.payload(chunk)).digest(), hashlib.sha256(
                next(blob.data for blob in scene_blobs() if blob.id == chunk.id)).digest())
            self.assertEqual(chunk.offset % 8, 0)
        self.assertEqual(package.file_size, os.path.getsize(package_path))

        report = sp.verify_package(package_path)
        self.assertTrue(report.ok, report.render())
        self.assertEqual(report.chunks, len(scene_blobs()))
        self.assertEqual(report.errors, [])
        self.assertIn("synthetic", report.render())

    def test_writer_is_reproducible(self) -> None:
        first = self.write_scene("first.nvs")
        second = self.write_scene("second.nvs")
        with open(first, "rb") as handle:
            first_bytes = handle.read()
        with open(second, "rb") as handle:
            second_bytes = handle.read()
        self.assertEqual(first_bytes, second_bytes, "identical input must produce an identical package")

    def test_large_blob_is_compressed(self) -> None:
        payload = b"AB" * 100_000
        package_path = self.path("compressed.nvs")
        manifest = scene_manifest()
        manifest["resources"].append(
            {"id": "buf/big", "type": "buffer", "blob": "buf/big", "usage": ["storage"],
             "bytes": {"offset": 0, "size": len(payload)}}
        )
        blobs = scene_blobs() + [sp.Blob("buf/big", "buffer", payload)]
        sp.write_package(package_path, manifest, blobs)
        package = sp.read_package(package_path)
        big = package.chunk("buf/big")
        self.assertTrue(big.compressed, "a 200 KB repetitive blob should be stored as a zstd frame")
        self.assertLess(big.stored_size, big.raw_size)
        self.assertTrue(package.flags & sp.HEADER_FLAG_CHUNKS_ZSTD)
        self.assertEqual(package.payload(big), payload)
        self.assertTrue(sp.verify_package(package_path).ok)

    # -- tampering ----------------------------------------------------------------------------------------------------
    def test_every_blob_tamper_is_detected_and_named(self) -> None:
        package_path = self.write_scene("tamper.nvs")
        with open(package_path, "rb") as handle:
            original = bytearray(handle.read())
        package = sp.read_package(package_path)
        for chunk in package.chunks:
            if chunk.raw_size == 0:
                continue
            tampered = bytearray(original)
            tampered[chunk.offset + chunk.stored_size // 2] ^= 0x80
            target = self.path(f"tampered-{chunk.id.replace('/', '_')}.nvs")
            with open(target, "wb") as handle:
                handle.write(tampered)
            report = sp.verify_package(target)
            self.assertFalse(report.ok, f"tampering {chunk.id} went unnoticed")
            self.assertTrue(
                any(chunk.id in error for error in report.errors),
                f"the failure for {chunk.id} does not name the chunk: {report.errors}",
            )
            result = self.c_verify(target)
            self.assertNotEqual(result.returncode, 0, f"the C tool accepted a tampered {chunk.id}")
            self.assertIn(chunk.id, result.stderr)

    def test_manifest_tamper_is_detected(self) -> None:
        package_path = self.write_scene("manifest-tamper.nvs")
        package = sp.read_package(package_path)
        with open(package_path, "r+b") as handle:
            handle.seek(package.manifest_offset)
            byte = handle.read(1)
            handle.seek(package.manifest_offset)
            handle.write(bytes([byte[0] ^ 0x20]))
        report = sp.verify_package(package_path)
        self.assertFalse(report.ok)
        self.assertIn("manifest", "\n".join(report.errors))

    # -- container corruption -----------------------------------------------------------------------------------------
    def test_corrupt_containers_fail_with_a_reason(self) -> None:
        good = self.write_scene("good.nvs")
        with open(good, "rb") as handle:
            data = bytes(handle.read())

        def expect_failure(name: str, blob: bytes, *needles: str) -> None:
            target = self.path(name)
            with open(target, "wb") as handle:
                handle.write(blob)
            report = sp.verify_package(target)
            self.assertFalse(report.ok, f"{name} verified as clean")
            text = "\n".join(report.errors)
            for needle in needles:
                self.assertIn(needle, text, f"{name}: {text}")

        expect_failure("bad-magic.nvs", b"NOTCAP" + data[6:], "magic")
        expect_failure("truncated.nvs", data[:-1], "file is")
        expect_failure("trailing.nvs", data + b"\x00", "but the file is")
        expect_failure("short.nvs", data[:64], "shorter than the")

        huge_size = bytearray(data)
        struct.pack_into("<Q", huge_size, sp.CHUNK_STRUCT.size * 0 + sp.HEADER_SIZE + 80, 1 << 40)
        expect_failure("huge-chunk.nvs", bytes(huge_size), "outside the file")

        duplicated = bytearray(data)
        second_entry = sp.HEADER_SIZE + sp.CHUNK_ENTRY_SIZE
        first_entry = sp.HEADER_SIZE
        duplicated[second_entry : second_entry + 64] = duplicated[first_entry : first_entry + 64]
        expect_failure("duplicate-id.nvs", bytes(duplicated), "appears twice")

    def test_version_mismatch_is_rejected(self) -> None:
        package_path = self.write_scene("version.nvs")
        with open(package_path, "rb") as handle:
            data = bytearray(handle.read())
        for offset, value, needle in ((8, 2, "not supported"), (10, 1, "newer")):
            patched = bytearray(data)
            struct.pack_into("<H", patched, offset, value)
            target = self.path(f"version-{offset}.nvs")
            with open(target, "wb") as handle:
                handle.write(patched)
            with self.assertRaises(sp.PackageError) as raised:
                sp.read_package(target)
            self.assertEqual(raised.exception.code, "unsupported")
            self.assertIn(needle, str(raised.exception))
            report = sp.verify_package(target)
            self.assertFalse(report.ok)
            self.assertIn(needle, "\n".join(report.errors))
            result = self.run_cli("verify", target)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn(needle, result.stdout + result.stderr)

    # -- manifest reference integrity ---------------------------------------------------------------------------------
    def test_missing_resource_reference_fails(self) -> None:
        manifest = scene_manifest()
        manifest["passes"][0]["attachments"][0]["texture"] = "tex/missing"
        package = self.write_scene("missing-texture.nvs", manifest)
        report = self.assert_verify_fails(package, "unknown resource 'tex/missing'")
        self.assertIn("manifest.passes[0].attachments[0].texture", "\n".join(report.errors))

    def test_missing_shader_blob_fails(self) -> None:
        manifest = scene_manifest()
        manifest["pipelines"][0]["stages"][1]["msl_blob"] = "msl/missing"
        package = self.write_scene("missing-msl.nvs", manifest)
        self.assert_verify_fails(package, "no chunk named 'msl/missing'")

    def test_reflection_blob_is_optional_for_the_container(self) -> None:
        """A stage without shader reflection is still a valid package: the container is a general archive.

        Whether such a stage is replayable is a question for the replayer, not for the format.
        """
        manifest = copy.deepcopy(scene_manifest())
        for pipeline in manifest["pipelines"]:
            for stage in pipeline["stages"]:
                stage.pop("reflection_blob")
        blobs = [blob for blob in scene_blobs() if blob.kind != "reflection"]
        package = self.path("no-reflection.nvs")
        sp.write_package(package, manifest, blobs)
        report = sp.verify_package(package)
        self.assertTrue(report.ok, report.render())

        # When present the field must still be a string naming a reflection chunk.
        broken = copy.deepcopy(manifest)
        broken["pipelines"][0]["stages"][0]["reflection_blob"] = 7
        package = self.path("bad-reflection.nvs")
        sp.write_package(package, broken, blobs)
        self.assert_verify_fails(package, "reflection_blob")

        broken = copy.deepcopy(manifest)
        broken["pipelines"][0]["stages"][0]["reflection_blob"] = "msl/render.vs"
        package = self.path("wrong-kind-reflection.nvs")
        sp.write_package(package, broken, blobs)
        self.assert_verify_fails(package, "expected reflection")

    def test_blob_of_the_wrong_kind_fails(self) -> None:
        manifest = scene_manifest()
        manifest["pipelines"][0]["stages"][0]["msl_blob"] = "tex/color"
        package = self.write_scene("wrong-kind.nvs", manifest)
        self.assert_verify_fails(package, "is a texture, expected msl")

    def test_pipeline_reference_fails(self) -> None:
        manifest = scene_manifest()
        manifest["passes"][0]["pipelines"] = ["pipe/ghost"]
        package = self.write_scene("missing-pipeline.nvs", manifest)
        self.assert_verify_fails(package, "unknown pipeline 'pipe/ghost'")

    def test_pass_draw_cross_reference_fails(self) -> None:
        manifest = scene_manifest()
        manifest["passes"][0]["draws"] = []
        package = self.write_scene("unlisted-draw.nvs", manifest)
        self.assert_verify_fails(package, "does not list draw 'draw/tri'")

        manifest = scene_manifest()
        manifest["passes"][0]["draws"].append("draw/ghost")
        package = self.write_scene("ghost-draw.nvs", manifest)
        self.assert_verify_fails(package, "unknown draw 'draw/ghost'")

        manifest = scene_manifest()
        manifest["draws"][0]["pass"] = "pass/compute"
        package = self.write_scene("wrong-pass.nvs", manifest)
        self.assert_verify_fails(package, "does not list draw")

        manifest = scene_manifest()
        manifest["passes"][0]["pipelines"] = []
        package = self.write_scene("no-pipeline.nvs", manifest)
        self.assert_verify_fails(package, "lists draws but no pipeline")

    def test_argument_table_bounds_fail(self) -> None:
        manifest = scene_manifest()
        manifest["draws"][0]["argument_table"]["vertex_buffers"][0]["length"] = 4096
        package = self.write_scene("overrun.nvs", manifest)
        self.assert_verify_fails(package, "exceeds the 48-byte buffer 'buf/vbuf'")

    def test_attachment_level_outside_mip_range_fails(self) -> None:
        manifest = scene_manifest()
        manifest["passes"][0]["attachments"][0]["level"] = 3
        package = self.write_scene("bad-level.nvs", manifest)
        self.assert_verify_fails(package, "outside the resource mip range")

    def test_contract_violations_are_reported(self) -> None:
        mutations = {
            "bad-pass-kind.nvs": (lambda m: m["passes"][0].__setitem__("kind", "render2"), "is not one of"),
            "no-viewport.nvs": (lambda m: m["pipelines"][0]["state"].pop("viewport"), "viewport: expected four numbers"),
            "clear-without-values.nvs": (
                lambda m: m["passes"][0]["attachments"][0].pop("clear"),
                "clear: expected one to four numbers",
            ),
            "frame-count-mismatch.nvs": (lambda m: m["frames"].pop(), "frame_count is 2"),
            "no-capture-game.nvs": (lambda m: m["capture"].pop("game"), "manifest.capture.game"),
            "no-capture.nvs": (lambda m: m.pop("capture"), "manifest.capture: expected dict"),
            "capture-not-an-object.nvs": (lambda m: m.__setitem__("capture", []), "manifest.capture: expected dict"),
            "no-frames.nvs": (lambda m: m.pop("frames"), "manifest.frames: expected list"),
            "no-resources.nvs": (lambda m: m.pop("resources"), "manifest.resources: expected list"),
            "bad-primitive.nvs": (lambda m: m["draws"][0].__setitem__("primitive_type", "quad"), "is not one of"),
            "bad-blend-factor.nvs": (
                lambda m: m["pipelines"][0]["state"]["blend"]["attachments"][0].__setitem__("rgb_source", "linear"),
                "is not one of",
            ),
        }
        for name, (mutate, needle) in mutations.items():
            manifest = scene_manifest()
            mutate(manifest)
            package = self.write_scene(name, manifest)
            self.assert_verify_fails(package, needle)

    def test_unknown_manifest_fields_are_preserved(self) -> None:
        """A capture may add debug-only fields; a reader keeps them and does not fail on them."""
        manifest = scene_manifest()
        manifest["capture"]["vendor_note"] = "driver 1.2"
        manifest["pipelines"][0]["state"]["debug"] = {"mailbox": True}
        manifest["passes"][0]["extra_note"] = [1, 2, 3]
        package = self.write_scene("extra-fields.nvs", manifest)
        report = sp.verify_package(package)
        self.assertTrue(report.ok, report.render())
        with sp.read_package(package) as parsed:
            self.assertEqual(parsed.manifest["capture"]["vendor_note"], "driver 1.2")

    def test_missing_chunk_behind_reference_fails_and_unused_chunks_warn(self) -> None:
        """A reference to a blob the package never carried fails; a carried-but-unused blob only warns."""
        manifest = scene_manifest()
        manifest["resources"].append(
            {"id": "buf/absent", "type": "buffer", "blob": "buf/absent", "usage": ["storage"],
             "bytes": {"offset": 0, "size": 16}}
        )
        package = self.path("dropped-blob.nvs")
        sp.write_package(package, manifest, scene_blobs() + [sp.Blob("buf/orphan", "buffer", b"orphan")])
        report = self.assert_verify_fails(package, "no chunk named 'buf/absent'")
        self.assertTrue(any("buf/orphan" in warning for warning in report.warnings), report.warnings)
        # The C tool only checks hashes, so this package is still structurally sound there.
        self.assertEqual(self.c_verify(package).returncode, 0)

    # -- API misuse ---------------------------------------------------------------------------------------------------
    def test_writer_rejects_bad_input(self) -> None:
        target = self.path("bad.nvs")
        with self.assertRaises(sp.PackageError):
            sp.write_package(target, {"capture": {}}, [sp.Blob("bad id", "msl", b"x")])
        with self.assertRaises(sp.PackageError):
            sp.write_package(target, {"capture": {}}, [sp.Blob("a", "msl", b"x"), sp.Blob("a", "msl", b"y")])
        with self.assertRaises(sp.PackageError):
            sp.write_package(target, {"capture": {}}, [sp.Blob("a", "nope", b"x")])
        with self.assertRaises(sp.PackageError):
            sp.write_package(target, "not json", [])
        with self.assertRaises(sp.PackageError):
            sp.write_package(target, "[1, 2]", [])
        with self.assertRaises(sp.PackageError):
            sp.write_package(target, {"capture": {}}, [sp.Blob("x" * 64, "msl", b"x")])
        self.assertFalse(os.path.exists(target), "a rejected write must not leave a package behind")

    def test_failed_write_keeps_the_previous_package(self) -> None:
        package = self.write_scene("atomic.nvs")
        with open(package, "rb") as handle:
            before = handle.read()
        with self.assertRaises(sp.PackageError):
            sp.write_package(package, scene_manifest(), [sp.Blob("a b", "msl", b"x")])
        with open(package, "rb") as handle:
            self.assertEqual(handle.read(), before)
        self.assertTrue(sp.verify_package(package).ok)

    def test_reader_api_errors(self) -> None:
        package_path = self.write_scene("api.nvs")
        package = sp.read_package(package_path)
        with self.assertRaises(sp.PackageError):
            package.chunk("does/not/exist")
        with self.assertRaises(sp.PackageError):
            sp.read_package(self.path("does-not-exist.nvs"))

    # -- CLI -----------------------------------------------------------------------------------------------------------
    def test_cli_verify_and_info(self) -> None:
        package = self.write_scene("cli.nvs")
        verified = self.run_cli("verify", package)
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        self.assertIn("PASS", verified.stdout)
        self.assertIn("synthetic", verified.stdout)

        quiet = self.run_cli("verify", "--quiet", package)
        self.assertEqual(quiet.returncode, 0)
        self.assertEqual(quiet.stdout, "")

        machine = self.run_cli("verify", "--json", package)
        self.assertEqual(machine.returncode, 0)
        payload = json.loads(machine.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["chunks"], len(scene_blobs()))

        info = self.run_cli("info", package)
        self.assertEqual(info.returncode, 0, info.stderr)
        self.assertIn("format        1.0", info.stdout)
        self.assertIn("scene         ", info.stdout)

    def test_cli_reports_the_bad_chunk_and_exits_nonzero(self) -> None:
        package = self.write_scene("cli-bad.nvs")
        parsed = sp.read_package(package)
        chunk = parsed.chunk("buf/storage")
        with open(package, "r+b") as handle:
            handle.seek(chunk.offset + 20)
            byte = handle.read(1)
            handle.seek(chunk.offset + 20)
            handle.write(bytes([byte[0] ^ 0x01]))
        failed = self.run_cli("verify", package)
        self.assertEqual(failed.returncode, 1)
        self.assertIn("buf/storage", failed.stdout)

    def test_cli_cat_and_manifest(self) -> None:
        package = self.write_scene("cli-cat.nvs")
        dumped = self.run_cli("cat", package, "buf/ibuf")
        self.assertEqual(dumped.returncode, 0, dumped.stderr)
        self.assertEqual(dumped.stdout.encode(), struct.pack("<6H", 0, 1, 2, 0, 2, 3))
        manifest = self.run_cli("manifest", package)
        self.assertEqual(manifest.returncode, 0, manifest.stderr)
        self.assertEqual(json.loads(manifest.stdout), scene_manifest())

    def test_cli_version(self) -> None:
        result = self.run_cli("--version")
        self.assertEqual(result.returncode, 0)
        self.assertIn(sp.VERSION, result.stdout)


if __name__ == "__main__":
    unittest.main()
