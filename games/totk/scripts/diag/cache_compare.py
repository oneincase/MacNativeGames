#!/usr/bin/env python3
"""Compare two shader caches of the same session: which variants the translator changed, and whether it changed them only
by turning a raw global load into a vector load.

A shader cache (<out dir of the shader service, totk_shader_rt.m totk_rt_dir) holds per variant: <variant>.code, _b.metal,
_b.json. Two runs of the same save and scene against two translator builds produce the same variant names (a variant is
named after the shader and its deps), so the caches are comparable file by file:

  identical     the MSL is byte identical: the change does not reach this shader
  vector        the MSL gained a linear raw load (reinterpret_cast<[const ]device uint2|uint4*> through the pool table,
                GlobalToStorage.TryGenerateRawVectorLoad) and nothing else: only the words of a 64/128-bit access changed
  other         anything else: a change the gate has to explain before it is accepted (the tool exits 1)

usage: cache_compare.py --a analysis/ab/cache-new --b analysis/ab/cache-old [--json analysis/ab/cache-compare.json]
                        [--list-other 10]
"""
import argparse
import collections
import json
import re
import sys
from pathlib import Path

VEC = re.compile(r'reinterpret_cast<(?:const )?device uint([0-9]+)\*>')


def msl(cache: Path, name: str) -> str:
    return (cache / name.replace('_b.json', '_b.metal')).read_text(errors='replace')


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--a', required=True, type=Path, help='cache of the new translator')
    ap.add_argument('--b', required=True, type=Path, help='cache of the reference translator')
    ap.add_argument('--json', type=Path, help='write the per-variant verdicts here')
    ap.add_argument('--list-other', type=int, default=5, help='print this many "other" variants (0: none)')
    args = ap.parse_args()

    a_names = {p.name for p in args.a.glob('*_b.json')}
    b_names = {p.name for p in args.b.glob('*_b.json')}
    both = sorted(a_names & b_names)
    rows = []
    for name in both:
        ma, mb = msl(args.a, name), msl(args.b, name)
        stage = json.loads((args.a / name).read_text()).get('stage')
        if ma == mb:
            rows.append({'variant': name, 'stage': stage, 'verdict': 'identical'})
            continue
        va, vb = collections.Counter(VEC.findall(ma)), collections.Counter(VEC.findall(mb))
        added = va - vb
        if added and not (vb - va):
            rows.append({'variant': name, 'stage': stage, 'verdict': 'vector',
                         'vector_loads_added': {f'uint{k}': n for k, n in added.items()}})
        else:
            rows.append({'variant': name, 'stage': stage, 'verdict': 'other',
                         'vector_before': {f'uint{k}': n for k, n in vb.items()},
                         'vector_after': {f'uint{k}': n for k, n in va.items()}})

    tally = collections.Counter(r['verdict'] for r in rows)
    per_stage = collections.defaultdict(collections.Counter)
    for r in rows:
        per_stage[r['stage']][r['verdict']] += 1

    print(f'variants: {len(a_names)} in A (new), {len(b_names)} in B (reference), {len(both)} in both')
    print(f'  only in A: {len(a_names - b_names)}   only in B: {len(b_names - a_names)} (not compared: different shaders reached)')
    for v in ('identical', 'vector', 'other'):
        print(f'  {v:9s} {tally[v]:5d}')
    print(f"{'stage':6s}" + ''.join(f'{v:>10s}' for v in ('identical', 'vector', 'other')))
    for stage in sorted(per_stage):
        c = per_stage[stage]
        print(f'{stage or "?":6s}{c["identical"]:>10d}{c["vector"]:>10d}{c["other"]:>10d}')
    others = [r for r in rows if r['verdict'] == 'other']
    for r in others[:args.list_other]:
        print(f"  other: {r['stage']} {r['variant'][:24]} vector before {r['vector_before']} after {r['vector_after']}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({'a': str(args.a), 'b': str(args.b), 'both': len(both),
                                         'only_a': sorted(a_names - b_names), 'only_b': sorted(b_names - a_names),
                                         'tally': dict(tally), 'per_stage': {k: dict(v) for k, v in per_stage.items()},
                                         'variants': rows}, indent=2) + '\n')
        print(f'\n{args.json}')
    return 1 if others else 0


if __name__ == '__main__':
    sys.exit(main())
