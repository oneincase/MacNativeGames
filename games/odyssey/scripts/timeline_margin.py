#!/usr/bin/env python3
"""Per-frame present margin from a TOTK_TIMELINE file (task 0n): the `margin` events (totk_timeline.h TL_MARGIN), written
when a present is committed on the display link. a = microseconds before its target refresh, b = microseconds from the
game's enqueue to that refresh. Prints the distribution, how many presents had less than one refresh of margin, and the
frames with the smallest margins, so a latency A/B can be read frame by frame.
usage: timeline_margin.py <timeline file> [--hz 60] [--list N]"""
import argparse
import statistics


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(p / 100 * len(v)))] if v else float('nan')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('timeline')
    ap.add_argument('--hz', type=float, default=60)
    ap.add_argument('--list', type=int, default=10)
    a = ap.parse_args()
    rows = []
    for line in open(a.timeline):
        if line.startswith('#'):
            continue
        f = line.rstrip('\n').split('\t')
        if len(f) == 6 and f[1] == 'margin':
            rows.append((int(f[3]), int(f[4]) / 1000.0, int(f[5]) / 1000.0))   # frame, margin ms, enqueue->refresh ms
    if not rows:
        raise SystemExit('no margin events (was the present on the display link, and TOTK_TIMELINE set?)')
    m = [r[1] for r in rows]; e = [r[2] for r in rows if r[2] > 0]
    refresh = 1000.0 / a.hz
    print(f'presents {len(rows)}: margin p50 {statistics.median(m):.2f} / p5 {pct(m, 5):.2f} / min {min(m):.2f} ms; '
          f'below one refresh ({refresh:.2f} ms): {sum(x < refresh for x in m)}')
    if e:
        print(f'enqueue -> target refresh p50 {statistics.median(e):.2f} / p95 {pct(e, 95):.2f} / max {max(e):.2f} ms')
    print('smallest margins (frame, margin ms, enqueue->refresh ms):')
    for r in sorted(rows, key=lambda r: r[1])[:a.list]:
        print(f'  {r[0]:8} {r[1]:8.2f} {r[2]:8.2f}')


if __name__ == '__main__':
    main()
