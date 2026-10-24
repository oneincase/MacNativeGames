/* See replay_zstd.h. */
#include "replay_zstd.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "nvs_format.h"

#ifndef SCENECAP_WITH_LIBZSTD
#define SCENECAP_WITH_LIBZSTD 1
#endif

#if SCENECAP_WITH_LIBZSTD
#include <dlfcn.h>
#endif

typedef size_t (*sc_zstd_decompress_fn)(void *dst, size_t dst_cap, const void *src, size_t src_len);
typedef unsigned (*sc_zstd_is_error_fn)(size_t code);
typedef const char *(*sc_zstd_error_name_fn)(size_t code);

static char g_last_error[512];
#if SCENECAP_WITH_LIBZSTD
static char g_unavailable[512];
static int g_probe_done;
static int g_ready;
static void *g_handle;
static sc_zstd_decompress_fn g_decompress;
static sc_zstd_is_error_fn g_is_error;
static sc_zstd_error_name_fn g_error_name;
#endif

static void set_last(const char *fmt, const char *a, const char *b)
{
    if (!fmt) {
        g_last_error[0] = '\0';
        return;
    }
    snprintf(g_last_error, sizeof g_last_error, fmt, a ? a : "", b ? b : "");
}

#if SCENECAP_WITH_LIBZSTD
/* 依次尝试常见安装位置；也可用 SCENECAP_LIBZSTD 指定完整路径。 */
static const char *const kCandidates[] = {
    "/opt/homebrew/lib/libzstd.dylib",
    "/opt/homebrew/opt/zstd/lib/libzstd.dylib",
    "/usr/local/lib/libzstd.dylib",
    "/usr/local/opt/zstd/lib/libzstd.dylib",
    "libzstd.1.dylib",
    "libzstd.dylib",
};

static void probe(void)
{
    const char *enumerated;
    size_t i;
    if (g_probe_done) return;
    g_probe_done = 1;
    enumerated = getenv("SCENECAP_LIBZSTD");
    if (enumerated && enumerated[0]) {
        g_handle = dlopen(enumerated, RTLD_NOW | RTLD_LOCAL);
        if (!g_handle) {
            snprintf(g_unavailable, sizeof g_unavailable, "cannot load SCENECAP_LIBZSTD=%s: %s", enumerated, dlerror());
            return;
        }
    }
    for (i = 0; !g_handle && i < sizeof kCandidates / sizeof kCandidates[0]; i++) {
        g_handle = dlopen(kCandidates[i], RTLD_NOW | RTLD_LOCAL);
    }
    if (!g_handle) {
        snprintf(g_unavailable, sizeof g_unavailable,
                 "no libzstd found (set SCENECAP_LIBZSTD=<path to libzstd.dylib> or install zstd)");
        return;
    }
    *(void **)(&g_decompress) = dlsym(g_handle, "ZSTD_decompress");
    *(void **)(&g_is_error) = dlsym(g_handle, "ZSTD_isError");
    *(void **)(&g_error_name) = dlsym(g_handle, "ZSTD_getErrorName");
    if (!g_decompress || !g_is_error || !g_error_name) {
        snprintf(g_unavailable, sizeof g_unavailable, "libzstd is missing ZSTD_decompress/ZSTD_isError/ZSTD_getErrorName");
        dlclose(g_handle);
        g_handle = NULL;
        return;
    }
    g_ready = 1;
}
#endif

int replay_zstd_available(void)
{
#if SCENECAP_WITH_LIBZSTD
    probe();
    return g_ready;
#else
    return 0;
#endif
}

const char *replay_zstd_unavailable_reason(void)
{
#if SCENECAP_WITH_LIBZSTD
    probe();
    if (g_ready) return NULL;
    return g_unavailable[0] ? g_unavailable : "libzstd is unavailable";
#else
    return "built with WITH_LIBZSTD=0 (real zstd frames cannot be decoded)";
#endif
}

size_t replay_zstd_decompress(const void *src, size_t src_len, void *dst, size_t dst_cap, const char **why)
{
    const char *reason = NULL;
    size_t produced = 0;
#if SCENECAP_WITH_LIBZSTD
    probe();
    if (g_ready) {
        size_t code = g_decompress(dst, dst_cap, src, src_len);
        if (g_is_error(code)) {
            reason = g_error_name(code);
        } else {
            produced = code;
        }
    } else {
        reason = g_unavailable[0] ? g_unavailable : "libzstd is unavailable";
    }
#else
    (void)src;
    (void)src_len;
    (void)dst;
    (void)dst_cap;
    reason = "built with WITH_LIBZSTD=0 (real zstd frames cannot be decoded)";
#endif
    if (produced != dst_cap) {
        if (why) *why = reason ? reason : "zstd decoder returned a short result";
        return 0;
    }
    if (why) *why = NULL;
    return produced;
}

size_t replay_zstd_hook(void *user, const void *src, size_t src_len, void *dst, size_t dst_cap)
{
    const char *lib_reason = NULL;
    nvs_error err;
    size_t produced;
    const char *builtin_reason;
    (void)user;
    /* 先试 libzstd（可解任意帧），再退回容器内置的 raw/RLE 解码器。 */
    produced = replay_zstd_decompress(src, src_len, dst, dst_cap, &lib_reason);
    if (produced == dst_cap) {
        set_last(NULL, NULL, NULL);
        return produced;
    }
    memset(&err, 0, sizeof err);
    produced = nvs_zstd_decompress_ex(src, src_len, dst, dst_cap, &err);
    if (produced == dst_cap) {
        set_last(NULL, NULL, NULL);
        return produced;
    }
    builtin_reason = err.message[0] ? err.message : "built-in decoder rejected the frame";
    snprintf(g_last_error, sizeof g_last_error, "libzstd: %s; built-in: %s", lib_reason ? lib_reason : "unavailable",
             builtin_reason);
    return 0;
}

const char *replay_zstd_last_error(void)
{
    return g_last_error[0] ? g_last_error : NULL;
}
