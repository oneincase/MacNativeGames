#!/usr/bin/env python3
"""Count Odyssey shader programs recognized by the reference extraction logic.

Only hashes, stages, and source archive names are saved; shader bytes stay in
memory and the verified, ignored RomFS image.
"""
import argparse
import collections
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
SEED = HERE.parent / 'totk/scripts/prepare_shader_seed.py'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--romfs', type=Path, default=HERE / 'Odyssey_build/extracted/local/romfs')
    parser.add_argument('--output', type=Path, default=HERE / 'Odyssey_build/analysis/shader-keys.json')
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('totk_seed', SEED)
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)
    manifest = json.loads((args.romfs / 'index.json').read_text())
    if manifest['status'] != 'pass':
        raise ValueError('RomFS not verified')
    seen = {}
    archives = []
    with (args.romfs / 'romfs.bin').open('rb') as image:
        for row in manifest['files']:
            if not row['path'].startswith('ShaderData/') or not row['path'].endswith('.szs'):
                continue
            image.seek(row['offset'])
            entries = seed.sarc(seed.yaz0(image.read(row['size'])))
            stages = collections.Counter()
            for name, payload in entries.items():
                if name.endswith('.sharcb'):
                    iterator = seed.sharcb_shaders(payload, 'botw')
                elif name.endswith('.bfsha'):
                    def embedded():
                        pos = 0
                        while (pos := payload.find(b'BNSH\0\0\0\0', pos)) >= 0:
                            for shader in seed.bnsh_shaders(payload[pos:], 'botw'):
                                yield shader
                            pos += 8
                    iterator = embedded()
                else:
                    continue
                for key, stage, control, code in iterator:
                    seen.setdefault(key, dict(stage=stage, source=row['path']))
                    stages[stage] += 1
            archives.append(dict(path=row['path'], stages=dict(stages)))
            print(f"{row['path']}: {dict(stages)}", flush=True)
    summary = dict(collections.Counter(item['stage'] for item in seen.values()))
    report = dict(status='pass', scope='static shader extraction only; no Metal compilation or rendering',
                  unique_shaders=len(seen), stages=summary, archives=archives,
                  keys=seen)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(dict(unique_shaders=len(seen), stages=summary, archives=len(archives))))


if __name__ == '__main__':
    main()
