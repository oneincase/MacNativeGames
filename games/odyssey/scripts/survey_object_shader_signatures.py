#!/usr/bin/env python3
"""Find candidate shader containers in the verified local ObjectData archives.

This is a signature survey, not a parsed shader count. It exports only archive
paths and hit counts to an ignored analysis report.
"""
import importlib.util
import json
from pathlib import Path

GAME = Path(__file__).resolve().parents[1]
ROMFS = GAME / 'Odyssey_build/extracted/local/romfs'
OUTPUT = GAME / 'Odyssey_build/analysis/object-shader-signatures.json'
SEED = GAME.parent / 'totk/scripts/prepare_shader_seed.py'


def main():
    index = json.loads((ROMFS / 'index.json').read_text())
    if index.get('status') != 'pass':
        raise SystemExit('Verified RomFS index required')
    spec = importlib.util.spec_from_file_location('shader_seed', SEED)
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)
    files = [row for row in index['files'] if row['path'].startswith('ObjectData/')]
    hits = []
    with (ROMFS / 'romfs.bin').open('rb') as image:
        for i, row in enumerate(files, 1):
            image.seek(row['offset'])
            data = image.read(row['size'])
            if data.startswith(b'Yaz0'):
                data = seed.yaz0(data)
            counts = {name: data.count(signature) for name, signature in
                      (('BNSH', b'BNSH\0\0\0\0'), ('BAHS', b'BAHS'), ('FSHA', b'FSHA'))}
            if any(counts.values()):
                hits.append({'path': row['path'], 'signatures': counts})
            if i % 500 == 0:
                print(f'Scanned {i}/{len(files)} ObjectData files; {len(hits)} candidate archives', flush=True)
    report = {'status': 'pass', 'scope': 'ObjectData signature survey, not validated shader count',
              'files_scanned': len(files), 'archives_with_signatures': len(hits), 'hits': hits}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2) + '\n')
    print(f'Completed {len(files)} files; {len(hits)} candidate archives', flush=True)


if __name__ == '__main__':
    main()
