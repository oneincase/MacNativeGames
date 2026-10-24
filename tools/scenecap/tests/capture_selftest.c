/* Unit-test harness for the scenecap capture module.
 *
 * It plays the role of the replay layer: a fake host supplies texture metadata, texture/buffer bytes and shader
 * sources, then a deterministic synthetic scene is recorded. The Python tests build this file, run it and verify
 * the package it writes with the scenecap package tools.
 *
 * Usage: capture_selftest [--missing-msl] [--env] [--coherent] [--render-only] <package>
 *
 * --coherent sizes the fake colour/depth textures like the 64x64 pass and --render-only drops the compute
 * dispatch, so the resulting package is a scene the offline replayer supports (integration test and docs loop).
 */
#include "../capture/scenecap_capture.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define FAKE_TEXTURE_COLOR 100ull
#define FAKE_TEXTURE_DEPTH 200ull
#define FAKE_VERTEX_BUFFER 0x1000ull
#define FAKE_UNIFORM_BUFFER 0x2000ull
#define FAKE_INDEX_BUFFER 0x3000ull
#define FAKE_STORAGE_BUFFER 0x4000ull
#define FAKE_VERTEX_BUFFER2 0x5000ull
#define FAKE_VERTEX_BYTES 48u
#define FAKE_UNIFORM_BYTES 64u
#define FAKE_INDEX_BYTES 12u
#define FAKE_STORAGE_BYTES 256u

static int g_missing_msl;
static int g_no_host;
static int g_coherent;    /* size the fake attachments like the recorded 64x64 pass, so the package can be replayed */
static int g_render_only; /* skip the compute dispatch, so the package holds a scene the replayer supports */

static int fake_texture_info(void *user, uint64_t serial, scenecap_texture_info *out) {
    (void)user;
    memset(out, 0, sizeof *out);
    const uint32_t side = g_coherent ? 64u : 4u;
    if (serial == FAKE_TEXTURE_COLOR) {
        out->width = side;
        out->height = side;
        out->depth = 1;
        out->mip_levels = 1;
        out->array_layers = 1;
        out->sample_count = 1;
        out->texture_type = 1;
        snprintf(out->pixel_format, sizeof out->pixel_format, "bgra8unorm");
        return 1;
    }
    if (serial == FAKE_TEXTURE_DEPTH) {
        out->width = side;
        out->height = side;
        out->depth = 1;
        out->mip_levels = 1;
        out->array_layers = 1;
        out->sample_count = 1;
        out->texture_type = 1;
        snprintf(out->pixel_format, sizeof out->pixel_format, "depth32float");
        return 1;
    }
    return 0;
}

static long fake_texture_read(void *user, uint64_t serial, uint32_t level, uint32_t layer, void *dst, size_t capacity) {
    (void)user;
    if (serial == FAKE_TEXTURE_COLOR) {
        size_t length = g_coherent ? capacity : 64;
        if (capacity < length) return -1;
        for (size_t i = 0; i < length; i++) ((unsigned char *)dst)[i] = (unsigned char)(i ^ level ^ layer);
        return (long)length;
    }
    if (serial == FAKE_TEXTURE_DEPTH) {
        size_t length = g_coherent ? capacity : 64;
        if (capacity < length) return -1;
        for (size_t i = 0; i < length; i++) ((unsigned char *)dst)[i] = (unsigned char)(0x80 + i);
        return (long)length;
    }
    return -1;
}

static long fake_buffer_read(void *user, uint64_t address, uint64_t length, void *dst, size_t capacity) {
    (void)user;
    if (capacity < length) return -1;
    for (uint64_t i = 0; i < length; i++) ((unsigned char *)dst)[i] = (unsigned char)(address + i * 7);
    return (long)length;
}

static int fake_shader_source(void *user, const char *key, const char **text, size_t *length) {
    (void)user;
    static char buffer[256];
    if (g_missing_msl && strncmp(key, "fs0001", 6) == 0) return 0;
    if (strncmp(key, "vs0001", 6) == 0) {
        snprintf(buffer, sizeof buffer, "#include <metal_stdlib>\nvertex float4 vs0001() { return float4(1); }\n");
    } else if (strncmp(key, "fs0001", 6) == 0) {
        snprintf(buffer, sizeof buffer, "#include <metal_stdlib>\nfragment float4 fs0001() { return float4(0); }\n");
    } else if (strncmp(key, "cs0001", 6) == 0) {
        snprintf(buffer, sizeof buffer,
                 "#include <metal_stdlib>\nkernel void cs0001() {}\n");
    } else {
        return 0;
    }
    *text = buffer;
    *length = strlen(buffer);
    return 1;
}

static int fake_shader_reflection(void *user, const char *key, const char **text, size_t *length) {
    (void)user;
    static char buffer[256];
    if (g_missing_msl && strncmp(key, "fs0001", 6) == 0) return 0;
    if (strncmp(key, "cs0001", 6) == 0) snprintf(buffer, sizeof buffer, "{\"tg\":[8,1,1],\"buffers\":2}");
    else snprintf(buffer, sizeof buffer, "{\"stage\":\"%s\",\"bindings\":4}", key);
    *text = buffer;
    *length = strlen(buffer);
    return 1;
}

static void fake_log(void *user, const char *message) {
    (void)user;
    fprintf(stderr, "capture: %s\n", message);
}

static void fill_state(scenecap_state *state, int blend) {
    memset(state, 0, sizeof *state);
    state->blend.enabled = (uint8_t)blend;
    state->blend.attachment_count = 1;
    state->blend.attachments[0].enabled = (uint8_t)blend;
    state->blend.attachments[0].rgb_operation = SCENECAP_BLEND_OP_ADD;
    state->blend.attachments[0].alpha_operation = SCENECAP_BLEND_OP_ADD;
    state->blend.attachments[0].rgb_source = SCENECAP_BLEND_ONE;
    state->blend.attachments[0].rgb_destination = SCENECAP_BLEND_ONE_MINUS_SOURCE_ALPHA;
    state->blend.attachments[0].alpha_source = SCENECAP_BLEND_ONE;
    state->blend.attachments[0].alpha_destination = SCENECAP_BLEND_ONE_MINUS_SOURCE_ALPHA;
    state->blend.attachments[0].write_mask = 0xF;
    state->depth_stencil.depth_test = 1;
    state->depth_stencil.depth_write = 1;
    state->depth_stencil.depth_compare = SCENECAP_COMPARE_LESS_EQUAL;
    state->depth_stencil.stencil_enabled = 0;
    state->depth_stencil.stencil_front_compare = SCENECAP_COMPARE_ALWAYS;
    state->depth_stencil.stencil_back_compare = SCENECAP_COMPARE_ALWAYS;
    state->raster.cull = SCENECAP_CULL_BACK;
    state->raster.fill = SCENECAP_FILL_FILL;
    state->raster.frontface = SCENECAP_FRONT_CCW;
    state->raster.depth_bias = 0.0f;
    state->raster.depth_slope_scale = 0.0f;
    state->raster.depth_clamp = 1.0f;
    state->viewport[0] = 0.0f;
    state->viewport[1] = 0.0f;
    state->viewport[2] = 64.0f;
    state->viewport[3] = 64.0f;
    state->scissor[0] = 0.0f;
    state->scissor[1] = 0.0f;
    state->scissor[2] = 64.0f;
    state->scissor[3] = 64.0f;
}

static uint32_t g_frames = 1;   /* --frames N: record N consecutive frames in one package */
int main(int argc, char **argv) {
    const char *path = NULL;
    int env_mode = 0;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--missing-msl") == 0) g_missing_msl = 1;
        else if (strcmp(argv[i], "--env") == 0) env_mode = 1;
        else if (strcmp(argv[i], "--no-host") == 0) g_no_host = 1;
        else if (strcmp(argv[i], "--coherent") == 0) g_coherent = 1;
        else if (strcmp(argv[i], "--render-only") == 0) g_render_only = 1;
        else if (strcmp(argv[i], "--frames") == 0 && i + 1 < argc) g_frames = (uint32_t)strtoul(argv[++i], NULL, 10);
        else path = argv[i];
    }
    if (!path && !env_mode) {
        fprintf(stderr, "usage: %s [--missing-msl] [--env] [--coherent] [--render-only] <package>\n", argv[0]);
        return 2;
    }

    scenecap_options options;
    memset(&options, 0, sizeof options);
    if (env_mode) {
        int requested = scenecap_options_from_env(&options);
        printf("env requested=%d frames=%u limit=%llu compress=%d\n", requested, options.frame_count,
               (unsigned long long)options.max_total_bytes, options.compress);
        if (!requested) return 0;
    }
    if (env_mode) return 0;
    options.game = "synthetic-capture";
    options.title_id = "0100000000000000";
    options.game_version = "1.0.0";
    options.git_commit = "0123456789abcdef0123456789abcdef01234567";
    options.port_version = "nier-native/0.1";
    options.build_version = "capture-selftest";
    options.gpu = "synthetic-selftest";   /* 合成场景：不要冒充真机 GPU，真实捕获走 Metal device name */
    options.macos = "26.0";
    options.metal = "4.0";
    options.captured_at = "2026-01-01T00:00:00Z";
    options.output_path = path;
    options.frame_count = g_frames ? g_frames : 1;
    options.compress = 0;

    scenecap_host host;
    memset(&host, 0, sizeof host);
    if (g_no_host) memset(&host, 0, sizeof host);
    host.texture_info = g_no_host ? NULL : fake_texture_info;
    host.texture_read = fake_texture_read;
    host.buffer_read = fake_buffer_read;
    host.shader_source = fake_shader_source;
    host.shader_reflection = fake_shader_reflection;
    host.log = fake_log;

    char err[512];
    scenecap_capture *capture = scenecap_create(&host, &options, err, sizeof err);
    if (!capture) {
        fprintf(stderr, "scenecap_create: %s\n", err);
        return 1;
    }

    for (uint32_t f = 0; f < options.frame_count; f++) {
        if (options.frame_count > 1) printf("frame %u of %u\n", f + 1, options.frame_count);
        scenecap_frame_begin(capture, options.first_frame + f);

    scenecap_attachment attachments[2];
    memset(attachments, 0, sizeof attachments);
    attachments[0].slot = 0;
    attachments[0].has_texture = 1;
    attachments[0].texture = FAKE_TEXTURE_COLOR;
    attachments[0].load = SCENECAP_LOAD_CLEAR;
    attachments[0].store = SCENECAP_STORE_STORE;
    attachments[0].clear[0] = 0.1f;
    attachments[0].clear[1] = 0.2f;
    attachments[0].clear[2] = 0.3f;
    attachments[0].clear[3] = 1.0f;
    attachments[0].clear_count = 4;
    attachments[1].slot = 8;
    attachments[1].has_texture = 1;
    attachments[1].texture = FAKE_TEXTURE_DEPTH;
    attachments[1].load = SCENECAP_LOAD_CLEAR;
    attachments[1].store = SCENECAP_STORE_STORE;
    attachments[1].clear[0] = 1.0f;
    attachments[1].clear_count = 1;

    scenecap_pass pass;
    memset(&pass, 0, sizeof pass);
    pass.kind = SCENECAP_PASS_RENDER;
    pass.width = 64;
    pass.height = 64;
    pass.pixel_format = "bgra8unorm";
    pass.attachments = attachments;
    pass.attachment_count = 2;
    pass.label = "main";
    scenecap_pass_begin(capture, &pass);

    scenecap_state state;
    fill_state(&state, 1);
    scenecap_pipeline pipeline;
    memset(&pipeline, 0, sizeof pipeline);
    pipeline.vertex_shader = "vs0001";
    pipeline.fragment_shader = "fs0001";
    pipeline.state = state;

    scenecap_binding bindings[4];
    memset(bindings, 0, sizeof bindings);
    bindings[0].kind = SCENECAP_BIND_VERTEX_BUFFER;
    bindings[0].index = 0;
    bindings[0].resource = FAKE_VERTEX_BUFFER;
    bindings[0].size = FAKE_VERTEX_BYTES;
    bindings[1].kind = SCENECAP_BIND_TEXTURE;
    bindings[1].index = 0;
    bindings[1].stage = SCENECAP_STAGE_FRAGMENT;
    bindings[1].resource = FAKE_TEXTURE_COLOR;
    bindings[2].kind = SCENECAP_BIND_SAMPLER;
    bindings[2].index = 0;
    bindings[2].stage = SCENECAP_STAGE_FRAGMENT;
    bindings[3].kind = SCENECAP_BIND_UNIFORM_BUFFER;
    bindings[3].index = 0;
    bindings[3].resource = FAKE_UNIFORM_BUFFER;
    bindings[3].size = FAKE_UNIFORM_BYTES;

    scenecap_draw_desc draw;
    memset(&draw, 0, sizeof draw);
    draw.pipeline = &pipeline;
    draw.vertex_count = 3;
    draw.index_type = SCENECAP_INDEX_UINT16;
    draw.index_count = 3;
    draw.index_buffer = FAKE_INDEX_BUFFER;
    draw.index_bytes = FAKE_INDEX_BYTES;
    draw.instances = 1;
    draw.primitive = SCENECAP_PRIM_TRIANGLE;
    draw.bindings = bindings;
    draw.binding_count = 4;
    draw.label = "first";
    scenecap_draw(capture, &draw);

    bindings[0].resource = FAKE_VERTEX_BUFFER2;
    bindings[0].size = 72;
    draw.index_type = SCENECAP_INDEX_NONE;
    draw.index_count = 0;
    draw.vertex_count = 4;
    draw.instances = 2;
    draw.label = "second";
    scenecap_draw(capture, &draw);

    scenecap_pass_end(capture);

    if (!g_render_only) {
        scenecap_binding compute_bindings[2];
        memset(compute_bindings, 0, sizeof compute_bindings);
        compute_bindings[0].kind = SCENECAP_BIND_UNIFORM_BUFFER;
        compute_bindings[0].index = 0;
        compute_bindings[0].resource = FAKE_UNIFORM_BUFFER;
        compute_bindings[0].size = FAKE_UNIFORM_BYTES;
        compute_bindings[1].kind = SCENECAP_BIND_STORAGE_BUFFER;
        compute_bindings[1].index = 0;
        compute_bindings[1].resource = FAKE_STORAGE_BUFFER;
        compute_bindings[1].size = FAKE_STORAGE_BYTES;
        scenecap_pipeline compute;
        memset(&compute, 0, sizeof compute);
        compute.kernel_shader = "cs0001";
        fill_state(&compute.state, 0);
        const uint32_t grid[3] = {8, 1, 1};
        scenecap_dispatch(capture, &compute, compute_bindings, 2, grid, "blur");
    }

    /* Close the window explicitly: this is where the package is written. */
    }
    if (!scenecap_finish(capture, err, sizeof err)) {
        fprintf(stderr, "scenecap_finish: %s\n", err);
        scenecap_destroy(capture);
        return 1;
    }
    printf("package %s\n", scenecap_package_path(capture));
    printf("draws %llu\n", (unsigned long long)scenecap_draw_count(capture));
    scenecap_destroy(capture);
    return 0;
}
