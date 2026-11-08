#!/usr/bin/env python3
"""Compare two pictures of the same scene: the brightness, saturation and contrast of the whole picture, and the pixels
that differ, so a rendering change can be shown to leave the picture alone (or to be caught doing otherwise).

Metrics over both pictures (sRGB-encoded values as stored, 0..1):
  luminance  Y = 0.2126 R + 0.7152 G + 0.0722 B, its mean and standard deviation (the contrast)
  saturation (max - min) / max of the three channels, per pixel, its mean (a pixel with max 0 is 0)
The pictures are compared as they are; give PNGs of the same size from the same save and camera. A small difference in
the game's own animation (a few frames apart) shows up in the per-pixel numbers, so read those against the means.

usage: picture_compare.py --a new.png --b old.png [--json out.json]
       (needs numpy and Pillow: /Volumes/bit2t/switch2/venv_oot/bin/python scripts/diag/picture_compare.py ...)
"""
import argparse
import json
import sys

import numpy as np
from PIL import Image

LIMIT = 0.01          # 1 %: the gate's bound on each whole-picture metric


def stats(path):
    a = np.asarray(Image.open(path).convert('RGB'), dtype=np.float32) / 255.0
    y = 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]
    mx = a.max(axis=2)
    mn = a.min(axis=2)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0.0)
    return {'size': [int(a.shape[1]), int(a.shape[0])], 'luminance': float(y.mean()),
            'contrast': float(y.std()), 'saturation': float(sat.mean())}, a


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--a', required=True)
    ap.add_argument('--b', required=True)
    ap.add_argument('--json')
    args = ap.parse_args()

    sa, a = stats(args.a)
    sb, b = stats(args.b)
    if sa['size'] != sb['size']:
        raise SystemExit(f'different sizes: {sa["size"]} vs {sb["size"]}')
    rel = {k: (abs(sa[k] - sb[k]) / sb[k] if sb[k] else 0.0) for k in ('luminance', 'contrast', 'saturation')}
    d = np.abs(a - b).max(axis=2)
    pixel = {'mean_abs': float(d.mean()), 'p99_abs': float(np.percentile(d, 99)), 'max_abs': float(d.max()),
             'over_8_255': float((d > 8.0 / 255.0).mean()), 'over_32_255': float((d > 32.0 / 255.0).mean())}
    ok = all(v <= LIMIT for v in rel.values())
    print(f"picture {sa['size'][0]}x{sa['size'][1]}")
    print(f"{'':12s}{'A (new)':>12s}{'B (ref)':>12s}{'relative':>10s}   (bound {LIMIT:.0%})")
    for k in ('luminance', 'contrast', 'saturation'):
        print(f'{k:12s}{sa[k]:12.4f}{sb[k]:12.4f}{rel[k]:9.2%} {"ok" if rel[k] <= LIMIT else "OVER"}')
    print(f"pixels: mean |d| {pixel['mean_abs']:.4f}  p99 {pixel['p99_abs']:.4f}  max {pixel['max_abs']:.4f}  "
          f">8/255 {pixel['over_8_255']:.2%}  >32/255 {pixel['over_32_255']:.2%}")
    print('verdict: picture unchanged within 1 %' if ok else 'verdict: the whole-picture metrics moved over 1 %')
    if args.json:
        with open(args.json, 'w') as f:
            json.dump({'a': args.a, 'b': args.b, 'stats_a': sa, 'stats_b': sb, 'relative': rel,
                       'pixels': pixel, 'limit': LIMIT, 'ok': ok}, f, indent=2)
            f.write('\n')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
