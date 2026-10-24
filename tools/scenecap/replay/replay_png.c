/* See replay_png.h. */
#include "replay_png.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <zlib.h>

static void put_u32(unsigned char *out, unsigned value)
{
    out[0] = (unsigned char)(value >> 24);
    out[1] = (unsigned char)(value >> 16);
    out[2] = (unsigned char)(value >> 8);
    out[3] = (unsigned char)value;
}

static void write_chunk_header(unsigned char *out, unsigned length, const char *type)
{
    put_u32(out, length);
    memcpy(out + 4, type, 4);
}

/* 单块写入：长度 + 类型 + 数据 + CRC32。 */
static int write_chunk(FILE *file, const char *type, const unsigned char *data, size_t length)
{
    unsigned char header[8];
    unsigned char crc_bytes[4];
    uLong crc;
    if (length > 0xffffffffu) return 0;
    write_chunk_header(header, (unsigned)length, type);
    if (fwrite(header, 1, 8, file) != 8) return 0;
    if (length && fwrite(data, 1, length, file) != length) return 0;
    crc = crc32(0L, Z_NULL, 0);
    crc = crc32(crc, header + 4, 4);
    if (length) crc = crc32(crc, data, (uInt)length);
    put_u32(crc_bytes, (unsigned)crc);
    if (fwrite(crc_bytes, 1, 4, file) != 4) return 0;
    return 1;
}

static int fail(char *why, size_t why_cap, const char *message, const char *detail)
{
    if (why && why_cap) {
        if (detail) {
            snprintf(why, why_cap, "%s: %s", message, detail);
        } else {
            snprintf(why, why_cap, "%s", message);
        }
    }
    return 0;
}

int sc_png_write_rgba8(const char *path, const unsigned char *rgba, unsigned width, unsigned height, char *why,
                       size_t why_cap)
{
    static const unsigned char signature[8] = {0x89, 'P', 'N', 'G', 0x0d, 0x0a, 0x1a, 0x0a};
    size_t row_bytes, raw_len, compressed_cap;
    uLongf compressed_len;
    unsigned char *raw = NULL, *compressed = NULL;
    unsigned char ihdr[13];
    char *temporary = NULL;
    size_t path_len;
    FILE *file = NULL;
    unsigned y;
    int ok = 0;

    if (!path || !rgba) return fail(why, why_cap, "png: null path or pixels", NULL);
    if (!width || !height) return fail(why, why_cap, "png: zero-sized image", NULL);
    row_bytes = (size_t)width * 4;
    if (height > (SIZE_MAX / (row_bytes + 1))) return fail(why, why_cap, "png: image is too large", NULL);
    raw_len = (size_t)height * (row_bytes + 1);
    raw = (unsigned char *)malloc(raw_len);
    if (!raw) return fail(why, why_cap, "png: out of memory", NULL);
    for (y = 0; y < height; y++) {
        unsigned char *row = raw + (size_t)y * (row_bytes + 1);
        row[0] = 0; /* filter: none, keeps the bytes reproducible */
        memcpy(row + 1, rgba + (size_t)y * row_bytes, row_bytes);
    }
    compressed_cap = compressBound((uLong)raw_len) + 64;
    compressed = (unsigned char *)malloc(compressed_cap);
    if (!compressed) {
        free(raw);
        return fail(why, why_cap, "png: out of memory", NULL);
    }
    compressed_len = (uLongf)compressed_cap;
    {
        int zr = compress2(compressed, &compressed_len, raw, (uLong)raw_len, 6);
        if (zr != Z_OK) {
            free(raw);
            free(compressed);
            return fail(why, why_cap, "png: zlib compression failed", NULL);
        }
    }
    free(raw);
    raw = NULL;

    path_len = strlen(path);
    temporary = (char *)malloc(path_len + 16);
    if (!temporary) {
        free(compressed);
        return fail(why, why_cap, "png: out of memory", NULL);
    }
    snprintf(temporary, path_len + 16, "%s.tmp%ld", path, (long)getpid());
    file = fopen(temporary, "wb");
    if (!file) {
        free(temporary);
        free(compressed);
        return fail(why, why_cap, "png: cannot create", path);
    }
    put_u32(ihdr + 0, width);
    put_u32(ihdr + 4, height);
    ihdr[8] = 8;  /* bit depth */
    ihdr[9] = 6;  /* colour type: truecolour with alpha */
    ihdr[10] = 0; /* compression: deflate */
    ihdr[11] = 0; /* filter method: adaptive */
    ihdr[12] = 0; /* interlace: none */
    if (fwrite(signature, 1, 8, file) == 8 && write_chunk(file, "IHDR", ihdr, sizeof ihdr) &&
        write_chunk(file, "IDAT", compressed, (size_t)compressed_len) && write_chunk(file, "IEND", NULL, 0) &&
        fflush(file) == 0) {
        ok = 1;
    }
    if (fclose(file) != 0) ok = 0;
    free(compressed);
    if (ok && rename(temporary, path) != 0) {
        ok = fail(why, why_cap, "png: cannot replace", path);
    }
    if (!ok && why && why_cap && !why[0]) snprintf(why, why_cap, "png: cannot write %s", path);
    if (!ok) remove(temporary);
    free(temporary);
    return ok;
}
