# scenecap_replay — offline Metal replayer

`scenecap_replay` re-renders a captured scene from a `.nvs` package without the game, the port or `d2r_gfx`:

```
scenecap_replay <package.nvs> -o <output-dir> [--no-cache] [--validate-only] [--verbose]
```

It rebuilds every texture and buffer from the package's blob region, compiles the captured MSL with
`newLibraryWithSource:`, builds a `MTLRenderPipelineState` per pipeline/attachment combination, encodes the passes in
manifest order and writes one PNG per colour-bearing render pass plus an `index.json`.

Exit codes:

| code | meaning |
| --- | --- |
| 0 | every pass replayed |
| 1 | validation or render failure (the failing pass/draw/key is named on stderr and in `index.json.errors`) |
| 2 | usage error, unreadable package, or a package the build cannot decode (for example real zstd frames built with `WITH_LIBZSTD=0`) |
| 3 | replay completed, but at least one pass is explicitly unsupported (see `index.json.unsupported`) |

`--validate-only` performs the same package checks without creating a `MTLDevice`, so it also works on machines with no
Metal device and in CI.

## Output contract

The directory layout is fixed because the diff tool reads it:

```
<dir>/index.json
<dir>/pass_000_<slug>.png
<dir>/pass_001_<slug>.png
```

`index.json` (schema `scenecap.replay/1`):

```json
{
  "schema": "scenecap.replay/1",
  "package": {"path": "...", "sha256": "<64 hex>", "captured_frame": 0, "game": "...", "build": "..."},
  "device": {"name": "Apple M5", "metal_version": "Metal4"},
  "passes": [
    {"index": 0, "kind": "render", "slug": "main", "width": 1920, "height": 1080,
     "pixel_format": "RGBA8Unorm", "encoding": "srgb", "png": "pass_000_main.png",
     "draws": 12, "status": "ok",
     "metrics": {"pixels": 2073600, "min": [0,0,0,255], "max": [255,255,255,255], "mean": [63.5, 70.25, 81.75, 255]}}
  ],
  "unsupported": [{"pass": 3, "reason": "compute passes are not replayed yet", "pass_id": "post.compute"}],
  "errors": []
}
```

* `index` is the pass position in `manifest.passes`, zero padded to three digits in the file name.
* `slug` is the pass id lower-cased to `[a-z0-9_]` (other characters collapse to `_`, capped at 32 characters,
  de-duplicated with the pass index).
* Passes are written with `NSJSONWritingSortedKeys`, so two replays of one package produce identical bytes.
* Each pass also carries an optional `metrics` object (`pixels`, and `min`/`max`/`mean` per RGBA channel of the exported
  PNG bytes, colour channels therefore sRGB-encoded). It is extra information for reports: the diff tool ignores keys
  it does not know, so a plain pass entry without it is still accepted.
* PNG files are RGBA8, non-premultiplied, top-left origin (never flipped) and **sRGB encoded**, which is why every
  pass records `"encoding": "srgb"`. A `_srgb` swap-chain format is read back as-is (its bytes already hold sRGB
  values, with BGRA reordered to RGBA); a linear `unorm`/`unorm16`/`half`/`float` format is sRGB encoded while writing
  the PNG. Alpha is never gamma encoded.
* The PNG size is the size of the pass's slot-0 colour attachment at its `level`, so a mip or array-layer capture
  exports that subresource.

## What is replayed

* Resources are created from `manifest.resources`: pixel format, width/height/depth, mip levels, array layers, sample
  count and texture type. Texture bytes are uploaded per mip level and per slice with the tight row pitch recorded in
  the blob (`replaceRegion:mipmapLevel:slice:withBytes:bytesPerRow:bytesPerImage:`); 3D textures slice along `z` and
  cube/cube-array textures count `6 * array_layers` slices. The 256-byte `bytesPerRow` alignment only applies to
  `MTLBuffer` copies, not to `replaceRegion`.
* Supported pixel formats: `rgba8unorm`, `rgba8unorm_srgb`, `bgra8unorm`, `bgra8unorm_srgb`, `rgba8snorm`, `r8unorm`,
  `rg8unorm`, `rgba16unorm`, `rgba16float`, `rgba32float`, `r32float`, `rg32float`, `rgb10a2unorm`, `bgr10a2unorm`,
  `depth16unorm`, `depth32float`, `depth24_stencil8`, `depth32float_stencil8`, `stencil8`. An unknown format is a
  hard error naming the pass and texture together with the supported list.
* Attachment load/store actions are honoured: `clear` uses the captured clear colour (or depth/stencil clear value),
  `load` keeps the content restored from the resource snapshot, `dontcare` leaves it undefined. The exported slot-0
  attachment is always stored, even if the capture marked it `dontcare` (a warning is recorded).
* Viewport, scissor (intersected with the pass rectangle), cull mode, front-facing winding, fill mode and depth bias
  come from `manifest.pipelines[].state`; a missing viewport defaults to the pass size.
* Draws replay `primitive_type`, `instances`, the index type and `index.start`/`index.count`. Indexed draws need
  `draw.index.buffer` (and optionally `draw.index.offset`), see *Extension* below.
* Multisample resources are rejected explicitly (`sampleCount > 1`); `compute` and `blit` passes are reported through
  `unsupported` and never silently skipped.

### Binding convention

The manifest's argument table has no stages, so the replayer binds each list once per stage:

| manifest | Metal |
| --- | --- |
| `vertex_buffers[k]` | vertex buffer slot `k` (with `offset`/`length`) |
| `uniform_buffers[k]` | vertex **and** fragment buffer slot `k` |
| `fragment_buffers[k]` | fragment buffer slot `k` |
| `textures[k]` | vertex and fragment texture slot `k` |
| `samplers[k]` | vertex and fragment sampler slot `k` |

The synthetic scene uses `buffer(0)` for its vertex data, `texture(0)`/`sampler(0)` for its checker board, matching
this convention.

### Entry point names

The manifest has no field for the shader entry point, so the replayer resolves it in this order:

1. `stages[].entry` when the capture recorded it,
2. the library's only function (the usual case),
3. the conventional names `vs_main`/`vertex_main`/`main_vs`/`main0`/`main` for vertex and
   `fs_main`/`fragment_main`/`main_fs`/`main0`/`main` for fragment.

Failure names the pipeline key, the stage and the Metal diagnostics, and lists the library's functions.

### Extension: index buffer binding

The base manifest contract has no slot for the index buffer, so the replayer accepts an optional extension on
indexed draws:

```json
"index": {"type": "uint16", "start": 0, "count": 3, "buffer": "indices", "offset": 0}
```

`buffer` names a `buffer` resource; `offset` defaults to 0 and is added to `start * sizeof(type)`. An indexed draw
without that binding is a hard error (the alternative would silently render the wrong triangles), and the error names
the draw. The capture side (`d2r_gfx`) must emit this field for indexed draws.

## Pipeline cache

The first replay compiles MSL at runtime; the resulting `MTLBinaryArchive` is written next to the package so later
replays skip compilation:

```
<pkg-dir>/<pkg-stem>.scenecap-cache/pipelines.metallib
<pkg-dir>/<pkg-stem>.scenecap-cache/cache.json
```

`cache.json` records `schema` (`scenecap.replay.cache/1`), the device name, the package SHA-256, the pipeline count
and the archive hit/reject counters. A cache written for another package or device is discarded and rebuilt. A cache
that fails to load or to serialize never changes the rendered pixels: it falls back to source compilation and records
a warning. `--no-cache` avoids touching the package directory. This machine has no offline Metal compiler (`xcrun
--find metal` is unavailable with the Command Line Tools only), which is why the cache uses `MTLBinaryArchive`
instead of an ahead-of-time `metallib`.

## Compression

Blobs may be stored verbatim or as real zstd frames. `nvs_format.c` decodes verbatim and raw/RLE frames itself; real
zstd frames (Huffman/FSE, what CPython 3.14's `compression.zstd` writes) are decoded through `libzstd` loaded at
runtime, so building the replayer needs neither `zstd.h` nor a link-time dependency:

* default build (`WITH_LIBZSTD=1`): looks for `$SCENECAP_LIBZSTD`, `/opt/homebrew/lib/libzstd.dylib`, the Homebrew
  `opt` paths, `/usr/local/lib/libzstd.dylib` and the plain `libzstd.dylib` names;
* `make WITH_LIBZSTD=0`: real zstd frames become an explicit `unsupported`/`error` entry naming the chunk and the
  reason — the replayer never prints a black or blank PNG to cover a chunk it could not decode.

## Synthetic self-test

`scenecap_synth.py` writes the package the tests replay; it needs no game data:

```
python3 tools/scenecap/replay/scenecap_synth.py /tmp/synth.nvs [--raw] [--indexed] [--compute] [--pixel-format F]
```

The scene is one 64×64 `bgra8unorm_srgb` attachment cleared to a known linear colour, one 4×4 `rgba8unorm` texture
whose centre texel is distinct, and one triangle whose three vertices sample the same UV, so the centre pixel must be
that texel and the corners must keep the clear colour. `--raw` stores blobs verbatim (the default of the d2r capture
kernel), `--indexed` draws through an index buffer, `--compute` appends a compute pass that must be reported as
unsupported.

## Tests

`tools/scenecap/replay/tests/test_scenecap_replay.py` is part of `make check`. It renders the synthetic package and
asserts the corner/centre pixels against an independently computed sRGB transfer function, checks the `index.json`
contract, byte-for-byte reproducibility of two runs, raw/indexed equivalence, the pipeline cache, the failure paths
(missing blob, tampered payload hash, unknown pixel format, usage errors) and, through `nvs_reader_probe`, that the C
reader decodes both verbatim and real-zstd packages to the bytes the Python writer produced.

`make check` builds `tools/scenecap/replay/build/scenecap_replay` and `.../nvs_reader_probe` and hands their paths to
the tests through `SCENECAP_REPLAY`/`SCENECAP_PROBE`.

## Limitations

* `compute` and `blit` passes are reported as unsupported, not replayed.
* Multisampled attachments, stencil-only passes and `MTLHeap`-backed resources are not implemented.
* Only the slot-0 colour attachment is exported; extra attachments are rendered but not captured.
* Reflection blobs are carried through the package and validated, but not used to resolve entry points or binding
  indices yet.
