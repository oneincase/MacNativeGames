#!/usr/bin/env python3
"""Read a Switch NSP or NSZ container and print what it holds, without extracting anything.

    python3 survey_container.py <game.nsp|game.nsz> --keys <prod.keys> [--json <out.json>]

This is the first step of a new-game survey (see docs): it answers which NCAs are in the package, which title
they belong to, which one is the Program, how big the sections are and whether a common ticket is present, so the
rest of the port can be planned against the actual dump instead of its filename. It prints no key values.
"""
import argparse, hashlib, json, struct, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / 'totk' / 'scripts'))
import extract_exefs as ee   # load_keys, container_entries, common_tickets, nca_inventory, read_nca_header


def section_summary(hdr):
    out = []
    for i in range(4):
        start, end = struct.unpack_from('<II', hdr, 0x240 + 16 * i)
        fs = hdr[0x400 + 512 * i:0x600 + 512 * i]
        out.append(dict(index=i, start=start * 512, end=end * 512, size=(end - start) * 512,
                        fs_type=fs[2], fs_version=fs[3], hash_type=fs[4],
                        bktr=(fs[4] == 4)))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('container', type=Path)
    ap.add_argument('--keys', required=True, type=Path)
    ap.add_argument('--json', type=Path)
    args = ap.parse_args()
    keys = ee.load_keys(args.keys)
    report = dict(container=str(args.container.resolve()), size=args.container.stat().st_size)
    with args.container.open('rb') as f:
        kind, entries = ee.container_entries(f, args.container.stat().st_size)
        tickets = ee.common_tickets(f, entries)
        records = ee.nca_inventory(f, entries, keys)
        report['kind'] = kind
        report['common_tickets'] = {rights.hex(): 'present' for rights in tickets}
        report['entries'] = [dict(name=n, offset=o, size=s) for n, o, s in entries]
        report['ncas'] = []
        for record, hdr in records:
            report['ncas'].append(dict(record, sections=section_summary(hdr),
                                       rights_id=hdr[0x230:0x240].hex(),
                                       npdm=(hdr[0x207] if record['content_type'] == 0 else None)))
    text = json.dumps(report, indent=2)
    print(text)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(text + '\n')


if __name__ == '__main__':
    main()
