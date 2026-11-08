"""OoT's upscaler modes and the NVN2 task / mesh driver-segment tables.

`drv_fillable` decides what the replay layer is said to fill, and the red line "reads positions the replay layer does not
fill" is what a debugger follows; `driver_bank_fill` writes the task (0x670) and mesh (0x770) storage tables, so the
predicate must cover them or every task / mesh shader is reported for nothing (nvn_mtl.m, 2026-11-08).

`ndsr_mode` picks what of agl's upscaler network runs: the old copy bypass dropped the last kernel's light and tone step
and made the picture pale (skill C36), so the default is the last kernel alone and a source-level regression here is the
cheap way to keep it.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'games/totk/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m'

PROBE_MAIN = '''
int main(int argc, char **argv) {
    if (argc < 2) return 2;
    if (!strcmp(argv[1], "fill")) {                 /* fill <stage> <offset>: does the replay fill that c0 word? */
        if (argc < 4) return 2;
        printf("%d\\n", drv_fillable(atoi(argv[2]), 0, (unsigned)strtoul(argv[3], NULL, 16)));
        return 0;
    }
    if (!strcmp(argv[1], "fillable")) {             /* the NVN2 task / mesh tables the 2026-11-08 red line named */
        assert(drv_fillable(3, 0, 0x670));          /* task: 16 slots of 16 bytes from 0x670 */
        assert(drv_fillable(3, 0, 0x760));
        assert(!drv_fillable(3, 0, 0x770));         /* the mesh table is not the task's */
        assert(drv_fillable(4, 0, 0x770));          /* mesh: from 0x770 */
        assert(drv_fillable(4, 0, 0x780));          /* slot 1, the read a run reported */
        assert(drv_fillable(4, 0, 0x860));
        assert(!drv_fillable(4, 0, 0x870));
        assert(drv_fillable(0, 2, 0x168));          /* c2: texture handle table */
        assert(drv_fillable(0, 2, 0x968 - 8));
        assert(!drv_fillable(2, 0, 0x100));         /* geometry has no table here */
        puts("driver segment fillability ok");
        return 0;
    }
    if (!strcmp(argv[1], "mode")) {                 /* mode: NDSR_FULL / _COPY / _FINAL, bypass, keeps the last kernel */
        printf("%d %d %d\\n", ndsr_mode(), ndsr_bypass(), ndsr_keep_final());
        return 0;
    }
    return 2;
}
'''


def extract(source, start, end=None):
    """Source text from `start` (inclusive) to the matching closing brace, or through `end` when given."""
    head = source.index(start)
    if end:
        return source[head:source.index(end, head) + len(end)]
    depth = 0
    for i in range(source.index('{', head), len(source)):
        if source[i] == '{':
            depth += 1
        elif source[i] == '}':
            depth -= 1
            if depth == 0:
                return source[head:i + 1]
    raise AssertionError(f'unterminated block at {start!r}')


def probe_source(source):
    return ("#include <assert.h>\n#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n#include <strings.h>\n"
            '#define MLOG(...) ((void)0)\n'
            + extract(source, 'static int drv_fillable(int stage, int bank, unsigned off) {')
            + '\n'
            + extract(source, 'enum { NDSR_FULL = 0, NDSR_COPY = 1, NDSR_FINAL = 2 };',
                      'static int ndsr_keep_final(void) { return ndsr_mode() == NDSR_FINAL; }')
            + PROBE_MAIN)


@unittest.skipUnless(shutil.which('clang'), 'clang required')
class OotUpscalerAndDriverSegments(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text()
        cls.tmp = tempfile.TemporaryDirectory()
        c_path = Path(cls.tmp.name) / 'probe.c'
        c_path.write_text(probe_source(cls.source))
        cls.exe = Path(cls.tmp.name) / 'probe'
        # GAME_NS2 / GAME_OOT: the task / mesh cases and the NVN2 compute layout are behind them in the host
        subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-DGAME_NS2=1', '-DGAME_OOT=1',
                        str(c_path), '-o', str(cls.exe)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_probe(self, *args, env=None):
        return subprocess.run([str(self.exe), *args], check=True, capture_output=True, text=True, env=env).stdout

    def mode(self, value):
        env = {k: v for k, v in os.environ.items() if k != 'TOTK_NDSR_BYPASS'}
        if value is not None:
            env['TOTK_NDSR_BYPASS'] = value
        return tuple(int(v) for v in self.run_probe('mode', env=env).split())

    def test_task_and_mesh_storage_tables_are_fillable(self):
        self.assertIn('driver segment fillability ok', self.run_probe('fillable'))

    def test_every_stage_the_fill_writes_is_fillable(self):
        """driver_bank_fill's c0 tables and drv_fillable's predicate must name the same stages. Stages 3 / 4 were missing
        from the predicate and every NVN2 task / mesh shader was reported as reading 0 (2026-11-08)."""
        fill = extract(self.source, 'static void driver_bank_fill(uint8_t *d, int bank, int stage) {')
        bases = re.findall(r'stage == (\d) \? (0x[0-9a-f]+)', fill)
        self.assertIn(('3', '0x670'), bases)   # NVN2 task
        self.assertIn(('4', '0x770'), bases)   # NVN2 mesh
        for stage, base in bases:
            self.assertEqual(self.run_probe('fill', stage, base).strip(), '1',
                             f'stage {stage} is filled at {base} but drv_fillable does not cover it')

    def test_default_runs_the_light_kernel_alone(self):
        # NDSR_FINAL, the eight layers skipped, the last kernel as translated
        self.assertEqual(self.mode(None), (2, 1, 1))
        self.assertEqual(self.mode(''), (2, 1, 1))
        self.assertEqual(self.mode('final'), (2, 1, 1))
        self.assertEqual(self.mode('light'), (2, 1, 1))

    def test_explicit_modes_stay_reachable(self):
        self.assertEqual(self.mode('0'), (0, 0, 0))       # the whole enhancement network
        self.assertEqual(self.mode('off'), (0, 0, 0))
        self.assertEqual(self.mode('1'), (1, 1, 0))       # the old copy bypass
        self.assertEqual(self.mode('on'), (1, 1, 0))

    def test_the_bypassed_copy_is_not_the_default(self):
        self.assertNotEqual(self.mode(None)[:2], (1, 1), 'the copy drops the light and tone step (skill C36)')


if __name__ == '__main__':
    unittest.main()
