#!/usr/bin/env python3
"""perf_baseline.py: one reproducible performance run of a game build in the development tree.

The measurements the port already makes are written by the runtime and are enough to attribute a frame:
  TOTK_BENCH_FRAMES=<file>   "<frame> <ns>" at every game frame (hle_nvn.c), for interval statistics
  DM_NVN_PASSGPU=<file>      per-pass GPU time with the timeline increment (nvn_mtl.m), whose sum over a frame is
                             the time the GPU was busy; the "长帧" lines give each frame's GPU and CPU side
  the log                    「翻译细项」(submit cycles / CPU / wall), 「渲染通道」, 「分段提交」, 「拷贝编码器」,
                             memory readouts, and the long-frame causes

This script starts a game, waits until it has been playing for --warmup seconds (so cold pipelines and shader
translation stay out of the window), records for --seconds, then writes a report (JSON + a text summary):
frame interval p50/p95/p99, mean fps, 1 % low, the GPU time of the window, the passes that cost the most GPU time,
and the CPU-side per-frame numbers. Two reports can be compared with --compare.

A game that can load a save by itself uses TOTK_AUTOLOAD=1 (runtime/autoload.c); otherwise the run has to be
brought into the world by hand, and the window still starts once the world runs (--warmup counts from the first
"game speed" line, which the game prints once it steps frames).

usage: perf_baseline.py --binary build/totk-native-oot --game rewritten/oot --out analysis/perf/base
       perf_baseline.py --compare analysis/perf/a.json analysis/perf/b.json
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

ROOT = Path(__file__).resolve().parents[1]
LAUNCH_GPU = {   # the launcher's GPU settings (games/oot/README.md)
    'TOTK_GPU_ALLOW_SUBMIT': '1', 'TOTK_GPU_WIDEN': '1', 'TOTK_GPU_BLOCKRT': '1', 'TOTK_GPU_MIDCLEAR': '1'}


def read_frames(path):
    """The game frames of the recording: (frame, interval_ms)."""
    rows = []
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            rows.append((int(parts[0]), int(parts[1])))
        except ValueError:
            continue
    rows.sort()
    return [(b[0], (b[1] - a[1]) / 1e6) for a, b in zip(rows, rows[1:])]


def pct(values, p):
    if not values:
        return 0.0
    v = sorted(values)
    return v[min(len(v) - 1, (len(v) * p) // 100)]


def frames_report_of(iv):
    if not iv:
        return {}
    mean = statistics.fmean(iv)
    low = sorted(iv, reverse=True)
    n1 = max(1, len(iv) // 100)
    return {'frames': len(iv), 'fps_mean': 1000.0 / mean, 'interval_ms': {'p50': pct(iv, 50), 'p95': pct(iv, 95),
            'p99': pct(iv, 99), 'max': max(iv)}, 'fps_1pct_low': 1000.0 / statistics.fmean(low[:n1]),
            'over_16_7': sum(1 for x in iv if x > 16.7)}


def passgpu_report(path):
    """Per-pass GPU time for the recorded frames: inc_ms sums to the GPU's busy time in the frame."""
    if not path.exists():
        return {}
    rows = []
    for line in path.read_text().splitlines():
        p = line.split('\t')
        if len(p) < 13 or not p[0].isdigit():
            continue
        rows.append({'frame': int(p[0]), 'idx': int(p[1]), 'kind': p[2], 'gpu_ms': float(p[3]),
                     'inc_ms': float(p[4]), 'w': int(p[5]), 'h': int(p[6]), 'load': p[9], 'draws': int(p[10])})
    if not rows:
        return {}
    frames = sorted({r['frame'] for r in rows})
    keep = set(frames[-max(1, len(frames) // 4):])          # the last quarter of the window, steady state
    rows = [r for r in rows if r['frame'] in keep]
    per_frame = {}
    for r in rows:
        per_frame.setdefault(r['frame'], []).append(r)
    # by pass index (a frame's pass n is the same pass next frame): the mean increment and what it draws
    agg = {}
    for f, rs in per_frame.items():
        for r in rs:
            a = agg.setdefault(r['idx'], {'kind': r['kind'], 'w': r['w'], 'h': r['h'], 'load': r['load'],
                                          'draws': [], 'inc': []})
            a['inc'].append(r['inc_ms'])
            a['draws'].append(r['draws'])
    for idx, a in agg.items():
        a['inc_ms_mean'] = statistics.fmean(a['inc'])
        a['draws_mean'] = statistics.fmean(a['draws'])
        a['passes_seen'] = len(a['inc'])
    total = statistics.fmean([sum(r['inc_ms'] for r in rs) for rs in per_frame.values()])
    top = sorted(({'idx': i, 'kind': a['kind'], 'size': f"{a['w']}x{a['h']}", 'load': a['load'],
                   'draws': round(a['draws_mean'], 1), 'inc_ms': round(a['inc_ms_mean'], 2),
                   'share': round(100 * a['inc_ms_mean'] / total, 1)} for i, a in agg.items()),
                 key=lambda r: -r['inc_ms'])
    return {'gpu_busy_ms_per_frame': round(total, 2), 'per_pass_count': len(agg), 'top_passes': top[:15]}


LOG_PATTERNS = {
    'submit': re.compile(r'翻译细项 至帧 \d+：每帧提交 周期 ([\d.]+) M（每条指令 ([\d.]+) 周期）、CPU ([\d.]+) ms 其中内核态 ([\d.]+) ms、墙钟 ([\d.]+) ms'),
    'passes': re.compile(r'渲染通道 至帧 \d+：每帧 ([\d.]+) 个，其中分层通道 ([\d.]+) 个'),
    'segments': re.compile(r'分段提交 至帧 \d+：每帧额外 ([\d.]+) 段'),
    'copies': re.compile(r'拷贝编码器 至帧 \d+：每帧 ([\d.]+) 个（其中打断渲染通道 ([\d.]+)）；uniform 更新 ([\d.]+) 次'),
    'pool': re.compile(r'\[pool\] up to frame \d+: (\d+) table walks, ([\d.]+) ms a frame'),
    'gpu_frame': re.compile(r'整帧 ([\d.]+) ms，提交\+出图 ([\d.]+) ms，GPU ([\d.]+) ms'),
    'mem': re.compile(r'system wired memory peak (\d+) MB|内存池 至帧 \d+：在册 (\d+) 个 / (\d+) MB'),
    'speed': re.compile(r'game speed: ([\d.]+) of real time \(([\d.]+) s of game time in ([\d.]+) s, ([\d.]+) fps\)'),
    'yield': re.compile(r'主线程让出 每帧 (\d+) 次 / ([\d.]+) ms'),
}


def log_report(text):
    out = {}
    sub = LOG_PATTERNS['submit'].findall(text)
    if sub:
        cyc, per, cpu, sys_, wall = (statistics.fmean(float(x) for x in t) for t in zip(*sub[-3:]))
        out['submit'] = {'cycles_m_per_frame': round(cyc, 2), 'cycles_per_insn': round(per, 3),
                         'cpu_ms_per_frame': round(cpu, 3), 'sys_ms_per_frame': round(sys_, 3),
                         'wall_ms_per_frame': round(wall, 3)}
    for name in ('passes', 'segments', 'copies', 'gpu_frame', 'pool', 'yield'):
        v = LOG_PATTERNS[name].findall(text)
        if v:
            out.setdefault('counters_raw', {})[name] = ' '.join(v[-1])
    mem = [m for m in re.findall(r'system wired memory peak (\d+) MB', text)]
    if mem:
        out['wired_peak_mb'] = int(mem[-1])
    pool = LOG_PATTERNS['mem'].findall(text)
    out.setdefault('counters_ms', {})
    sp = LOG_PATTERNS['speed'].findall(text)
    if sp:
        out['game_speed_last'] = {'ratio': float(sp[-1][0]), 'fps': float(sp[-1][3])}
    out.pop('counters_ms') if not out['counters_ms'] else None
    return out


def run(args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    frames = out / 'frames.tsv'
    passgpu = out / 'passgpu.tsv'
    log = out / 'run.log'
    env = {k: v for k, v in os.environ.items() if not k.startswith(('TOTK_', 'DM_'))}
    env.update(LAUNCH_GPU)
    env.update({'TOTK_AUTOLOAD': '1', 'TOTK_BENCH_FRAMES': str(frames), 'TOTK_FPS_OVERLAY': args.overlay})
    if args.passgpu:
        # Off by default: on macOS 27.2 with this GPU the stage-boundary counter sampling crashed the game at ~frame 630
        # (SIGSEGV in AGXMetalG17G) before it wrote a row, so a run with it must be treated as an experiment.
        env['DM_NVN_PASSGPU'] = str(passgpu)
    for item in args.env:
        k, _, v = item.partition('=')
        env[k] = v
    print(f'[perf] {args.binary} {args.game} → {out}  warmup {args.warmup}s, record {args.seconds}s', flush=True)
    with log.open('w') as lf:
        proc = subprocess.Popen([str(ROOT / args.binary), args.game, 'boot'], cwd=ROOT, env=env,
                                stdout=lf, stderr=subprocess.STDOUT)
        try:
            # Settled = the last SETTLE_FRAMES game frames (about 5 s of play) had no frame over SETTLE_MS, and the world
            # has been up for --warmup seconds: the load, world streaming and cold pipeline builds stay out of the window.
            SETTLE_MS, SETTLE_FRAMES = 100.0, 150
            t_world, settled_at = None, None
            deadline = time.time() + args.boot_timeout
            while time.time() < deadline:
                time.sleep(2)
                text = log.read_text(errors='replace')
                if 'game speed:' in text:
                    t_world = t_world or time.time()
                if proc.poll() is not None:
                    print('[perf] the game exited early; see', log, flush=True)
                    return 1
                iv = [ms for _, ms in read_frames(frames)] if frames.exists() else []
                if len(iv) >= SETTLE_FRAMES and max(iv[-SETTLE_FRAMES:]) < SETTLE_MS:
                    settled_at = settled_at or time.time()
                else:
                    settled_at = None if not t_world or time.time() - t_world > 120 else settled_at
                if settled_at and time.time() - settled_at >= args.warmup:
                    break
            if not settled_at:
                print('[perf] the game never settled (no 5 s of play under %g ms a frame); see' % SETTLE_MS, log,
                      flush=True)
                return 1
            record_from = len(read_frames(frames))
            print(f'[perf] settled at frame {record_from}; recording {args.seconds}s', flush=True)
            time.sleep(args.seconds)
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
        finally:
            if proc.poll() is None:
                proc.kill()
    iv_all = read_frames(frames)
    tail = iv_all[record_from:]
    (out / 'window.tsv').write_text(''.join(f'{f}\t{ms * 1e6:.0f}\n' for f, ms in
                                            [(iv_all[i][0], iv_all[i][1]) for i in range(record_from, len(iv_all))]))
    report = {'label': args.label or out.name, 'binary': args.binary, 'game': args.game,
              'window_segments': {'seconds': args.seconds, 'warmup_s': args.warmup},
              'env': {k: v for k, v in env.items() if k.startswith(('TOTK_', 'DM_'))}}
    report.update(frames_report_of([ms for _, ms in tail]))
    report['gpu'] = passgpu_report(passgpu)
    report.update(log_report(log.read_text(errors='replace')))
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print_summary(report)
    return 0


def print_summary(r):
    print(f"\n=== {r['label']} ===")
    if not r.get('frames'):
        print('no frames recorded'); return
    iv = r['interval_ms']
    print(f"frames {r['frames']}  fps {r['fps_mean']:.1f}  1% low {r['fps_1pct_low']:.1f}  "
          f"p50 {iv['p50']:.2f}  p95 {iv['p95']:.2f}  p99 {iv['p99']:.2f}  max {iv['max']:.1f} ms  "
          f">16.7ms {r['over_16_7']}")
    g = r.get('gpu') or {}
    if g:
        print(f"GPU busy {g['gpu_busy_ms_per_frame']:.2f} ms/frame over {g['per_pass_count']} passes")
        for p in g['top_passes'][:10]:
            print(f"   pass {p['idx']:3d} {p['kind']} {p['size']:>10s} draws {p['draws']:6.1f}  "
                  f"inc {p['inc_ms']:6.2f} ms  {p['share']:5.1f}%")
    s = r.get('submit')
    if s:
        print(f"submit cpu {s['cpu_ms_per_frame']:.2f} ms  wall {s['wall_ms_per_frame']:.2f} ms  "
              f"{s['cycles_m_per_frame']:.1f}M cycles/frame")
    c = r.get('counters_raw') or {}
    for k, line in c.items():
        print(f'  {k:9s} {line}')
    if r.get('wired_peak_mb'):
        print(f"wired peak {r['wired_peak_mb']} MB")
    if r.get('game_speed_last'):
        print(f"game speed {r['game_speed_last']['ratio']:.3f} of real time ({r['game_speed_last']['fps']:.1f} fps)")


def compare(paths):
    rs = [json.loads(Path(p).read_text()) for p in paths]
    keys = [('fps_mean', 'fps'), ('fps_1pct_low', '1% low')]
    print(f"{'':24s}" + ''.join(f"{r['label']:>16s}" for r in rs))
    for k, name in keys:
        print(f'{name:24s}' + ''.join(f"{r.get(k, 0):16.1f}" for r in rs))
    for k, name in (('p50', 'p50 ms'), ('p95', 'p95 ms'), ('p99', 'p99 ms')):
        print(f'{name:24s}' + ''.join(f"{r['interval_ms'][k]:16.2f}" for r in rs))
    for r in rs:
        g = r.get('gpu') or {}
        print(f"{'GPU busy ms':24s}{g.get('gpu_busy_ms_per_frame', 0):16.2f}" if r is rs[0] else
              f"{'':24s}{g.get('gpu_busy_ms_per_frame', 0):16.2f}", end='')
    print()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--binary', default='build/totk-native-oot')
    ap.add_argument('--game', default='rewritten/oot')
    ap.add_argument('--out')
    ap.add_argument('--label')
    ap.add_argument('--warmup', type=float, default=25.0, help='seconds of play before the window (cold pipelines out)')
    ap.add_argument('--seconds', type=float, default=30.0)
    ap.add_argument('--boot-timeout', type=float, default=600.0)
    ap.add_argument('--overlay', default='0', help='TOTK_FPS_OVERLAY: 0 keeps the diagnostic overlay (and its cost) out')
    ap.add_argument('--passgpu', action='store_true',
                    help='DM_NVN_PASSGPU per-pass GPU time (crashes on macOS 27.2 / AGX G17; keep it out of a baseline run)')
    ap.add_argument('--env', action='append', default=[], metavar='KEY=VAL')
    ap.add_argument('--compare', nargs='+', metavar='REPORT')
    args = ap.parse_args()
    if args.compare:
        compare(args.compare); return 0
    if not args.out:
        ap.error('--out is required unless --compare is used')
    return run(args)


if __name__ == '__main__':
    sys.exit(main())
