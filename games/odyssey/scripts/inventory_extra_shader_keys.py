#!/usr/bin/env python3
"""Inventory nested shader stages outside Odyssey's ShaderData directory.

Reads the user's verified local RomFS. Writes identifiers and counts only to an
ignored local analysis file; shader bytes remain in memory.
"""
import collections
import importlib.util
import json
from pathlib import Path

GAME = Path(__file__).resolve().parents[1]
ROMFS = GAME / 'Odyssey_build/extracted/local/romfs'
BASE = GAME / 'Odyssey_build/analysis/shader-keys.json'
OUTPUT = GAME / 'Odyssey_build/analysis/extra-shader-keys.json'
SEED = GAME.parent / 'totk/scripts/prepare_shader_seed.py'


def main():
    manifest = json.loads((ROMFS / 'index.json').read_text())
    if manifest.get('status') != 'pass':
        raise SystemExit('Verified RomFS index required')
    prior = json.loads(BASE.read_text())
    if prior.get('status') != 'pass':
        raise SystemExit('ShaderData inventory required')
    known = set(prior['keys'])
    spec = importlib.util.spec_from_file_location('shader_seed', SEED)
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)

    def shaders(payload, depth=0):
        if depth > 4:
            raise ValueError('shader archive nesting exceeds four levels')
        if payload.startswith(b'SARC'):
            for child in seed.sarc(payload).values():
                yield from shaders(child, depth + 1)
        elif payload.startswith(b'BAHS'):
            yield from seed.sharcb_shaders(payload, 'botw')
        else:
            pos = 0
            while (pos := payload.find(b'BNSH\0\0\0\0', pos)) >= 0:
                yield from seed.bnsh_shaders(payload[pos:], 'botw')
                pos += 8

    seen = {}
    archives = []
    with (ROMFS / 'romfs.bin').open('rb') as image:
        for row in manifest['files']:
            path = row['path']
            if not path.startswith(('EffectData/', 'LayoutData/', 'SystemData/')) or not path.endswith('.szs'):
                continue
            image.seek(row['offset'])
            data = image.read(row['size'])
            if data.startswith(b'Yaz0'):
                data = seed.yaz0(data)
            if b'BNSH\0\0\0\0' not in data and b'BAHS' not in data:
                continue
            stages = collections.Counter()
            for key, stage, control, code in shaders(data):
                seen.setdefault(key, stage)
                stages[stage] += 1
            archives.append({'path': path, 'stage_instances': sum(stages.values()),
                             'stages': dict(stages)})
    new = set(seen) - known
    report = {'status': 'pass', 'scope': 'EffectData, LayoutData and SystemData SZS only',
              'archives_with_stages': len(archives),
              'stage_instances': sum(row['stage_instances'] for row in archives),
              'unique_keys': len(seen), 'new_vs_shader_data': len(new),
              'subtotal_with_shader_data': len(known | set(seen)),
              'stages': dict(collections.Counter(seen.values())),
              'archives': archives, 'keys': seen}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ('archives_with_stages', 'stage_instances',
                                             'unique_keys', 'new_vs_shader_data',
                                             'subtotal_with_shader_data', 'stages')}))


if __name__ == '__main__':
    main()
