"""build_app.py takes a seed file's digest from the record prepare_shader_seed.py writes (<seed>.verified.json) while the
file's identity is unchanged, and hashes it otherwise."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
import build_app


class SeedDigests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.seed = Path(self.tmp.name)/'v143'
        self.seed.mkdir()
        self.file = self.seed/'a_b.metal'
        self.file.write_text('// shader')
        s = self.file.stat()
        self.record = {'schema': 1, 'files': {'a_b.metal': [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, 'f'*64]}}
        self.seed.with_name('v143.verified.json').write_text(json.dumps(self.record))

    def tearDown(self):
        self.tmp.cleanup()

    def test_an_unchanged_file_takes_the_recorded_digest(self):
        self.assertEqual(build_app.seed_digests(self.seed)['a_b.metal'], 'f'*64)   # not its real digest: it was not read
        self.assertEqual(build_app.seed_sha256(self.seed, 'a_b.metal', build_app.seed_digests(self.seed)), 'f'*64)

    def test_a_changed_file_is_hashed(self):
        old = self.file.stat()
        self.file.write_text('// other')
        os.utime(self.file, ns=(old.st_atime_ns, old.st_mtime_ns))
        record = build_app.seed_digests(self.seed)
        self.assertNotIn('a_b.metal', record)
        self.assertEqual(build_app.seed_sha256(self.seed, 'a_b.metal', record), hashlib.sha256(b'// other').hexdigest())

    def test_no_record_hashes_everything(self):
        self.seed.with_name('v143.verified.json').unlink()
        self.assertEqual(build_app.seed_digests(self.seed), {})


if __name__ == '__main__':
    unittest.main()
