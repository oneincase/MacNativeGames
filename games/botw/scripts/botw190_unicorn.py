"""What verify_realtime_patch.py and verify_pad_repeat_patch.py share for Breath of the Wild 1.9.0: the parser of the check[] and
put[] tables of games/totk/runtime/game_patches_botw.c, the adrp encoder, the VFR object and the Unicorn machine the update runs in."""
import math
import re
import struct
from pathlib import Path

from unicorn import Uc, UC_ARCH_ARM64, UC_HOOK_CODE, UC_MODE_ARM
from unicorn import arm64_const as A
from unicorn.arm64_const import UC_ARM64_REG_PC

ROOT = Path(__file__).resolve().parents[2] / "totk"
VFR = 0x7000_0000
CORE_STUB, FMODF_STUB = 0x159fee0, 0x15a05d0


def adrp(rd, site, target):
    pages = (target >> 12) - (site >> 12)
    assert -(1 << 20) <= pages < (1 << 20)
    imm = pages & 0x1fffff
    return 0x90000000 | ((imm & 3) << 29) | ((imm >> 2) << 5) | rd


def c_190(function):
    """The check[] and put[] tables of `function` in the 1.9.0 branch of game_patches_botw.c: (check {off: original word},
    put {off: ("adrp", register, site) | ("word", value)}). Nothing about the patch words is restated in this script."""
    c = (ROOT / "runtime/game_patches_botw.c").read_text().split("Breath of the Wild 1.9.0", 1)[1]
    body = c[c.index(f"static void {function}("):]
    body = body[:body.index("\n}\n")]
    tables = {}
    for name in ("check", "put"):
        text = re.search(name + r"\[\w*\] = \{(.*?)\};", body, re.S).group(1)
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        entries = re.findall(r"\{0x([0-9a-f]+)u,\s*(?:adrp_reg\((\d+), \(uintptr_t\)m->base \+ 0x([0-9a-f]+), page\)|0x([0-9a-f]+)u)\}", text)
        assert len(entries) == text.count("{0x"), f"{function}: a {name}[] entry of game_patches_botw.c is in a form this script cannot read"
        tables[name] = {}
        for off, reg, site, value in entries:
            off = int(off, 16)
            if reg:
                assert int(site, 16) == off, f"{function}: adrp at {off:#x} is written for site {site}"
                tables[name][off] = ("adrp", int(reg), off)
            else:
                tables[name][off] = ("word", int(value, 16))
    check = {off: v[1] for off, v in tables["check"].items()}
    assert set(tables["put"]) <= set(check), f"{function}: put[] writes an instruction check[] does not verify"
    return check, tables["put"]


def patch_words(put, offsets, data_off):
    """The words the C writes at `offsets` (all of them, nothing dropped), the adrp ones aimed at the data page."""
    assert set(put) == set(offsets), f"game_patches_botw.c writes {sorted(map(hex, put))}, expected {sorted(map(hex, offsets))}"
    return {off: adrp(v[1], v[2], data_off) if v[0] == "adrp" else v[1] for off, v in put.items()}


def f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


def sreg(mu, r):
    return struct.unpack("<f", struct.pack("<I", mu.reg_read(r) & 0xffffffff))[0]


def vfr_object(vfr):
    """The 1.6.0 and 1.9.0 VFR object laid out the way main+0x183eb98 builds it (interval 2, 1/30 s per frame, every core's pointers), at `vfr`."""
    o = bytearray(0x160)
    struct.pack_into("<I", o, 0x2c, 1)
    struct.pack_into("<f", o, 0x30, 1.0)
    struct.pack_into("<II", o, 0x34, 2, 2)          # interval, previous interval (second setup at main+0x183ec94)
    struct.pack_into("<ff", o, 0x3c, 1.0, 1.0)      # ratio, previous ratio
    struct.pack_into("<I", o, 0x88, 0x59)
    struct.pack_into("<IIf", o, 0x150, 2, 30, 1 / 30)
    for i in range(3):                              # per-core pointer arrays, as main+0x183ec3c..0x183ec8c set them
        for base, target in ((0x48, 0x34), (0x60, 0x3c), (0xf0, 0x8c + 4 * i), (0x108, 0x98 + 4 * i),
                             (0x120, 0xa4 + 4 * i), (0x138, 0xb0 + 4 * i)):
            struct.pack_into("<Q", o, base + 8 * i, vfr + target)
    return bytes(o)


def machine(image, data_off, patches):
    """The flat 1.9.0 image with `patches` written over it, the VFR singleton (a runtime pointer) at VFR with its object, and the
    stubs of nn::os's current core (core 0) and fmodf."""
    mu = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    mu.mem_map(0, data_off + 0x4000)
    mu.mem_write(0, image)
    for offset, w in patches.items():
        mu.mem_write(offset, struct.pack("<I", w))
    mu.mem_map(VFR, 0x100000)
    mu.mem_write(0x1e93890, struct.pack("<Q", VFR))                  # ksys::VFR singleton

    def stubs(uc, addr, size, _):
        if addr == CORE_STUB:                                        # nn::os current core (+1): core 0
            uc.reg_write(A.UC_ARM64_REG_X0, 1)
        elif addr == FMODF_STUB:
            r = f32(math.fmod(sreg(uc, A.UC_ARM64_REG_S0), sreg(uc, A.UC_ARM64_REG_S1)))
            uc.reg_write(A.UC_ARM64_REG_S0, struct.unpack("<I", struct.pack("<f", r))[0])
        else:
            return
        uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(A.UC_ARM64_REG_LR))
    mu.hook_add(UC_HOOK_CODE, stubs, begin=CORE_STUB, end=FMODF_STUB + 4)
    mu.mem_write(VFR, vfr_object(VFR))
    return mu
