#include "game_patches.h"
#include <libkern/OSCacheControl.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
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

/* NIER audio-engine state probe (NIER_AUDIO_STATE_PROBE=1): read-only snapshot of the guest engine's audio-device
   object, once a second from the present thread.
   Beyond the sink slot the engine has its own device object (0x110 bytes, built by main+0xd8ab8c). The steady-state
   poll main+0xd8ad0c has exactly one static caller, main+0xcdc7e8 (the engine tick), and returns at once unless
   obj+0x10c is set; main+0xd8ab8c writes that byte only when two guest gates allow it (the byte at main+0x2bc78d0+0xa7
   and the word at main+0x2bc74f8+0x2c) and AcquireAudioDeviceNotificationForOutput returns 0. Reading those statics
   answers, without hooking guest code and without touching state: is the object built, did the engine ever reach
   "ready", and which gate says no. The slots are followed through their relocated values (R_AARCH64_RELATIVE), so
   they work in the loaded module whichever base it got. */
static uint64_t aud_r64(const uint8_t *b, uint64_t o) { uint64_t v; memcpy(&v, b + o, 8); return v; }
static uint32_t aud_r32(const uint8_t *b, uint64_t o) { uint32_t v; memcpy(&v, b + o, 4); return v; }
/* Follow the relocated pointer stored at main+got, add field, and check [off, off+size) stays inside main. */
static int aud_at(const uint8_t *b, uint64_t got, uint64_t field, uint64_t size, uint64_t *off) {
    uint64_t base = (uintptr_t)b, a; memcpy(&a, b + got, 8);
    if (a < base) return 0;
    a -= base;
    if (a >= 0x2d00000ull || field > 0x2d00000ull - a || size > 0x2d00000ull - a - field) return 0;
    *off = a + field; return 1;
}
/* The device object is not in the image: the engine allocates it through the guest allocator, which this host serves
   from the process heap, so the slot holds a plain host pointer. Copy through the kernel interface instead of
   dereferencing it: an unmapped or freed object then reports unreadable rather than faulting the probe. */
static int aud_peek(uint64_t addr, void *dst, size_t n) {
    mach_vm_size_t got = 0;
    if (!addr) return 0;
    return mach_vm_read_overwrite(mach_task_self(), addr, n, (mach_vm_address_t)dst, &got) == KERN_SUCCESS && got == n;
}
/* The guest resolves "sound/..." through its own Hw::DvdSystem table, not through our FS layer (the FS log never sees
   a sound path). main+0x982330 matches a path against trees with main+0x981788, where a node is {char *name; ?;
   child *dirs; child *files} and a child is {char *name; child *next}. The record tables hang off *(main+0x12c7890)
   (records of 0x70, payload at +8) and *(main+0x12c7888) (count then payload at +8). Dumping the roots and their
   first directory children answers whether "sound" is registered at all -- i.e. whether the gap is registration. */
static void aud_tree(uint64_t node, const char *tag, const void *unused) { (void)unused;
    uint64_t name = 0, dirs = 0, w[6] = {0}; char txt[64] = {0};
    if (!node) { fprintf(stderr, "[audstate] %s node=0\n", tag); return; }
    if (!aud_peek(node, w, sizeof w)) {
        fprintf(stderr, "[audstate] %s node=%#llx unreadable\n", tag, (unsigned long long)node); return; }
    name = w[0]; dirs = w[2];
    if (name) aud_peek(name, txt, sizeof txt - 1);
    fprintf(stderr, "[audstate] %s node=%#llx words=%#llx,%#llx,%#llx,%#llx,%#llx,%#llx name=\"%s\"\n", tag,
            (unsigned long long)node, (unsigned long long)w[0], (unsigned long long)w[1], (unsigned long long)w[2],
            (unsigned long long)w[3], (unsigned long long)w[4], (unsigned long long)w[5], txt);
    /* A node also carries an entry array at word4 with a count at word5 (the "rom:/" root showed ptr,0x40): print the
       head of that array, dereferencing the first two words of each 0x20-byte slot as candidate name strings. */
    if (unused && w[4] && w[5] && w[5] < 512) {
        uint64_t cnt = w[5] < 12 ? w[5] : 12;
        for (uint64_t i = 0; i < cnt; i++) {
            uint64_t sl[4] = {0}; char a[48] = {0}, b2[48] = {0};
            if (!aud_peek(w[4] + i * 0x20, sl, sizeof sl)) break;
            if (sl[0]) aud_peek(sl[0], a, sizeof a - 1);
            if (sl[1]) aud_peek(sl[1], b2, sizeof b2 - 1);
            fprintf(stderr, "[audstate] %s  ent[%llu] %#llx,%#llx,%#llx,%#llx  A=\"%s\" B=\"%s\"\n", tag,
                    (unsigned long long)i, (unsigned long long)sl[0], (unsigned long long)sl[1],
                    (unsigned long long)sl[2], (unsigned long long)sl[3], a, b2);
        }
    }
    for (uint64_t c = dirs, i = 0; c && i < 40; i++) {
        uint64_t cn = 0, nx = 0; char ct[64] = {0};
        if (!aud_peek(c, &cn, 8) || !aud_peek(c + 8, &nx, 8)) break;
        if (cn) aud_peek(cn, ct, sizeof ct - 1);
        fprintf(stderr, "[audstate] %s  dir[%llu] \"%s\"\n", tag, (unsigned long long)i, ct);
        c = nx;
    }
}
static void aud_tree_dump(const uint8_t *b) {
    uint64_t a_slot = 0, a_base = 0, a_cslot = 0, bp = 0; uint32_t a_n = 0, b_n = 0;
    memcpy(&a_slot, b + 0x12c7890, 8); memcpy(&a_cslot, b + 0x12c7898, 8);
    if (a_slot) aud_peek(a_slot, &a_base, 8);
    if (a_cslot) aud_peek(a_cslot, &a_n, 4);
    fprintf(stderr, "[audstate] dvdA base=%#llx n=%u\n", (unsigned long long)a_base, a_n);
    if (a_n) aud_tree(a_base + 8, "dvdA0", b);
    uint64_t cslot = 0, cbase = 0; uint32_t cn = 0;
    memcpy(&cslot, b + 0x12c78b0, 8);
    if (cslot) { aud_peek(cslot, &cn, 4); if (cn) aud_peek(cslot + 8, &cbase, 8); }
    fprintf(stderr, "[audstate] dvdC n=%u node=%#llx\n", cn, (unsigned long long)cbase);
    if (cn) aud_tree(cbase, "dvdC0", b);
    memcpy(&bp, b + 0x12c7888, 8);
    if (bp) {
        aud_peek(bp, &b_n, 4);
        fprintf(stderr, "[audstate] dvdB n=%u\n", b_n);
        aud_tree(bp + 8, "dvdB0", b);
    }
}

void nier_audio_state_tick(uint64_t frame) {
    static int on = -1, lines; static uint64_t last_obj, last_fp;
    if (on < 0) on = getenv("NIER_AUDIO_STATE_PROBE") != NULL;
    if (!on || frame % 60 || lines >= 64) return;
    const uint8_t *b = runtime_module_base("main"); if (!b) return;
    uint64_t off, obj = 0;
    if (aud_at(b, 0x12cc9d8, 0, 8, &off)) obj = aud_r64(b, off);
    uint8_t ob[0x110]; int vo = aud_peek(obj, ob, sizeof ob);
    uint32_t ch = vo ? aud_r32(ob, 0x108) : 0; unsigned ready = vo ? ob[0x10c] : 0;
    uint64_t vt = vo ? aud_r64(ob, 0) : 0;
    unsigned a7 = 0, acq = 0, flag = 0; uint32_t at2c = 0; uint64_t ev[4] = {0, 0, 0, 0};
    if (aud_at(b, 0x12ca938, 0xa7, 1, &off)) a7 = b[off];
    if (aud_at(b, 0x12cc7c8, 0x2c, 4, &off)) at2c = aud_r32(b, off);
    if (aud_at(b, 0x12cd3e8, 0, 1, &off)) acq = b[off];
    if (aud_at(b, 0x12cc948, 0, 1, &off)) flag = b[off];
    if (aud_at(b, 0x12cd3f0, 0, 32, &off)) for (int i = 0; i < 4; i++) ev[i] = aud_r64(b, off + 8 * i);
    /* Wwise sound-system boot. The function at main+0x8c9870 (only caller main+0x58f814) formats "<rom>/sound/Init.bnk"
       plus the three index paths sound/WwiseInfo.wai, sound/WwiseStreamInfo.wai and sound/WwiseVoiceInfo.wai into the
       scratch buffer whose address sits in the slot main+0x12c6768, then stores the result of each registration in the
       dword behind slots main+0x12c6778/+0x6780/+0x6790/+0x6798. Reading that buffer answers "did the guest's sound
       system ever start" without hooking guest code, and a non-zero registration result names the first failing file. */
    char wtext[65] = {0}; uint64_t wbuf = 0; uint32_t st[4] = {0, 0, 0, 0};
    memcpy(&wbuf, b + 0x12c6768, 8);               /* the slots hold plain pointers, not image offsets */
    if (wbuf) {
        aud_peek(wbuf, wtext, sizeof wtext - 1);
        for (size_t i = 0; i < sizeof wtext; i++) if (wtext[i] && (wtext[i] < 0x20 || wtext[i] > 0x7e)) wtext[i] = '.';
    }
    static const uint64_t stslot[4] = {0x12c6778, 0x12c6780, 0x12c6790, 0x12c6798};
    for (int i = 0; i < 4; i++) {
        uint64_t p = 0; memcpy(&p, b + stslot[i], 8);
        if (p) aud_peek(p, &st[i], 4);
    }
    /* The game's own SoundSystem::Env init (main+0x8c9b40 area) is the layer above Wwise: it reads the three .wai
       index files, keeps their handles in the slots main+0x12c66c8/+0x12c6788/+0x12c66d0 (a zero handle makes it log
       "[SoundSystem::Env] Wwise … 読み込みエラー"), then drives the engine object at main+0x12c66d8 with the GameSync
       names Volume_Master / SE_ListenerPreset / SE_ListenerPresetFadeTime and records each result in
       main+0x12c66e8/+0x12c67a8/+0x12c67b0. Reading those answers whether the info files loaded and whether the
       engine accepted the syncs. */
    uint64_t envh[3] = {0, 0, 0}, envy[3] = {0, 0, 0}, envobj = 0; uint32_t envst[3] = {0, 0, 0};
    static const uint64_t hslot[3] = {0x12c66c8, 0x12c6788, 0x12c66d0};
    for (int i = 0; i < 3; i++) {                  /* slot -> object (possibly on the host heap) -> handle */
        memcpy(&envy[i], b + hslot[i], 8);
        if (envy[i]) aud_peek(envy[i], &envh[i], 8);
    }
    memcpy(&envobj, b + 0x12c66d8, 8);
    static const uint64_t eslot[3] = {0x12c66e8, 0x12c67a8, 0x12c67b0};
    for (int i = 0; i < 3; i++) {
        uint64_t p = 0; memcpy(&p, b + eslot[i], 8);
        if (p) aud_peek(p, &envst[i], 4);
    }
    uint64_t fp = ((uint64_t)ch << 32) ^ ready ^ ((uint64_t)a7 << 8) ^ ((uint64_t)at2c << 12) ^ ((uint64_t)acq << 20)
                ^ ((uint64_t)flag << 24) ^ ev[0] ^ ev[1] ^ ev[2] ^ ev[3] ^ obj ^ ((uint64_t)vo << 48)
                ^ wbuf ^ st[0] ^ ((uint64_t)st[1] << 20) ^ ((uint64_t)st[2] << 40) ^ ((uint64_t)st[3] << 50)
                ^ envh[0] ^ envh[1] ^ envh[2] ^ envobj ^ envst[0] ^ ((uint64_t)envst[1] << 12)
                ^ envy[0] ^ envy[1] ^ envy[2]
                ^ ((uint64_t)envst[2] << 28);
    if (frame % 600 && fp == last_fp) return;      /* print on change, and once every 10 s to show liveness */
    last_fp = fp; lines++;
    fprintf(stderr, "[audstate] F=%llu obj=%#llx ok=%d vt=%#llx ch=%u ready=%u a7=%#x at2c=%#x acq=%u flag=%u "
            "ev=%#llx,%#llx,%#llx,%#llx\n",
            (unsigned long long)frame, (unsigned long long)obj, vo, (unsigned long long)vt, ch, ready, a7, at2c, acq,
            flag, (unsigned long long)ev[0], (unsigned long long)ev[1], (unsigned long long)ev[2],
            (unsigned long long)ev[3]);
    fprintf(stderr, "[audstate] wwise buf=%#llx text=\"%s\" st=%#x,%#x,%#x,%#x\n", (unsigned long long)wbuf, wtext,
            st[0], st[1], st[2], st[3]);
    if (vo && obj != last_obj) {                   /* first sight of an object (or a new one): full 0x110 bytes */
        last_obj = obj;
        for (int row = 0; row < 0x110; row += 0x20) {
            char line[256]; size_t o = 0;
            o += snprintf(line + o, sizeof line - o, "[audstate] obj+%#04x:", row);
            for (int i = 0; i < 0x20; i += 4) o += snprintf(line + o, sizeof line - o, " %08x", aud_r32(ob, row + i));
            fprintf(stderr, "%s\n", line);
        }
    }
    fprintf(stderr, "[audstate] env y=%#llx,%#llx,%#llx h=%#llx,%#llx,%#llx obj=%#llx st=%#x,%#x,%#x\n",
            (unsigned long long)envy[0], (unsigned long long)envy[1], (unsigned long long)envy[2],
            (unsigned long long)envh[0], (unsigned long long)envh[1], (unsigned long long)envh[2],
            (unsigned long long)envobj, envst[0], envst[1], envst[2]);
    if (frame == 60 || frame == 600) aud_tree_dump(b);
    fflush(stderr);
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
