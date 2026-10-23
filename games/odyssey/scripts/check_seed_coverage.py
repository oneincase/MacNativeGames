#!/usr/bin/env python3
"""Offline check: would the runtime pick a shader-seed variant for each variant in a live translation cache?

Follows totk_shader_rt.m totk_rt_variant: a cached variant of the same shader key is used when deps_match (every slot it
recorded equals the bound state, unrecorded slots taking the defaults tex=2d, attr=float, fout=float) and, for a vertex
shader, its depth_minus_w equals the device's depth mode. The bound state of each live variant is taken to be its own
recorded deps (the translator asked exactly those slots). Prints counts only; reads keys and deps, never shader bytes.

usage: check_seed_coverage.py <live cache dir> <seed dir> [--json out.json]
"""
import argparse
from collections import Counter
import json
from pathlib import Path

DEFAULTS = {'tex': '2d', 'img': None, 'attr': 'float', 'fout': 'float'}


def load_variants(folder):
    out = {}
    for path in folder.glob('*.variants'):
        try:
            rows = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        out[path.stem] = [r for r in rows if isinstance(r, dict) and 'variant' in r]
    return out


def stage_of(folder, variant):
    try:
        return json.loads((folder / f'{variant}_b.json').read_text()).get('stage')
    except (OSError, ValueError):
        return None


def mismatch(seed_deps, bound):
    """Kinds/answers on which a seed variant disagrees with the bound state (empty = deps_match)."""
    bad = []
    for kind, default in DEFAULTS.items():
        for slot, want in (seed_deps.get(kind) or {}).items():
            have = (bound.get(kind) or {}).get(slot, default)
            if have != want:
                bad.append(f'{kind}:{have}')
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('live', type=Path)
    ap.add_argument('seed', type=Path)
    ap.add_argument('--json', type=Path)
    a = ap.parse_args()
    live, seed = load_variants(a.live), load_variants(a.seed)
    result, reasons = Counter(), Counter()
    unmatched = []
    for key, rows in live.items():
        for row in rows:
            bound = row.get('deps') or {}
            stage = stage_of(a.live, row['variant'])
            candidates = seed.get(key)
            if not candidates:
                result['unmatched'] += 1; reasons['shader key not in seed'] += 1
                unmatched.append((key, 'key missing')); continue
            why = []
            for cand in candidates:
                bad = mismatch(cand.get('deps') or {}, bound)
                if stage == 'vs' and bool(cand.get('depth_minus_w')) != bool(row.get('depth_minus_w')):
                    bad.append(f"depth_minus_w:{bool(row.get('depth_minus_w'))}")
                if not bad:
                    break
                why.append(bad)
            else:
                result['unmatched'] += 1
                first = min(why, key=len)
                for b in sorted(set(first)):
                    reasons[b] += 1
                unmatched.append((key, ','.join(sorted(set(first)))))
                continue
            result['matched'] += 1
    total = result['matched'] + result['unmatched']
    print(f"live variants {total} (shaders {len(live)}); seed shaders {len(seed)} "
          f"(variants {sum(len(v) for v in seed.values())})")
    print(f"matched {result['matched']} / {total}; unmatched {result['unmatched']}")
    for reason, n in reasons.most_common():
        print(f'  {n:5}  {reason}')
    if a.json:
        a.json.write_text(json.dumps({'matched': result['matched'], 'unmatched': result['unmatched'],
                                      'reasons': dict(reasons), 'unmatched_variants': unmatched}, indent=1))


if __name__ == '__main__':
    main()
