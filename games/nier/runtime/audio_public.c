/* nn::audio's AudioOut API (audout) for NieR:Automata. The game only imports the public C++ wrappers
   (OpenDefaultAudioOut, AppendAudioOutBuffer, StartAudioOut, GetReleasedAudioOutBuffer, …), never audren's renderer, and
   on this build the SDK's own wrapper does not get as far as connecting to audout:u: it opens the audren device service
   three times, reads the active channel count / device name, takes the device notification event and then waits on it
   instead of opening the output (see docs/status-1.0.3.md). Rather than emulate that stall, this file answers the public
   API directly: each AudioOut becomes a Core Audio AudioQueue, appended buffers are mixed to what the queue wants and
   the tags come back when the device has played them, so the game is paced by the real output exactly as with the
   audout:u IPC service (runtime/audio_out.c, which stays for ports whose SDK does reach it).
   Layouts are the SDK's own: AudioOut = {u32 sample_rate, u32 channel_count, u32 sample_format, u32 state, x0x10 host},
   AudioOutBuffer = 0x28 bytes {next(tag), sample_buffer, capacity, data_size, data_offset}. */
#include "runtime.h"
#include <AudioToolbox/AudioToolbox.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

enum { STATE_STARTED = 0, STATE_STOPPED = 1, FORMAT_PCM16 = 2, QUEUE_MAX = 32 };

typedef struct { uint64_t next, sample_buffer, capacity, data_size, data_offset; } AudioOutBuffer;
_Static_assert(sizeof(AudioOutBuffer) == 0x28, "AudioOutBuffer");

typedef struct {
    uint32_t rate, channels, format, state;   /* the fields the SDK's getters read */
    void *host;                               /* HostOut */
} AudioOut;

typedef struct { uint64_t tag, bytes; } Queued;
typedef struct {
    AudioOut *owner;
    pthread_mutex_t lock;
    AudioQueueRef aq;
    Queued queue[QUEUE_MAX]; unsigned nqueue;
    uint64_t released[QUEUE_MAX]; unsigned nreleased;
    uint64_t played_samples;
    int silent;
    pthread_t thread; int running;
} HostOut;

static uint64_t now_ns(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return (uint64_t)t.tv_sec * 1000000000ull + (uint64_t)t.tv_nsec; }
static void trace(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

static void trace(const char *fmt, ...) {
    if (!getenv("NIER_TRACE_AUDIO")) return;
    va_list ap; va_start(ap, fmt); fputs("[audio] ", stderr); vfprintf(stderr, fmt, ap); fputc('\n', stderr); va_end(ap);
}

static uint64_t release_head(HostOut *h) {
    if (!h->nqueue) return 0;
    const Queued head = h->queue[0];
    memmove(h->queue, h->queue + 1, (--h->nqueue) * sizeof *h->queue);
    if (h->nreleased < QUEUE_MAX) h->released[h->nreleased++] = head.tag;
    h->played_samples += head.bytes / (2ull * h->owner->channels);
    return head.tag;
}
static void played(void *user, AudioQueueRef q, AudioQueueBufferRef b) {   /* Core Audio thread */
    HostOut *h = user;
    pthread_mutex_lock(&h->lock); uint64_t tg = release_head(h); uint64_t n = h->played_samples; pthread_mutex_unlock(&h->lock);
    static unsigned rel; if (getenv("NIER_TRACE_AUDIO") && rel < 4 && ++rel) fprintf(stderr, "[audio] callback released tag %#llx host %p\n", (unsigned long long)tg, (void *)h);
    static unsigned shots; if (getenv("NIER_TRACE_AUDIO") && (++shots <= 4)) {
        UInt32 run = 0, sz = sizeof run; AudioQueueGetProperty(q, kAudioQueueProperty_IsRunning, &run, &sz);
        fprintf(stderr, "[audio] played callback %u q=%p running=%u samples %llu\n", shots, (void *)q, run, (unsigned long long)n); }
    AudioQueueFreeBuffer(q, b);
}
static void *silent_playback(void *arg) {
    HostOut *h = arg; uint64_t due = 0;
    for (;;) {
        pthread_mutex_lock(&h->lock);
        if (!h->running) { pthread_mutex_unlock(&h->lock); return NULL; }
        uint64_t now = now_ns();
        if (h->owner->state == STATE_STARTED && h->nqueue) {
            if (!due) due = now + h->queue[0].bytes / (2ull * h->owner->channels) * 1000000000ull / h->owner->rate;
            if (now >= due) { release_head(h); due = 0; pthread_mutex_unlock(&h->lock); continue; }
        } else due = 0;
        pthread_mutex_unlock(&h->lock);
        struct timespec ts = {0, 1000000}; nanosleep(&ts, NULL);
    }
}
static void to_stereo(const int16_t *in, int16_t *out, uint64_t frames, uint32_t channels) {
    for (uint64_t i = 0; i < frames; i++) {
        const int16_t *s = in + i * channels;
        if (channels == 2) { out[2 * i] = s[0]; out[2 * i + 1] = s[1]; continue; }
        if (channels == 1) { out[2 * i] = out[2 * i + 1] = s[0]; continue; }
        float c = s[2] * 0.7071f, l = s[0] + c + s[4] * 0.7071f, r = s[1] + c + s[5] * 0.7071f;
        l *= 0.5f; r *= 0.5f;
        out[2 * i] = (int16_t)(l > 32767 ? 32767 : l < -32768 ? -32768 : l); out[2 * i + 1] = (int16_t)(r > 32767 ? 32767 : r < -32768 ? -32768 : r);
    }
}

/* ---------- the public API ---------- */
uint64_t nier_audio_open_default(void *out_raw, const void *param_raw) {
    AudioOut *o = out_raw;
    uint32_t rate = 48000, channels = 2, p0 = 0, p1 = 0;
    /* SDK 12.3.9 InitializeAudioOutParameter writes {u32 rate@0; u16 channelCount@4; u16 0xCAFE@6}; the game stores the
       channel count with a halfword at param+4, so reading p[1] as u32 yields 0xCAFE0000 and silently falls back to 2. */
    if (param_raw) { const unsigned char *b = param_raw; uint32_t w0; uint16_t ch; memcpy(&w0, b, 4); memcpy(&ch, b + 4, 2);
        p0 = w0; p1 = ch; if (w0 >= 8000 && w0 <= 192000) rate = w0; if (ch == 1 || ch == 2 || ch == 6) channels = ch; }
    fprintf(stderr, "[audio] OpenDefaultAudioOut parameter u32(+0) %u u16(+4) %u u16(+6) %u -> using %u Hz x%u\n",
            (unsigned)p0, (unsigned)p1, (unsigned)(param_raw ? ((const uint16_t *)param_raw)[3] : 0), rate, channels);
    trace("OpenDefaultAudioOut parameter words %#x %#x -> using %u Hz x%u", p0, p1, rate, channels);
    HostOut *h = calloc(1, sizeof *h);
    if (!h) return 0x815;
    pthread_mutex_init(&h->lock, NULL);
    h->owner = o; h->running = 1;
    o->rate = rate; o->channels = channels; o->format = FORMAT_PCM16; o->state = STATE_STOPPED; o->host = h;
    const char *e = getenv("BY3_AUDIO");
    h->silent = e && !strcmp(e, "0");
    if (!h->silent) {
        AudioStreamBasicDescription f = {.mSampleRate = rate, .mFormatID = kAudioFormatLinearPCM,
            .mFormatFlags = kLinearPCMFormatFlagIsSignedInteger | kLinearPCMFormatFlagIsPacked, .mBytesPerPacket = 4, .mFramesPerPacket = 1,
            .mBytesPerFrame = 4, .mChannelsPerFrame = 2, .mBitsPerChannel = 16};
        OSStatus nq = AudioQueueNewOutput(&f, played, h, NULL, kCFRunLoopCommonModes, 0, &h->aq);
        if (nq) { fprintf(stderr, "[audio] no Core Audio output (new %d); continuing silent\n", (int)nq); h->silent = 1; }
        else if (getenv("NIER_TRACE_AUDIO")) fprintf(stderr, "[audio] queue created ok\n");
    }
    if (h->silent) pthread_create(&h->thread, NULL, silent_playback, h);
    trace("AudioOut open %u Hz x%u PCM16 -> %s", rate, channels, h->silent ? "silent sink" : "Core Audio (stereo)");
    return 0;
}
uint64_t nier_audio_start(void *out_raw) {
    AudioOut *o = out_raw; if (!o || !o->host) return 0;
    HostOut *h = o->host; o->state = STATE_STARTED;
    if (h->aq) {
        OSStatus rc = AudioQueueStart(h->aq, NULL);
        UInt32 running = 0, sz = sizeof running; AudioQueueGetProperty(h->aq, kAudioQueueProperty_IsRunning, &running, &sz);
        if (getenv("NIER_TRACE_AUDIO")) fprintf(stderr, "[audio] AudioQueueStart rc=%d running=%u\n", (int)rc, running);
    }
    trace("AudioOut start");
    return 0;
}
uint64_t nier_audio_stop(void *out_raw) {
    AudioOut *o = out_raw; if (!o || !o->host) return 0;
    HostOut *h = o->host; o->state = STATE_STOPPED;
    if (h->aq) AudioQueuePause(h->aq);
    trace("AudioOut stop");
    return 0;
}
uint64_t nier_audio_close(void *out_raw) {
    AudioOut *o = out_raw; if (!o || !o->host) return 0;
    HostOut *h = o->host; h->running = 0;
    if (h->aq) AudioQueueDispose(h->aq, true);
    if (h->silent) pthread_join(h->thread, NULL);
    pthread_mutex_destroy(&h->lock); free(h); o->host = NULL;
    trace("AudioOut close");
    return 0;
}
uint64_t nier_audio_append(void *out_raw, void *buf_raw) {
    AudioOut *o = out_raw; AudioOutBuffer *b = buf_raw;
    if (!o || !o->host || !b) return 0;
    HostOut *h = o->host;
    uint64_t bytes = b->data_size;
    pthread_mutex_lock(&h->lock);
    if (h->nqueue >= QUEUE_MAX) { pthread_mutex_unlock(&h->lock); return 0x8a5; }   /* queue full */
    /* The released tag the SDK hands back is the guest AudioOutBuffer object itself (the game matches it against the
       four buffers it keeps); the buffer's "next" field is the SDK's own linkage and is 0 in the game's buffers, so
       reporting that made every release look like "no buffer" and the engine re-created the output every second. */
    h->queue[h->nqueue++] = (Queued){(uint64_t)(uintptr_t)buf_raw, bytes};
    pthread_mutex_unlock(&h->lock);
    if (h->silent) return 0;
    uint32_t channels = o->channels;
    uint64_t frames = bytes / (2ull * channels), offset = b->data_offset;
    if (offset + bytes > b->capacity) { trace("AudioOut buffer out of range (offset %llu size %llu capacity %llu)", (unsigned long long)offset, (unsigned long long)bytes, (unsigned long long)b->capacity); return 0x8a5; }
    AudioQueueBufferRef qb;
    if (AudioQueueAllocateBuffer(h->aq, (UInt32)(frames ? frames * 4 : 4), &qb)) return 0x8a5;
    to_stereo((const int16_t *)(uintptr_t)(b->sample_buffer + offset), qb->mAudioData, frames, channels);
    qb->mAudioDataByteSize = (UInt32)(frames * 4);
    if (!frames) { memset(qb->mAudioData, 0, 4); qb->mAudioDataByteSize = 4; }
    OSStatus eq = AudioQueueEnqueueBuffer(h->aq, qb, 0, NULL);
    if (eq) { static unsigned fails; if (++fails <= 3 || fails % 200 == 0) fprintf(stderr, "[audio] enqueue failed rc=%d (n=%u)\n", (int)eq, fails); return 0x8a5; }
    static unsigned appends; if (++appends == 1 || appends % 500 == 0) trace("AudioOut appended %u buffers, %llu frames each", appends, (unsigned long long)frames);
    return 0;
}
uint64_t nier_audio_released(void *out_raw) {
    AudioOut *o = out_raw; if (!o || !o->host) return 0;
    HostOut *h = o->host; uint64_t tag = 0;
    pthread_mutex_lock(&h->lock);
    if (h->nreleased) { tag = h->released[0]; memmove(h->released, h->released + 1, (--h->nreleased) * sizeof *h->released); }
    pthread_mutex_unlock(&h->lock);
    static unsigned asks, hits; asks++; if (tag) hits++;
    if (getenv("NIER_TRACE_AUDIO") && hits <= 3 && tag) fprintf(stderr, "[audio] guest took tag %#llx host %p (ask %u)\n", (unsigned long long)tag, (void *)h, asks);
    if (getenv("NIER_TRACE_AUDIO") && (asks == 1 || asks % 2000 == 0))
        fprintf(stderr, "[audio] released: asks %u hits %u queue %u\n", asks, hits, h->nqueue);
    return tag;
}
uint64_t nier_audio_played_samples(void *out_raw) {
    AudioOut *o = out_raw; if (!o || !o->host) return 0;
    HostOut *h = o->host; pthread_mutex_lock(&h->lock); uint64_t v = h->played_samples; pthread_mutex_unlock(&h->lock); return v;
}
/* Small accessors: the SDK implementations are exactly these loads/stores, but they are guest code; answering them here
   keeps one owner of the struct while an override owns the device. */
uint64_t nier_audio_buffer_info(void *buf_raw, void *ptr, uint64_t capacity, uint64_t size) {
    AudioOutBuffer *b = buf_raw; b->sample_buffer = (uint64_t)(uintptr_t)ptr; b->capacity = capacity; b->data_size = size; return 0;
}
uint64_t nier_audio_buffer_pointer(const void *buf_raw) { return ((const AudioOutBuffer *)buf_raw)->sample_buffer; }
uint64_t nier_audio_buffer_size(const void *buf_raw) { return ((const AudioOutBuffer *)buf_raw)->data_size; }
uint64_t nier_audio_channel_count(const void *out_raw) { return ((const AudioOut *)out_raw)->channels; }
uint64_t nier_audio_sample_rate(const void *out_raw) { return ((const AudioOut *)out_raw)->rate; }
uint64_t nier_audio_sample_format(const void *out_raw) { return ((const AudioOut *)out_raw)->format; }
uint64_t nier_audio_state(const void *out_raw) { return ((const AudioOut *)out_raw)->state; }

void *runtime_audio_override(const char *name) {
    static const struct { const char *name; void *fn; } entries[] = {
        {"_ZN2nn5audio19OpenDefaultAudioOutEPNS0_8AudioOutERKNS0_17AudioOutParameterE", (void *)nier_audio_open_default},
        {"_ZN2nn5audio13StartAudioOutEPNS0_8AudioOutE", (void *)nier_audio_start},
        {"_ZN2nn5audio12StopAudioOutEPNS0_8AudioOutE", (void *)nier_audio_stop},
        {"_ZN2nn5audio13CloseAudioOutEPNS0_8AudioOutE", (void *)nier_audio_close},
        {"_ZN2nn5audio20AppendAudioOutBufferEPNS0_8AudioOutEPNS0_14AudioOutBufferE", (void *)nier_audio_append},
        {"_ZN2nn5audio25GetReleasedAudioOutBufferEPNS0_8AudioOutE", (void *)nier_audio_released},
        {"_ZN2nn5audio28GetAudioOutPlayedSampleCountEPKNS0_8AudioOutE", (void *)nier_audio_played_samples},
        {"_ZN2nn5audio21SetAudioOutBufferInfoEPNS0_14AudioOutBufferEPvmm", (void *)nier_audio_buffer_info},
        {"_ZN2nn5audio28GetAudioOutBufferDataPointerEPKNS0_14AudioOutBufferE", (void *)nier_audio_buffer_pointer},
        {"_ZN2nn5audio25GetAudioOutBufferDataSizeEPKNS0_14AudioOutBufferE", (void *)nier_audio_buffer_size},
        {"_ZN2nn5audio23GetAudioOutChannelCountEPKNS0_8AudioOutE", (void *)nier_audio_channel_count},
        {"_ZN2nn5audio21GetAudioOutSampleRateEPKNS0_8AudioOutE", (void *)nier_audio_sample_rate},
        {"_ZN2nn5audio23GetAudioOutSampleFormatEPKNS0_8AudioOutE", (void *)nier_audio_sample_format},
        {"_ZN2nn5audio16GetAudioOutStateEPKNS0_8AudioOutE", (void *)nier_audio_state},
    };
    for (size_t i = 0; i < sizeof entries / sizeof entries[0]; i++) if (!strcmp(name, entries[i].name)) return entries[i].fn;
    return NULL;
}
