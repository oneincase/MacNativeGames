/*
 * nvs_tool — command-line front end for the scenecap .nvs container.
 *
 *   nvs_tool verify <pkg>          structural + per-block SHA-256 verification (no manifest interpretation)
 *   nvs_tool info <pkg>            header and chunk directory
 *   nvs_tool cat <pkg> <chunk>     write one decompressed chunk to stdout
 *   nvs_tool selftest-write <pkg>  write the deterministic synthetic fixture used by the cross-language tests
 *   nvs_tool --version
 *
 * `scenecap verify` (the Python tool) resolves manifest references on top of these checks. This C tool exists so the
 * capture side can validate a package without Python and so both readers can be exercised against one fixture.
 */
#include "nvs_format.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char *status_name(int code)
{
    switch (code) {
    case NVS_OK: return "ok";
    case NVS_ERR_ARG: return "argument";
    case NVS_ERR_IO: return "i/o";
    case NVS_ERR_FORMAT: return "format";
    case NVS_ERR_UNSUPPORTED: return "unsupported";
    case NVS_ERR_NOMEM: return "out of memory";
    case NVS_ERR_TOO_LARGE: return "too large";
    default: return "unknown";
    }
}

/* The C tool is the capture-side check: exit 0 only when the package is clean, 2 for any diagnosed failure. */
static int report(const char *what, const nvs_error *err)
{
    fprintf(stderr, "%s: %s: %s\n", what, status_name(err->code), err->message);
    return 2;
}

static void print_size(uint64_t bytes)
{
    if (bytes >= 1024ull * 1024ull * 1024ull) {
        printf("%.2f GiB", (double)bytes / (1024.0 * 1024.0 * 1024.0));
    } else if (bytes >= 1024ull * 1024ull) {
        printf("%.2f MiB", (double)bytes / (1024.0 * 1024.0));
    } else if (bytes >= 1024ull) {
        printf("%.2f KiB", (double)bytes / 1024.0);
    } else {
        printf("%llu B", (unsigned long long)bytes);
    }
}

static int cmd_verify(const char *path)
{
    nvs_reader *reader = NULL;
    nvs_error err;
    int code = nvs_read_open(path, &reader, &err);
    size_t i;
    if (code != NVS_OK) return report(path, &err);
    code = nvs_read_verify_hashes(reader, &err);
    if (code != NVS_OK) {
        nvs_read_close(reader);
        return report(path, &err);
    }
    printf("ok: %s\n", path);
    printf("  format %u.%u, %u chunks, manifest ", (unsigned)nvs_read_header(reader)->major,
           (unsigned)nvs_read_header(reader)->minor, (unsigned)nvs_read_chunk_count(reader));
    print_size(nvs_read_header(reader)->manifest_raw_size);
    printf("\n");
    for (i = 0; i < nvs_read_chunk_count(reader); i++) {
        nvs_chunk_info chunk;
        if (nvs_read_chunk_at(reader, i, &chunk, &err) != NVS_OK) {
            nvs_read_close(reader);
            return report(path, &err);
        }
        printf("  %-40s %-10s %8llu B%s\n", chunk.id, nvs_kind_name(chunk.kind),
               (unsigned long long)chunk.raw_size, (chunk.flags & NVS_CHUNK_FLAG_ZSTD) ? " zstd" : "");
    }
    nvs_read_close(reader);
    return 0;
}

static int cmd_info(const char *path)
{
    nvs_reader *reader = NULL;
    nvs_error err;
    const nvs_package_header *header;
    size_t i;
    int code = nvs_read_open(path, &reader, &err);
    if (code != NVS_OK) return report(path, &err);
    header = nvs_read_header(reader);
    printf("path            %s\n", path);
    printf("magic           %s\n", NVS_MAGIC);
    printf("format          %u.%u\n", (unsigned)header->major, (unsigned)header->minor);
    printf("flags           0x%08x%s%s\n", header->flags,
           (header->flags & NVS_HEADER_FLAG_MANIFEST_ZSTD) ? " manifest-zstd" : "",
           (header->flags & NVS_HEADER_FLAG_CHUNKS_ZSTD) ? " chunks-zstd" : "");
    printf("file size       %llu\n", (unsigned long long)header->file_size);
    printf("chunk table     %llu\n", (unsigned long long)header->chunk_table_offset);
    printf("manifest        %llu stored / %llu raw\n", (unsigned long long)header->manifest_stored_size,
           (unsigned long long)header->manifest_raw_size);
    printf("chunks          %u\n", (unsigned)header->chunk_count);
    for (i = 0; i < header->chunk_count; i++) {
        nvs_chunk_info chunk;
        char hex[16];
        if (nvs_read_chunk_at(reader, i, &chunk, &err) != NVS_OK) {
            nvs_read_close(reader);
            return report(path, &err);
        }
        nvs_hex(chunk.sha256, 6, hex);
        printf("  %-40s %-10s @%-10llu %8llu/%8llu B %s\n", chunk.id, nvs_kind_name(chunk.kind),
               (unsigned long long)chunk.offset, (unsigned long long)chunk.stored_size,
               (unsigned long long)chunk.raw_size, hex);
    }
    nvs_read_close(reader);
    return 0;
}

static int cmd_cat(const char *path, const char *id)
{
    nvs_reader *reader = NULL;
    nvs_error err;
    void *data = NULL;
    size_t len = 0;
    int code = nvs_read_open(path, &reader, &err);
    if (code != NVS_OK) return report(path, &err);
    code = nvs_read_blob(reader, id, &data, &len, &err);
    if (code != NVS_OK) {
        nvs_read_close(reader);
        return report(id, &err);
    }
    if (len && fwrite(data, 1, len, stdout) != len) {
        fprintf(stderr, "write to stdout failed: %s\n", strerror(errno));
        free(data);
        nvs_read_close(reader);
        return 2;
    }
    free(data);
    nvs_read_close(reader);
    return 0;
}

/* ------------------------------------------------------------------------------------------------------------- */
/* deterministic fixture                                                                                         */
/* ------------------------------------------------------------------------------------------------------------- */

static const char fixture_vert_msl[] =
    "#include <metal_stdlib>\n"
    "using namespace metal;\n"
    "struct VOut { float4 position [[position]]; float2 uv; };\n"
    "vertex VOut vmain(uint vid [[vertex_id]]) {\n"
    "    const float2 p[3] = {float2(-1.0, -1.0), float2(3.0, -1.0), float2(-1.0, 3.0)};\n"
    "    VOut out;\n"
    "    out.position = float4(p[vid], 0.0, 1.0);\n"
    "    out.uv = p[vid] * 0.5 + 0.5;\n"
    "    return out;\n"
    "}\n";

static const char fixture_frag_msl[] =
    "#include <metal_stdlib>\n"
    "using namespace metal;\n"
    "struct VOut { float4 position [[position]]; float2 uv; };\n"
    "fragment float4 fmain(VOut in [[stage_in]], texture2d<float> tex [[texture(0)]]) {\n"
    "    constexpr sampler s(coord::normalized, filter::linear, address::clamp_to_edge);\n"
    "    return tex.sample(s, in.uv);\n"
    "}\n";

static const char fixture_vert_reflection[] =
    "{\"stage\":\"vertex\",\"function\":\"vmain\",\"entries\":[],\"buffers\":[],\"textures\":[],\"samplers\":[]}";

static const char fixture_frag_reflection[] =
    "{\"stage\":\"fragment\",\"function\":\"fmain\",\"entries\":[],\"buffers\":[],"
    "\"textures\":[{\"index\":0,\"type\":\"texture2d\",\"access\":\"sample\"}],"
    "\"samplers\":[{\"index\":0,\"type\":\"sampler\"}]}";

static const char fixture_manifest[] =
    "{\n"
    "  \"capture\": {\"game\": \"synthetic\", \"title_id\": \"0000000000000000\", \"game_version\": \"0.0.1\",\n"
    "              \"git_commit\": \"0000000000000000000000000000000000000000\", \"port_version\": \"0.1.0\",\n"
    "              \"build_version\": \"selftest\", \"gpu\": \"Apple M-series\", \"macos\": \"15.0\",\n"
    "              \"metal\": \"3.2\", \"frame_first\": 0, \"frame_count\": 1,\n"
    "              \"captured_at\": \"2026-01-01T00:00:00Z\"},\n"
    "  \"frames\": [{\"index\": 0, \"passes\": [\"pass/main\"], \"presented\": true,\n"
    "               \"drawable\": {\"width\": 64, \"height\": 64, \"pixel_format\": \"bgra8unorm\"}}],\n"
    "  \"passes\": [{\"id\": \"pass/main\", \"frame\": 0, \"kind\": \"render\", \"width\": 64, \"height\": 64,\n"
    "               \"pixel_format\": \"bgra8unorm\",\n"
    "               \"attachments\": [{\"slot\": 0, \"texture\": \"tex/color0\", \"level\": 0, \"layer\": 0,\n"
    "                                 \"load\": \"clear\", \"store\": \"store\", \"clear\": [0.0, 0.0, 0.0, 1.0]}],\n"
    "               \"pipelines\": [\"pipe/main\"], \"draws\": [\"draw/0\"]}],\n"
    "  \"pipelines\": [{\"id\": \"pipe/main\", \"label\": \"main\",\n"
    "                 \"stages\": [{\"stage\": \"vertex\", \"msl_blob\": \"msl/main.vert\",\n"
    "                             \"reflection_blob\": \"refl/main.vert\"},\n"
    "                            {\"stage\": \"fragment\", \"msl_blob\": \"msl/main.frag\",\n"
    "                             \"reflection_blob\": \"refl/main.frag\"}],\n"
    "                 \"state\": {\"blend\": {\"enabled\": false, \"attachments\": []},\n"
    "                           \"depth_stencil\": {\"depth_test\": true, \"depth_write\": true,\n"
    "                                              \"depth_compare\": \"less\", \"stencil\": null},\n"
    "                           \"raster\": {\"cull\": \"none\", \"fill\": \"fill\", \"depth_bias\": 0,\n"
    "                                      \"depth_slope_scale\": 0.0, \"depth_clamp\": 0.0},\n"
    "                           \"viewport\": [0, 0, 64, 64], \"scissor\": [0, 0, 64, 64],\n"
    "                           \"frontface\": \"ccw\", \"primitive_type\": \"triangle\"}}],\n"
    "  \"draws\": [{\"id\": \"draw/0\", \"pass\": \"pass/main\", \"pipeline\": \"pipe/main\",\n"
    "              \"vertex\": {\"start\": 0, \"count\": 3}, \"index\": null, \"instances\": 1,\n"
    "              \"primitive_type\": \"triangle\",\n"
    "              \"argument_table\": {\"vertex_buffers\": [{\"index\": 0, \"buffer\": \"buf/vtx\",\n"
    "                                                       \"offset\": 0, \"length\": 48}],\n"
    "                                  \"textures\": [{\"index\": 0, \"texture\": \"tex/color0\"}],\n"
    "                                  \"samplers\": [{\"index\": 0, \"min_filter\": \"linear\",\n"
    "                                                 \"mag_filter\": \"linear\", \"address_u\": \"clamp_to_edge\",\n"
    "                                                 \"address_v\": \"clamp_to_edge\",\n"
    "                                                 \"address_w\": \"clamp_to_edge\"}],\n"
    "                                  \"uniform_buffers\": [{\"index\": 0, \"buffer\": \"buf/cst\",\n"
    "                                                        \"offset\": 0, \"length\": 16}]}}],\n"
    "  \"resources\": [\n"
    "    {\"id\": \"tex/color0\", \"type\": \"texture\", \"blob\": \"tex/color0\", \"pixel_format\": \"rgba8unorm\",\n"
    "     \"width\": 2, \"height\": 2, \"depth\": 1, \"mip_levels\": 1, \"array_layers\": 1, \"sample_count\": 1,\n"
    "     \"texture_type\": \"2d\", \"usage\": [\"shader_read\"], \"bytes\": {\"offset\": 0, \"size\": 16},\n"
    "     \"mip_range\": {\"first\": 0, \"count\": 1}, \"layer_range\": {\"first\": 0, \"count\": 1}},\n"
    "    {\"id\": \"buf/vtx\", \"type\": \"buffer\", \"blob\": \"buf/vtx\", \"usage\": [\"vertex\"],\n"
    "     \"bytes\": {\"offset\": 0, \"size\": 48}},\n"
    "    {\"id\": \"buf/cst\", \"type\": \"buffer\", \"blob\": \"buf/cst\", \"usage\": [\"uniform\"],\n"
    "     \"bytes\": {\"offset\": 0, \"size\": 16}},\n"
    "    {\"id\": \"buf/zeros\", \"type\": \"buffer\", \"blob\": \"buf/zeros\", \"usage\": [\"storage\"],\n"
    "     \"bytes\": {\"offset\": 0, \"size\": 65536}}\n"
    "  ]\n"
    "}\n";

static int write_fixture(const char *path)
{
    nvs_error err;
    nvs_write_options options;
    nvs_writer *writer;
    nvs_blob_input blob;
    uint8_t texels[16];
    uint8_t zeros[65536];
    float vertices[12] = {0.0f, 0.0f, 0.0f, 1.0f, 1.0f, 0.0f, 0.0f, 1.0f, 0.0f, 1.0f, 0.0f, 1.0f};
    float constants[4] = {1.0f, 0.5f, 0.25f, 1.0f};
    size_t i;
    int code;

    for (i = 0; i < sizeof texels; i++) texels[i] = (uint8_t)(i * 17u);
    memset(zeros, 0, sizeof zeros);
    memset(&options, 0, sizeof options);
    options.generator = "nvs-tool/1.0 (fixture)";
    writer = nvs_writer_create(path, &options, &err);
    if (!writer) return report(path, &err);

#define ADD_TEXT(chunk_id, chunk_kind, text)                                                                          \
    do {                                                                                                              \
        blob.id = (chunk_id);                                                                                         \
        blob.kind = (chunk_kind);                                                                                     \
        blob.data = (text);                                                                                           \
        blob.size = strlen(text);                                                                                     \
        blob.flags = 0;                                                                                               \
        code = nvs_writer_add_blob(writer, &blob, &err);                                                              \
        if (code != NVS_OK) return report(chunk_id, &err);                                                            \
    } while (0)
#define ADD_BYTES(chunk_id, chunk_kind, ptr, len)                                                                     \
    do {                                                                                                              \
        blob.id = (chunk_id);                                                                                         \
        blob.kind = (chunk_kind);                                                                                     \
        blob.data = (ptr);                                                                                            \
        blob.size = (len);                                                                                            \
        blob.flags = 0;                                                                                               \
        code = nvs_writer_add_blob(writer, &blob, &err);                                                              \
        if (code != NVS_OK) return report(chunk_id, &err);                                                            \
    } while (0)

    ADD_TEXT("msl/main.vert", NVS_KIND_MSL, fixture_vert_msl);
    ADD_TEXT("msl/main.frag", NVS_KIND_MSL, fixture_frag_msl);
    ADD_TEXT("refl/main.vert", NVS_KIND_REFLECTION, fixture_vert_reflection);
    ADD_TEXT("refl/main.frag", NVS_KIND_REFLECTION, fixture_frag_reflection);
    ADD_BYTES("tex/color0", NVS_KIND_TEXTURE, texels, sizeof texels);
    ADD_BYTES("buf/vtx", NVS_KIND_BUFFER, vertices, sizeof vertices);
    ADD_BYTES("buf/cst", NVS_KIND_BUFFER, constants, sizeof constants);
    /* A large constant block exercises the built-in RLE encoder on the write side and the decoder on the read side. */
    ADD_BYTES("buf/zeros", NVS_KIND_BUFFER, zeros, sizeof zeros);
#undef ADD_TEXT
#undef ADD_BYTES

    code = nvs_writer_set_manifest(writer, fixture_manifest, sizeof fixture_manifest - 1, &err);
    if (code != NVS_OK) {
        nvs_error scratch;
        nvs_writer_finish(writer, &scratch); /* frees the writer */
        return report("manifest", &err);
    }
    code = nvs_writer_finish(writer, &err);
    if (code != NVS_OK) return report(path, &err);
    printf("wrote %s (%zu blob(s))\n", path, (size_t)8);
    return 0;
}

static int usage(const char *argv0)
{
    fprintf(stderr,
            "usage: %s verify <package.nvs>\n"
            "       %s info <package.nvs>\n"
            "       %s cat <package.nvs> <chunk-id>\n"
            "       %s selftest-write <package.nvs>\n"
            "\n"
            "format %s (scenecap .nvs). verify checks the container and every block hash; the Python tool\n"
            "additionally resolves manifest references.\n",
            argv0, argv0, argv0, argv0, NVS_FORMAT_VERSION);
    return 1;
}

int main(int argc, char **argv)
{
    if (argc < 2) return usage(argv[0]);
    if (!strcmp(argv[1], "--version")) {
        printf("nvs_tool %s (format %s)\n", NVS_FORMAT_VERSION, NVS_FORMAT_VERSION);
        return 0;
    }
    if (!strcmp(argv[1], "verify") && argc == 3) return cmd_verify(argv[2]);
    if (!strcmp(argv[1], "info") && argc == 3) return cmd_info(argv[2]);
    if (!strcmp(argv[1], "cat") && argc == 4) return cmd_cat(argv[2], argv[3]);
    if (!strcmp(argv[1], "selftest-write") && argc == 3) return write_fixture(argv[2]);
    return usage(argv[0]);
}
