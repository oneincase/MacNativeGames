#!/usr/bin/env python3
"""Compare Odyssey's static NVN names with the copied native graphics layer.

This is a planning inventory, not proof of runtime calls or graphics correctness.
The analysis ELF and JSON report stay in the ignored local build directory.
"""
import argparse
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
GFX = HERE / 'third_party/d2r_gfx/gfx/nvn'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--main-elf', type=Path, default=HERE / 'Odyssey_build/analysis/main.elf')
    parser.add_argument('--output', type=Path, default=HERE / 'Odyssey_build/analysis/graphics-audit.json')
    args = parser.parse_args()
    image = args.main_elf.read_bytes()
    candidates = {match.decode() for match in re.findall(
        rb'(?<![A-Za-z0-9_])(nvn[A-Za-z0-9_]+)\x00', image)}
    bootstrap = 'nvnBootstrapLoader'
    names = set((GFX / 'names.txt').read_text().splitlines())
    implementations = set()
    for path in GFX.rglob('*'):
        if path.suffix not in ('.c', '.h', '.m', '.inc'):
            continue
        source = path.read_text(errors='ignore')
        implementations.update(re.findall(
            r'\bNVN(?:_NOPROTO|_EXTRA)?\s*\(\s*(nvn[A-Za-z0-9_]+)', source))
    api_candidates = candidates - {bootstrap}
    report = {
        'scope': 'NUL-terminated NVN-looking strings in Odyssey update main analysis ELF; not a runtime call trace',
        'candidate_count': len(api_candidates),
        'in_host_name_table': len(api_candidates & names),
        'missing_from_name_table': sorted(api_candidates - names),
        'registered_implementation_count': len(api_candidates & implementations),
        'without_registered_implementation': sorted(api_candidates - implementations),
        'bootstrap_string_present': bootstrap in candidates,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: value if not isinstance(value, list) else len(value)
                      for key, value in report.items()}))


if __name__ == '__main__':
    main()
