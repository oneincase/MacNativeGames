#!/usr/bin/env python3
"""Inventory Odyssey shader archive containers without exporting shader bytes.

Reads the verified local RomFS image and writes metadata only into Odyssey_build.
"""
import argparse
import collections
import json
import struct
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def yaz0(data):
    if data[:4] != b'Yaz0':
        raise ValueError('Expected Yaz0')
    expected = int.from_bytes(data[4:8], 'big')
    out = bytearray()
    at = 16
    while len(out) < expected:
        code = data[at]
        at += 1
        for bit in range(8):
            if code & (0x80 >> bit):
                out.append(data[at])
                at += 1
            else:
                first, second = data[at:at + 2]
                at += 2
                distance = ((first & 15) << 8) | second
                length = first >> 4
                if length == 0:
                    length = data[at] + 0x12
                    at += 1
                else:
                    length += 2
                source = len(out) - distance - 1
                if source < 0:
                    raise ValueError('Yaz0 reference before output')
                for _ in range(length):
                    out.append(out[source])
                    source += 1
            if len(out) >= expected:
                break
    if len(out) != expected:
        raise ValueError('Yaz0 size mismatch')
    return out


def sarc_entries(data):
    if data[:4] != b'SARC' or data[6:8] != b'\xff\xfe':
        raise ValueError('Expected little-endian SARC')
    data_offset = struct.unpack_from('<I', data, 12)[0]
    if data[0x14:0x18] != b'SFAT':
        raise ValueError('SARC SFAT missing')
    count = struct.unpack_from('<H', data, 0x1a)[0]
    sfnt = 0x20 + 16 * count
    if data[sfnt:sfnt + 4] != b'SFNT':
        raise ValueError('SARC SFNT missing')
    names_offset = sfnt + 8
    files = []
    for i in range(count):
        _, attribute, start, end = struct.unpack_from('<IIII', data, 0x20 + 16 * i)
        name_offset = names_offset + (attribute & 0xffffff) * 4
        zero = data.index(0, name_offset)
        name = data[name_offset:zero].decode('utf-8')
        if not (0 <= start < end and data_offset + end <= len(data)):
            raise ValueError('SARC entry bounds')
        files.append(dict(name=name, size=end - start,
                          magic=bytes(data[data_offset + start:data_offset + start + 4]).hex()))
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--romfs', type=Path, default=HERE / 'Odyssey_build/extracted/local/romfs')
    parser.add_argument('--output', type=Path, default=HERE / 'Odyssey_build/analysis/shader-assets.json')
    args = parser.parse_args()
    manifest = json.loads((args.romfs / 'index.json').read_text())
    if manifest['status'] != 'pass':
        raise ValueError('RomFS verification report missing')
    archives = []
    with (args.romfs / 'romfs.bin').open('rb') as image:
        for file in manifest['files']:
            if not file['path'].startswith('ShaderData/') or not file['path'].endswith('.szs'):
                continue
            image.seek(file['offset'])
            compressed = image.read(file['size'])
            data = yaz0(compressed)
            archives.append(dict(path=file['path'], compressed_bytes=file['size'],
                                 unpacked_bytes=len(data), entries=sarc_entries(data)))
            print(f"{file['path']}: {len(archives[-1]['entries'])} entries", flush=True)
    kinds = collections.Counter(name.rsplit('.', 1)[-1]
                                for archive in archives for entry in archive['entries']
                                for name in [entry['name']])
    report = dict(status='pass', scope='container inventory only; shader code not translated',
                  archives=archives, extension_counts=dict(kinds))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(dict(archives=len(archives), extensions=dict(kinds))))


if __name__ == '__main__':
    main()
