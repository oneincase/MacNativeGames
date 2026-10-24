/* Optional libzstd bridge for the scenecap replay reader.
 *
 * The container reader in tools/scenecap/format/nvs_format.c only decodes raw/RLE zstd blocks. Packages written by
 * the Python writer (CPython's compression.zstd) contain real compressed blocks, so the replay reader installs the
 * hook below with nvs_read_set_decompressor().  The hook prefers a libzstd loaded at run time (no link-time
 * dependency, no headers required) and falls back to the built-in raw/RLE decoder.  Everything that cannot be
 * decoded is reported as an explicit failure; nothing is silently substituted.
 */
#ifndef SCENECAP_REPLAY_ZSTD_H
#define SCENECAP_REPLAY_ZSTD_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Non-zero when this build can decode real zstd frames (libzstd found at run time or built in). */
int replay_zstd_available(void);

/* Why real zstd frames cannot be decoded, or NULL when they can. */
const char *replay_zstd_unavailable_reason(void);

/* Decodes exactly one zstd frame into dst; returns the produced byte count or 0 with *why set. */
size_t replay_zstd_decompress(const void *src, size_t src_len, void *dst, size_t dst_cap, const char **why);

/* nvs_decompress_fn-compatible hook: returns dst_cap on success, 0 on failure. */
size_t replay_zstd_hook(void *user, const void *src, size_t src_len, void *dst, size_t dst_cap);

/* Reason for the most recent hook failure (valid until the next call). */
const char *replay_zstd_last_error(void);

#ifdef __cplusplus
}
#endif

#endif /* SCENECAP_REPLAY_ZSTD_H */
