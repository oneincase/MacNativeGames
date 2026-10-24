/* Pixel format helpers shared by the replay renderer and the PNG writer.
 *
 * The replayer always writes 8-bit RGBA PNGs whose colour channels are sRGB-encoded and whose alpha is linear
 * (never premultiplied).  Attachments whose Metal format already stores sRGB bytes are passed through; linear
 * formats are encoded once here.
 */
#ifndef SCENECAP_REPLAY_PIXELS_H
#define SCENECAP_REPLAY_PIXELS_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum sc_pixel_kind {
    SC_PIXEL_KIND_NONE = 0,
    SC_PIXEL_KIND_U8_SRGB,   /* 8-bit channels that already hold sRGB-encoded values (rgba8unorm_srgb, ...) */
    SC_PIXEL_KIND_U8_LINEAR, /* 8-bit unorm channels that hold linear values (rgba8unorm, ...) */
    SC_PIXEL_KIND_UNORM16,
    SC_PIXEL_KIND_FLOAT16,
    SC_PIXEL_KIND_FLOAT32,
    SC_PIXEL_KIND_RGB10A2,
    SC_PIXEL_KIND_RG11B10,   /* 11/11/10-bit packed floats (rg11b10float) */
    SC_PIXEL_KIND_R32FLOAT,  /* one float channel (r32float, depth32float): exported as grey */
    SC_PIXEL_KIND_R16FLOAT,  /* one half channel (r16float): exported as grey */
    SC_PIXEL_KIND_R8UNORM,   /* one 8-bit unorm channel (r8unorm): exported as grey */
    SC_PIXEL_KIND_RG8UNORM,  /* two 8-bit unorm channels (rg8unorm): red and green, blue zero */
} sc_pixel_kind;

/* Bytes per pixel of one kind; 0 for SC_PIXEL_KIND_NONE. */
size_t sc_pixel_kind_extent(sc_pixel_kind kind);

/* Writes width*height RGBA8 sRGB pixels (top-left origin, alpha linear) with a why[] message on failure.
 * src_row_bytes is the distance between source rows (>= width*extent). */
int sc_pixels_to_rgba8_srgb(sc_pixel_kind kind, int source_is_bgra, const void *src, size_t src_len, unsigned width,
                            unsigned height, size_t src_row_bytes, unsigned char *dst, char *why, size_t why_cap);

/* sRGB transfer function (linear -> encoded); values outside [0,1] are clamped by the callers. */
double sc_srgb_encode(double linear);

/* Encodes one linear unorm8 value; exposed for tests that pin the expected bytes. */
unsigned char sc_linear8_to_srgb8(unsigned char linear);

/* Per-channel statistics of an exported RGBA8 image (channels 0..3 are R,G,B,A and hold the PNG byte values, so the
 * colour channels are sRGB-encoded).  Written into index.json so a report can compare passes without decoding PNGs. */
typedef struct sc_rgba8_stats {
    unsigned count;          /* number of pixels that contributed (width * height) */
    unsigned char lo[4];     /* minimum byte per channel */
    unsigned char hi[4];     /* maximum byte per channel */
    double mean[4];          /* arithmetic mean per channel, in byte units */
    unsigned non_finite;     /* pixels whose source sample was not finite; filled by the caller when known */
} sc_rgba8_stats;

void sc_rgba8_stats_compute(const unsigned char *rgba, size_t pixel_count, sc_rgba8_stats *out);

/* alpha 1.0 is required for opaque output; the helper only clamps. */
unsigned char sc_clamp_byte(double value);

#ifdef __cplusplus
}
#endif

#endif /* SCENECAP_REPLAY_PIXELS_H */
