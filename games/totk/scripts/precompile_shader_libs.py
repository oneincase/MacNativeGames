#!/usr/bin/env python3
"""Build the variant libraries a development tree has recorded, so later starts load them instead of compiling.

A development tree has no shader-libs folder (build_app.py step_shader_libs only builds the packaged app), so every shader
library the game needs is compiled on the submit thread the first time a draw uses it: 103 of them took 971 ms in one run,
and the frames that met them were the 1 s hitches (356 ms of libraries in the longest frame of a Kokiri Forest run).
nvn_mtl.m records the exact source it compiled in <chain cache>/variants/<sha256 of that source>.metal, which is the name
shader_lib_prebuilt looks for in build/shader-libs. This compiles those sources there, with the app's Metal flags.

Run it after a session that met new shaders; the next session loads them (the log says 预编译加载 N 个).

usage: precompile_shader_libs.py [--cache <chain cache>] [--out build/shader-libs] [--jobs N]
"""
import argparse
import concurrent.futures
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]        # games/totk
METAL_FLAGS = ['-fmetal-math-mode=safe']          # build_app.py METAL_FLAGS, the flags a packaged app compiles with


def chain_cache() -> Path:
    header = (ROOT / 'third_party/d2r_gfx/gfx/nvn/dmsh_chain.h').read_text()
    m = re.search(r'#define\s+DMSH_CHAIN\s+"([^"]+)"', header)
    if not m:
        raise SystemExit('no DMSH_CHAIN in dmsh_chain.h')
    found = sorted((ROOT / 'build/shader-cache').glob('rt-' + m.group(1) + '*'), key=lambda p: p.stat().st_mtime)
    return found[-1] if found else ROOT / 'build/shader-cache' / ('rt-' + m.group(1))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--cache', type=Path, help='the chain cache (default: build/shader-cache/rt-<DMSH_CHAIN>, newest)')
    ap.add_argument('--out', type=Path, default=ROOT / 'build/shader-libs')
    ap.add_argument('--jobs', type=int, default=os.cpu_count() or 4)
    args = ap.parse_args()
    cache = args.cache or chain_cache()
    src_dir = cache / 'variants'
    if not src_dir.is_dir():
        print(f'{src_dir}: nothing recorded yet (play once; nvn_mtl.m records each library it compiles)', file=sys.stderr)
        return 1
    metal = subprocess.run(['xcrun', '-find', 'metal'], capture_output=True, text=True).stdout.strip()
    if not metal:
        print('no Metal compiler (xcodebuild -downloadComponent MetalToolchain)', file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    todo = [(s, args.out / (s.stem + '.metallib')) for s in sorted(src_dir.glob('*.metal'))
            if not (args.out / (s.stem + '.metallib')).is_file()]
    print(f'{len(todo)} of {len(list(src_dir.glob("*.metal")))} recorded sources to compile -> {args.out}')

    def one(item):
        src, out = item
        partial = out.with_suffix('.partial')
        r = subprocess.run([metal, *METAL_FLAGS, '-o', partial, src], capture_output=True, text=True)
        if r.returncode:
            partial.unlink(missing_ok=True)
            return f'{src.name}: {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "failed"}'
        partial.replace(out)
        return None

    failed = []
    with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
        for i, err in enumerate(pool.map(one, todo), 1):
            if err:
                failed.append(err)
            if i % 100 == 0 or i == len(todo):
                print(f'  {i}/{len(todo)}  ({len(failed)} failed)', flush=True)
    if failed:
        log = args.out / 'precompile-failed.txt'
        log.write_text('\n'.join(failed) + '\n')
        print(f'{len(failed)} did not compile; see {log} (the runtime compiles those in play)')
    print('done')
    return 0


if __name__ == '__main__':
    sys.exit(main())
