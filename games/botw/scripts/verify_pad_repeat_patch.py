#!/usr/bin/env python3
"""Run the button loop of Breath of the Wild 1.6.0's sead::ControllerBase::updateDerivativeParams_ (main+0x126e490..
0x126e528) with Unicorn, as shipped and with the pad-repeat patch of games/totk/runtime/game_patches_botw.c (botw.md).

The loop counts the frames each button has been held and raises its repeat bit at the delay and then every pulse frames.
A controller is laid out the way its constructor (main+0x126e100) builds it, with the menus' delay 6 / pulse 2 on the
stick and d-pad directions (main+0x1a40958). A direction is held for 2 s at 30, 60, 95 and 120 fps:
- shipped at 30 fps is the reference (repeats at 0.2 s and then every 1/15 s);
- shipped at 120 fps is the bug (four times as fast) and must not match the reference;
- patched, with the host's tick (one per 1/30 s of game time, games/totk/runtime/game_patches_botw.c pad_tick_advance),
  every repeat must come within one 1/30 s tick of the reference at every frame rate and from every starting phase;
- patched with a tick on every frame at 30 fps must equal the shipped loop exactly (repeat bits and counts, frame by frame);
- released buttons reset their count on any frame, with or without a tick;
- negative control: the patched loop without its tick test (cbz w16 -> nop) runs four times as fast at 120 fps again.
Then the pause menu's fast-repeat countdown (main+0x1214474, the record {mask, 60, countdown, 9, 3, 60, 1}): shipped it
takes one off per frame and at zero branches on to the fast switch; patched it takes one off only on a tick, falls
through to the switch exactly once, and leaves a record alone on frames without a tick.
The patched words are read from game_patches_botw.c (pad_loop_ticked, menu_cd_ticked), so this checks what the runtime writes.
--version botw190 runs the 1.9.0 version (see verify_190): the loop counts in floats and the patch makes the counts, delays and
pulses 1/30 s of game time with no tick; the pause menu's countdown is left as shipped and runs in real time through the
VFR patch.
usage: verify_pad_repeat_patch.py [--version {botw160,botw190}] [main.patched.bin]
       (default botw160; the image defaults to totk/rewritten/<version>/main.patched.bin)"""
import argparse
import re
import struct
import sys
from pathlib import Path

from unicorn import Uc, UC_ARCH_ARM64, UC_HOOK_CODE, UC_MODE_ARM
from unicorn import arm64_const as A
from unicorn.arm64_const import UC_ARM64_REG_PC, UC_ARM64_REG_X11, UC_ARM64_REG_X19

sys.path.insert(0, str(Path(__file__).resolve().parent))
from botw190_unicorn import ROOT, VFR, adrp, c_190, f32, patch_words
from botw190_unicorn import machine as machine_190

parser = argparse.ArgumentParser(description="Unicorn check of the pad-repeat patch")
parser.add_argument("--version", choices=("botw160", "botw190"), default="botw160")
parser.add_argument("image", nargs="?", type=Path)
args = parser.parse_args()
image_path = args.image or ROOT / "rewritten" / args.version / "main.patched.bin"
image = image_path.read_bytes()
data_off = (len(image) + 0x3fff) & ~0x3fff
LOOP, END, WORDS, ADRP = 0x126e490, 0x126e528, 38, 7
CTRL = 0x7000_0000
STICKS = 0xff0000                     # the directions the menus set to delay 6 / pulse 2
HELD = 1 << 18                        # one of them

def verify_190():
    """1.9.0 (sead::ControllerBase::updateDerivativeParams_, main+0xeb0ef0): the button loop (main+0xeb0f6c..0xeb1000) adds s8
    to each held button's count, s8 being a constant 1.0 loaded through a GOT slot (main+0x1d6e9e0 -> 0x1dad0e0); the patch
    loads the VFR ratio from the data page instead, so counts, delays and pulses are 1/30 s of game time at any frame rate.
    The countdown of the pause menu (main+0xe67d00) takes the VFR's delta frames off per frame and is not patched; with the
    VFR patch (verify_realtime_patch.py's site 1) it runs in real time. The patch words and the shipped words are
    parsed from the put[] and check[] tables of game_patches_botw.c; this script restates none of them."""
    word = lambda a: struct.unpack_from("<I", image, a)[0]
    LOOP, LOOP_END, CALC, CALC_END = 0xeb0f6c, 0xeb1000, 0x151d3d0, 0x151d514
    CD, CD_NO_SWITCH, CD_SWITCH = 0xe67d00, 0xe67c98, 0xe67d3c
    # check[] of the C's patch_realtime and patch_pad_repeat: the words the image must hold; put[]: what the C writes
    realtime_check, realtime_put = c_190("patch_realtime")
    pad_check, pad_put = c_190("patch_pad_repeat")
    SITE1 = (0x151d484, 0x151d48c)
    SHIPPED = {**{off: w for off, w in realtime_check.items() if 0x151d484 <= off <= 0x151d490}, **pad_check}
    assert len(SHIPPED) > len(pad_check) > 0, SHIPPED
    for offset, original in SHIPPED.items():
        if word(offset) != original:
            raise SystemExit(f"main+{offset:#x} is {word(offset):08x}, not the shipped {original:08x}")
    # ldr s8, [x8] (0xbd400108) at main+0xeb0f88 reads x8: the GOT slot's pointer to 1.0 as shipped, the page once patched.
    # The slot is already relocated in the flat image, so the shipped checks below load the image's real 1.0.
    got, = struct.unpack_from("<Q", image, 0x1d6e9e0)
    assert got == 0x1dad0e0 and word(got) == 0x3f800000, (hex(got), hex(word(got)))
    # as the C writes them: site 1 (the VFR ratio, see verify_realtime_patch.py) and the loop's adrp x8, <page> and nop
    P1 = patch_words({off: v for off, v in realtime_put.items() if off in SITE1}, SITE1, data_off)
    P3 = patch_words(pad_put, (0xeb0f78, 0xeb0f80), data_off)

    def machine(patches):
        return machine_190(image, data_off, patches)

    def set_step(mu, seconds):                                           # the page: ratio, seconds
        mu.mem_write(data_off, struct.pack("<ff", f32(seconds * 30), seconds))

    def calc(mu):
        mu.reg_write(A.UC_ARM64_REG_X0, VFR)
        mu.reg_write(A.UC_ARM64_REG_W1, 2)
        mu.reg_write(A.UC_ARM64_REG_SP, VFR + 0x80000)
        mu.emu_start(CALC, CALC_END, count=2000)

    class Pad:
        def __init__(self, patches, fast=False):
            self.mu = machine(patches)
            c = bytearray(0x140)
            struct.pack_into("<i", c, 0x100, 0x1c)                       # buttons, as the constructor sets
            for i in range(32):                                          # counts are floats at +0x30 in 1.9.0
                c[0xb0 + i], c[0xd0 + i] = ((60, 1) if fast else (6, 2)) if STICKS >> i & 1 else (30, 1)
            self.mu.mem_write(CTRL, bytes(c))

        def frame(self, hold, seconds):
            set_step(self.mu, seconds)
            self.mu.mem_write(CTRL + 0x114, struct.pack("<I", hold))
            self.mu.mem_write(CTRL + 0x10, b"\0" * 4)
            self.mu.reg_write(UC_ARM64_REG_X19, CTRL)
            self.mu.reg_write(A.UC_ARM64_REG_SP, VFR + 0x80000)
            self.mu.emu_start(LOOP, LOOP_END, count=20000)
            repeat, = struct.unpack("<I", self.mu.mem_read(CTRL + 0x10, 4))
            return repeat, struct.unpack("<32f", self.mu.mem_read(CTRL + 0x30, 128))

    def pad_run(patches, fps, seconds=2.0, fast=False, release_at=None):
        pad, step, times, trace = Pad(patches, fast), f32(1 / fps), [], []
        for k in range(int(round(seconds * fps))):
            repeat, counts = pad.frame(HELD if release_at is None or k / fps < release_at else 0, step)
            trace.append((repeat, counts[18]))
            if repeat & HELD:
                times.append(k / fps)
        return times, trace

    failures = []

    def check(ok, what):
        print(("ok    " if ok else "FAIL  ") + what)
        if not ok:
            failures.append(what)

    ref, ref_trace = pad_run({}, 30)
    check(len(ref) > 5, f"site 3 shipped at 30 fps: first repeat {ref[0]:.3f} s, then every {ref[1] - ref[0]:.4f} s, {len(ref)} in 2 s")
    fast, _ = pad_run({}, 120)
    check(len(fast) > 3 * len(ref), f"site 3 shipped at 120 fps (the bug, and the negative control): {len(fast)} repeats in 2 s")
    for fps in (30, 60, 95, 120, 144, 165, 240, 360):
        got, _ = pad_run(P3, fps)
        n = min(len(got), len(ref))
        worst = max((abs(a - b) for a, b in zip(got[:n], ref[:n])), default=float("inf"))   # no repeat at all fails
        check(abs(len(got) - len(ref)) <= 1 and worst <= 1 / 30 + 1 / fps + 1e-9,
              f"site 3 patched at {fps} fps: {len(got)} repeats in 2 s (reference {len(ref)}), worst offset {worst * 1e3:.1f} ms")
    check(pad_run(P3, 30)[1] == ref_trace, "site 3 patched at 30 fps equals the shipped loop frame by frame (repeat bits and counts)")
    _, rel = pad_run(P3, 120, 1.0, release_at=0.5)
    check(rel[59][1] > 0 and rel[60][1] == 0, f"site 3 patched: a release resets the count at once (count {rel[59][1]:.2f} -> {rel[60][1]})")
    slow, _ = pad_run({}, 30, 4.0, fast=True)
    patched, _ = pad_run(P3, 120, 4.0, fast=True)
    shipped, _ = pad_run({}, 120, 4.0, fast=True)
    check(abs(len(patched) - len(slow)) <= 1 and len(shipped) > 3 * len(slow),
          f"site 3 delay 60 / pulse 1: shipped at 30 fps {len(slow)} repeats in 4 s, patched at 120 fps {len(patched)}, shipped at 120 fps {len(shipped)}")

    def countdown(patches, fps, seconds=3.0):
        """Site 4: the menu's record {mask, 60, countdown 60.0, 9, 3, 60, 1} after VFR::calc each frame; time until the fast switch."""
        mu, stop, switch_at = machine(patches), {}, None
        mu.mem_write(VFR + 0x10000 + 0x649f8, struct.pack("<IifBBBB", 0xf0000, 60, 60.0, 9, 3, 60, 1))

        def reached(uc, addr, size, _):
            if addr in (CD_NO_SWITCH, CD_SWITCH):
                stop["pc"] = addr
                uc.emu_stop()
        mu.hook_add(UC_HOOK_CODE, reached, begin=CD_NO_SWITCH, end=CD_SWITCH)
        for k in range(int(seconds * fps)):
            set_step(mu, f32(1 / fps))
            calc(mu)
            mu.reg_write(A.UC_ARM64_REG_X8, VFR + 0x10000 + 0x649f8)
            mu.reg_write(A.UC_ARM64_REG_X20, VFR + 0x10000 + 0x649f0)
            mu.reg_write(A.UC_ARM64_REG_SP, VFR + 0x80000)
            stop.clear()
            mu.emu_start(CD, 0xffffffff, count=400)
            if stop.get("pc") == CD_SWITCH and switch_at is None:
                switch_at = (k + 1) / fps
        return switch_at
    t30 = countdown({}, 30)
    check(t30 is not None and abs(t30 - 61 / 30) < 1e-9, f"site 4 shipped at 30 fps: the fast switch after {t30 and round(t30, 3)} s held")
    for fps in (60, 120, 144, 240, 360):
        t = countdown(P1, fps)
        check(t is not None and abs(t - t30) <= 1 / 30 + 1 / fps + 1e-9,
              f"site 4 unpatched countdown with the VFR patch at {fps} fps: the fast switch after {t and round(t, 3)} s held")
    t_no = countdown({}, 120)
    check(t_no is not None and t_no < 0.6, f"site 4 negative control (no VFR patch, 120 fps): the fast switch after {t_no and round(t_no, 3)} s")
    if failures:
        raise SystemExit(f"{len(failures)} check(s) failed")
    print("pad repeat patch (1.9.0): all checks passed")


if args.version == "botw190":
    verify_190()
    sys.exit(0)


src = (ROOT / "runtime/game_patches_botw.c").read_text()


def c_words(name, n=WORDS):
    body = re.search(r"static const uint32_t %s\[\w+\] = \{(.*?)\};" % name, src, re.S).group(1)
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    words = [int(w.rstrip("u"), 16) if w.startswith("0x") else int(w) for w in re.findall(r"0x[0-9a-fA-F]+u?|\b0\b", body)]
    if len(words) != n:
        raise SystemExit(f"{name}: {len(words)} words in game_patches_botw.c, expected {n}")
    return words


shipped = c_words("pad_loop_shipped")
ticked = c_words("pad_loop_ticked")
ticked[ADRP] = adrp(16, LOOP + 4 * ADRP, data_off)
for i in range(WORDS):
    actual, = struct.unpack_from("<I", image, LOOP + 4 * i)
    if actual != shipped[i]:
        raise SystemExit(f"main+{LOOP + 4 * i:#x} is {actual:08x}, not the shipped {shipped[i]:08x}")
no_tick_test = list(ticked)
no_tick_test[ticked.index(0x34000290)] = 0xd503201f          # cbz w16, next -> nop


class Loop:
    def __init__(self, words):
        self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        size = data_off + 0x4000
        self.uc.mem_map(0, size)
        self.uc.mem_write(0, image)
        self.uc.mem_write(LOOP, struct.pack("<%dI" % WORDS, *words))
        self.uc.mem_map(CTRL, 0x1000)
        c = bytearray(0x140)
        struct.pack_into("<i", c, 0x100, 0x1c)                 # buttons, as the constructor sets
        for i in range(32):
            c[0xb0 + i], c[0xd0 + i] = (6, 2) if STICKS >> i & 1 else (30, 1)
        self.uc.mem_write(CTRL, bytes(c))

    def frame(self, hold, tick):
        self.uc.mem_write(data_off + 8, struct.pack("<I", tick))
        self.uc.reg_write(UC_ARM64_REG_X19, CTRL)
        self.uc.reg_write(UC_ARM64_REG_X11, hold)
        self.uc.emu_start(LOOP, END, count=10000)
        if self.uc.reg_read(UC_ARM64_REG_PC) != END:
            raise SystemExit("the loop did not reach its end")
        repeat, = struct.unpack("<I", self.uc.mem_read(CTRL + 0x10, 4))
        counts = struct.unpack("<32I", self.uc.mem_read(CTRL + 0x30, 128))
        return repeat, counts


def run(words, fps, seconds=2.0, phase=0.0, ticks="host", release_at=None):
    """Hold HELD from t = 0 for `seconds`; returns the times of its repeats and every frame's (repeat, counts)."""
    loop, step = Loop(words), 1.0 / fps
    pad_phase, times, trace = phase, [], []
    for k in range(int(round(seconds * fps))):
        if ticks == "host":                                    # pad_tick_advance: the next frame's step on the page
            pad_phase += f32(step) * 30.0
            tick = 0
            if pad_phase >= 1.0:
                tick, pad_phase = 1, pad_phase - 1.0
                if pad_phase >= 1.0:
                    pad_phase = 0.0
        else:
            tick = ticks
        hold = HELD if release_at is None or k * step < release_at else 0
        repeat, counts = loop.frame(hold, tick)
        trace.append((repeat, counts))
        if repeat & HELD:
            times.append(k * step)
    return times, trace


failures = []


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what)
    if not ok:
        failures.append(what)


ref, ref_trace = run(shipped, 30, ticks=1)
check(abs(ref[0] - 0.2) < 1e-9 and all(abs(b - a - 1 / 15) < 1e-9 for a, b in zip(ref, ref[1:])),
      f"shipped at 30 fps: first repeat {ref[0]:.3f} s, then every {ref[1] - ref[0]:.4f} s, {len(ref)} in 2 s")
fast, _ = run(shipped, 120, ticks=1)
check(len(fast) > 3 * len(ref), f"shipped at 120 fps (the bug): {len(fast)} repeats in 2 s, first at {fast[0]:.3f} s")

for fps in (30, 60, 95, 120, 144, 165, 240, 360):
    worst, counts = 0.0, set()
    for phase in (0.0, 0.25, 0.5, 0.75, 0.999):
        got, _ = run(ticked, fps, phase=phase)
        counts.add(len(got))
        n = min(len(got), len(ref))
        worst = max([worst] + [abs(a - b) for a, b in zip(got[:n], ref[:n])])
    check(worst < 1 / 30 + 1 / fps and all(abs(c - len(ref)) <= 1 for c in counts),
          f"patched at {fps} fps: {sorted(counts)} repeats in 2 s (reference {len(ref)}), worst offset {worst * 1e3:.1f} ms")

same = run(ticked, 30, ticks=1)[1] == ref_trace
check(same, "patched with a tick on every frame equals the shipped loop frame by frame (repeat bits and counts)")

_, frozen = run(ticked, 120, seconds=0.5, ticks=0)
check(all(r == 0 and c[18] == 0 for r, c in frozen), "patched without ticks: no repeat and the count stays 0")

_, rel = run(ticked, 120, seconds=1.0, release_at=0.5)    # frame 60 releases; at 120 fps from phase 0 it has no tick
check(rel[59][1][18] > 0 and rel[60][1][18] == 0 and rel[61][1][18] == 0,
      f"patched: a release on a frame without a tick resets the count at once (count {rel[59][1][18]} -> {rel[60][1][18]})")

neg, _ = run(no_tick_test, 120)
check(len(neg) > 3 * len(ref), f"negative control, the tick test removed: {len(neg)} repeats in 2 s at 120 fps (the bug again)")

# the pause menu's countdown
from unicorn import UC_HOOK_CODE
from unicorn.arm64_const import UC_ARM64_REG_X10, UC_ARM64_REG_X27
CD, CD_DONE, CD_SWITCH, CD_WORDS, CD_ADRP = 0x1214474, 0x12145e0, 0x1214498, 9, 2
cd_shipped, cd_ticked = c_words("menu_cd_shipped", CD_WORDS), c_words("menu_cd_ticked", CD_WORDS)
cd_ticked[CD_ADRP] = adrp(13, CD + 4 * CD_ADRP, data_off)
for i in range(CD_WORDS):
    actual, = struct.unpack_from("<I", image, CD + 4 * i)
    if actual != cd_shipped[i]:
        raise SystemExit(f"main+{CD + 4 * i:#x} is {actual:08x}, not the shipped {cd_shipped[i]:08x}")
MENU = 0x7100_0000                                         # the menu object x27; its records at +0x649f8 + 16 i


class Countdown:
    def __init__(self, words):
        self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        self.uc.mem_map(0, data_off + 0x4000)
        self.uc.mem_write(0, image)
        self.uc.mem_write(CD, struct.pack("<%dI" % CD_WORDS, *words))
        self.uc.mem_map(MENU, 0x70000)
        rec = struct.pack("<IIIBBBB", 0xf0000, 60, 60, 9, 3, 60, 1)   # as main+0x1207d0c builds record 0
        self.uc.mem_write(MENU + 0x649f8, rec)
        self.uc.hook_add(UC_HOOK_CODE, lambda uc, a, sz, d: uc.emu_stop() if a == CD_SWITCH else None)

    def frame(self, tick):
        """Runs the block for record 0; returns (countdown after, reached the fast switch)."""
        self.uc.mem_write(data_off + 8, struct.pack("<I", tick))
        self.uc.reg_write(UC_ARM64_REG_X27, MENU)
        self.uc.reg_write(UC_ARM64_REG_X10, 0)
        self.uc.emu_start(CD, CD_DONE, count=20)
        pc = self.uc.reg_read(UC_ARM64_REG_PC)
        if pc not in (CD_DONE, CD_SWITCH):
            raise SystemExit(f"the countdown block stopped at {pc:#x}")
        value, = struct.unpack("<i", self.uc.mem_read(MENU + 0x64a00, 4))
        return value, pc == CD_SWITCH


def countdown_run(words, ticks):
    cd, out = Countdown(words), []
    for t in ticks:
        out.append(cd.frame(t))
    return out


ship = countdown_run(cd_shipped, [1] * 62)
check([v for v, _ in ship[:61]] == list(range(59, -2, -1)) and [sw for _, sw in ship].count(True) == 1 and ship[60][1],
      "shipped countdown: one off per frame, the fast switch on the frame it reaches zero, then nothing")
pat = countdown_run(cd_ticked, [1] * 62)
check(pat[:61] == ship[:61] and pat[61] == (-2, False),
      "patched countdown with a tick every frame equals the shipped block up to the switch; after it the value keeps falling (-2), no second switch")
pat2 = countdown_run(cd_ticked, [1, 0, 0, 0] * 62)
check([v for v, _ in pat2[::4]][:61] == [v for v, _ in ship[:61]] and [sw for _, sw in pat2].count(True) == 1 and pat2[240][1]
      and all(not sw and v == pat2[i - i % 4][0] for i, (v, sw) in enumerate(pat2) if i % 4),
      "patched countdown at 120 fps (a tick every 4th frame): frames without a tick leave the record alone, one off per tick, the one switch on the 61st tick")
neg = countdown_run(cd_shipped, [1, 0, 0, 0] * 62)
check(neg[60][1] and [v for v, _ in neg[:61]] == list(range(59, -2, -1)), "negative control: the shipped block ignores the tick and switches after 60 frames")

if failures:
    raise SystemExit(f"{len(failures)} check(s) failed")
print("pad repeat patch: all checks passed")
