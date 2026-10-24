#!/usr/bin/env python3
"""Generate the synthetic scenecap package used to bootstrap the offline replayer.

The package is deliberately tiny but exercises the whole replay path: one 64x64 sRGB colour attachment cleared to a
known linear colour, one 4x4 linear texture sampled at its centre texel, one draw of one triangle, and the same MSL
blob shared by the vertex and fragment stages.

Because every vertex samples the *same* UV, the triangle is a flat quad of texture colour: the four corners must keep
the clear colour and the centre must be the sampled texel.  That makes the replayer's result checkable with four pixel
assertions and no reference renderer.

Usage:
    python3 scenecap_synth.py <out.nvs> [--raw] [--indexed] [--compute]

--raw       store blobs verbatim (the d2r capture kernel's default) instead of zstd frames
--indexed   draw the triangle with an index buffer instead of a plain vertex range
--compute   append a compute pass that must be reported as unsupported by the replayer
"""

from __future__ import annotations

import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PYTHON_TOOLS = os.path.join(os.path.dirname(HERE), "python")
sys.path.insert(0, PYTHON_TOOLS)

import scenecap_pkg  # noqa: E402  (path set up above)

WIDTH = 64
HEIGHT = 64
COLOR_FORMAT = "bgra8unorm_srgb"
CLEAR_LINEAR = (0.05, 0.1, 0.2, 1.0)

TEXTURE_SIZE = 4
# Texel (2, 2) is the one the shader must sample; everything else is a colour the clear/other texels cannot produce.
TEXEL_LINEAR = (200, 120, 40, 255)
TEXTURE_FILL = (16, 200, 32, 255)
SAMPLE_UV = (0.625, 0.625)

# NDC positions: an isosceles triangle covering the centre of the image but none of its corners.
POSITIONS = ((-0.75, -0.75), (0.75, -0.75), (0.0, 0.75))

MSL_SOURCE = """#include <metal_stdlib>
using namespace metal;

struct SynthVertex {
    float2 position;
    float2 uv;
};

struct SynthVaryings {
    float4 position [[position]];
    float2 uv;
};

vertex SynthVaryings vs_main(uint vertex_id [[vertex_id]],
                             constant SynthVertex *vertices [[buffer(0)]]) {
    SynthVaryings out;
    out.position = float4(vertices[vertex_id].position, 0.0, 1.0);
    out.uv = vertices[vertex_id].uv;
    return out;
}

fragment float4 fs_main(SynthVaryings in [[stage_in]],
                        texture2d<float> checker [[texture(0)]],
                        sampler nearest_sampler [[sampler(0)]]) {
    return checker.sample(nearest_sampler, in.uv);
}
"""


# Reflection is opaque to the replayer, but the container contract requires one per stage: it records what the
# capture knew about the resource bindings.
REFLECTION_VERTEX = '{"stage":"vertex","entry":"vs_main","buffers":[{"index":0,"kind":"vertex_layout"}]}'
REFLECTION_FRAGMENT = (
    '{"stage":"fragment","entry":"fs_main","textures":[{"index":0}],"samplers":[{"index":0}]}'
)


def _srgb_encode(value: float) -> float:
    if value <= 0.0:
        return 0.0
    if value >= 1.0:
        return 1.0
    if value <= 0.0031308:
        return value * 12.92
    return 1.055 * (value ** (1.0 / 2.4)) - 0.055


def expected_srgb8(linear_channels) -> tuple[int, int, int, int]:
    """The bytes an sRGB attachment must hold for these linear values (alpha stays linear)."""
    result = []
    for index, value in enumerate(linear_channels):
        encoded = value if index == 3 else _srgb_encode(value)
        result.append(max(0, min(255, int(round(encoded * 255.0)))))
    return tuple(result)


def texture_bytes() -> bytes:
    data = bytearray()
    for y in range(TEXTURE_SIZE):
        for x in range(TEXTURE_SIZE):
            texel = TEXEL_LINEAR if (x, y) == (2, 2) else TEXTURE_FILL
            data.extend(struct.pack("<4B", *texel))
    return bytes(data)


def vertex_bytes() -> bytes:
    data = bytearray()
    for x, y in POSITIONS:
        data.extend(struct.pack("<4f", x, y, SAMPLE_UV[0], SAMPLE_UV[1]))
    return bytes(data)


def index_bytes() -> bytes:
    return struct.pack("<3H", 0, 1, 2)


def build_manifest(
    *,
    indexed: bool = False,
    with_compute_pass: bool = False,
    pixel_format: str = COLOR_FORMAT,
    texture_format: str = "rgba8unorm",
) -> dict:
    """The scene manifest of the synthetic capture (see tools/scenecap/README.md for the contract)."""
    attachment = {
        "slot": 0,
        "texture": "color",
        "level": 0,
        "layer": 0,
        "load": "clear",
        "store": "store",
        "clear": list(CLEAR_LINEAR),
    }
    draw = {
        "id": "draw.triangle",
        "pass": "main",
        "pipeline": "pipeline.synth",
        "instances": 1,
        "primitive_type": "triangle",
        "argument_table": {
            "vertex_buffers": [{"index": 0, "buffer": "vertices", "offset": 0, "length": 3 * 16}],
            "textures": [{"index": 0, "texture": "checker"}],
            "samplers": [
                {
                    "index": 0,
                    "min_filter": "nearest",
                    "mag_filter": "nearest",
                    "address_u": "clamp_to_edge",
                    "address_v": "clamp_to_edge",
                    "address_w": "clamp_to_edge",
                }
            ],
        },
    }
    if indexed:
        # draw.index.buffer/offset is the replayer's documented extension: the base contract has no slot for the
        # index buffer, so the replayer refuses an indexed draw without it instead of guessing.
        draw["vertex"] = {"start": 0, "count": 3}
        draw["index"] = {"type": "uint16", "start": 0, "count": 3, "buffer": "indices", "offset": 0}
    else:
        draw["vertex"] = {"start": 0, "count": 3}

    resources = [
        {
            "id": "color",
            "type": "texture",
            "blob": "blob.color",
            "bytes": {"offset": 0, "size": WIDTH * HEIGHT * 4},
            "usage": ["render_target", "shader_read"],
            "pixel_format": pixel_format,
            "width": WIDTH,
            "height": HEIGHT,
            "depth": 1,
            "mip_levels": 1,
            "array_layers": 1,
            "sample_count": 1,
            "texture_type": "2d",
            "mip_range": {"first": 0, "count": 1},
            "layer_range": {"first": 0, "count": 1},
        },
        {
            "id": "checker",
            "type": "texture",
            "blob": "blob.checker",
            "bytes": {"offset": 0, "size": TEXTURE_SIZE * TEXTURE_SIZE * 4},
            "usage": ["shader_read"],
            "pixel_format": texture_format,
            "width": TEXTURE_SIZE,
            "height": TEXTURE_SIZE,
            "depth": 1,
            "mip_levels": 1,
            "array_layers": 1,
            "sample_count": 1,
            "texture_type": "2d",
            "mip_range": {"first": 0, "count": 1},
            "layer_range": {"first": 0, "count": 1},
        },
        {
            "id": "vertices",
            "type": "buffer",
            "blob": "blob.vertices",
            "bytes": {"offset": 0, "size": 3 * 16},
            "usage": ["shader_read"],
        },
    ]
    if indexed:
        resources.append(
            {
                "id": "indices",
                "type": "buffer",
                "blob": "blob.indices",
                "bytes": {"offset": 0, "size": 3 * 2},
                "usage": ["shader_read"],
            }
        )

    passes = [
        {
            "id": "main",
            "frame": 0,
            "kind": "render",
            "width": WIDTH,
            "height": HEIGHT,
            "pixel_format": COLOR_FORMAT,
            "attachments": [attachment],
            "pipelines": ["pipeline.synth"],
            "draws": ["draw.triangle"],
        }
    ]
    frame_passes = ["main"]
    if with_compute_pass:
        passes.append(
            {
                "id": "post.compute",
                "frame": 0,
                "kind": "compute",
                "width": WIDTH,
                "height": HEIGHT,
                "pixel_format": COLOR_FORMAT,
                "attachments": [],
                "pipelines": [],
                "draws": [],
            }
        )
        frame_passes.append("post.compute")

    return {
        "capture": {
            "game": "scenecap-synth",
            "title_id": "0000000000000000",
            "game_version": "1.0.0",
            "git_commit": "synthetic",
            "port_version": "1.0.0",
            "build_version": "synth-1",
            "gpu": "synthetic",
            "macos": "synthetic",
            "metal": "synthetic",
            "captured_at": "1970-01-01T00:00:00Z",
            "frame_first": 0,
            "frame_count": 1,
        },
        "frames": [
            {
                "index": 0,
                "passes": frame_passes,
                "presented": True,
                "drawable": {"width": WIDTH, "height": HEIGHT, "pixel_format": COLOR_FORMAT},
            }
        ],
        "resources": resources,
        "pipelines": [
            {
                "id": "pipeline.synth",
                "stages": [
                    {"stage": "vertex", "msl_blob": "msl.synth", "reflection_blob": "refl.synth.vs"},
                    {"stage": "fragment", "msl_blob": "msl.synth", "reflection_blob": "refl.synth.fs"},
                ],
                "state": {
                    "blend": {"enabled": False, "attachments": []},
                    "depth_stencil": {"depth_test": False, "depth_write": False, "depth_compare": "always"},
                    "raster": {"cull": "none", "fill": "fill"},
                    "viewport": [0, 0, WIDTH, HEIGHT],
                    "scissor": [0, 0, WIDTH, HEIGHT],
                    "frontface": "ccw",
                    "primitive_type": "triangle",
                },
            }
        ],
        "passes": passes,
        "draws": [draw],
    }


BROKEN_MSL_SOURCE = """#include <metal_stdlib>
using namespace metal;

/* Deliberately invalid: the replayer must report the pipeline key and the Metal diagnostics. */
vertex float4 vs_main(uint vertexID [[vertex_id]]) { return this_function_does_not_exist(vertexID); }
"""


def build_blobs(
    *, indexed: bool = False, omit: tuple[str, ...] = (), broken_shader: bool = False
) -> list[scenecap_pkg.Blob]:
    """Every blob the manifest references; omit names one or more of them to build a broken package."""
    blobs = [
        scenecap_pkg.Blob("msl.synth", "msl", (BROKEN_MSL_SOURCE if broken_shader else MSL_SOURCE).encode("utf-8")),
        scenecap_pkg.Blob("refl.synth.vs", "reflection", REFLECTION_VERTEX.encode("utf-8")),
        scenecap_pkg.Blob("refl.synth.fs", "reflection", REFLECTION_FRAGMENT.encode("utf-8")),
        scenecap_pkg.Blob("blob.color", "texture", bytes([0] * (WIDTH * HEIGHT * 4))),
        scenecap_pkg.Blob("blob.checker", "texture", texture_bytes()),
        scenecap_pkg.Blob("blob.vertices", "buffer", vertex_bytes()),
    ]
    if indexed:
        blobs.append(scenecap_pkg.Blob("blob.indices", "buffer", index_bytes()))
    return [blob for blob in blobs if blob.id not in omit]


def write_synth_package(
    path: str,
    *,
    raw: bool = False,
    indexed: bool = False,
    with_compute_pass: bool = False,
    pixel_format: str = COLOR_FORMAT,
    texture_format: str = "rgba8unorm",
    omit: tuple[str, ...] = (),
    broken_shader: bool = False,
) -> scenecap_pkg.Package:
    """Writes one synthetic .nvs package and returns it read back."""
    manifest = build_manifest(
        indexed=indexed,
        with_compute_pass=with_compute_pass,
        pixel_format=pixel_format,
        texture_format=texture_format,
    )
    blobs = build_blobs(indexed=indexed, omit=omit, broken_shader=broken_shader)
    return scenecap_pkg.write_package(
        path,
        manifest,
        blobs,
        generator="scenecap-synth/1.0",
        compress=not raw,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the synthetic scenecap package for the offline replayer.")
    parser.add_argument("output", help="path of the .nvs package to write")
    parser.add_argument("--raw", action="store_true", help="store blobs verbatim instead of zstd frames")
    parser.add_argument("--indexed", action="store_true", help="draw through an index buffer")
    parser.add_argument("--compute", action="store_true", help="append a compute pass the replayer must report")
    parser.add_argument(
        "--broken-shader", action="store_true", help="store MSL that fails to compile (failure-path test)"
    )
    parser.add_argument(
        "--pixel-format",
        default=COLOR_FORMAT,
        help=f"pixel format of the colour attachment (default {COLOR_FORMAT})",
    )
    args = parser.parse_args(argv)
    package = write_synth_package(
        args.output,
        raw=args.raw,
        indexed=args.indexed,
        with_compute_pass=args.compute,
        pixel_format=args.pixel_format,
        broken_shader=args.broken_shader,
    )
    print(
        f"wrote {package.path} ({len(package.chunks)} chunk(s), {'raw' if args.raw else 'zstd'} storage)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
