/* CPU only, against the real runtime/threads.c: service-layer SystemEvent bindings and which waiters a signal wakes.
   1. Bindings end with their write handle (after nxnative 49ee623). 2,000 rounds: a SystemEvent is bound at one fixed
      address (half the rounds by hle_bind_system_event, half by a signal that binds it on the spot), signalled, destroyed
      (both handles closed, as nn::os::DestroySystemEvent does), and a decoy event is created at once and takes the freed
      handle numbers; then the SystemEvent is created again at the same address and signalled. Expected: every bind runs
      the SDK's CreateSystemEvent (4,000 creates), the new event is signalled, the decoy never is. A host thread (not a
      guest thread, like the display link's vsync callback) signals the same address all the time: it must never reach the
      decoy, and must not stop the process while the event is unbound. Before the fix the second bind returned early, and
      the signal went to the closed handle number, which the decoy now held (Tears of the Kingdom's handle numbering), or
      stopped the process (Breath of the Wild's, a stale handle).
   2. A signal wakes only the waiters of the signalled object (after nxnative df7badd; Breath of the Wild since 58ac238).
      18 guest threads wait on events of their own while event A, which nobody waits on, is signalled 5,000 times. Expected:
      fewer than 1,000 context switches in the process during the signals (measured about 100; before, when each signal
      woke all 18 waiters, 29,570 and 79 ms instead of 1.3 ms). Then each waiter is released by its own event, one
      waiting on 2 handles by its second, one on 6 (more than the 4 a waiter keeps, so it is woken by every signal) by its
      sixth, and each must return 0 with the right index.
   3. SignalToAddress with SignalAndModifyByWaitingCountIfEqual writes the value mesosphère does (after nxnative d47f899;
      KAddressArbiter, Ryujinx and yuzu agree): waking all (count <= 0) with waiters value - 2, with none value + 1; waking
      count with waiters: value - 1 when all of them fit, value unchanged when some stay. Guest threads wait on the address
      (WaitIfEqual); every one woken must return 0. Before the fix waking all with waiters wrote value - 1.
   Build and run from games/totk: make probe-system-event (GAME_BOTW=1 for Breath of the Wild's handle encoding and virtual
   cores; remove build/system-event-probe when switching). Run with no argument for both parts,
   `decoy` for one destroy / decoy / re-create round, `wake` for part 2 alone, `arbiter` for part 3 alone. Negative control: threads.c from before
   this change (copied outside the project, built with -Iruntime) stops at the first signal after a destroy (a closed
   write handle), `decoy` reports the decoy signalled (Tears of the Kingdom; Breath of the Wild stops at the stale
   handle), and `wake` reports about 30,000 context switches for Tears of the Kingdom (Breath of the Wild already
   filtered). */
#include "../../runtime/runtime.h"
#include <mach/mach.h>
#include <mach/mach_time.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

_Noreturn void runtime_fail(const char *message) { fprintf(stderr, "FAIL %s\n", message); exit(1); }
void runtime_thread_init(void) {}
uint64_t runtime_now_ns(void) { mach_timebase_info_data_t t; mach_timebase_info(&t); return (uint64_t)((__uint128_t)mach_absolute_time() * t.numer / t.denom); }
#ifdef GAME_BOTW
uint64_t runtime_hle_tsd_off;                                   /* hle_stack.c: 0 = no host-stack slot, the preemption signal does nothing */
int runtime_guest_code_pc(uint64_t pc) { (void)pc; return 0; }  /* loader.c */
#endif
void hle_bind_system_event(void *event, int autoclear);
void hle_signal_system_event(void *event);
static void need(int c, const char *m) { if (!c) runtime_fail(m); }
static void kernel(unsigned id, uint64_t *r) { need(svc_thread((int)id, r) == 1, "service dispatch"); }

/* ---------- the SDK side of a SystemEvent, as hle_bind_system_event calls it ---------- */
typedef struct { uint32_t rh, wh; int live; } FakeSystemEvent;
static atomic_uint creates;
static uint32_t fake_create(void *p, int autoclear, int interprocess) {
    (void)autoclear; need(interprocess == 1, "inter-process SystemEvent");
    FakeSystemEvent *e = p; need(!e->live, "CreateSystemEvent on a live SystemEvent");
    uint64_t r[31] = {0}; kernel(0x45, r); need(!r[0], "CreateEvent");
    e->wh = (uint32_t)r[1]; e->rh = (uint32_t)r[2]; e->live = 1; atomic_fetch_add(&creates, 1); return 0;
}
static uint32_t fake_writable(const void *p) { return ((const FakeSystemEvent *)p)->wh; }
void *runtime_symbol(const char *name) {
    if (!strcmp(name, "_ZN2nn2os17CreateSystemEventEPNS0_15SystemEventTypeENS0_14EventClearModeEb")) return (void *)fake_create;
    if (!strcmp(name, "_ZN2nn2os30GetWritableHandleOfSystemEventEPKNS0_15SystemEventTypeE")) return (void *)fake_writable;
    runtime_fail("unexpected guest symbol");
}
static void close_handle(uint32_t h) { uint64_t r[31] = {h}; kernel(0x16, r); need(!r[0], "CloseHandle"); }
static void fake_destroy(FakeSystemEvent *e) { close_handle(e->rh); close_handle(e->wh); e->live = 0; }   /* nn::os::DestroySystemEvent */
static int is_signaled(uint32_t rh) {   /* WaitSynchronization with timeout 0 */
    uint64_t r[31] = {0}; r[1] = (uintptr_t)&rh; r[2] = 1; r[3] = 0; kernel(0x18, r);
    need(r[0] == 0 || r[0] == 0xea01, "poll result"); return r[0] == 0;
}
static void clear_event(uint32_t h) { uint64_t r[31] = {h}; kernel(0x12, r); need(!r[0], "ClearEvent"); }

static FakeSystemEvent g_ev;
static atomic_int g_host_run;
static atomic_ulong g_host_signals;
static void *host_signaller(void *unused) {   /* a host thread, as the display link's vsync callback */
    (void)unused;
    while (atomic_load(&g_host_run)) { hle_signal_system_event(&g_ev); atomic_fetch_add(&g_host_signals, 1); }
    return NULL;
}

/* ---------- part 2: waiters ---------- */
enum { NWAIT = 18 };
typedef struct { uint32_t hs[8]; int n; atomic_int entered; uint64_t res, idx; } Waiter;
static void waiter_main(uint64_t arg) {
    Waiter *w = (Waiter *)(uintptr_t)arg; uint64_t r[31] = {0};
    atomic_store(&w->entered, 1);
    r[1] = (uintptr_t)w->hs; r[2] = (uint64_t)w->n; r[3] = (uint64_t)-1; kernel(0x18, r);
    w->res = r[0]; w->idx = r[1];
    kernel(0x0a, r); runtime_fail("ExitThread returned");
}
static uint64_t task_csw(void) {
    task_events_info_data_t ev; mach_msg_type_number_t c = TASK_EVENTS_INFO_COUNT;
    need(task_info(mach_task_self(), TASK_EVENTS_INFO, (task_info_t)&ev, &c) == KERN_SUCCESS, "task events");
    return (uint64_t)ev.csw;
}
static void new_event(uint32_t *w, uint32_t *r) { uint64_t x[31] = {0}; kernel(0x45, x); need(!x[0], "CreateEvent"); *w = (uint32_t)x[1]; *r = (uint32_t)x[2]; }

/* `probe decoy`: only the first destroy / decoy / re-create round, without the host thread (the negative control's
   part 1 failure: the decoy signalled); `probe wake`: only part 2; no argument: everything. */
static int decoy_once(void) {
    hle_bind_system_event(&g_ev, 0); fake_destroy(&g_ev);
    uint32_t dw, dr; new_event(&dw, &dr);
    hle_bind_system_event(&g_ev, 0); hle_signal_system_event(&g_ev);
    need(!is_signaled(dr), "the decoy that took the old handle numbers is never signalled");
    need(atomic_load(&creates) == 2, "the SystemEvent created again runs CreateSystemEvent");
    printf("{\"status\":\"pass\",\"part\":\"decoy\"}\n"); return 0;
}
static int wake_part(void);
static int arbiter_part(void);
int main(int argc, char **argv) {
    svc_thread_main_init();
    if (argc > 1 && !strcmp(argv[1], "decoy")) return decoy_once();
    if (argc > 1 && !strcmp(argv[1], "wake")) return wake_part();
    if (argc > 1 && !strcmp(argv[1], "arbiter")) return arbiter_part();
    /* part 1 */
    enum { ROUNDS = 2000 };
    unsigned reused = 0;
    hle_bind_system_event(&g_ev, 0); need(atomic_load(&creates) == 1, "first bind creates");
    fake_destroy(&g_ev);
    pthread_t host; atomic_store(&g_host_run, 1);
    hle_signal_system_event(&g_ev);   /* the guest re-binds an unbound event on the spot */
    need(atomic_load(&creates) == 2 && is_signaled(g_ev.rh), "signal after destroy binds a new event");
    fake_destroy(&g_ev);
    { /* a host thread signalling a destroyed SystemEvent: nothing to signal, and the process goes on */
      pthread_t one; need(!pthread_create(&one, NULL, host_signaller, NULL), "host thread");
      while (atomic_load(&g_host_signals) < 100) usleep(100);
      atomic_store(&g_host_run, 0); pthread_join(one, NULL); }
    atomic_store(&g_host_run, 1); need(!pthread_create(&host, NULL, host_signaller, NULL), "host thread");
    unsigned base = atomic_load(&creates);
    for (unsigned i = 0; i < ROUNDS; i++) {
        if (i & 1) hle_bind_system_event(&g_ev, 0); else hle_signal_system_event(&g_ev);
        need(g_ev.live, "bound SystemEvent is live");
        hle_signal_system_event(&g_ev); need(is_signaled(g_ev.rh), "SystemEvent signalled");
        clear_event(g_ev.rh);
        uint32_t ow = g_ev.wh, or_ = g_ev.rh;
        fake_destroy(&g_ev);
        uint32_t dw, dr; new_event(&dw, &dr);   /* the decoy takes the freed handle numbers (or slots) */
        if ((dw & 0x7fff) == (ow & 0x7fff) || (dr & 0x7fff) == (or_ & 0x7fff) || (dw & 0x7fff) == (or_ & 0x7fff) || (dr & 0x7fff) == (ow & 0x7fff)) reused++;
        hle_bind_system_event(&g_ev, 0);        /* the SystemEvent created again at the same address */
        need(g_ev.live && g_ev.wh != dw, "SystemEvent created again");
        hle_signal_system_event(&g_ev);
        need(is_signaled(g_ev.rh), "the new SystemEvent is signalled");
        need(!is_signaled(dr), "the decoy that took the old handle numbers is never signalled");
        close_handle(dr); close_handle(dw); fake_destroy(&g_ev);
    }
    atomic_store(&g_host_run, 0); pthread_join(host, NULL);
    unsigned rounds_creates = atomic_load(&creates) - base;
    need(rounds_creates == 2 * ROUNDS, "every bind after a destroy runs CreateSystemEvent");
    need(reused == ROUNDS, "the decoy reused the freed slots every round");
    need(atomic_load(&g_host_signals) > 0, "host thread signalled");
    printf("{\"part\":\"bind\",\"rounds\":%u,\"creates\":%u,\"decoy_reused_slots\":%u,\"host_signals\":%lu}\n",
           ROUNDS, rounds_creates, reused, (unsigned long)atomic_load(&g_host_signals));
    wake_part();
    return arbiter_part();
}
static int wake_part(void) {
    /* part 2 */
    Waiter w[NWAIT]; memset(w, 0, sizeof w);
    uint32_t aw, ar; new_event(&aw, &ar);
    uint32_t ew[NWAIT][6], er[NWAIT][6];
    for (int k = 0; k < NWAIT; k++) {
        w[k].n = k == 16 ? 2 : k == 17 ? 6 : 1;
        for (int j = 0; j < w[k].n; j++) { new_event(&ew[k][j], &er[k][j]); w[k].hs[j] = er[k][j]; }
    }
    uint32_t th[NWAIT];
    for (int k = 0; k < NWAIT; k++) {
        uint8_t *stack = mmap(NULL, 1 << 20, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0); need(stack != MAP_FAILED, "guest stack");
        uint64_t r[31] = {0}; r[1] = (uintptr_t)waiter_main; r[2] = (uintptr_t)&w[k]; r[3] = (uintptr_t)stack + (1 << 20); r[4] = 44; r[5] = -1;
#ifdef GAME_BOTW
        r[5] = (uint64_t)(1 + k % 2);   /* the main thread keeps core 0 (it waits here with usleep, not an SVC) */
#endif
        kernel(8, r); need(!r[0], "CreateThread"); th[k] = (uint32_t)r[1];
        r[0] = th[k]; kernel(9, r); need(!r[0], "StartThread");
    }
    for (int k = 0; k < NWAIT; k++) while (!atomic_load(&w[k].entered)) usleep(1000);
    usleep(100000);   /* all in WaitSynchronization */
    enum { SIGNALS = 5000 };
    uint64_t c0 = task_csw(), t0 = runtime_now_ns();
    for (int i = 0; i < SIGNALS; i++) { uint64_t r[31] = {aw}; kernel(0x11, r); need(!r[0], "SignalEvent"); r[0] = aw; kernel(0x12, r); }
    uint64_t c1 = task_csw(), t1 = runtime_now_ns();
    for (int k = 0; k < NWAIT; k++) { uint64_t r[31] = {ew[k][w[k].n - 1]}; kernel(0x11, r); need(!r[0], "release waiter"); }
    for (int k = 0; k < NWAIT; k++) {
        uint64_t r[31] = {0}; r[1] = (uintptr_t)&th[k]; r[2] = 1; r[3] = 2000000000; kernel(0x18, r); need(!r[0], "waiter exited");
        need(w[k].res == 0 && w[k].idx == (uint64_t)(w[k].n - 1), "waiter woke on its own event with the right index");
    }
    uint64_t csw = c1 - c0;
    printf("{\"status\":\"%s\",\"part\":\"wake\",\"signals\":%d,\"waiters\":%d,\"context_switches\":%llu,\"signal_ms\":%.1f}\n",
           csw < 1000 ? "pass" : "fail", SIGNALS, NWAIT, (unsigned long long)csw, (t1 - t0) / 1e6);
    if (csw >= 1000) runtime_fail("signals to an object nobody waits on woke waiters");
    return 0;
}

/* ---------- part 3: SignalToAddress, SignalAndModifyByWaitingCountIfEqual ---------- */
typedef struct { _Atomic int32_t *addr; int32_t value; atomic_int entered; atomic_int done; uint64_t res; } AddrWaiter;
static void addr_waiter_main(uint64_t arg) {
    AddrWaiter *w = (AddrWaiter *)(uintptr_t)arg; uint64_t r[31] = {0};
    atomic_store(&w->entered, 1);
    r[0] = (uintptr_t)w->addr; r[1] = 2; r[2] = (uint64_t)(uint32_t)w->value; r[3] = (uint64_t)-1; kernel(0x34, r);   /* WaitIfEqual, no timeout */
    w->res = r[0]; atomic_store(&w->done, 1);
    kernel(0x0a, r); runtime_fail("ExitThread returned");
}
/* nwait guest threads wait on a fresh address holding 100; SignalToAddress(type 2, 100, count) must write want and wake
   min(count, nwait) of them (all for count <= 0); the rest are released by a plain signal afterwards. */
static void arbiter_case(int nwait, int count, int32_t want) {
    static _Atomic int32_t words[16]; static int next; _Atomic int32_t *addr = &words[next++]; atomic_store(addr, 100);
    AddrWaiter w[4]; memset(w, 0, sizeof w); uint32_t th[4];
    for (int k = 0; k < nwait; k++) {
        w[k].addr = addr; w[k].value = 100;
        uint8_t *stack = mmap(NULL, 1 << 20, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0); need(stack != MAP_FAILED, "guest stack");
        uint64_t r[31] = {0}; r[1] = (uintptr_t)addr_waiter_main; r[2] = (uintptr_t)&w[k]; r[3] = (uintptr_t)stack + (1 << 20); r[4] = 44; r[5] = -1;
#ifdef GAME_BOTW
        r[5] = (uint64_t)(1 + k % 2);   /* as in part 2: the main thread keeps core 0 */
#endif
        kernel(8, r); need(!r[0], "CreateThread"); th[k] = (uint32_t)r[1];
        r[0] = th[k]; kernel(9, r); need(!r[0], "StartThread");
    }
    for (int k = 0; k < nwait; k++) while (!atomic_load(&w[k].entered)) usleep(1000);
    usleep(100000);   /* all in WaitForAddress */
    uint64_t r[31] = {0}; r[0] = (uintptr_t)addr; r[1] = 2; r[2] = 100; r[3] = (uint64_t)(uint32_t)count; kernel(0x35, r);
    need(!r[0], "SignalToAddress");
    int32_t got = atomic_load(addr);
    if (got != want) { fprintf(stderr, "SignalToAddress type 2, %d waiter(s), count %d: value %d, want %d\n", nwait, count, got, want); runtime_fail("SignalAndModifyByWaitingCountIfEqual value"); }
    int woken = count <= 0 || count >= nwait ? nwait : count;
    usleep(100000);
    int done = 0; for (int k = 0; k < nwait; k++) done += atomic_load(&w[k].done);
    need(done == woken, "SignalToAddress woke the expected number of waiters");
    if (woken < nwait) { r[0] = (uintptr_t)addr; r[1] = 0; r[2] = 0; r[3] = (uint64_t)-1; kernel(0x35, r); need(!r[0], "release the rest"); }
    for (int k = 0; k < nwait; k++) {
        r[0] = 0; r[1] = (uintptr_t)&th[k]; r[2] = 1; r[3] = 2000000000; kernel(0x18, r); need(!r[0], "address waiter exited");
        need(w[k].res == 0, "a woken address waiter returns 0");
    }
}
static int arbiter_part(void) {
    arbiter_case(2, -1, 98);   /* waking all with waiters: value - 2 */
    arbiter_case(0, -1, 101);  /* waking all, nobody waiting: value + 1 */
    arbiter_case(2, 2, 99);    /* waking count, all fit: value - 1 */
    arbiter_case(3, 1, 100);   /* waking count, some stay: unchanged */
    printf("{\"status\":\"pass\",\"part\":\"arbiter\",\"cases\":4}\n");
    return 0;
}
