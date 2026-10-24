/*
 * Self-contained tests for the .nvs container (no framework, exit code 0 means pass).
 * Built and run by `make check` and by tools/scenecap/tests/test_nvs_format.py.
 */
#include "nvs_format.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int failures = 0;

#define CHECK(cond, ...)                                                                                              \
    do {                                                                                                              \
        if (!(cond)) {                                                                                                \
            fprintf(stderr, "FAIL %s:%d: ", __FILE__, __LINE__);                                                      \
            fprintf(stderr, __VA_ARGS__);                                                                             \
            fprintf(stderr, "\n");                                                                                    \
            failures++;                                                                                               \
        }                                                                                                             \
    } while (0)

static const char *kManifest =
    "{\"capture\":{\"game\":\"synthetic\",\"title_id\":\"0\",\"game_version\":\"0.0.1\",\"git_commit\":\"none\","
    "\"port_version\":\"0.1\",\"build_version\":\"test\",\"gpu\":\"cpu\",\"macos\":\"15.0\",\"metal\":\"3.2\","
    "\"frame_first\":0,\"frame_count\":1,\"captured_at\":\"2026-01-01T00:00:00Z\"},"
    "\"frames\":[],\"passes\":[],\"pipelines\":[],\"draws\":[],\"resources\":[]}";

static void temp_path(char *buffer, size_t capacity, const char *name)
{
    snprintf(buffer, capacity, "/tmp/scenecap-test-%ld-%s.nvs", (long)getpid(), name);
    unlink(buffer);
}

static void test_sha256(void)
{
    static const char *abc = "abc";
    static const char *empty = "";
    static const char *quick = "The quick brown fox jumps over the lazy dog";
    char hex[65];
    uint8_t digest[32];
    nvs_sha256_digest(abc, 3, digest);
    nvs_hex(digest, 32, hex);
    CHECK(!strcmp(hex, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"), "sha256(abc) = %s", hex);
    nvs_sha256_digest(empty, 0, digest);
    nvs_hex(digest, 32, hex);
    CHECK(!strcmp(hex, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"), "sha256(empty) = %s", hex);
    nvs_sha256_digest(quick, strlen(quick), digest);
    nvs_hex(digest, 32, hex);
    CHECK(!strcmp(hex, "d7a8fbb307d7809469ca9abcb0082e4f8d5651e46d3cdb762d02d0bf37c9e592"), "sha256(quick) = %s", hex);

    /* Incremental and one-shot hashing must agree across block boundaries. */
    {
        uint8_t one_shot[32], chunked[32];
        uint8_t data[200];
        nvs_sha256 ctx;
        size_t i;
        for (i = 0; i < sizeof data; i++) data[i] = (uint8_t)(i * 7u);
        nvs_sha256_digest(data, sizeof data, one_shot);
        nvs_sha256_init(&ctx);
        nvs_sha256_update(&ctx, data, 1);
        nvs_sha256_update(&ctx, data + 1, 55);
        nvs_sha256_update(&ctx, data + 56, 64);
        nvs_sha256_update(&ctx, data + 120, sizeof data - 120);
        nvs_sha256_final(&ctx, chunked);
        CHECK(!memcmp(one_shot, chunked, 32), "incremental sha256 differs from one-shot");
    }
}

static void test_zstd(void)
{
    static const size_t sizes[] = {0, 1, 2, 3, 255, 256, 65535, 65536, 65537, 140000, 200000};
    size_t index;
    for (index = 0; index < sizeof sizes / sizeof sizes[0]; index++) {
        size_t size = sizes[index];
        uint8_t *plain = (uint8_t *)malloc(size ? size : 1);
        uint8_t *frame = (uint8_t *)malloc(nvs_zstd_bound(size));
        uint8_t *round = (uint8_t *)malloc(size ? size : 1);
        size_t frame_size, round_size, i;
        nvs_error err;
        for (i = 0; i < size; i++) plain[i] = (uint8_t)((i * 31u + 7u) & 0xffu);
        frame_size = nvs_zstd_compress(NULL, plain, size, frame, nvs_zstd_bound(size));
        CHECK(frame_size > 0, "compress returned 0 for %zu bytes", size);
        round_size = nvs_zstd_decompress_ex(frame, frame_size, round, size ? size : 1, &err);
        CHECK(round_size == size, "decompress %zu -> %zu (%s)", size, round_size, err.message);
        CHECK(size == 0 || !memcmp(plain, round, size), "zstd round trip differs at %zu bytes", size);
        free(plain);
        free(frame);
        free(round);
    }
    /* RLE blocks must survive: 100000 zero bytes decode to 100000 zeros. */
    {
        uint8_t *zeros = (uint8_t *)calloc(100000, 1);
        uint8_t *frame = (uint8_t *)malloc(nvs_zstd_bound(100000));
        uint8_t *round = (uint8_t *)malloc(100000);
        nvs_error err;
        size_t frame_size = nvs_zstd_compress(NULL, zeros, 100000, frame, nvs_zstd_bound(100000));
        CHECK(frame_size > 0 && frame_size < 100000, "zero run did not use RLE (%zu bytes)", frame_size);
        CHECK(nvs_zstd_decompress_ex(frame, frame_size, round, 100000, &err) == 100000, "RLE decode failed: %s",
              err.message);
        CHECK(!memcmp(zeros, round, 100000), "RLE content differs");
        free(zeros);
        free(frame);
        free(round);
    }
    /* Malformed input is rejected, never read out of bounds. */
    {
        uint8_t buffer[16] = {0};
        nvs_error err;
        CHECK(nvs_zstd_decompress_ex(buffer, sizeof buffer, buffer, sizeof buffer, &err) == 0,
              "garbage accepted as a zstd frame");
        CHECK(err.code == NVS_ERR_FORMAT, "garbage frame error code %d", err.code);
    }
}

static void add_text(nvs_writer *writer, const char *id, nvs_kind kind, const char *text)
{
    nvs_error err;
    int code = nvs_writer_add_text(writer, id, kind, text, &err);
    CHECK(code == NVS_OK, "add %s: %s", id, err.message);
}

static nvs_writer *make_writer(char *path, size_t capacity, const char *name)
{
    nvs_error err;
    nvs_write_options options;
    nvs_writer *writer;
    temp_path(path, capacity, name);
    memset(&options, 0, sizeof options);
    options.generator = "test/1.0";
    writer = nvs_writer_create(path, &options, &err);
    CHECK(writer != NULL, "writer: %s", err.message);
    return writer;
}

static void write_demo(const char *path)
{
    nvs_writer *writer = make_writer((char *)path, 4096, "demo");
    nvs_error err;
    int code;
    uint8_t texels[16];
    size_t i;
    if (!writer) return;
    for (i = 0; i < sizeof texels; i++) texels[i] = (uint8_t)i;
    add_text(writer, "msl/one", NVS_KIND_MSL, "// metal");
    add_text(writer, "refl/one", NVS_KIND_REFLECTION, "{\"stage\":\"vertex\"}");
    {
        nvs_blob_input blob;
        blob.id = "tex/one";
        blob.kind = NVS_KIND_TEXTURE;
        blob.data = texels;
        blob.size = sizeof texels;
        blob.flags = 0;
        code = nvs_writer_add_blob(writer, &blob, &err);
        CHECK(code == NVS_OK, "add tex: %s", err.message);
    }
    code = nvs_writer_set_manifest(writer, kManifest, strlen(kManifest), &err);
    CHECK(code == NVS_OK, "manifest: %s", err.message);
    code = nvs_writer_finish(writer, &err);
    CHECK(code == NVS_OK, "finish: %s", err.message);
}

static void test_round_trip(void)
{
    char path[4096];
    nvs_reader *reader = NULL;
    nvs_error err;
    char *manifest = NULL;
    size_t manifest_len = 0;
    void *texels = NULL;
    size_t texels_len = 0;
    const nvs_package_header *header;
    int code;

    write_demo(path);
    code = nvs_read_open(path, &reader, &err);
    CHECK(code == NVS_OK, "open: %s", err.message);
    if (code != NVS_OK) return;
    header = nvs_read_header(reader);
    CHECK(header->major == NVS_FORMAT_MAJOR && header->minor == NVS_FORMAT_MINOR, "version %u.%u",
          (unsigned)header->major, (unsigned)header->minor);
    CHECK(header->chunk_count == 3, "chunk count %u", (unsigned)header->chunk_count);
    CHECK(header->file_size > NVS_HEADER_SIZE, "file size %llu", (unsigned long long)header->file_size);
    code = nvs_read_verify_hashes(reader, &err);
    CHECK(code == NVS_OK, "hashes: %s", err.message);
    code = nvs_read_manifest(reader, &manifest, &manifest_len, &err);
    CHECK(code == NVS_OK && manifest_len > 0, "manifest: %s", err.message);
    if (code == NVS_OK) {
        CHECK(strstr(manifest, "\"nvs\"") != NULL, "envelope has no nvs object");
        CHECK(strstr(manifest, "\"manifest\"") != NULL, "envelope has no manifest object");
        CHECK(strstr(manifest, "scenecap") != NULL, "envelope has no format marker");
        free(manifest);
    }
    code = nvs_read_blob(reader, "tex/one", &texels, &texels_len, &err);
    CHECK(code == NVS_OK && texels_len == 16, "blob: %s", err.message);
    if (code == NVS_OK) {
        CHECK(((uint8_t *)texels)[15] == 15, "blob content differs");
        free(texels);
    }
    code = nvs_read_blob(reader, "missing/chunk", &texels, &texels_len, &err);
    CHECK(code == NVS_ERR_FORMAT, "missing chunk should fail with FORMAT, got %d", code);
    CHECK(strstr(err.message, "missing/chunk") != NULL, "missing chunk message: %s", err.message);
    nvs_read_close(reader);
    unlink(path);
}

static void test_tamper(void)
{
    char path[4096];
    nvs_reader *reader = NULL;
    nvs_error err;
    nvs_chunk_info chunk;
    FILE *file;
    long offset;
    int byte;
    int code;

    write_demo(path);
    code = nvs_read_open(path, &reader, &err);
    CHECK(code == NVS_OK, "open before tamper: %s", err.message);
    if (code != NVS_OK) return;
    code = nvs_read_find_chunk(reader, "tex/one", &chunk, &err);
    CHECK(code == NVS_OK, "find tex: %s", err.message);
    offset = (long)chunk.offset + 3;
    nvs_read_close(reader);

    file = fopen(path, "r+b");
    CHECK(file != NULL, "cannot reopen %s for tampering", path);
    if (!file) return;
    CHECK(fseek(file, offset, SEEK_SET) == 0, "seek");
    byte = fgetc(file);
    CHECK(byte != EOF, "read tamper byte");
    CHECK(fseek(file, offset, SEEK_SET) == 0, "seek back");
    fputc(byte ^ 0x40, file);
    fclose(file);

    reader = NULL;
    code = nvs_read_open(path, &reader, &err);
    /* The directory itself is untouched, so opening still succeeds and the hash check must fail. */
    CHECK(code == NVS_OK, "open after tamper: %s", err.message);
    if (code == NVS_OK) {
        code = nvs_read_verify_hashes(reader, &err);
        CHECK(code == NVS_ERR_FORMAT, "tampered chunk passed verification (code %d)", code);
        CHECK(strstr(err.message, "tex/one") != NULL, "tamper message should name the chunk: %s", err.message);
        {
            void *data = NULL;
            size_t len = 0;
            code = nvs_read_blob(reader, "tex/one", &data, &len, &err);
            CHECK(code == NVS_ERR_FORMAT, "tampered blob read returned %d", code);
            CHECK(strstr(err.message, "hash mismatch") != NULL, "blob error: %s", err.message);
            free(data);
        }
        nvs_read_close(reader);
    }
    unlink(path);
}

static void patch_byte(const char *path, long offset, uint8_t value)
{
    FILE *file = fopen(path, "r+b");
    CHECK(file != NULL, "cannot open %s", path);
    if (!file) return;
    CHECK(fseek(file, offset, SEEK_SET) == 0, "seek");
    fputc(value, file);
    fclose(file);
}

static void test_version_gate(void)
{
    char path[4096];
    nvs_reader *reader = NULL;
    nvs_error err;
    int code;

    write_demo(path);
    patch_byte(path, 8, 2); /* major = 2 */
    code = nvs_read_open(path, &reader, &err);
    CHECK(code == NVS_ERR_UNSUPPORTED, "major mismatch returned %d", code);
    CHECK(strstr(err.message, "newer") != NULL || strstr(err.message, "not supported") != NULL,
          "major message: %s", err.message);

    write_demo(path);
    patch_byte(path, 10, 1); /* minor = 1 */
    code = nvs_read_open(path, &reader, &err);
    CHECK(code == NVS_ERR_UNSUPPORTED, "minor mismatch returned %d", code);

    write_demo(path);
    patch_byte(path, 0, 'X'); /* magic */
    code = nvs_read_open(path, &reader, &err);
    CHECK(code == NVS_ERR_FORMAT, "bad magic returned %d", code);
    unlink(path);
}

static void test_writer_rejects(void)
{
    char path[4096];
    nvs_writer *writer;
    nvs_error err;
    int code;

    writer = make_writer(path, sizeof path, "reject");
    if (writer) {
        code = nvs_writer_add_text(writer, "bad id", NVS_KIND_MSL, "x", &err);
        CHECK(code == NVS_ERR_ARG, "space in id accepted (%d)", code);
        code = nvs_writer_add_text(writer, "ok/id", NVS_KIND_MSL, "x", &err);
        CHECK(code == NVS_OK, "valid id rejected: %s", err.message);
        code = nvs_writer_add_text(writer, "ok/id", NVS_KIND_MSL, "y", &err);
        CHECK(code == NVS_ERR_ARG, "duplicate id accepted (%d)", code);
        code = nvs_writer_add_text(writer, "kindless", NVS_KIND_NONE, "x", &err);
        CHECK(code == NVS_ERR_ARG, "NVS_KIND_NONE accepted (%d)", code);
        code = nvs_writer_set_manifest(writer, "[]", 2, &err);
        CHECK(code == NVS_ERR_ARG, "array manifest accepted (%d)", code);
        code = nvs_writer_set_manifest(writer, "{} trailing", 11, &err);
        CHECK(code == NVS_ERR_ARG, "trailing data accepted (%d)", code);
        code = nvs_writer_set_manifest(writer, "\"text\"", 6, &err);
        CHECK(code == NVS_ERR_ARG, "string manifest accepted (%d)", code);
        code = nvs_writer_set_manifest(writer, "{\"a\":1}", 7, &err);
        CHECK(code == NVS_OK, "object manifest rejected: %s", err.message);
        code = nvs_writer_set_manifest(writer, "{\"a\":2}", 7, &err);
        CHECK(code == NVS_ERR_ARG, "second manifest accepted (%d)", code);
        code = nvs_writer_finish(writer, &err);
        CHECK(code == NVS_OK, "finish: %s", err.message);
        unlink(path);
    }

    /* A writer that never got a manifest must not leave a file behind. */
    writer = make_writer(path, sizeof path, "nomanifest");
    if (writer) {
        code = nvs_writer_finish(writer, &err);
        CHECK(code == NVS_ERR_ARG, "finish without manifest returned %d", code);
        CHECK(access(path, F_OK) != 0, "a manifest-less package was created");
    }
}

static void test_many_chunks(void)
{
    char path[4096];
    nvs_writer *writer = make_writer(path, sizeof path, "many");
    nvs_error err;
    char id[64];
    int i;
    int code;
    nvs_reader *reader = NULL;

    if (!writer) return;
    for (i = 0; i < 300; i++) {
        snprintf(id, sizeof id, "buf/%03d", i);
        code = nvs_writer_add_text(writer, id, NVS_KIND_BUFFER, "0123456789abcdef", &err);
        CHECK(code == NVS_OK, "add %s: %s", id, err.message);
    }
    code = nvs_writer_set_manifest(writer, kManifest, strlen(kManifest), &err);
    CHECK(code == NVS_OK, "manifest: %s", err.message);
    code = nvs_writer_finish(writer, &err);
    CHECK(code == NVS_OK, "finish: %s", err.message);
    code = nvs_read_open(path, &reader, &err);
    CHECK(code == NVS_OK && nvs_read_chunk_count(reader) == 300, "chunk count %zu (%s)",
          nvs_read_chunk_count(reader), err.message);
    if (reader) {
        CHECK(nvs_read_verify_hashes(reader, &err) == NVS_OK, "many-chunk hashes: %s", err.message);
        nvs_read_close(reader);
    }
    unlink(path);
}

int main(void)
{
    test_sha256();
    test_zstd();
    test_round_trip();
    test_tamper();
    test_version_gate();
    test_writer_rejects();
    test_many_chunks();
    if (failures) {
        fprintf(stderr, "%d check(s) failed\n", failures);
        return 1;
    }
    printf("nvs_format: all checks passed\n");
    return 0;
}
