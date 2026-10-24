"""Tests for the scenecap capture module.

The capture module runs inside the game's replay layer, so these tests drive it with a fake host (a synthetic
scene whose textures, buffers and shaders are deterministic). The package it writes is then checked with the
independent Python reader and with the command line verifier, which exercises exactly the contract a replay port
depends on.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TOOL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(os.path.dirname(TOOL_DIR))
sys.path.insert(0, os.path.join(TOOL_DIR, "python"))

import scenecap_pkg as sp  # noqa: E402

CC = shutil.which("cc")
CLI = os.path.join(TOOL_DIR, "scenecap")
FORMAT_DIR = os.path.join(TOOL_DIR, "format")
CAPTURE_DIR = os.path.join(TOOL_DIR, "capture")
CFLAGS = [
    "-std=c11",
    "-Wall",
    "-Wextra",
    "-Werror",
    "-fsanitize=undefined",
    "-fno-sanitize-recover=all",
    "-I" + FORMAT_DIR,
]


def build_harness(out_dir):
    binary = os.path.join(out_dir, "capture_selftest")
    sources = [
        os.path.join(TOOL_DIR, "tests", "capture_selftest.c"),
        os.path.join(CAPTURE_DIR, "scenecap_capture.c"),
        os.path.join(FORMAT_DIR, "nvs_format.c"),
    ]
    subprocess.run([CC, *CFLAGS, *sources, "-o", binary], check=True, cwd=REPO_ROOT)
    return binary


@unittest.skipUnless(CC, "a C compiler is required")
class CaptureTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="scenecap-capture-")
        cls.binary = build_harness(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_capture(self, *args, env=None, name="scene.nvs"):
        path = os.path.join(self.tmp, name)
        merged = dict(os.environ)
        if env:
            merged.update(env)
        proc = subprocess.run([self.binary, *args, path], capture_output=True, text=True, env=merged, cwd=REPO_ROOT)
        return proc, path

    def load(self, path):
        package = sp.read_package(path)
        self.addCleanup(package.close)
        return package

    def test_package_verifies_and_describes_the_scene(self):
        proc, path = self.run_capture(name="full.nvs")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = sp.verify_package(path)
        self.assertTrue(report.ok, report.render())
        self.assertEqual(report.errors, [])

        package = self.load(path)
        manifest = package.manifest
        capture = manifest["capture"]
        self.assertEqual(capture["game"], "synthetic-capture")
        self.assertEqual(capture["title_id"], "0100000000000000")
        self.assertEqual(capture["frame_first"], 0)
        self.assertEqual(capture["frame_count"], 1)
        self.assertEqual(capture["captured_at"], "2026-01-01T00:00:00Z")
        self.assertEqual([frame["index"] for frame in manifest["frames"]], [0])

        kinds = [item["kind"] for item in manifest["passes"]]
        self.assertEqual(kinds, ["render", "compute"])
        render = manifest["passes"][0]
        self.assertEqual((render["width"], render["height"]), (64, 64))
        self.assertEqual(render["pixel_format"], "bgra8unorm")
        slots = [item["slot"] for item in render["attachments"]]
        self.assertEqual(slots, [0, 8])
        color = render["attachments"][0]
        self.assertEqual(color["load"], "clear")
        self.assertEqual(color["store"], "store")
        self.assertEqual(color["clear"], [0.1, 0.2, 0.3, 1.0])
        self.assertEqual(render["draws"], ["draw/0", "draw/1"])
        compute = manifest["passes"][1]
        self.assertEqual(compute["kind"], "compute")
        self.assertEqual(compute["attachments"], [])

        pipelines = {item["id"]: item for item in manifest["pipelines"]}
        self.assertEqual(set(pipelines), {"pipe/0", "pipe/1"})
        render_pipe = pipelines["pipe/0"]
        self.assertEqual([stage["stage"] for stage in render_pipe["stages"]], ["vertex", "fragment"])
        for stage in render_pipe["stages"]:
            self.assertEqual(package.chunk(stage["msl_blob"]).kind, "msl")
            self.assertEqual(package.chunk(stage["reflection_blob"]).kind, "reflection")
        state = render_pipe["state"]
        self.assertTrue(state["blend"]["enabled"])
        self.assertEqual(state["blend"]["attachments"][0]["rgb_operation"], "add")
        self.assertTrue(state["depth_stencil"]["depth_test"])
        self.assertTrue(state["depth_stencil"]["depth_write"])
        self.assertEqual(state["depth_stencil"]["depth_compare"], "less_equal")
        self.assertEqual(state["raster"]["cull"], "back")
        self.assertEqual(state["raster"]["fill"], "fill")
        self.assertEqual(state["viewport"], [0.0, 0.0, 64.0, 64.0])
        self.assertEqual(state["scissor"], [0.0, 0.0, 64.0, 64.0])
        self.assertEqual(state["frontface"], "ccw")
        self.assertEqual(state["primitive_type"], "triangle")
        self.assertEqual([stage["stage"] for stage in pipelines["pipe/1"]["stages"]], ["kernel"])

        draws = {item["id"]: item for item in manifest["draws"]}
        self.assertEqual(set(draws), {"draw/0", "draw/1"})
        first = draws["draw/0"]
        self.assertEqual(first["vertex"], {"start": 0, "count": 3})
        self.assertEqual(first["index"]["type"], "uint16")
        self.assertEqual(first["index"]["count"], 3)
        self.assertEqual(first["instances"], 1)
        self.assertEqual(first["primitive_type"], "triangle")
        self.assertEqual(first["argument_table"]["vertex_buffers"][0]["buffer"], "buf/4096/48")
        self.assertEqual(first["argument_table"]["textures"][0]["texture"], "tex/100/0/0")
        self.assertEqual(first["argument_table"]["uniform_buffers"][0]["length"], 64)
        self.assertEqual(first["argument_table"]["samplers"][0]["index"], 0)
        self.assertEqual(draws["draw/1"]["instances"], 2)
        self.assertNotIn("index", draws["draw/1"])

        dispatch = manifest["dispatches"][0]
        self.assertEqual(dispatch["grid"], [8, 1, 1])
        self.assertEqual(dispatch["pass"], "pass/1")
        self.assertEqual(dispatch["argument_table"]["storage_buffers"][0]["buffer"], "buf/16384/256")

        resources = {item["id"]: item for item in manifest["resources"]}
        self.assertEqual(resources["tex/100/0/0"]["type"], "texture")
        self.assertIn("render_target", resources["tex/100/0/0"]["usage"])
        self.assertIn("sample", resources["tex/100/0/0"]["usage"])
        self.assertEqual(resources["tex/100/0/0"]["pixel_format"], "bgra8unorm")
        self.assertEqual(resources["tex/200/0/0"]["usage"], ["depth_stencil"])
        self.assertEqual(resources["buf/12288/12"]["usage"], ["index"])
        self.assertEqual(resources["buf/20480/72"]["usage"], ["vertex"])
        self.assertEqual(resources["buf/8192/64"]["usage"], ["uniform"])
        self.assertEqual(resources["buf/16384/256"]["usage"], ["storage"])

    def test_snapshots_hold_the_host_bytes(self):
        proc, path = self.run_capture(name="bytes.nvs")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        package = self.load(path)
        color = package.payload("tex/100/0/0")
        self.assertEqual(len(color), 64)
        self.assertEqual(color, bytes(i & 0xFF for i in range(64)))
        depth = package.payload("tex/200/0/0")
        self.assertEqual(depth, bytes(0x80 + i for i in range(64)))
        vertex = package.payload("buf/4096/48")
        self.assertEqual(vertex, bytes((0x1000 + i * 7) & 0xFF for i in range(48)))
        uniform = package.payload("buf/8192/64")
        self.assertEqual(uniform, bytes((0x2000 + i * 7) & 0xFF for i in range(64)))
        # The blob id is a digest of the shader key (the package caps ids at 63 bytes), so take it from the
        # manifest reference instead of spelling it out.
        vertex_id = package.manifest["pipelines"][0]["stages"][0]["msl_blob"]
        msl = package.payload(vertex_id)
        self.assertIn(b"vs0001", msl)
        self.assertIn(b"metal_stdlib", msl)

    def test_packages_are_reproducible(self):
        first, first_path = self.run_capture(name="first.nvs")
        second, second_path = self.run_capture(name="second.nvs")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        with open(first_path, "rb") as handle:
            left = handle.read()
        with open(second_path, "rb") as handle:
            right = handle.read()
        self.assertEqual(left, right)

    def test_tampered_snapshot_is_reported_by_the_verifier(self):
        proc, path = self.run_capture(name="tamper.nvs")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        package = self.load(path)
        chunk = package.chunk("tex/100/0/0")
        target = chunk.offset + max(1, chunk.stored_size // 2)
        package.close()
        with open(path, "r+b") as handle:
            handle.seek(target)
            byte = handle.read(1)
            handle.seek(target)
            handle.write(bytes([byte[0] ^ 0xFF]))
        report = sp.verify_package(path)
        self.assertFalse(report.ok)
        self.assertTrue(any("tex/100/0/0" in error for error in report.errors), report.render())
        cli = subprocess.run([sys.executable, CLI, "verify", path], capture_output=True, text=True)
        self.assertEqual(cli.returncode, 1, cli.stdout + cli.stderr)
        self.assertIn("tex/100/0/0", cli.stdout)

    def test_a_missing_shader_drops_its_draws_but_keeps_the_package(self):
        proc, path = self.run_capture("--missing-msl", name="missing.nvs")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("no MSL for shader fs0001", proc.stderr)
        report = sp.verify_package(path)
        self.assertTrue(report.ok, report.render())
        package = self.load(path)
        manifest = package.manifest
        self.assertEqual(manifest["draws"], [])
        self.assertEqual([item["id"] for item in manifest["pipelines"]], ["pipe/1"])
        self.assertEqual([item["kind"] for item in manifest["passes"]], ["render", "compute"])
        self.assertEqual(manifest["passes"][0]["draws"], [])
        self.assertEqual(manifest["passes"][0]["pipelines"], [])
        self.assertEqual(manifest["dispatches"][0]["pipeline"], "pipe/1")

    def test_an_unreadable_host_still_yields_a_valid_package(self):
        proc, path = self.run_capture("--no-host", name="nohost.nvs")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = sp.verify_package(path)
        self.assertTrue(report.ok, report.render())
        package = self.load(path)
        manifest = package.manifest
        self.assertEqual(manifest["draws"], [])
        self.assertEqual(manifest["dispatches"][0]["pass"], "pass/1")
        warnings = manifest["warnings"]
        self.assertGreater(warnings["dropped_draws"], 0)

    def test_environment_parsing(self):
        proc = subprocess.run([self.binary, "--env"], capture_output=True, text=True, cwd=REPO_ROOT)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("env requested=0", proc.stdout)

        env = dict(os.environ)
        env.update(
            {
                "SCENECAP": "1",
                "SCENECAP_FRAMES": "3:2",
                "SCENECAP_MAX_BYTES": "1048576",
                "SCENECAP_COMPRESS": "1",
            }
        )
        proc = subprocess.run([self.binary, "--env"], capture_output=True, text=True, env=env, cwd=REPO_ROOT)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("requested=1", proc.stdout)
        self.assertIn("frames=2", proc.stdout)
        self.assertIn("limit=1048576", proc.stdout)
        self.assertIn("compress=1", proc.stdout)

    def test_an_unwritable_path_fails_cleanly(self):
        path = os.path.join(self.tmp, "missing-dir", "scene.nvs")
        proc = subprocess.run([self.binary, path], capture_output=True, text=True, cwd=REPO_ROOT)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("scenecap_finish:", proc.stderr)
        self.assertIn(path, proc.stderr)
        self.assertFalse(os.path.exists(path))

    def test_capture_ignores_events_after_the_window(self):
        proc, path = self.run_capture(name="window.nvs")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        package = self.load(path)
        self.assertEqual(len(package.manifest["frames"]), 1)


if __name__ == "__main__":
    unittest.main()
