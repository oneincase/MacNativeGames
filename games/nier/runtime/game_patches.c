#include "game_patches.h"
#include <libkern/OSCacheControl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Bayonetta 3: the player's 1.2.0 exefs patches (60 fps, 900p, LOD, film grain), applied to main after relocation and
   before its text is sealed. scripts/prepare_patches.py turns each .pchtxt into patches/<slug>.tsv ("offset new orig",
   hex), already checked against the original image and the rewrite; here every site is checked again against the loaded
   bytes, and a mismatch stops the start (a wrong version or a rewritten word must never be patched).
   BY3_PATCHES=<slug>,<slug>,... picks them (the launcher sets it from the settings; unset or empty: none). */
static int hexbytes(const char *s, uint8_t *out, size_t cap, size_t *n) {
    size_t k = 0;
    for (; s[0] && s[1] && s[0] != '\t' && s[0] != '\n'; s += 2) {
        unsigned v; if (k == cap || sscanf(s, "%2x", &v) != 1) return 0;
        out[k++] = (uint8_t)v;
    }
    *n = k; return k > 0;
}
static void apply_file(Module *m, const char *slug) {
    char path[512]; snprintf(path, sizeof path, "patches/%s.tsv", slug);
    FILE *f = fopen(path, "r");
    if (!f) { fprintf(stderr, "[by3] patch %s: %s missing (scripts/prepare_patches.py)\n", slug, path); runtime_fail("game patch table missing"); }
    char line[4096]; unsigned applied = 0;
    while (fgets(line, sizeof line, f)) {
        if (line[0] == '#' || line[0] == '\n') continue;
        uint64_t off = strtoull(line, NULL, 0); char *a = strchr(line, '\t'), *b = a ? strchr(a + 1, '\t') : NULL;
        uint8_t now[1024], was[1024]; size_t nn, nw;
        if (!a || !b || !hexbytes(a + 1, now, sizeof now, &nn) || !hexbytes(b + 1, was, sizeof was, &nw) || nn != nw)
            runtime_fail("malformed game patch table");
        if (off + nn > m->text_end && off < m->ro_off) runtime_fail("game patch in the rewrite area");
        if (off + nn > m->file_size) runtime_fail("game patch beyond main");
        if (memcmp(m->base + off, was, nw)) { fprintf(stderr, "[by3] patch %s at main+%#llx: original bytes differ\n", slug, (unsigned long long)off); runtime_fail("game patch does not match this game version"); }
        memcpy(m->base + off, now, nn); sys_icache_invalidate(m->base + off, nn); applied++;
    }
    fclose(f);
    fprintf(stderr, "[by3] game patch %s: %u sites\n", slug, applied);
}
/* NIER audio probe: the engine's audio device constructor (main+0xd8c548) is only reachable through the device manager
   vtable slot main+0x12b4428+0xf8 = main+0x12b4520, and static analysis says that slot is never called (zero BL callers,
   no 8-byte data reference). Rewrite the slot, after relocation and before main is sealed, to a host stub that logs the call
   and forwards to the original: one boot then answers whether the slot is ever taken (and with what `this`). */
static void *g_audio_sink_orig;
uint64_t nier_audio_sink_stub(uint64_t a0, uint64_t a1, uint64_t a2, uint64_t a3) {
    fprintf(stderr, "[audiosink] main+0x12b4520 called this=%#llx a1=%#llx a2=%#llx a3=%#llx\n",
            (unsigned long long)a0, (unsigned long long)a1, (unsigned long long)a2, (unsigned long long)a3);
    fflush(stderr);
    if (!g_audio_sink_orig) return 0;
    return ((uint64_t (*)(uint64_t, uint64_t, uint64_t, uint64_t))g_audio_sink_orig)(a0, a1, a2, a3);
}
static void audio_sink_probe(Module *m) {
    if (!getenv("NIER_AUDIO_SINK_PROBE")) return;
    const uint64_t slot_off = 0x12b4520;
    void **slot = (void **)(m->base + slot_off);
    uintptr_t orig = (uintptr_t)*slot;
    if (orig < (uintptr_t)m->base || orig >= (uintptr_t)m->base + m->text_end) {
        fprintf(stderr, "[audiosink] slot main+%#llx holds %#llx (not text in main); not patched\n",
                (unsigned long long)slot_off, (unsigned long long)orig);
        return;
    }
    g_audio_sink_orig = (void *)orig;
    *slot = (void *)nier_audio_sink_stub;
    fprintf(stderr, "[audiosink] patched slot main+%#llx: main+%#llx -> host stub\n",
            (unsigned long long)slot_off, (unsigned long long)(orig - (uintptr_t)m->base));
    sys_icache_invalidate(slot, sizeof *slot);
}
void runtime_game_patches(Module *main_module) {
    audio_sink_probe(main_module);
    const char *list = getenv("BY3_PATCHES");
    if (!list || !*list) { fprintf(stderr, "[by3] game patches: none (BY3_PATCHES)\n"); return; }
    char buf[1024]; snprintf(buf, sizeof buf, "%s", list);
    for (char *save = NULL, *s = strtok_r(buf, ",", &save); s; s = strtok_r(NULL, ",", &save)) {
        for (const char *c = s; *c; c++) if (!((*c >= 'a' && *c <= 'z') || (*c >= '0' && *c <= '9') || *c == '-')) runtime_fail("BY3_PATCHES: bad patch name");
        apply_file(main_module, s);
    }
}

/* The TotK host hooks the shared graphics layer and the input keys call keep their names and answer "off". */
void runtime_frame_step_pin(double ms) { (void)ms; }
void runtime_frame_step_update(uint64_t now_ns) { (void)now_ns; }
int runtime_output_res(unsigned *w, unsigned *h) { (void)w; (void)h; return 0; }
uint64_t runtime_output_res_extra_bytes(void) { return 0; }
uint64_t runtime_output_size_current(void) { return (uint64_t)1080 << 32 | 1920; }
int runtime_output_res_toggle(void) { return 0; }
int runtime_output_mode_cycle(void) { return 0; }
int runtime_aspect_toggle(void) { return 0; }
float runtime_aspect_now(void) { return 0; }
