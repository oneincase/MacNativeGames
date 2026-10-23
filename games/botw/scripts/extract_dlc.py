#!/usr/bin/env python3
"""Breath of the Wild: import one add-on content (DLC) NSP in the layout extract_aoc_romfs.py writes for the card image.

The NSP holds one Data NCA (content type 5) of title 01007EF00011F001 (index 1) or 01007EF00011F002 (index 2). It is
decrypted with the package's common ticket (or the key area for a rights-free NCA), all six IVFC levels are verified, and
the RomFS goes to <output>/<index>/{romfs.bin, index.json, runtime-index.bin}. The RSA signature is not checked. Keys stay
in memory; nothing prints them.
usage: extract_dlc.py <dlc.nsp> --keys <prod.keys> --output <new folder>
"""
import argparse
import json
import sys
from pathlib import Path

TOTK_SCRIPTS = next(p for p in (Path(__file__).resolve().parents[2] / 'totk/scripts', Path(__file__).resolve().parents[2] / 'scripts')
                    if (p / 'extract_exefs.py').exists())
sys.path.insert(0, str(TOTK_SCRIPTS))
from extract_exefs import require, load_keys, container_entries, common_tickets, nca_inventory, nca_ctr_key  # noqa: E402
from extract_romfs import verify_ivfc, index_romfs  # noqa: E402
from extract_aoc_romfs import AOC, runtime_index  # noqa: E402

PUBLIC_DATA = 5   # NCA content type of a DLC's Data NCA


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('dlc', type=Path, help='the add-on content package (.nsp)')
    ap.add_argument('--keys', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True, help='a new folder; the pack goes into <output>/<index>')
    args = ap.parse_args()
    require(not args.output.exists(), 'Output exists; choose a new directory')
    keys = load_keys(args.keys)
    with args.dlc.open('rb') as f:
        kind, entries = container_entries(f, args.dlc.stat().st_size)
        require(kind == 'nsp', 'Add-on content must be an NSP')
        tickets = common_tickets(f, entries)
        data = [(r, h) for r, h in nca_inventory(f, entries, keys) if r['content_type'] == PUBLIC_DATA]
        require(len(data) == 1, 'Expected one add-on content Data NCA')
        record, hdr = data[0]
        require(record['title_id'] in AOC, f'The Data NCA belongs to title {record["title_id"]}, not to Breath of the Wild '
                                           f'add-on content ({", ".join(AOC)})')
        index = AOC[record['title_id']]
        folder = args.output / str(index)
        result = verify_ivfc(f, record['nca_offset'], hdr, nca_ctr_key(hdr, keys, tickets), folder / 'romfs.bin')
    files = index_romfs(folder / 'romfs.bin')
    (folder / 'runtime-index.bin').write_bytes(runtime_index(files))
    report = dict(status='pass', title_id=record['title_id'], index=index, nca=record['name'], romfs_size=result['romfs_size'],
                  romfs_sha256=result['romfs_sha256'], files=files, ivfc_blocks=result['ivfc_blocks'],
                  integrity='FS header and all six IVFC levels verified; RSA signature not checked')
    (folder / 'index.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(dict(status='pass', index=index, files=len(files), size=report['romfs_size'], sha256=report['romfs_sha256'])), flush=True)


if __name__ == '__main__':
    main()
