# scenecap scene packages (`.nvs`)

A `.nvs` package is a self-contained, versioned recording of one to N rendered frames: the pass structure,
pipeline state, draw sequence with argument tables, the textures and buffers those draws reference, the MSL that
was compiled and its reflection. The capture side lives in the `d2r_gfx` NVN→Metal layer; the offline replayer and
the comparison tool read the same package on any machine with Python, without the game.

This directory holds the container contract and the two reference implementations:

| Path | What it is |
| --- | --- |
| `format/nvs_format.h`, `format/nvs_format.c` | The writer and reader in C11, zero dependencies (no Objective-C, no Metal, no zstd library). Capture code links this. |
| `format/nvs_tool.c` | `nvs_tool verify|info|cat|selftest-write` — a C-only build of the checks, plus a deterministic fixture writer. |
| `python/scenecap_pkg.py` | The Python reader/writer, the manifest verifier and the JSON envelope checks. |
| `scenecap` | The command line: `scenecap verify <pkg>`, `info`, `cat`, `manifest`. |
| `tests/test_nvs_format.{c,py}` | The C self-test and the Python unittest suite (cross-language round trips, tampering, version gating). |

The format is little-endian on the wire regardless of host byte order. All offsets are absolute file offsets and all
sizes are bytes. Readers additionally reject `header_size != 128`, unknown `flags` bits, a `file_size` that differs
from the real length, a table that runs past the file, overlapping or out-of-file chunk ranges, non-zero layout
padding, a duplicated chunk id, and a stored size that disagrees with `raw_size` on an uncompressed chunk.

## File layout

```
0                header (128 B)
128              chunk table (chunk_count × 128 B)
…                manifest payload   (zstd frame or plain JSON, 8-byte aligned)
…                blob payloads      (each 8-byte aligned; gaps are zero bytes)
file_size        end of file — no trailing bytes
```

### Header (128 bytes)

| Offset | Type | Field | Meaning |
| --- | --- | --- | --- |
| 0 | `char[8]` | `magic` | `"SCENECAP"`, no NUL |
| 8 | `u16` | `major` | format major, currently `1` |
| 10 | `u16` | `minor` | format minor, currently `0` |
| 12 | `u32` | `header_size` | `128` |
| 16 | `u32` | `flags` | bit 0: the manifest payload is a zstd frame; bit 1: at least one chunk is a zstd frame |
| 20 | `u32` | `chunk_count` | number of chunk-table entries |
| 24 | `u32` | `chunk_entry_size` | `128`, so a newer writer cannot silently shorten an entry |
| 28 | `u64` | `chunk_table_offset` | always `128` |
| 36 | `u64` | `manifest_offset` | absolute offset of the manifest payload |
| 44 | `u64` | `manifest_stored_size` | bytes on disk (the zstd frame when compressed) |
| 52 | `u64` | `manifest_raw_size` | bytes after decompression |
| 60 | `u64` | `file_size` | total file size; readers reject a shorter or longer file |
| 68 | `u8[32]` | `manifest_sha256` | SHA-256 of the stored (compressed) manifest payload |
| 100 | `u8[28]` | `reserved` | zero; readers reject a non-zero byte, so the header cannot be altered invisibly |

### Chunk table entry (128 bytes)

| Offset | Type | Field | Meaning |
| --- | --- | --- | --- |
| 0 | `char[64]` | `id` | NUL-terminated, 1–63 bytes of `[A-Za-z0-9._:/-]`; unique, order is the writer's |
| 64 | `u32` | `kind` | `1` json, `2` msl, `3` reflection, `4` texture, `5` buffer, `6` binary |
| 68 | `u32` | `flags` | bit 0: this payload is a zstd frame |
| 72 | `u64` | `offset` | absolute, 8-byte aligned |
| 80 | `u64` | `stored_size` | bytes on disk |
| 88 | `u64` | `raw_size` | bytes after decompression (equal to `stored_size` when not compressed) |
| 96 | `u8[32]` | `sha256` | SHA-256 of the stored bytes |

Chunk ids are the scene's namespace, and they are also the reference keys in the manifest: `msl/...`,
`refl/...`, `tex/...`, `buf/...` by convention, but any id that satisfies the charset rule works. The C reader
requires the table to be sorted the way the writer emits it only in the sense that it checks for duplicates; the
order carries no meaning.

### JSON envelope (the manifest payload)

The manifest payload is not the scene manifest itself but an envelope that repeats the directory, so that a reader
that only has the decompressed manifest can still verify every chunk it refers to, and so that a package can be
diffed without parsing the binary table:

```json
{
  "nvs": {
    "format": "scenecap",
    "version": "1.0",
    "generator": "d2r_gfx/0.3.1",
    "chunk_count": 12,
    "manifest_bytes": 4352
  },
  "chunks": [
    {"id": "msl/render.vs", "kind": "msl", "flags": 0, "compression": "none",
     "stored_bytes": 512, "raw_bytes": 512,
     "sha256": "9f2c…"}
  ],
  "manifest": { "capture": { … }, "frames": [ … ] }
}
```

`nvs.manifest_bytes` is the length of the envelope in bytes, which equals the header's `manifest_raw_size`. The
Python reader cross-checks the envelope against the header and the chunk table; the C reader trusts the table and
verifies the hashes. `manifest` is stored as-is: the writer does not reformat, reorder or validate the scene
manifest beyond checking that it is a single JSON object.

## Scene manifest

Every section is required; the Python verifier (`scenecap verify`) enforces the field names, types and the
cross-references described below. Unknown extra fields are preserved and ignored, so a capture can carry
debug-only data without a format bump.

### `capture`

```json
"capture": {
  "game": "nier", "title_id": "0100…", "game_version": "1.0.2",
  "git_commit": "…40 hex…", "port_version": "0.3.1", "build_version": "release-7-gdeadbeef",
  "gpu": "Apple M2", "macos": "15.2", "metal": "3.2",
  "frame_first": 0, "frame_count": 2, "captured_at": "2026-02-03T04:05:06Z"
}
```

All ten strings must be present and non-empty. `frame_first`/`frame_count` must agree with `frames[]`:
`frames[i].index` is `frame_first + i`, and `len(frames) == frame_count`.

### `frames`

```json
{"index": 0, "passes": ["pass/render", "pass/compute"], "presented": false,
 "drawable": {"width": 1920, "height": 1080, "pixel_format": "bgra8unorm"}}
```

`index` (ascending, no gaps), `passes` (ordered pass ids, each must exist), optional `presented`, optional
`drawable`.

### `passes`

```json
{"id": "pass/render", "frame": 0, "kind": "render", "width": 1920, "height": 1080,
 "pixel_format": "bgra8unorm",
 "attachments": [
   {"slot": 0, "texture": "tex/color", "level": 0, "layer": 0, "load": "clear", "store": "store",
    "clear": [0.1, 0.2, 0.3, 1.0]}
 ],
 "pipelines": ["pipe/render"], "draws": ["draw/tri"]}
```

- `kind`: `render`, `compute`, `blit`.
- `attachments[]`: `slot`, `texture` (a texture resource), `level` and `layer` within that resource's mip/layer
  range, `load` (`load`|`clear`|`dontcare`), `store` (`store`|`dontcare`), and `clear` values (1–4 numbers,
  required when `load` is `clear`). The capture layer writes colour attachments in slots 0–7 and the depth
  attachment in slot 8.
- `pipelines`, `draws`: ids; every id must exist and every draw's `pass` must point back. A pass that lists draws
  must list at least one pipeline (so a clear-only or blit pass may list neither, and a compute pass lists its
  compute pipeline).
- `frame` is optional for a pass reused across frames; when present it must be a captured frame index. Captures
  always write it.

### `pipelines`

```json
{"id": "pipe/render", "key": "r|vs0001|fs0001",
 "stages": [
   {"stage": "vertex",   "msl_blob": "msl/render.vs", "reflection_blob": "refl/render.vs"},
   {"stage": "fragment", "msl_blob": "msl/render.fs", "reflection_blob": "refl/render.fs"}],
 "state": { … }}
```

`stages[].stage` is one of `vertex`, `fragment`, `kernel`, `object`, `mesh`, `tile`; `msl_blob` must name an
`msl` chunk and `reflection_blob` a `reflection` chunk (both optional individually, but at least one stage must
carry an MSL blob). `key` is the capture's pipeline cache key (informational). `state` is the complete pipeline
state, and it is the state the replayer builds its pipeline from:

```json
"state": {
  "blend": {"enabled": true, "attachments": [
     {"index": 0, "enabled": true,
      "rgb_operation": "add", "rgb_source": "source_alpha", "rgb_destination": "one_minus_source_alpha",
      "alpha_operation": "add", "alpha_source": "one", "alpha_destination": "one_minus_source_alpha"}]},
  "depth_stencil": {"depth_test": true, "depth_write": true, "depth_compare": "less", "stencil": null},
  "raster": {"cull": "back", "fill": "fill", "depth_bias": 0, "depth_slope_scale": 1.0, "depth_clamp": 0.0},
  "viewport": [0, 0, 1920, 1080],
  "scissor": [0, 0, 1920, 1080],
  "frontface": "ccw",
  "primitive_type": "triangle"
}
```

`blend.attachments[].rgb_operation`/`alpha_operation` are Metal blend operations (`add`, `subtract`,
`reverse_subtract`, `min`, `max`); `rgb_source`/`rgb_destination` are blend factors (`zero`, `one`,
`source_color`, `one_minus_source_color`, `source_alpha`, `one_minus_source_alpha`, `destination_color`,
`one_minus_destination_color`, `destination_alpha`, `one_minus_destination_alpha`, `source_alpha_saturated`,
`blend_color`, `one_minus_blend_color`, `blend_alpha`, `one_minus_blend_alpha`). `depth_compare` is one of
`never`, `less`, `equal`, `less_equal`, `greater`, `not_equal`, `greater_equal`, `always`. The viewport and
scissor are four numbers (`x`, `y`, `width`, `height`). `frontface` is `cw`|`ccw`, `cull` is `none`|`front`|`back`,
`fill` is `fill`|`lines`|`points`, `primitive_type` is `point`|`line`|`line_strip`|`triangle`|`triangle_strip`.

### `draws`

```json
{"id": "draw/tri", "pass": "pass/render", "pipeline": "pipe/render", "label": "first",
 "vertex": {"start": 0, "count": 3},
 "index": {"type": "uint16", "start": 0, "count": 3, "buffer": "buf/index"},
 "instances": 1, "primitive_type": "triangle",
 "argument_table": {
   "vertex_buffers": [{"index": 0, "stage": 0, "buffer": "buf/vbuf", "offset": 0, "length": 48}],
   "textures": [{"index": 0, "stage": 1, "texture": "tex/color"}],
   "samplers": [{"index": 0, "stage": 1}],
   "uniform_buffers": [{"index": 0, "stage": 0, "buffer": "buf/ubuf", "offset": 0, "length": 64}],
   "storage_buffers": [{"index": 0, "stage": 1, "buffer": "buf/storage", "offset": 0, "length": 256}],
   "fragment_buffers": [{"index": 0, "stage": 1, "buffer": "buf/fbuf", "offset": 0, "length": 16}]
 },
 "state": { … }}
```

`vertex.count` is required for a non-indexed draw; `index.type` (`uint16`|`uint32`) marks an indexed draw, and an
8-bit index buffer is widened to `uint16` before the draw. `index.count`, `index.buffer` (a buffer resource id),
`instances` and `primitive_type` are written by the capture layer; a replayer must not guess the index buffer.
`stage` is `0` (vertex) or `1` (fragment); samplers record only their slot and stage (the NVN sampler state is not
part of the package). Each argument-table entry names an existing resource of the right type, and each
`offset + length` must stay inside that resource's declared byte range. `state` is the state at draw time; the
replayer builds the pipeline from `pipelines[].state`, so `state` here is a record for comparison and debugging.

### `dispatches`

```json
{"id": "dispatch/0", "pass": "pass/compute", "pipeline": "pipe/compute",
 "grid": [8, 1, 1],
 "argument_table": {"textures": [{"index": 0, "stage": 0, "texture": "tex/out"}],
                    "uniform_buffers": [{"index": 0, "stage": 0, "buffer": "buf/params", "offset": 0, "length": 64}]}}
```

`grid` is required and takes three positive dimensions. Dispatches share the argument-table shape of draws and are
recorded and validated, but the replayer does not execute compute passes today (see `## Limits`).

### `resources`

```json
{"id": "tex/color", "type": "texture", "blob": "tex/color",
 "pixel_format": "rgba8unorm", "width": 1920, "height": 1080, "depth": 1,
 "mip_levels": 1, "array_layers": 1, "sample_count": 1, "texture_type": "2d",
 "usage": ["render_target", "shader_read"],
 "bytes": {"offset": 0, "size": 8294400},
 "mip_range": {"first": 0, "count": 1}, "layer_range": {"first": 0, "count": 1}}
```

- `type` is `texture` or `buffer`; `blob` must name a chunk of that kind (ids usually match, but need not).
- `bytes` selects a range inside the blob, so several resources can share one packed chunk. The range must fit.
- A texture also declares `pixel_format`, `width`, `height`, `depth`, `mip_levels`, `array_layers`,
  `sample_count`, `texture_type` (`1d`, `2d`, `3d`, `cube`, `2d_array`, `cube_array`, `2d_multisample`) and
  `mip_range`/`layer_range` inside those counts. A buffer declares `usage` only.
- `usage` is advisory (`vertex`, `index`, `uniform`, `storage`, `shader_read`, `shader_write`, `render_target`,
  `copy_source`, `copy_destination`).
- A snapshot holds exactly one mip level and one array layer per texture (`mip_range.count` and
  `layer_range.count` are 1), even when the resource declares a fuller `mip_levels`/`array_layers`. The replayer
  allocates the full resource and uploads the recorded range.

## Compression

Every payload (the envelope included) is a Zstandard frame when compression makes it smaller, and the exact stored
bytes when it does not:

- The Python side uses `compression.zstd` at level 3 by default (`write_package(..., level=...)`).
- The C writer has a built-in encoder that emits only *raw* and *RLE* blocks — enough to shrink uniform clear
  colours, zero-filled buffers and large constant tables without a dependency. For real ratios the capture code
  installs `nvs_writer_set_compressor()` (libzstd or Apple's `libcompression`); a frame that does not shrink the
  payload is discarded and the payload is stored verbatim.
- The C reader's built-in decoder understands verbatim payloads and the raw/RLE frames its own encoder produces.
  A frame that uses compressed blocks needs `nvs_read_set_decompressor()`; without one it fails with
  `NVS_ERR_UNSUPPORTED` rather than guessing. The Python reader always has real zstd.

## Version policy

- `major` changes when an existing reader would misread a package: field layout, header/table offsets, the meaning
  of an existing field, the envelope shape. A reader accepts only `major == 1` today.
- `minor` changes when the package remains readable by an older reader: a new optional manifest field, a new chunk
  kind, a new enum member, a new flag bit. A reader accepts `minor <=` its own minor, and refuses a newer minor
  (`nvs: package format 1.2 is newer than this reader's 1.0`) so that a replayer never silently drops data it does
  not understand. Writers therefore bump `minor` exactly when adding something a reader may want to see.
- Adding an optional manifest field and leaving `minor` alone is allowed only for debug data no other tool consumes.
- Readers must not reject unknown JSON fields; they may warn. Writers must not repurpose a field.
- `reserved` header bytes stay zero in `1.x`; a future minor that uses one bumps `minor`.

## Command line

`tools/scenecap/scenecap` is the single entry point; every subcommand is a thin wrapper over the component below
it, so the same commands work in every game tree and in the synthetic loop:

```
scenecap verify   <package.nvs> [--json] [--quiet]          # format/python/scenecap_pkg.py
scenecap info     <package.nvs> [--json]                    # header, capture metadata, directory
scenecap cat      <package.nvs> <chunk-id> > blob.bin
scenecap manifest <package.nvs>
scenecap synth    <out.nvs> [--raw] [--indexed] [--compute] [--broken-shader] [--pixel-format F]
scenecap replay   <package.nvs> -o <dir> [replayer options…]
scenecap diff     <base-dir> <candidate-dir> -o report.md [diff options…]
scenecap --version
```

With no arguments it prints the usage and exits `0`. `synth` writes a synthetic package (one clear + one textured
triangle) so the whole chain can be exercised without a game. `replay` forwards to
`replay/build/scenecap_replay`; if the replayer is not built it explains how to build it and exits `2`. `diff`
forwards to `compare/scenecap_diff.py` and keeps the full command in its report. Set `SCENECAP_REPLAY` to point at
another replayer binary.

`verify` checks, in order: the magic and version gate, the header/table/manifest layout, every payload's SHA-256
(header manifest hash and each chunk hash), the envelope against the header and the table, and the manifest's
references and cross-references. It exits `0` only when no error was found; a failure names the chunk or the
manifest path, for example:

```
FAIL scene.nvs
  capture: synthetic 0100000000000001 1.0.0 frame 0+2 (Apple M2, macOS 15.2, Metal 3.2)
  container: 12 chunk(s), manifest 4352 bytes
  error: chunk 'tex/color' hash mismatch: directory records 9f2c…, payload hashes to 1a0b…
```

Exit codes: `0` clean, `1` invalid package, `2` usage or I/O error, `3` a pass the replayer could not execute
(only `replay` uses `3`; the render passes it did execute are still written). Warnings (an unreferenced chunk, a
texture chunk no draw or attachment binds) do not fail verification; they are printed so a capture regression is
visible.

## C API

```c
#include "nvs_format.h"

nvs_writer *writer = nvs_writer_create("frame.nvs", NULL, &err);   /* NULL options = defaults, "scenecap/1.0" */
nvs_blob_input blob = { .id = "tex/color", .kind = NVS_KIND_TEXTURE,
                        .data = pixels, .size = pixel_bytes };
nvs_writer_add_blob(writer, &blob, &err);
nvs_writer_add_text(writer, "msl/render.vs", NVS_KIND_MSL, msl_source, &err);
nvs_writer_set_manifest(writer, manifest_json, manifest_len, &err);
if (nvs_writer_finish(writer, &err) != NVS_OK)          /* atomic: temp file + rename, frees the writer */
    fprintf(stderr, "capture: %s\n", err.message);
```

`nvs_writer_finish` writes to `<path>.tmp.<pid>` and renames, so an interrupted capture never truncates an existing
package; a failure leaves the previous file intact. The reader mirrors it: `nvs_read_open`, `nvs_read_verify_hashes`,
`nvs_read_manifest`, `nvs_read_blob`, `nvs_read_close`. `nvs_read_blob` verifies that payload's hash before handing
out the bytes, so a caller cannot accidentally replay corrupted data. Every function returns `nvs_status` and fills
`nvs_error.message` with a sentence that names the offending chunk.

## Python API

```python
import scenecap_pkg as sp

with sp.read_package("frame.nvs") as package:   # verifies structure and every payload hash
    package.manifest                           # the scene manifest as a dict
    package.payload("tex/color")               # decompressed bytes, cached per chunk

report = sp.verify_package("frame.nvs")         # structure + hashes + manifest references
report.ok, report.errors, report.warnings

sp.write_package("out.nvs", manifest_dict, [sp.Blob("msl/render.vs", "msl", source_bytes)])
```

`read_package` raises `PackageError` (`code` is `format`, `unsupported`, `io` or `argument`) instead of returning a
half-valid object; `verify_package` never raises for package contents and reports every problem it finds (up to 200).

## Running the tests

```
python -m unittest discover -s tools/scenecap/tests -q
make check            # the whole repository, including this suite and the C self-test
```

The unittest suite compiles `format/nvs_format.c` twice with `-std=c11 -Wall -Wextra -Werror
-fsanitize=undefined` (the C self-test and `nvs_tool`), then checks: C-written package → Python reader/verifier;
Python-written package → C verifier; a byte flip in every chunk is reported with that chunk's id; the manifest
payload is protected too; a truncated, padded, mis-versioned or duplicated-id file is rejected; a missing or
wrong-kind reference fails with the offending manifest path; the writer rejects bad ids and keeps the previous
package on failure; and the CLI's exit codes match the format contract.

## Limits

`manifest_raw_size` ≤ 64 MiB, `raw_size` per chunk ≤ 16 GiB, `chunk_count` ≤ 2^32−1, chunk ids ≤ 63 bytes,
manifest JSON nesting ≤ 256 levels. Readers refuse a file whose `file_size` disagrees with its length, whose chunk
ranges overlap or leave the file, whose padding is not zero, or whose table entry size is not 128.

## Capture from the running game (d2r_gfx NVN → Metal)

`capture/` turns one..N rendered frames of a port into a scene package while the game runs.  It is split in
three layers so that the recording logic is testable without a game:

* `capture/scenecap_capture.c` — the recorder.  Plain C11, no Metal and no Objective-C: it takes passes,
  draws, dispatches and argument tables, asks the host for texture metadata/bytes, buffer bytes and shader
  sources, and writes the package through `format/nvs_format.c`.  `tests/capture_selftest.c` drives it with a
  synthetic host (`tests/test_capture.py` runs that binary).
* `capture/scenecap_nvn.h` — the hook API the replay layer calls (`scenecap_nvn_frame`, `_pass_begin`,
  `_pass_end`, `_draw`, `_dispatch`, `_request`).
* `capture/scenecap_nvn.inc` — the glue between that API and the replay layer's private state (`S`, `R`,
  `g_pass`, `g_cur_frame`, the texture registry, the shader objects and the Metal queue helpers).  It is
  textually included at the end of `nvn_mtl.m`; `SCENECAP_NVN_TOTK` selects the fork's texture lookup
  (`0` = nier's global `g_treg` table, `1` = the `bound_tex()` registry used by totk, odyssey and by3).  It also
  implements the host callbacks (texture snapshot readback, guest buffer reads through `pend_materialize`, MSL /
  reflection bytes from the shader cache).

Integration in `games/<game>/third_party/d2r_gfx/gfx/nvn/nvn_mtl.m` is four call sites plus the include:
`scenecap_nvn_frame()` in `mtl_set_frame`, `scenecap_nvn_pass_begin/end()` around `enc_r_begin`/`enc_r_end`,
`scenecap_nvn_draw()` at the end of `draw_inner` (state and bindings are final there, before the primitive
dispatch), and `scenecap_nvn_dispatch()` in `dispatch`.  The compile-time include lives at the bottom of the
file:

```c
#define SCENECAP_NVN_TOTK 0             /* 1 for totk, odyssey, by3 */
#define SCENECAP_NVN_DEFAULT_GAME "nier" /* optional; defaults to nier/totk from the fork flag */
#include "scenecap_nvn.inc"
```

`runtime/input.c` accepts `scenecap [<path>]`: the next frame begins a capture into `<path>` (or the
environment's path).  The include points at the shared tree, so `tools/scenecap/capture/` is the source of truth;
`tools/scenecap/sync.py` additionally keeps a copy of the six capture/format files inside each game tree, which is
what the game's `$(wildcard third_party/d2r_gfx/gfx/nvn/*.c)` build needs to compile the recorder and the container
writer.  The copies are byte-identical to the source and only ever contain scenecap files; a game's own files are
never overwritten.

### Multi-game sync

`nier`, `totk`, `odyssey` and `by3` carry the hooks (four call sites plus the include, the `scenecap` input
command, and the copied files). `botw` has no `third_party/d2r_gfx` tree yet, so it is skipped and reported.
To refresh or check every tree:

```
tools/scenecap/sync.py            # copy the six files into each game's gfx/nvn/
tools/scenecap/sync.py --check    # exit 1 and list every drifted file
tools/scenecap/sync.py --list     # the canonical file list and the game trees it targets
```

`make check` runs `sync.py --check`, so a tree that falls behind fails the build.

### Environment

| variable | meaning |
| --- | --- |
| `SCENECAP=1` | capture the frames below; without it every hook is a no-op |
| `SCENECAP_PKG` | output path; `%llu` or `#` becomes the first frame number (default `scenecap-%llu.nvs`) |
| `SCENECAP_FRAMES` | `<count>` or `<first>:<count>` (default `1`) |
| `SCENECAP_MAX_BYTES` | total budget for the resource snapshots (default 512 MiB) |
| `SCENECAP_COMPRESS=1` | let the writer compress blobs (default: store verbatim) |
| `SCENECAP_GAME`, `SCENECAP_TITLE_ID`, `SCENECAP_GAME_VERSION`, `SCENECAP_GIT_COMMIT`, `SCENECAP_PORT_VERSION`, `SCENECAP_BUILD_VERSION` | capture metadata overrides; the GPU, macOS and Metal strings are queried from the system |

The capture is only armed inside the window: `scenecap_frame_begin` ignores every frame outside
`[first, first+count)` and writes the package once the window ends (or at shutdown).  No Metal command is
issued and no package is written when `SCENECAP` is unset, so the hooks leave the port's behaviour unchanged.

### What is recorded, and what is dropped

Per recorded frame: every render pass (attachments with level/layer, load/store, clear values, size and pixel
format), every kept draw (pipeline key, vertex/index ranges, instances, primitive, per-draw state and argument
table) and every compute dispatch (kernel, grid, bindings).  Resources are snapshotted once per package: the
textures referenced by attachments and bindings (one mip level / array layer per resource) and the buffers of
argument tables and index data.  MSL and reflection JSON come from the shader cache; a draw whose shader
source is unavailable is dropped and counted in `warnings.dropped_draws`, and a pass that ends up without a
kept draw or dispatch is dropped, so the package never references something it does not contain.  A texture
over the per-texture or total budget is skipped the same way.  Verify a capture with:

```
tools/scenecap/scenecap verify out.nvs
```

MSAA targets are not snapshotted yet (`texture_read` declines them), and the capture assumes the replay
layer calls the hooks from its decision thread; the encoder thread never calls them.

## Replay and compare

Build the standalone replayer once (Metal, Foundation and zlib only — it never links the game):

```
make tools/scenecap/replay/build/scenecap_replay
```

Then replay a package and compare two replay directories pixel by pixel:

```
tools/scenecap/scenecap replay scene.nvs -o out-a
tools/scenecap/scenecap replay scene.nvs -o out-b
tools/scenecap/scenecap diff out-a out-b -o report.md
```

`out-*/index.json` (`schema: scenecap.replay/1`) lists every pass with its PNG, status and pixel metrics; `diff`
writes a Markdown report (plus `.json` and difference images) and exits `1` on a regression. `diff --baseline
<dir> <candidate> [--update-baseline]` compares against a stored baseline instead of two live replays. The
replayer caches compiled pipelines next to the package (`<pkg>.scenecap-cache/`) and rebuilds them when the
package hash or the Metal device changes; `--no-cache` disables it and `--validate-only` checks the package
without touching Metal.

A Chinese end-to-end guide (capture, replay, diff, supported features and multi-game integration) lives in
[`docs/scenecap.md`](../../docs/scenecap.md).
