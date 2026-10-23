#!/usr/bin/env python3
"""Local analysis only: find which RomFS archives contain the code of shaders the runtime translated (its shader cache)
but the static inventory (analysis/shader-keys.json, extra-shader-keys.json) does not list. Prints archive paths and
counts only, never shader bytes.

usage: python3 -I scripts/locate_runtime_shaders.py "$PWD" [RomFS path prefixes ...]   (from games/odyssey; default ShaderData/)
22 October 2026: 844 runtime shaders, 182 in the inventory. Of 80 sampled misses, 50 were in ShaderData (47 in
alRenderMaterial.szs) and 27 in EffectData/EffectPtcl.szs, i.e. in archives the inventory had scanned: a key mismatch."""
import json, glob, os, sys, importlib.util, collections, random
GAME = sys.argv[1]; prefixes = sys.argv[2:] or ['ShaderData/']
B = GAME + '/Odyssey_build/'; C = B + 'build/shader-cache/rt-totk-native-v4-unverified/'
spec = importlib.util.spec_from_file_location('seed', GAME + '/third_party/totk-scripts/prepare_shader_seed.py'); seed = importlib.util.module_from_spec(spec); spec.loader.exec_module(seed)
inv = set()
def walk(o):
    if isinstance(o, dict):
        for k, v in o.items():
            if isinstance(k, str) and len(k) == 64: inv.add(k)
            walk(v)
    elif isinstance(o, list):
        for v in o: walk(v)
    elif isinstance(o, str) and len(o) == 64: inv.add(o)
for f in ['shader-keys.json', 'extra-shader-keys.json']: walk(json.load(open(B + 'analysis/' + f)))
miss = [os.path.basename(f)[:-9] for f in glob.glob(C + '*.variants') if os.path.basename(f)[:-9] not in inv]
random.seed(1); sample = random.sample(miss, min(80, len(miss)))
probes = {}
for k in sample:
    v = json.load(open(C + k + '.variants'))[0]['variant']; code = open(C + v + '.code', 'rb').read()
    probes[k] = code[16:16 + 96]
man = json.load(open(B + 'extracted/local/romfs/index.json'))
found = collections.Counter(); located = set()
with open(B + 'extracted/local/romfs/romfs.bin', 'rb') as img:
    for row in man['files']:
        p = row['path']
        if not any(p.startswith(x) for x in prefixes): continue
        img.seek(row['offset']); raw = img.read(row['size'])
        try: data = seed.yaz0(raw) if raw[:4] == b'Yaz0' else raw
        except Exception: continue
        for k, pr in probes.items():
            if pr in data: found[p] += 1; located.add(k)
print('sample', len(probes), 'located', len(located))
for p, n in found.most_common(15): print(n, p)
