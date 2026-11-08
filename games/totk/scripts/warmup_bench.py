#!/usr/bin/env python3
"""Repeat one measurement of a game's world load, alternating two arms, and report medians.

The hitches that a warm-up or a shader cache change affects happen in the first seconds after a world starts, which the
steady-state window of perf_baseline.py deliberately leaves out. This runs the same save N times per arm, alternating the
arms run by run so a machine that gets slower or hotter across the session affects both alike, and reports for each arm:

  long frames       game frames over 100 ms after the world starts (count, their total, the longest)
  library loads     the runtime's 「metallib 载入 N 个 / X ms」 at the end of the window: how much library work play paid
  warm-up           「管线预热完成：着色器 N 个 … 后台用时 X ms」: what was moved behind the loading screen
  frames            all recorded frames of the window and the fps the window implies

usage: warmup_bench.py --runs 5 --seconds 45 --a TOTK_WARM_LIBS=1 --b TOTK_WARM_LIBS=0
       warmup_bench.py --runs 5 --seconds 45              (A = defaults, B not run)
"""
import argparse
import json
import os
import re
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # games/totk
LAUNCH_GPU = {'TOTK_GPU_ALLOW_SUBMIT': '1', 'TOTK_GPU_WIDEN': '1', 'TOTK_GPU_BLOCKRT': '1', 'TOTK_GPU_MIDCLEAR': '1'}
LONG = re.compile(r'整帧 ([\d.]+) ms')
LIB = re.compile(r'metallib 载入 (\d+) 个 / ([\d.]+) ms')
WARM = re.compile(r'管线预热完成：着色器 (\d+) 个 .*?后台用时 ([\d.]+) ms')
FRAME = re.compile(r'^(\d+) (\d+)$', re.M)


def parse(log: Path):
    text = log.read_text(errors='replace')
    long_frames = [float(x) for x in LONG.findall(text) if float(x) > 100.0]
    libs = [(int(n), float(ms)) for n, ms in LIB.findall(text)]
    warm = [(int(n), float(ms)) for n, ms in WARM.findall(text)]
    frames = [(int(a), int(b)) for a, b in FRAME.findall((log.parent / 'frames.tsv').read_text(errors='replace'))]
    frames.sort()
    iv = [(b[1] - a[1]) / 1e6 for a, b in zip(frames, frames[1:])]
    return {
        'long_n': len(long_frames), 'long_ms': round(sum(long_frames), 0), 'long_max': round(max(long_frames), 0) if long_frames else 0,
        'lib_n': libs[-1][0] if libs else 0, 'lib_ms': round(libs[-1][1], 0) if libs else 0,
        'warm_shaders': warm[-1][0] if warm else 0, 'warm_ms': round(warm[-1][1], 0) if warm else 0,
        'frames': len(iv), 'fps': round(1000.0 / statistics.fmean(iv), 1) if iv else 0,
    }


def run_once(env_extra: dict, seconds: float, tag: str, boot_timeout: float = 300.0):
    out = ROOT / 'analysis' / 'warmup-bench' / tag
    out.mkdir(parents=True, exist_ok=True)
    log = out / 'run.log'
    frames = out / 'frames.tsv'
    env = {k: v for k, v in os.environ.items() if not k.startswith(('TOTK_', 'DM_'))}
    env.update(LAUNCH_GPU)
    env.update({'TOTK_AUTOLOAD': '1', 'TOTK_FPS_OVERLAY': '0', 'DM_BOOTPROF': '1', 'TOTK_BENCH_FRAMES': str(frames)})
    env.update(env_extra)
    frames.unlink(missing_ok=True)
    with log.open('w') as lf:
        proc = subprocess.Popen([str(ROOT / 'build/totk-native-oot'), 'rewritten/oot', 'boot'], cwd=ROOT, env=env,
                                stdout=lf, stderr=subprocess.STDOUT)
        try:
            t_world, deadline = None, time.time() + boot_timeout
            while time.time() < deadline:
                time.sleep(0.5)
                if proc.poll() is not None:
                    raise SystemExit(f'{tag}: the game exited early, see {log}')
                if t_world is None and 'game speed:' in log.read_text(errors='replace'):
                    t_world = time.time()
                if t_world and time.time() - t_world >= seconds:
                    break
            if t_world is None:
                raise SystemExit(f'{tag}: the game never started playing frames, see {log}')
        finally:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
    return parse(log)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--runs', type=int, default=5, help='runs per arm')
    ap.add_argument('--seconds', type=float, default=45.0, help='seconds recorded after the world starts')
    ap.add_argument('--a', action='append', default=[], metavar='KEY=VAL', help='environment of arm A (defaults: none)')
    ap.add_argument('--b', action='append', default=[], metavar='KEY=VAL', help='environment of arm B (omit to run only A)')
    ap.add_argument('--out', default='analysis/warmup-bench/summary.json')
    args = ap.parse_args()

    def env_of(items):
        e = {}
        for item in items:
            k, _, v = item.partition('=')
            e[k] = v
        return e

    arms = [('A', env_of(args.a))]
    if args.b:
        arms.append(('B', env_of(args.b)))
    results = {name: [] for name, _ in arms}
    for i in range(args.runs):
        for name, envd in arms:                       # alternate within a repetition, so drift hits both arms
            tag = f'{time.strftime("%H%M%S")}-{name}{i + 1}'
            r = run_once(envd, args.seconds, tag)
            r['tag'] = tag
            results[name].append(r)
            print(f"  {name}{i + 1}: long>{100}ms {r['long_n']} / {r['long_ms']:.0f} ms (max {r['long_max']:.0f}) | "
                  f"libs {r['lib_n']} / {r['lib_ms']:.0f} ms | warm {r['warm_shaders']} shaders / {r['warm_ms']:.0f} ms | "
                  f"{r['frames']} frames, {r['fps']:.1f} fps", flush=True)
    summary = {}
    for name, rows in results.items():
        if not rows:
            continue
        summary[name] = {k: round(statistics.median([r[k] for r in rows]), 1)
                         for k in ('long_n', 'long_ms', 'long_max', 'lib_n', 'lib_ms', 'warm_shaders', 'warm_ms', 'frames', 'fps')}
        summary[name]['runs'] = len(rows)
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({'seconds': args.seconds, 'arms': {k: v for k, v in
                                                                (('A', args.a), ('B', args.b)) if v or k == 'A'},
                               'median': summary, 'runs': results}, indent=2) + '\n')
    print('\nmedian per arm (' + ', '.join(f'{n}={len(r)} runs' for n, r in results.items() if r) + ')')
    keys = [('long_n', 'long frames >100ms'), ('long_ms', 'total of them (ms)'), ('long_max', 'longest (ms)'),
            ('lib_n', 'libraries loaded in play'), ('lib_ms', 'their time (ms)'), ('warm_shaders', 'warm-up shaders'),
            ('warm_ms', 'warm-up time (ms)'), ('frames', 'frames'), ('fps', 'fps')]
    print(f"{'':26s}" + ''.join(f'{n:>12s}' for n in summary))
    for k, label in keys:
        print(f'{label:26s}' + ''.join(f'{summary[n][k]:12.1f}' for n in summary))
    print(f'\n{out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
