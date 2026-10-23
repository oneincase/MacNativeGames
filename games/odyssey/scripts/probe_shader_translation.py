#!/usr/bin/env python3
"""Translate one verified Odyssey shader with the reference native translator.

The shader input and generated MSL remain in ignored Odyssey_build. This only
checks translation, not Metal compilation or rendering.
"""
import argparse
import hashlib
import importlib.util
import json
import struct
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
SEED = HERE / 'third_party/totk-scripts/prepare_shader_seed.py'
TRANSLATOR = HERE / 'prebuilt/macos-arm64/totk_xlate'


def load_seed():
    spec = importlib.util.spec_from_file_location('totk_seed', SEED)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spirv-cross', type=Path, required=True)
    parser.add_argument('--archive', default='ShaderData/alProgramShader.szs')
    parser.add_argument('--stage', choices=('vs', 'ps', 'cs'), default='vs')
    parser.add_argument('--romfs', type=Path, default=HERE / 'Odyssey_build/extracted/local/romfs')
    parser.add_argument('--output', type=Path, default=HERE / 'Odyssey_build/analysis/shader-probe')
    args = parser.parse_args()
    expected = (TRANSLATOR.with_suffix('.sha256')).read_text().split()[0]
    if hashlib.sha256(TRANSLATOR.read_bytes()).hexdigest() != expected:
        raise ValueError('Reference translator hash mismatch')
    seed = load_seed()
    manifest = json.loads((args.romfs / 'index.json').read_text())
    if manifest['status'] != 'pass':
        raise ValueError('RomFS not verified')
    selected = next(item for item in manifest['files'] if item['path'] == args.archive)
    with (args.romfs / 'romfs.bin').open('rb') as image:
        image.seek(selected['offset'])
        archive = seed.sarc(seed.yaz0(image.read(selected['size'])))
    candidate_count = 0
    selected_shader = None
    for name, payload in archive.items():
        if name.endswith('.sharcb'):
            candidates = seed.sharcb_shaders(payload, 'botw')
        elif name.endswith('.bfsha'):
            pos = payload.find(b'BNSH\0\0\0\0')
            candidates = seed.bnsh_shaders(payload[pos:], 'botw') if pos >= 0 else ()
        else:
            continue
        for row in candidates:
            candidate_count += 1
            if selected_shader is None and row[1] == args.stage:
                selected_shader = row
    if selected_shader is None:
        raise ValueError(f'No {args.stage} shader in {args.archive}')
    key, stage, control, code = selected_shader
    c1, length = struct.unpack_from('<II', control, 0x700)
    args.output.mkdir(parents=True, exist_ok=True)
    variant = key + '-' + seed.SUFFIX
    code_path = args.output / (variant + '.code')
    code_path.write_bytes(code[0 if stage == 'cs' else 0x30:length])
    request = dict(variant=variant, stage=stage, code=str(code_path.resolve()),
                   c1=code[c1:length].hex(), depth_minus_w=True, **seed.ANSWERS)
    if stage == 'cs':
        request['local'] = list(struct.unpack_from('<III', control, 0x748))
    process = subprocess.run([str(TRANSLATOR), '--service', '--spirv-cross', str(args.spirv_cross.resolve()),
                              '--out', str(args.output.resolve()), '--chain', seed.CHAIN],
                             input=json.dumps(request) + '\n', text=True, capture_output=True, timeout=180)
    code_path.unlink(missing_ok=True)
    if process.returncode or not process.stdout.strip():
        raise RuntimeError(f'translator exited {process.returncode}: {process.stderr[-1000:]}')
    reply = json.loads(process.stdout.splitlines()[0])
    result = dict(status='pass' if reply.get('ok') else 'failed', archive=args.archive, shader_key=key,
                  stage=stage, candidate_count=candidate_count, translator_reply=reply)
    if reply.get('ok'):
        msl = args.output / (variant + '_b.metal')
        reflection = args.output / (variant + '_b.json')
        if not msl.is_file() or not reflection.is_file():
            raise ValueError('Translator did not produce MSL and reflection')
        result['msl_bytes'] = msl.stat().st_size
        result['reflection_bytes'] = reflection.stat().st_size
    (args.output / ('report-' + stage + '-' + Path(args.archive).stem + '.json')).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    if not reply.get('ok'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
