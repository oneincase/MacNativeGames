"""Captured Zeus 2 raw movement must survive a stuck native left-stick profile."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

TOTK = Path(__file__).resolve().parents[1] / 'games/totk'


@unittest.skipUnless(shutil.which('xcrun'), 'needs Apple command line tools')
class SwitchLeftFallbackTests(unittest.TestCase):
    def test_captured_motion_and_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / 'probe'
            build = subprocess.run([
                'xcrun', 'clang', '-std=c11', '-Wall', '-Wextra', '-Werror',
                f'-I{TOTK}/runtime', str(TOTK / 'tests/switch_left/probe.c'),
                '-o', str(probe),
            ], capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([str(probe)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('PASS:', run.stdout)
