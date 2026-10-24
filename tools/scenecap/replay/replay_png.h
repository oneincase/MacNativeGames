/* Minimal, deterministic RGBA8 PNG writer (zlib deflate, filter 0, one IDAT, no ancillary chunks).
 *
 * The replay output contract needs 8-bit RGBA, non-premultiplied, top-left origin pixels.  Everything that reads
 * those files (the scenecap diff tool, image viewers) accepts this shape, and the bytes only depend on the pixels.
 */
#ifndef SCENECAP_REPLAY_PNG_H
#define SCENECAP_REPLAY_PNG_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Writes width*height RGBA8 pixels to path atomically (temporary file + rename). */
int sc_png_write_rgba8(const char *path, const unsigned char *rgba, unsigned width, unsigned height, char *why,
                       size_t why_cap);

#ifdef __cplusplus
}
#endif

#endif /* SCENECAP_REPLAY_PNG_H */
