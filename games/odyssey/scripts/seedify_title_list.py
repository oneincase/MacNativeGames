#!/usr/bin/env python3
"""Make the title pipeline list (pso_title.tsv) work on a cache that holds only the shader seed.

The list was recorded on a played cache: its rows name runtime variants (<key>-<answers hash>_b), and a seed-only cache
names the same shaders' variants differently, so the startup warm-up found none of them (0 title shaders before the first
frame). For each variant a row names, this finds the variant the runtime would pick on a seed-only cache, the same way
totk_rt_variant does: same shader key, the first row of the seed's <key>.variants whose deps match the recorded variant's
deps (unrecorded slots tex=2d / attr=float / fout=float) and, for a vertex shader, whose depth_minus_w is equal. It keeps every
original row (a played cache, like the player's, has those names) and adds the rewritten one. S rows name one variant;
R rows are a pso_skey in hex whose first fields are char vs[80] and fs[80] (nvn_pso_skey.h), replaced and zero-padded.
Rows naming files a cache lacks are skipped by the warm-up, so each cache uses the rows it can. An R row's other fields
(vertex layout, formats) came from the recorded variants' reflection, so it is rewritten only when every substituted
seed variant's MSL is byte-identical to the recorded one, or its reflection (_b.json without key / deps) is identical.

usage: seedify_title_list.py <title list> <cache it was recorded on> <seed dir> [--write <out>]
Prints counts only.
"""
import argparse
from collections import Counter
import json
from pathlib import Path

DEFAULTS = {'tex': '2d', 'attr': 'float', 'fout': 'float', 'img': None}
NAME = 80


def variants(folder, key, cache):
    if key not in cache:
        try:
            cache[key] = json.loads((folder / f'{key}.variants').read_text())
        except (OSError, ValueError):
            cache[key] = []
    return cache[key]


def deps_match(seed_deps, bound):
    for kind, default in DEFAULTS.items():
        for slot, want in (seed_deps.get(kind) or {}).items():
            if (bound.get(kind) or {}).get(slot, default) != want:
                return False
    return True


def same_shader(recorded, seed, rec_name, seed_name, how):
    if rec_name == seed_name:
        return True
    if (recorded / f'{rec_name}.metal').read_bytes() == (seed / f'{seed_name}.metal').read_bytes():
        how['identical MSL'] += 1; return True
    strip = lambda p: {k: v for k, v in json.loads(p.read_text()).items() if k not in ('key', 'deps')}
    if strip(recorded / f'{rec_name}.json') == strip(seed / f'{seed_name}.json'):
        how['identical reflection'] += 1; return True
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('title', type=Path)
    ap.add_argument('recorded', type=Path)
    ap.add_argument('seed', type=Path)
    ap.add_argument('--write', type=Path)
    a = ap.parse_args()
    rec_cache, seed_cache, mapped, why, how = {}, {}, {}, Counter(), Counter()

    def seed_name(name):   # '<variant>_b' recorded -> '<variant>_b' on a seed-only cache, or None
        if name in mapped:
            return mapped[name]
        variant = name[:-2] if name.endswith('_b') else name
        key = variant.split('-')[0]
        row = next((r for r in variants(a.recorded, key, rec_cache) if r.get('variant') == variant), None)
        out = None
        if row is None:
            why['recorded variant not found'] += 1
        else:
            stage = json.loads((a.recorded / f'{variant}_b.json').read_text()).get('stage')
            for cand in variants(a.seed, key, seed_cache):
                if not deps_match(cand.get('deps') or {}, row.get('deps') or {}):
                    continue
                if stage == 'vs' and bool(cand.get('depth_minus_w')) != bool(row.get('depth_minus_w')):
                    continue
                out = cand['variant'] + '_b'
                break
            if out is None:
                why['no seed variant matches'] += 1
            elif not (a.seed / f'{out}.json').exists() or not (a.seed / f'{out[:-2]}_b.metal').exists():
                why['seed variant files missing'] += 1; out = None
        mapped[name] = out
        return out

    rows = [l for l in a.title.read_text().splitlines() if l]
    out, stats = [], Counter()
    for line in rows:
        out.append(line)
        f = line.split('\t')
        if f[0] == 'S' and len(f) >= 3:
            new = seed_name(f[1])
            if new and new != f[1]:
                out.append('\t'.join([f[0], new] + f[2:])); stats['S rewritten'] += 1
            elif new == f[1]:
                stats['S already a seed name'] += 1
            else:
                stats['S unmapped'] += 1
        elif f[0] == 'R' and len(f) >= 2:
            raw = bytearray.fromhex(f[1])
            names = [raw[i:i + NAME].split(b'\0')[0].decode() for i in (0, NAME)]
            news = [seed_name(n) if n else '' for n in names]
            if any(n and m is None for n, m in zip(names, news)):
                stats['R unmapped'] += 1; continue
            if news == names:
                stats['R already seed names'] += 1; continue
            if not all(not n or same_shader(a.recorded, a.seed, n, m, how) for n, m in zip(names, news)):
                stats['R dropped: seed variant differs from the recorded one'] += 1; continue
            for i, n in zip((0, NAME), news):
                enc = n.encode()
                if len(enc) >= NAME:
                    raise SystemExit(f'variant name too long for pso_skey: {n}')
                raw[i:i + NAME] = enc + b'\0' * (NAME - len(enc))
            hexed = raw.hex()
            back = [bytes.fromhex(hexed)[i:i + NAME].split(b'\0')[0].decode() for i in (0, NAME)]
            assert back == news and len(hexed) == len(f[1]), 'pso_skey round trip failed'
            out.append('\t'.join([f[0], hexed] + f[2:])); stats['R rewritten'] += 1
        else:
            stats[f'{f[0]} rows kept as is'] += 1
    seen, final = set(), []
    for line in out:
        if line not in seen:
            seen.add(line); final.append(line)
    print(f'title rows {len(rows)} -> {len(final)}')
    for k, v in sorted(stats.items()):
        print(f'  {v:4}  {k}')
    for k, v in how.most_common():
        print(f'  R substitution accepted by: {v:4}  {k}')
    for k, v in why.most_common():
        print(f'  unmapped variant: {v:4}  {k}')
    if a.write:
        a.write.write_text('\n'.join(final) + '\n')


if __name__ == '__main__':
    main()
