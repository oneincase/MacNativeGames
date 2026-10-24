/* Prints what the C read side decodes from a .nvs package.
 *
 * The replay test suite uses this to prove that the reader linked into scenecap_replay (nvs_format.c plus the optional
 * libzstd bridge) decodes both verbatim and real-zstd packages to the same bytes the Python writer produced.
 *
 * Output is one line per record, stable enough to parse:
 *   format=scenecap major=1 minor=0 chunks=6 zstd=1
 *   chunk id=... kind=texture raw=16384 stored=... zstd=0 bytes=16384 sha256=<hex of decoded payload>
 *   manifest bytes=3597 sha256=<hex of the decoded manifest>
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "nvs_format.h"
#include "replay_zstd.h"

static int print_digest(const void *data, size_t length, char *out, size_t out_cap)
{
    uint8_t digest[32];
    if (out_cap < 65) return 0;
    nvs_sha256_digest(data, length, digest);
    nvs_hex(digest, sizeof digest, out);
    return 1;
}

int main(int argc, char **argv)
{
    nvs_error err;
    nvs_reader *reader = NULL;
    size_t count;
    char hex[65];

    if (argc != 2) {
        fprintf(stderr, "usage: nvs_reader_probe <package.nvs>\n");
        return 2;
    }
    memset(&err, 0, sizeof err);
    if (nvs_read_open(argv[1], &reader, &err) != NVS_OK) {
        fprintf(stderr, "open: %s\n", err.message);
        return 1;
    }
    nvs_read_set_decompressor(reader, replay_zstd_hook, NULL);

    const nvs_package_header *header = nvs_read_header(reader);
    printf("format=scenecap major=%u minor=%u chunks=%u zstd=%d\n", (unsigned)header->major, (unsigned)header->minor,
           (unsigned)header->chunk_count, replay_zstd_available());

    count = nvs_read_chunk_count(reader);
    for (size_t index = 0; index < count; index++) {
        nvs_chunk_info info;
        void *data = NULL;
        size_t length = 0;

        memset(&info, 0, sizeof info);
        memset(&err, 0, sizeof err);
        if (nvs_read_chunk_at(reader, index, &info, &err) != NVS_OK) {
            fprintf(stderr, "chunk %zu: %s\n", index, err.message);
            nvs_read_close(reader);
            return 1;
        }
        if (nvs_read_blob(reader, info.id, &data, &length, &err) != NVS_OK) {
            fprintf(stderr, "chunk %s: %s\n", info.id, err.message);
            nvs_read_close(reader);
            return 1;
        }
        if (!print_digest(data, length, hex, sizeof hex)) {
            free(data);
            nvs_read_close(reader);
            return 1;
        }
        printf("chunk id=%s kind=%s raw=%llu stored=%llu zstd=%d bytes=%zu sha256=%s\n", info.id,
               nvs_kind_name(info.kind), (unsigned long long)info.raw_size, (unsigned long long)info.stored_size,
               (info.flags & NVS_CHUNK_FLAG_ZSTD) ? 1 : 0, length, hex);
        free(data);
    }

    {
        char *manifest = NULL;
        size_t manifest_len = 0;
        memset(&err, 0, sizeof err);
        if (nvs_read_manifest(reader, &manifest, &manifest_len, &err) != NVS_OK) {
            fprintf(stderr, "manifest: %s\n", err.message);
            nvs_read_close(reader);
            return 1;
        }
        if (!print_digest(manifest, manifest_len, hex, sizeof hex)) {
            free(manifest);
            nvs_read_close(reader);
            return 1;
        }
        printf("manifest bytes=%zu sha256=%s\n", manifest_len, hex);
        free(manifest);
    }

    nvs_read_close(reader);
    return 0;
}
