"""Integration tests: multi-game sync, the unified `scenecap` CLI and the synth -> verify -> replay -> diff loop.

The sync tests are pure file-system checks.  The replay step needs the built replayer (and a Metal device) and skips
itself otherwise, exactly like tools/scenecap/replay/tests.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = TOOL_DIR.parents[1]
CLI = TOOL_DIR / "scenecap"
SYNC = TOOL_DIR / "sync.py"
PYTHON = sys.executable

sys.path.insert(0, str(TOOL_DIR / "python"))

import scenecap_pkg  # noqa: E402

REPLAY = os.environ.get("SCENECAP_REPLAY") or str(TOOL_DIR / "replay" / "build" / "scenecap_replay")
GAMES = ("nier", "totk", "odyssey", "by3")


def run_python(script: Path, *arguments: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PYTHON, str(script), *arguments],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )


def run_cli(*arguments: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return run_python(CLI, *arguments, env=env)


class SyncTests(unittest.TestCase):
    """tools/scenecap/sync.py: the capture kernel copies in every game tree."""

    def test_repository_copies_match_the_source_tree(self) -> None:
        completed = run_python(SYNC, "--check")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        for game in GAMES:
            self.assertIn(f"ok      games/{game}", completed.stdout)
        # botw has no d2r_gfx checkout yet: skipped, never a failure.
        self.assertIn("skipped games/botw", completed.stdout)

    def make_fixture(self) -> str:
        """A staged checkout: source tree plus one game with an empty gfx/nvn directory."""
        root = tempfile.mkdtemp(prefix="scenecap-sync-")
        self.addCleanup(shutil.rmtree, root, True)
        for subdir in ("format", "capture"):
            shutil.copytree(TOOL_DIR / subdir, Path(root, "tools", "scenecap", subdir))
        Path(root, "games", "fixture", "third_party", "d2r_gfx", "gfx", "nvn").mkdir(parents=True)
        return root

    def test_check_detects_a_modified_copy(self) -> None:
        root = self.make_fixture()
        synced = run_python(SYNC, "--root", root, "--game", "fixture")
        self.assertEqual(synced.returncode, 0, synced.stdout + synced.stderr)
        self.assertEqual(run_python(SYNC, "--root", root, "--check").returncode, 0)

        target = Path(root, "games", "fixture", "third_party", "d2r_gfx", "gfx", "nvn", "scenecap_capture.h")
        target.write_bytes(target.read_bytes() + b"/* local edit */\n")

        drift = run_python(SYNC, "--root", root, "--check")
        self.assertEqual(drift.returncode, 1)
        self.assertIn("drift   games/fixture: 1 file(s)", drift.stderr)
        self.assertIn("games/fixture/third_party/d2r_gfx/gfx/nvn/scenecap_capture.h", drift.stderr)
        self.assertIn("out of sync", drift.stderr)

    def test_foreign_file_is_not_overwritten_without_force(self) -> None:
        root = self.make_fixture()
        nvn = Path(root, "games", "fixture", "third_party", "d2r_gfx", "gfx", "nvn")
        foreign = nvn / "scenecap_nvn.h"
        foreign.write_bytes(b"/* game-owned header: do not touch */\n")

        refused = run_python(SYNC, "--root", root, "--game", "fixture")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("refusing to overwrite", refused.stderr)
        self.assertEqual(foreign.read_bytes(), b"/* game-owned header: do not touch */\n")

        forced = run_python(SYNC, "--root", root, "--game", "fixture", "--force")
        self.assertEqual(forced.returncode, 0, forced.stdout + forced.stderr)
        self.assertEqual(foreign.read_bytes(), (TOOL_DIR / "capture" / "scenecap_nvn.h").read_bytes())

    def test_list_reports_files_and_game_trees(self) -> None:
        completed = run_python(SYNC, "--list")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("files         6", completed.stdout)
        for game in GAMES:
            self.assertIn(game, completed.stdout)
        self.assertIn("skipped", completed.stdout)
        self.assertIn("botw", completed.stdout)

    def test_usage_and_unknown_game_exit_codes(self) -> None:
        self.assertEqual(run_python(SYNC, "--bogus").returncode, 2)
        unknown = run_python(SYNC, "--check", "--game", "no-such-game")
        self.assertEqual(unknown.returncode, 1)
        self.assertIn("no such game tree", unknown.stderr)


class CliTests(unittest.TestCase):
    """`scenecap` itself: the usage line and the synth/info/replay/diff plumbing."""

    def setUp(self) -> None:
        self.directory = tempfile.mkdtemp(prefix="scenecap-cli-")
        self.addCleanup(shutil.rmtree, self.directory, True)

    def package(self, name: str = "scene.nvs") -> str:
        return os.path.join(self.directory, name)

    def test_no_arguments_prints_usage(self) -> None:
        completed = run_cli()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("usage: scenecap", completed.stdout)
        for command in ("verify", "info", "synth", "replay", "diff"):
            self.assertIn(command, completed.stdout)

    def test_synth_then_verify_and_info(self) -> None:
        package = self.package()
        synth = run_cli("synth", package, "--raw")
        self.assertEqual(synth.returncode, 0, synth.stderr)
        self.assertTrue(os.path.isfile(package))
        self.assertIn("wrote", synth.stdout)

        verify = run_cli("verify", package)
        self.assertEqual(verify.returncode, 0, verify.stderr)
        self.assertIn("PASS", verify.stdout)

        info = run_cli("info", package)
        self.assertEqual(info.returncode, 0, info.stderr)
        self.assertIn("scene         1 pass(es)", info.stdout)
        self.assertIn("scenecap-synth", info.stdout)

        machine = run_cli("info", package, "--json")
        self.assertEqual(machine.returncode, 0, machine.stderr)
        payload = json.loads(machine.stdout)
        self.assertEqual(payload["format"], "1.0")
        self.assertEqual(payload["capture"]["game"], "scenecap-synth")

    def test_replay_reports_a_missing_binary(self) -> None:
        env = dict(os.environ, SCENECAP_REPLAY=os.path.join(self.directory, "no-such-replayer"))
        completed = run_cli("replay", self.package(), "-o", os.path.join(self.directory, "out"), env=env)
        self.assertEqual(completed.returncode, 2)
        self.assertIn("not built", completed.stderr)

    def test_diff_forwards_its_arguments(self) -> None:
        completed = run_cli(
            "diff", self.directory, self.directory, "-o", os.path.join(self.directory, "report.md")
        )
        # Two identical (empty) directories are not a valid replay pair: the comparison tool decides and says why.
        self.assertEqual(completed.returncode, 2)
        self.assertTrue(completed.stderr.strip() or completed.stdout.strip())

    def test_synth_verify_replay_diff_loop(self) -> None:
        if not os.access(REPLAY, os.X_OK):
            self.skipTest(f"replayer {REPLAY} is not built")
        package = self.package("loop.nvs")
        self.assertEqual(run_cli("synth", package).returncode, 0)
        self.assertEqual(run_cli("verify", package).returncode, 0)

        first = os.path.join(self.directory, "first")
        second = os.path.join(self.directory, "second")
        replayed = run_cli("replay", package, "-o", first)
        if replayed.returncode != 0 and "no Metal device" in replayed.stderr:
            self.skipTest("this host exposes no Metal device")
        self.assertEqual(replayed.returncode, 0, replayed.stderr)
        self.assertEqual(run_cli("replay", package, "-o", second).returncode, 0)

        report = os.path.join(self.directory, "report.md")
        compared = run_cli("diff", first, second, "-o", report)
        self.assertEqual(compared.returncode, 0, compared.stdout + compared.stderr)
        self.assertTrue(os.path.isfile(report))
        with open(report, "r", encoding="utf-8") as handle:
            self.assertIn("PASS", handle.read())
        with scenecap_pkg.read_package(package) as parsed:
            self.assertEqual(len(parsed.manifest["passes"]), 1)


class CaptureLoopTests(unittest.TestCase):
    """The capture kernel's own package (not only the synthetic scene) survives verify -> replay -> diff."""

    def test_captured_package_replays_and_diffs(self) -> None:
        compiler = shutil.which("cc")
        if compiler is None:
            self.skipTest("a C compiler is required")
        if not os.access(REPLAY, os.X_OK):
            self.skipTest(f"replayer {REPLAY} is not built")

        directory = tempfile.mkdtemp(prefix="scenecap-capture-loop-")
        self.addCleanup(shutil.rmtree, directory, True)
        harness = os.path.join(directory, "capture_selftest")
        subprocess.run(
            [
                compiler,
                "-std=c11",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-I" + str(TOOL_DIR / "format"),
                str(TOOL_DIR / "tests" / "capture_selftest.c"),
                str(TOOL_DIR / "capture" / "scenecap_capture.c"),
                str(TOOL_DIR / "format" / "nvs_format.c"),
                "-o",
                harness,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
        package = os.path.join(directory, "captured.nvs")
        subprocess.run(
            [harness, "--coherent", "--render-only", package],
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )

        verify = run_cli("verify", package)
        self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)

        first = os.path.join(directory, "first")
        second = os.path.join(directory, "second")
        replayed = run_cli("replay", package, "-o", first)
        if replayed.returncode != 0 and "no Metal device" in replayed.stderr:
            self.skipTest("this host exposes no Metal device")
        self.assertEqual(replayed.returncode, 0, replayed.stderr)
        self.assertEqual(run_cli("replay", package, "-o", second).returncode, 0)

        report = os.path.join(directory, "report.md")
        compared = run_cli("diff", first, second, "-o", report)
        self.assertEqual(compared.returncode, 0, compared.stdout + compared.stderr)
        with open(report, "r", encoding="utf-8") as handle:
            self.assertIn("PASS", handle.read())


if __name__ == "__main__":
    unittest.main()
