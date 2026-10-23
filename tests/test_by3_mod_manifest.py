"""games/by3/scripts/make_mod_manifest.py: Bayonetta 3's interface (#144): the work folder's RomFS by default, no TOTK app
or version options."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_mod_manifest import fake_romfs

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT/'games/by3/scripts/make_mod_manifest.py'


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)


class By3ModManifest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.romfs = fake_romfs(self.root/'romfs')
        self.mod = self.root/'mods/ui/romfs'
        (self.mod/'UI').mkdir(parents=True)
        (self.mod/'UI/C.bfres').write_bytes(b'changed')

    def tearDown(self):
        self.tmp.cleanup()

    def test_romfs_folder_writes_the_manifest(self):
        done = run(self.mod, '--romfs', self.romfs)
        self.assertEqual(done.returncode, 0, done.stderr)
        rows = [line.split('\t') for line in (self.mod/'manifest.tsv').read_text().splitlines()[1:]]
        self.assertEqual([(r[0], r[1]) for r in rows], [('UI/C.bfres', str(len(b'changed')))])

    def test_totk_options_are_gone(self):
        for option in (['--app', self.root/'Bayonetta 3.app'], ['--version', 'v120']):
            done = run(self.mod, *option)
            self.assertEqual(done.returncode, 2)
            self.assertIn('unrecognized arguments', done.stderr)

    def test_missing_romfs_names_the_fix(self):
        done = run(self.mod, '--romfs', self.root/'nowhere')
        self.assertEqual(done.returncode, 2)
        self.assertIn('make prepare', done.stderr)
        self.assertFalse((self.mod/'manifest.tsv').exists())


if __name__ == '__main__':
    unittest.main()
