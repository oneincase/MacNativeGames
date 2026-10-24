/* See replay_pixels.h. */
#include "replay_pixels.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

/* rg11b10float packs three tiny floats: 5-bit exponent and a 6-bit (r, g) or 5-bit (b) mantissa, no sign.
 * Infinitesimal (exponent 0) keeps the subnormal rule; inf / nan decode to 0 so the PNG stays readable. */
static double decode_uf11(unsigned bits, unsigned mantissa_bits)
{
    unsigned exponent = (bits >> mantissa_bits) & 0x1fu;
    unsigned mantissa = bits & ((1u << mantissa_bits) - 1u);
    double scale = (double)(1u << mantissa_bits);
    if (exponent == 0) return mantissa == 0 ? 0.0 : ((double)mantissa / scale) * pow(2.0, -14.0);
    if (exponent == 31) return 0.0;
    return (1.0 + (double)mantissa / scale) * pow(2.0, (double)exponent - 15.0);
}

size_t sc_pixel_kind_extent(sc_pixel_kind kind)
{
    switch (kind) {
        case SC_PIXEL_KIND_U8_SRGB:
        case SC_PIXEL_KIND_U8_LINEAR: return 4;
        case SC_PIXEL_KIND_UNORM16: return 8;
        case SC_PIXEL_KIND_FLOAT16: return 8;
        case SC_PIXEL_KIND_FLOAT32: return 16;
        case SC_PIXEL_KIND_RGB10A2:
        case SC_PIXEL_KIND_RG11B10:
        case SC_PIXEL_KIND_R32FLOAT: return 4;
        case SC_PIXEL_KIND_R16FLOAT: return 2;
        case SC_PIXEL_KIND_R8UNORM: return 1;
        case SC_PIXEL_KIND_RG8UNORM: return 2;
        case SC_PIXEL_KIND_NONE: break;
    }
    return 0;
}

void sc_rgba8_stats_compute(const unsigned char *rgba, size_t pixel_count, sc_rgba8_stats *out)
{
    size_t index;
    unsigned channel;
    if (!out) return;
    memset(out, 0, sizeof *out);
    for (channel = 0; channel < 4; channel++) {
        out->lo[channel] = 255;
        out->hi[channel] = 0;
    }
    if (!rgba) return;
    out->count = (unsigned)pixel_count;
    for (index = 0; index < pixel_count; index++) {
        for (channel = 0; channel < 4; channel++) {
            unsigned char value = rgba[index * 4 + channel];
            if (value < out->lo[channel]) out->lo[channel] = value;
            if (value > out->hi[channel]) out->hi[channel] = value;
            out->mean[channel] += (double)value;
        }
    }
    if (pixel_count) {
        for (channel = 0; channel < 4; channel++) out->mean[channel] /= (double)pixel_count;
    }
    if (!out->count) {
        for (channel = 0; channel < 4; channel++) {
            out->lo[channel] = 0;
            out->hi[channel] = 0;
        }
    }
}

unsigned char sc_clamp_byte(double value)
{
    if (!(value > 0.0)) return 0; /* also maps NaN to 0 so output stays deterministic */
    if (value >= 255.0) return 255;
    return (unsigned char)lrint(value);
}

double sc_srgb_encode(double linear)
{
    if (!(linear > 0.0)) return 0.0;
    if (linear <= 0.0031308) return 12.92 * linear;
    return 1.055 * pow(linear, 1.0 / 2.4) - 0.055;
}

unsigned char sc_linear8_to_srgb8(unsigned char linear)
{
    return sc_clamp_byte(sc_srgb_encode((double)linear / 255.0) * 255.0);
}

static double half_to_double(unsigned short bits)
{
    int sign = (bits >> 15) & 1;
    int exponent = (bits >> 10) & 0x1f;
    int mantissa = bits & 0x3ff;
    double value;
    if (exponent == 0) {
        value = (double)mantissa / 1024.0 * ldexp(1.0, -14);
    } else if (exponent == 31) {
        value = mantissa ? NAN : INFINITY;
    } else {
        value = (1.0 + (double)mantissa / 1024.0) * ldexp(1.0, exponent - 15);
    }
    return sign ? -value : value;
}

static unsigned char encode_channel(double value)
{
    if (!(value > 0.0)) return 0;
    if (value >= 1.0) return 255;
    return sc_clamp_byte(sc_srgb_encode(value) * 255.0);
}

static void fail(char *why, size_t why_cap, const char *message)
{
    if (why && why_cap) snprintf(why, why_cap, "%s", message);
}

int sc_pixels_to_rgba8_srgb(sc_pixel_kind kind, int source_is_bgra, const void *src, size_t src_len, unsigned width,
                            unsigned height, size_t src_row_bytes, unsigned char *dst, char *why, size_t why_cap)
{
    size_t extent = sc_pixel_kind_extent(kind);
    size_t row;
    if (!src || !dst) {
        fail(why, why_cap, "null pixel buffer");
        return 0;
    }
    if (!extent || !width || !height) {
        fail(why, why_cap, "pixel conversion needs a supported format and non-zero dimensions");
        return 0;
    }
    if (src_row_bytes < (size_t)width * extent) {
        fail(why, why_cap, "source row is shorter than the declared width");
        return 0;
    }
    if (src_len < src_row_bytes * (size_t)(height - 1) + (size_t)width * extent) {
        fail(why, why_cap, "source buffer is shorter than the declared height");
        return 0;
    }
    for (row = 0; row < height; row++) {
        const unsigned char *in = (const unsigned char *)src + row * src_row_bytes;
        unsigned char *out = dst + row * (size_t)width * 4;
        unsigned x;
        for (x = 0; x < width; x++) {
            unsigned char r = 0, g = 0, b = 0, a = 255;
            switch (kind) {
                case SC_PIXEL_KIND_U8_SRGB: {
                    const unsigned char *p = in + (size_t)x * extent;
                    if (source_is_bgra) {
                        b = p[0];
                        g = p[1];
                        r = p[2];
                        a = p[3];
                    } else {
                        r = p[0];
                        g = p[1];
                        b = p[2];
                        a = p[3];
                    }
                    break;
                }
                case SC_PIXEL_KIND_U8_LINEAR: {
                    const unsigned char *p = in + (size_t)x * extent;
                    if (source_is_bgra) {
                        b = sc_linear8_to_srgb8(p[0]);
                        g = sc_linear8_to_srgb8(p[1]);
                        r = sc_linear8_to_srgb8(p[2]);
                        a = p[3];
                    } else {
                        r = sc_linear8_to_srgb8(p[0]);
                        g = sc_linear8_to_srgb8(p[1]);
                        b = sc_linear8_to_srgb8(p[2]);
                        a = p[3];
                    }
                    break;
                }
                case SC_PIXEL_KIND_UNORM16: {
                    const unsigned short *p = (const unsigned short *)(in + (size_t)x * extent);
                    r = encode_channel((double)p[0] / 65535.0);
                    g = encode_channel((double)p[1] / 65535.0);
                    b = encode_channel((double)p[2] / 65535.0);
                    a = sc_clamp_byte((double)p[3] / 65535.0 * 255.0);
                    break;
                }
                case SC_PIXEL_KIND_FLOAT16: {
                    const unsigned short *p = (const unsigned short *)(in + (size_t)x * extent);
                    r = encode_channel(half_to_double(p[0]));
                    g = encode_channel(half_to_double(p[1]));
                    b = encode_channel(half_to_double(p[2]));
                    a = sc_clamp_byte(half_to_double(p[3]) * 255.0);
                    break;
                }
                case SC_PIXEL_KIND_FLOAT32: {
                    const float *p = (const float *)(in + (size_t)x * extent);
                    r = encode_channel((double)p[0]);
                    g = encode_channel((double)p[1]);
                    b = encode_channel((double)p[2]);
                    a = sc_clamp_byte((double)p[3] * 255.0);
                    break;
                }
                case SC_PIXEL_KIND_R16FLOAT: {
                    const unsigned short *p = (const unsigned short *)(in + (size_t)x * extent);
                    r = g = b = encode_channel(half_to_double(p[0]));
                    a = 255;
                    break;
                }
                case SC_PIXEL_KIND_R8UNORM: {
                    r = g = b = sc_linear8_to_srgb8(in[(size_t)x * extent]);
                    a = 255;
                    break;
                }
                case SC_PIXEL_KIND_RG8UNORM: {
                    const unsigned char *p = in + (size_t)x * extent;
                    r = sc_linear8_to_srgb8(p[0]);
                    g = sc_linear8_to_srgb8(p[1]);
                    b = 0;
                    a = 255;
                    break;
                }
                case SC_PIXEL_KIND_R32FLOAT: {
                    const float *p = (const float *)(in + (size_t)x * extent);
                    r = g = b = encode_channel((double)p[0]);
                    a = 255;
                    break;
                }
                case SC_PIXEL_KIND_RG11B10: {
                    unsigned word;
                    memcpy(&word, in + (size_t)x * extent, sizeof word);
                    r = encode_channel(decode_uf11(word & 0x7ffu, 6));
                    g = encode_channel(decode_uf11((word >> 11) & 0x7ffu, 6));
                    b = encode_channel(decode_uf11((word >> 22) & 0x3ffu, 5));
                    a = 255;
                    break;
                }
                case SC_PIXEL_KIND_RGB10A2: {
                    unsigned word;
                    memcpy(&word, in + (size_t)x * extent, sizeof word);
                    r = encode_channel((double)(word & 0x3ffu) / 1023.0);
                    g = encode_channel((double)((word >> 10) & 0x3ffu) / 1023.0);
                    b = encode_channel((double)((word >> 20) & 0x3ffu) / 1023.0);
                    a = sc_clamp_byte((double)((word >> 30) & 0x3u) / 3.0 * 255.0);
                    break;
                }
                default: {
                    fail(why, why_cap, "unsupported pixel kind");
                    return 0;
                }
            }
            out[(size_t)x * 4 + 0] = r;
            out[(size_t)x * 4 + 1] = g;
            out[(size_t)x * 4 + 2] = b;
            out[(size_t)x * 4 + 3] = a;
        }
    }
    return 1;
}
