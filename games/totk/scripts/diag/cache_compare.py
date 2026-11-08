#!/usr/bin/env python3
"""Compare two shader caches: which variants a translator (or host) change reached, and what it did to them.

A shader cache (<out dir of the shader service, totk_shader_rt.m totk_rt_dir) holds per variant: <variant>.code, _b.metal,
_b.json. Two runs of the same save and scene produce the same variant names (a variant is named after the shader and its
deps), so the caches are comparable file by file:

  identical     the MSL is byte identical: the change does not reach this shader
  vector        the MSL gained a raw 64/128-bit load as one vector load (reinterpret_cast<[const ]device uint2|uint4*>,
                GlobalToStorage.TryGenerateRawVectorLoad) and lost none
  raw-helper    both sides reach the pool table (reinterpret_cast<[const ]device ...*>) and the MSL differs, but no vector
                load was added: the raw helper's own body changed (0006/0011/0013)
  other         anything else: a change the gate has to explain before it is accepted (the tool exits 1)

Two ways to use it:

  the gate           cache_compare.py --a <new> --b <reference> --json out.json
  the A/B baseline   cache_compare.py --a <new> --b <reference> --write-baseline DIR [--chain <chain>]
                     DIR is a copy of A in which every variant that is not identical is replaced by B's files, its
                     _b.json "chain" rewritten to <chain> so the host accepts it: the same cache as A except for the
                     change under test, which scripts/ab_translator.py then measures with one binary in both arms
                     (--cache-a <new> --cache-b DIR)

usage: cache_compare.py --a analysis/ab/cache-new --b analysis/ab/cache-old [--json analysis/ab/cache-compare.json]
                        [--list-other 10] [--write-baseline analysis/ab/base --chain totk-native-v11-unverified]
"""
import argparse
import collections
import json
import re
import shutil
import sys
from pathlib import Path

VEC = re.compile(r'reinterpret_cast<(?:const )?device uint([0-9]+)\*>')
RAW = re.compile(r'reinterpret_cast<(?:const )?device \w+\*>')


def msl(cache: Path, name: str) -> str:
    return (cache / name.replace('_b.json', '_b.metal')).read_text(errors='replace')


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--a', required=True, type=Path, help='cache of the new translator')
    ap.add_argument('--b', required=True, type=Path, help='cache of the reference translator')
    ap.add_argument('--json', type=Path, help='write the per-variant verdicts here')
    ap.add_argument('--list-other', type=int, default=5, help='print this many "other" variants (0: none)')
    ap.add_argument('--write-baseline', type=Path, metavar='DIR',
                    help='write a copy of A with every changed variant replaced by B\'s files (the A/B baseline)')
    ap.add_argument('--chain', help='with --write-baseline: the chain text to stamp into the replaced _b.json files')
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
        elif RAW.search(ma) and RAW.search(mb):
            rows.append({'variant': name, 'stage': stage, 'verdict': 'raw-helper'})
        else:
            rows.append({'variant': name, 'stage': stage, 'verdict': 'other',
                         'vector_before': {f'uint{k}': n for k, n in vb.items()},
                         'vector_after': {f'uint{k}': n for k, n in va.items()},
                         'raw_before': bool(RAW.search(mb)), 'raw_after': bool(RAW.search(ma))})

    tally = collections.Counter(r['verdict'] for r in rows)
    per_stage = collections.defaultdict(collections.Counter)
    for r in rows:
        per_stage[r['stage']][r['verdict']] += 1

    print(f'variants: {len(a_names)} in A (new), {len(b_names)} in B (reference), {len(both)} in both')
    print(f'  only in A: {len(a_names - b_names)}   only in B: {len(b_names - a_names)} (not compared: different shaders reached)')
    for v in ('identical', 'vector', 'raw-helper', 'other'):
        print(f'  {v:10s} {tally[v]:5d}')
    print(f"{'stage':6s}" + ''.join(f'{v:>11s}' for v in ('identical', 'vector', 'raw-helper', 'other')))
    for stage in sorted(per_stage):
        c = per_stage[stage]
        print(f'{stage or "?":6s}{c["identical"]:>11d}{c["vector"]:>11d}{c["raw-helper"]:>11d}{c["other"]:>11d}')
    others = [r for r in rows if r['verdict'] == 'other']
    for r in others[:args.list_other]:
        print(f"  other: {r['stage']} {r['variant'][:24]} raw {r['raw_before']}->{r['raw_after']} "
              f"vector {r['vector_before']}->{r['vector_after']}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({'a': str(args.a), 'b': str(args.b), 'both': len(both),
                                         'only_a': sorted(a_names - b_names), 'only_b': sorted(b_names - a_names),
                                         'tally': dict(tally), 'per_stage': {k: dict(v) for k, v in per_stage.items()},
                                         'variants': rows}, indent=2) + '\n')
        print(f'\n{args.json}')

    if args.write_baseline:
        out = args.write_baseline
        if out.exists():
            shutil.rmtree(out)
        shutil.copytree(args.a, out)
        replaced = 0
        for r in rows:
            if r['verdict'] == 'identical':
                continue
            base = r['variant'][:-len('_b.json')]
            for suffix in ('_b.metal', '_b.json', '.res.json'):
                src, dst = args.b / (base + suffix), out / (base + suffix)
                if src.exists():
                    shutil.copy2(src, dst)
            if args.chain:   # the host drops a cached variant whose chain is not the one it runs
                j = json.loads((out / (base + '_b.json')).read_text())
                j['chain'] = args.chain
                (out / (base + '_b.json')).write_text(json.dumps(j, indent=1) + '\n')
            replaced += 1
        print(f'\n{out}: {replaced} variants replaced by the reference (of {len(both)} compared)'
              + (f', chain stamped {args.chain}' if args.chain else ''))
    return 1 if others else 0


if __name__ == '__main__':
    sys.exit(main())
