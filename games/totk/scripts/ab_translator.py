#!/usr/bin/env python3
"""A/B two shader-versions of the translator on one host binary, alternating the arms run by run.

The host runs the native service at build/shader-service/totk_xlate (totk_shader_rt.m service_start), with no switch for
another path, so the arms are the same program slot: this installs each arm's binary before its runs and gives each arm
its own shader cache (TOTK_SHADER_CACHE), since the two translators write different MSL for the same variant. The host
binary itself is shared, so the chain and everything the replay does are equal in both arms; the cache of each arm is
warmed by the first runs (the runs that populate it are the ones the game spends translating).

Each repetition runs both benches for both arms, arm A then arm B, and the next repetition starts over, so a machine that
gets slower or hotter affects the arms alike:

  warmup_bench.py   the first seconds after the world starts: long frames, library loads, fps
  perf_baseline.py  a settled window: frame interval p50/p95/p99, fps, 1% low, and the GPU's own span per frame
                    (@gpu_ms of DM_NVN_TSV, Metal's command buffer timestamps: the pass table is coarse on macOS 27.2)

usage: ab_translator.py --a build/xlate-ab/new/totk_xlate --b build/xlate-ab/old/totk_xlate \\
           --runs 3 --seconds 30 [--warmup 25] [--skip-warmup|--skip-perf] [--label-prefix ldg128]

The binary at build/shader-service/totk_xlate when the run starts is put back when it ends (its copy is kept as
build/shader-service/totk_xlate.ab-orig), so install the build the tree should keep before starting.
"""
import argparse
import collections
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]         # games/totk
SLOT = ROOT / 'build/shader-service/totk_xlate'
OUT = ROOT / 'analysis/ab/translator'


def install(binary: Path, saved: Path):
    if not saved.exists():
        shutil.copy2(SLOT, saved)
    shutil.copy2(binary, SLOT)
    os.chmod(SLOT, 0o755)


def run(cmd, log):
    print('  $', ' '.join(str(c) for c in cmd), flush=True)
    with log.open('a') as lf:
        r = subprocess.run(cmd, cwd=ROOT, stdout=lf, stderr=subprocess.STDOUT)
    return r.returncode


def bench_warmup(arm, rep, seconds, cache):
    out = OUT / 'warmup' / f'{arm}-{rep}.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    rc = run([sys.executable, 'scripts/warmup_bench.py', '--runs', '1', '--seconds', str(seconds),
              '--a', f'TOTK_SHADER_CACHE={cache}', '--out', str(out.relative_to(ROOT))], OUT / 'driver.log')
    return json.loads(out.read_text())['median']['A'] if rc == 0 and out.exists() else None


def bench_perf(arm, rep, seconds, warmup, cache, passgpu):
    out = OUT / 'perf' / f'{arm}-{rep}'
    tsv = OUT / 'perf' / f'{arm}-{rep}-nvn.tsv'
    cmd = [sys.executable, 'scripts/perf_baseline.py', '--binary', 'build/totk-native-oot', '--game', 'rewritten/oot',
           '--warmup', str(warmup), '--seconds', str(seconds), '--label', f'{arm}{rep}',
           '--env', f'TOTK_SHADER_CACHE={cache}', '--env', f'DM_NVN_TSV={tsv}', '--out', str(out.relative_to(ROOT))]
    if passgpu:
        cmd.append('--passgpu')
    rc = run(cmd, OUT / 'driver.log')
    report = out / 'report.json'
    if rc != 0 or not report.exists():
        return None
    r = json.loads(report.read_text())
    r['gpu_tsv'] = gpu_stats(out / 'window.tsv', tsv)
    return r


def gpu_stats(window, tsv):
    """p50/p95/p99 of the GPU's own span (@gpu_ms, Metal's command buffer timestamps) over the recorded frames.

    perf_baseline's pass table (DM_NVN_PASSGPU) is coarse on macOS 27.2 / this GPU (a frame's passes add up to ~0.5 ms
    against a 28 ms command buffer span), so the per-frame @gpu_ms row of DM_NVN_TSV is the number to read."""
    if not window.exists() or not tsv.exists():
        return {}
    frames = {int(line.split()[0]) for line in window.read_text().splitlines() if line.split()}
    vals = []
    draws = collections.defaultdict(list)
    for line in tsv.read_text(errors='replace').splitlines():
        p = line.split('\t')
        if len(p) != 3:
            continue
        try:
            if int(p[0]) not in frames:
                continue
        except ValueError:
            continue
        if p[1] == '@gpu_ms':
            try:
                v = float(p[2])
                if 0 < v < 1000:      # a span over a second is a stall, not this frame's GPU work
                    vals.append(v)
            except ValueError:
                pass
        elif p[1] in ('@draws', '@passes'):
            draws[p[1]].append(float(p[2]))
    if not vals:
        return {}
    v = sorted(vals)
    def pct(p):
        return round(v[min(len(v) - 1, len(v) * p // 100)], 2)
    return {'frames': len(v), 'mean': round(statistics.fmean(v), 2), 'p50': pct(50), 'p95': pct(95),
            'p99': pct(99), 'max': round(v[-1], 2),
            'draws': round(statistics.fmean(draws['@draws']), 1) if draws['@draws'] else None,
            'passes': round(statistics.fmean(draws['@passes']), 1) if draws['@passes'] else None}


def med(rows, path):
    vals = []
    for r in rows:
        cur = r
        for k in path:
            cur = (cur or {}).get(k)
            if cur is None:
                break
        if isinstance(cur, (int, float)):
            vals.append(float(cur))
    return round(statistics.median(vals), 2) if vals else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--a', required=True, help='translator of arm A (the new build)')
    ap.add_argument('--b', required=True, help='translator of arm B (the reference build)')
    ap.add_argument('--runs', type=int, default=3, help='repetitions; each one runs both arms')
    ap.add_argument('--seconds', type=float, default=30.0)
    ap.add_argument('--warmup', type=float, default=25.0, help='perf_baseline: settled seconds before the window')
    ap.add_argument('--passgpu', action='store_true', help='perf_baseline --passgpu (GPU busy ms; crashed once on macOS 27.2)')
    ap.add_argument('--skip-warmup', action='store_true')
    ap.add_argument('--skip-perf', action='store_true')
    ap.add_argument('--label-prefix', default='ab')
    ap.add_argument('--cache-a', type=Path, help='arm A cache (default analysis/ab/translator/cache-a)')
    ap.add_argument('--cache-b', type=Path, help='arm B cache (default analysis/ab/translator/cache-b)')
    args = ap.parse_args()

    arms = {'a': Path(args.a).resolve(), 'b': Path(args.b).resolve()}
    for name, path in arms.items():
        if not path.is_file():
            raise SystemExit(f'arm {name}: no translator at {path}')
    OUT.mkdir(parents=True, exist_ok=True)
    saved = ROOT / 'build/shader-service/totk_xlate.ab-orig'
    caches = {'a': (args.cache_a or OUT / 'cache-a').resolve(), 'b': (args.cache_b or OUT / 'cache-b').resolve()}
    for c in caches.values():
        c.mkdir(parents=True, exist_ok=True)
    (OUT / 'driver.log').write_text(f'# {time.strftime("%Y-%m-%d %H:%M:%S")} {args.a} vs {args.b}\n')

    warm = {name: [] for name in arms}
    perf = {name: [] for name in arms}
    try:
        for rep in range(1, args.runs + 1):
            # The arm run first in a repetition meets the colder, quieter machine and the earlier world state (the game's
            # idle scene keeps evolving: 1,637 draws a frame early in a session against 1,848 later), so the order flips
            # every repetition and the paired difference is read across a balanced set.
            order = ['a', 'b'] if rep % 2 else ['b', 'a']
            for name in order:
                path = arms[name]
                install(path, saved)
                print(f'[ab] repetition {rep} arm {name.upper()} ({path.name})', flush=True)
                if not args.skip_warmup:
                    r = bench_warmup(name, rep, args.seconds, caches[name])
                    print(f'  warmup: {r}', flush=True)
                    if r:
                        warm[name].append(r)
                if not args.skip_perf:
                    r = bench_perf(name, rep, args.seconds, args.warmup, caches[name], args.passgpu)
                    if r:
                        r['arm'] = name
                        r['rep'] = rep
                        perf[name].append(r)
                        g = r.get('gpu_tsv') or {}
                        iv = r.get('interval_ms') or {}
                        print(f"  perf: fps {r.get('fps_mean') or 0:.1f} p50 {iv.get('p50') or 0:.2f} "
                              f"p95 {iv.get('p95') or 0:.2f} p99 {iv.get('p99') or 0:.2f} "
                              f"GPU p50 {g.get('p50')} p95 {g.get('p95')} p99 {g.get('p99')} ms", flush=True)
    finally:
        if saved.exists():
            shutil.copy2(saved, SLOT)
            os.chmod(SLOT, 0o755)
            print(f'[ab] restored build/shader-service/totk_xlate from {saved.name}', flush=True)

    summary = {'arms': {k: str(v) for k, v in arms.items()}, 'runs': args.runs, 'seconds': args.seconds,
               'warmup_s': args.warmup, 'warmup_bench': {}, 'perf': {}}
    for name in arms:
        w, p = warm[name], perf[name]
        summary['warmup_bench'][name] = {
            'runs': len(w),
            'long_n': med(w, ['long_n']), 'long_ms': med(w, ['long_ms']), 'long_max': med(w, ['long_max']),
            'lib_n': med(w, ['lib_n']), 'lib_ms': med(w, ['lib_ms']), 'frames': med(w, ['frames']), 'fps': med(w, ['fps'])}
        summary['perf'][name] = {
            'runs': len(p), 'fps_mean': med(p, ['fps_mean']), 'fps_1pct_low': med(p, ['fps_1pct_low']),
            'interval_p50': med(p, ['interval_ms', 'p50']), 'interval_p95': med(p, ['interval_ms', 'p95']),
            'interval_p99': med(p, ['interval_ms', 'p99']), 'interval_max': med(p, ['interval_ms', 'max']),
            'gpu_ms_p50': med(p, ['gpu_tsv', 'p50']), 'gpu_ms_p95': med(p, ['gpu_tsv', 'p95']),
            'gpu_ms_p99': med(p, ['gpu_tsv', 'p99']), 'gpu_ms_mean': med(p, ['gpu_tsv', 'mean']),
            'gpu_busy_ms': med(p, ['gpu', 'gpu_busy_ms_per_frame']),
            'draws': med(p, ['gpu_tsv', 'draws']),
            'cpu_ms': med(p, ['submit', 'cpu_ms_per_frame']), 'wall_ms': med(p, ['submit', 'wall_ms_per_frame']),
            'wired_peak_mb': med(p, ['wired_peak_mb'])}
    summary['label'] = args.label_prefix
    (OUT / f'summary-{args.label_prefix}.json').write_text(json.dumps(summary, indent=2) + '\n')

    print(f"\n=== {args.label_prefix}: median per arm ({args.runs} repetitions) ===")
    print(f"{'':28s}{'A (new)':>12s}{'B (ref)':>12s}{'A-B':>10s}")
    def line(label, key, sub=None):
        a = summary['perf']['a'][key] if sub is None else summary['perf']['a'][sub][key]
        b = summary['perf']['b'][key] if sub is None else summary['perf']['b'][sub][key]
        d = (a - b) if isinstance(a, (int, float)) and isinstance(b, (int, float)) else None
        print(f'{label:28s}{a if a is not None else "-":>12}{b if b is not None else "-":>12}'
              f'{("%+.2f" % d) if d is not None else "-":>10}')
    for label, key in (('fps', 'fps_mean'), ('fps 1% low', 'fps_1pct_low'), ('p50 ms', 'interval_p50'),
                       ('p95 ms', 'interval_p95'), ('p99 ms', 'interval_p99'), ('max ms', 'interval_max'),
                       ('GPU p50 ms', 'gpu_ms_p50'), ('GPU p95 ms', 'gpu_ms_p95'), ('GPU p99 ms', 'gpu_ms_p99'),
                       ('GPU mean ms', 'gpu_ms_mean'), ('draws/frame', 'draws'),
                       ('submit cpu ms', 'cpu_ms'), ('wired peak MB', 'wired_peak_mb')):
        line(label, key)
    for label, key in (('warmup: long frames', 'long_n'), ('warmup: their ms', 'long_ms'),
                       ('warmup: longest ms', 'long_max'), ('warmup: fps', 'fps')):
        a, b = summary['warmup_bench']['a'][key], summary['warmup_bench']['b'][key]
        d = (a - b) if isinstance(a, (int, float)) and isinstance(b, (int, float)) else None
        print(f'{label:28s}{a if a is not None else "-":>12}{b if b is not None else "-":>12}'
              f'{("%+.2f" % d) if d is not None else "-":>10}')
    pairs = {}
    a_rows = {r['rep']: r for r in perf['a']}
    b_rows = {r['rep']: r for r in perf['b']}
    for rep in sorted(set(a_rows) & set(b_rows)):
        a_row, b_row = a_rows[rep], b_rows[rep]
        if not isinstance(a_row.get('fps_mean'), (int, float)) or not isinstance(b_row.get('fps_mean'), (int, float)):
            continue
        pairs[rep] = {
            'fps': round(a_row['fps_mean'] - b_row['fps_mean'], 2),
            'gpu_p50': round((a_row.get('gpu_tsv') or {}).get('p50', 0) - (b_row.get('gpu_tsv') or {}).get('p50', 0), 2),
            'draws': round(((a_row.get('gpu_tsv') or {}).get('draws') or 0) -
                           ((b_row.get('gpu_tsv') or {}).get('draws') or 0), 1)}
    summary['paired'] = pairs
    if pairs:
        print(f'\npaired per repetition (A - B): ' + '  '.join(
            f"rep{rep}: fps {d['fps']:+.2f}, GPU p50 {d['gpu_p50']:+.2f} ms, draws {d['draws']:+.0f}"
            for rep, d in pairs.items()))
        for key, label in (('fps', 'paired fps'), ('gpu_p50', 'paired GPU p50 ms')):
            print(f'{label:28s}{statistics.median([d[key] for d in pairs.values()]):+.2f}')
    print(f'\n{OUT}/summary-{args.label_prefix}.json')
    return 0


if __name__ == '__main__':
    sys.exit(main())
