"""
Persona Q2 font patch - patches the game's code.bin so that an
upscaled font is drawn at the original size and spacing.

    python patch_code.py code.bin                 # for the 2x scale font
    python patch_code.py code.bin --x4            # TEST for a larger (4x) font
    python patch_code.py code.bin out.bin         # set output name

Uses your own code.bin file.  The input file is never changed;
the result is written to code_patched.bin next to it.
Every byte that is about to change is checked first; if the file
is not the expected one, nothing is written.  Needs only Python 3.
"""
import argparse
import struct
import sys

BASE_DEFAULT = 0x100000


def _s(n):
    return n >> 1, n & 1


def _vfp3(base, sd, sn, sm):
    vd, d = _s(sd)
    vn, n = _s(sn)
    vm, m = _s(sm)
    return base | (d << 22) | (vn << 16) | (vd << 12) | (n << 7) | (m << 5) | vm


def vmul(sd, sn, sm):
    return _vfp3(0xEE200A00, sd, sn, sm)


def vdiv(sd, sn, sm):
    return _vfp3(0xEE800A00, sd, sn, sm)


def vsub(sd, sn, sm):
    return _vfp3(0xEE300A40, sd, sn, sm)     # VSUB = VADD with bit 6 set


def vldr(sd, rn, off):
    assert off % 4 == 0 and 0 <= off <= 1020
    vd, d = _s(sd)
    return 0xED900A00 | (d << 22) | (rn << 16) | (vd << 12) | (off // 4)


def vstr(sd, rn, off):
    assert off % 4 == 0 and 0 <= off <= 1020
    vd, d = _s(sd)
    return 0xED800A00 | (d << 22) | (rn << 16) | (vd << 12) | (off // 4)


def vldr_pc(sd, here, target):
    off = target - (here + 8)
    assert off >= 0 and off % 4 == 0 and off <= 1020, "literal too far"
    return vldr(sd, 15, off)


def ldr_pc(rd, here, target):
    off = target - (here + 8)
    assert 0 <= off < 4096, "literal too far"
    return 0xE59F0000 | (rd << 12) | off


def ldrb(rd, rn, imm):
    return 0xE5D00000 | (rn << 16) | (rd << 12) | imm


def add_lsl(rd, rn, rm, sh):
    return 0xE0800000 | (rn << 16) | (rd << 12) | (sh << 7) | rm


def mov_lsr(rd, rm, sh):
    return 0xE1A00020 | (rd << 12) | (sh << 7) | rm


def and_imm(rd, rn, imm8):
    return 0xE2000000 | (rn << 16) | (rd << 12) | imm8


def branch(here, target):
    off = (target - (here + 8)) >> 2
    assert -(1 << 23) <= off < (1 << 23)
    return 0xEA000000 | (off & 0xFFFFFF)


def ldr_imm(rd, rn, imm):          # ldr rd,[rn,#imm]
    return 0xE5900000 | (rn << 16) | (rd << 12) | imm

SP = 13

TABLE_PATCHES = [
    (0x123680, 0xE3A06801, 0xE3A06804, "atlas byte size 0x10000 -> 0x40000"),
    (0x123684, 0xE3A01C01, 0xE3A01C02, "atlas width/height 0x100 -> 0x200"),
    (0x1D4790, 0xE1A00C20, 0xE1A00BA0, "cell x origin = col*32"),
    (0x1D47AC, 0xE0822000, 0xE0802082, "cell y = row*32 + y"),
    (0x1D47B8, 0xE1A0E283, 0xE1A0E303, "64 tiles per row"),
    (0x1D4848, 0xE3510010, 0xE3510020, "inner loop bound 16 -> 32"),
    (0x1D4858, 0xE3500010, 0xE3500020, "outer loop bound 16 -> 32"),
]

TABLE_PATCHES_64 = [
    (0x123680, 0xE3A06801, 0xE3A06601, "atlas byte size 0x10000 -> 0x100000"),
    (0x123684, 0xE3A01C01, 0xE3A01B01, "atlas width/height 0x100 -> 0x400"),
    (0x1D4790, 0xE1A00C20, 0xE1A00B20, "cell x origin = col*64"),
    (0x1D47AC, 0xE0822000, 0xE0802102, "cell y = row*64 + y"),
    (0x1D47B8, 0xE1A0E283, 0xE1A0E383, "128 tiles per row"),
    (0x1D4848, 0xE3510010, 0xE3510040, "inner loop bound 16 -> 64"),
    (0x1D4858, 0xE3500010, 0xE3500040, "outer loop bound 16 -> 64"),
]

CACHE_UV = {32: 0.5, 64: 0.25}     # the caves shrink the UV size by 256/atlas_size

HOOKS = {
    "A": (0x1A19D4, 0xE590000C, "FUN_001a1898"),   # ldr r0,[r0,#0xc]
    "B": (0x1A2A80, 0xE5911034, "FUN_001a2958"),   # ldr r1,[r1,#0x34]
    "C": (0x1A2AE0, 0xEDDD8A06, "FUN_001a2958"),   # vldr s17,[sp,#0x18]
}

RET = {"A": 0x1A19D8, "B": 0x1A2A84, "C": 0x1A2AE4}

TAB_A = 0x57D130     # per-slot "target size" floats  {13.0, 12.0}

TAB_B = 0x57D138     # per-slot "native size" floats  {13.0, 12.0}


def build_items():
    """Return three lists of (asm text, function(here, literals)->word)."""
    A = [
        ("ldrb r1,[r5,#0x15]",      lambda h, L: ldrb(1, 5, 0x15)),
        ("ldr r2,=0x57d130",        lambda h, L: ldr_pc(2, h, L["tabA"])),
        ("add r2,r2,r1,lsl #2",     lambda h, L: add_lsl(2, 2, 1, 2)),
        ("vldr s0,[r2]",            lambda h, L: vldr(0, 2, 0)),
        ("ldr r2,=0x57d138",        lambda h, L: ldr_pc(2, h, L["tabB"])),
        ("add r2,r2,r1,lsl #2",     lambda h, L: add_lsl(2, 2, 1, 2)),
        ("vldr s1,[r2]",            lambda h, L: vldr(1, 2, 0)),
        ("vdiv.f32 s0,s0,s1",       lambda h, L: vdiv(0, 0, 1)),
        ("vldr s1,[sp,#0x20]",      lambda h, L: vldr(1, SP, 0x20)),
        ("vmul.f32 s1,s1,s0",       lambda h, L: vmul(1, 1, 0)),
        ("vstr s1,[sp,#0x20]",      lambda h, L: vstr(1, SP, 0x20)),
        ("vldr s1,[sp,#0x24]",      lambda h, L: vldr(1, SP, 0x24)),
        ("vmul.f32 s1,s1,s0",       lambda h, L: vmul(1, 1, 0)),
        ("vstr s1,[sp,#0x24]",      lambda h, L: vstr(1, SP, 0x24)),
        ("ldr r2,=dytab",           lambda h, L: ldr_pc(2, h, L["dytabp"])),
        ("add r2,r2,r1,lsl #2",     lambda h, L: add_lsl(2, 2, 1, 2)),
        ("vldr s1,[r2]",            lambda h, L: vldr(1, 2, 0)),
        ("vldr s2,[sp,#0x1c]",      lambda h, L: vldr(2, SP, 0x1C)),
        ("vsub.f32 s2,s2,s1",       lambda h, L: vsub(2, 2, 1)),
        ("vstr s2,[sp,#0x1c]",      lambda h, L: vstr(2, SP, 0x1C)),
        ("vldr s0,[half]",          lambda h, L: vldr_pc(0, h, L["half"])),
        ("vldr s1,[sp,#0x10]",      lambda h, L: vldr(1, SP, 0x10)),
        ("vmul.f32 s1,s1,s0",       lambda h, L: vmul(1, 1, 0)),
        ("vstr s1,[sp,#0x10]",      lambda h, L: vstr(1, SP, 0x10)),
        ("vldr s1,[sp,#0x14]",      lambda h, L: vldr(1, SP, 0x14)),
        ("vmul.f32 s1,s1,s0",       lambda h, L: vmul(1, 1, 0)),
        ("vstr s1,[sp,#0x14]",      lambda h, L: vstr(1, SP, 0x14)),
        ("ldr r0,[r0,#0xc]  (original)", lambda h, L: HOOKS["A"][1]),
        ("b 0x%x" % RET["A"],       lambda h, L: branch(h, RET["A"])),
    ]
    B = [
        ("ldr r2,[r1,#8]",          lambda h, L: ldr_imm(2, 1, 8)),
        ("mov r2,r2,lsr #8",        lambda h, L: mov_lsr(2, 2, 8)),
        ("and r2,r2,#0xff",         lambda h, L: and_imm(2, 2, 0xFF)),
        ("ldr r3,=0x57d130",        lambda h, L: ldr_pc(3, h, L["tabA"])),
        ("add r3,r3,r2,lsl #2",     lambda h, L: add_lsl(3, 3, 2, 2)),
        ("vldr s4,[r3]",            lambda h, L: vldr(4, 3, 0)),
        ("ldr r3,=0x57d138",        lambda h, L: ldr_pc(3, h, L["tabB"])),
        ("add r3,r3,r2,lsl #2",     lambda h, L: add_lsl(3, 3, 2, 2)),
        ("vldr s5,[r3]",            lambda h, L: vldr(5, 3, 0)),
        ("vdiv.f32 s4,s4,s5",       lambda h, L: vdiv(4, 4, 5)),
        ("vmul.f32 s0,s0,s4",       lambda h, L: vmul(0, 0, 4)),
        ("vmul.f32 s1,s1,s4",       lambda h, L: vmul(1, 1, 4)),
        ("vstr s0,[sp,#0x20]",      lambda h, L: vstr(0, SP, 0x20)),
        ("vstr s1,[sp,#0x24]",      lambda h, L: vstr(1, SP, 0x24)),
        ("ldr r3,=dytab",           lambda h, L: ldr_pc(3, h, L["dytabp"])),
        ("add r3,r3,r2,lsl #2",     lambda h, L: add_lsl(3, 3, 2, 2)),
        ("vldr s5,[r3]",            lambda h, L: vldr(5, 3, 0)),
        ("vldr s6,[sp,#0x1c]",      lambda h, L: vldr(6, SP, 0x1C)),
        ("vsub.f32 s6,s6,s5",       lambda h, L: vsub(6, 6, 5)),
        ("vstr s6,[sp,#0x1c]",      lambda h, L: vstr(6, SP, 0x1C)),
        ("ldr r1,[r1,#0x34]  (original)", lambda h, L: HOOKS["B"][1]),
        ("b 0x%x" % RET["B"],       lambda h, L: branch(h, RET["B"])),
    ]
    C = [
        ("vldr s4,[sp,#0x10]",      lambda h, L: vldr(4, SP, 0x10)),
        ("vldr s5,[sp,#0x14]",      lambda h, L: vldr(5, SP, 0x14)),
        ("vldr s6,[half]",          lambda h, L: vldr_pc(6, h, L["half"])),
        ("vmul.f32 s4,s4,s6",       lambda h, L: vmul(4, 4, 6)),
        ("vmul.f32 s5,s5,s6",       lambda h, L: vmul(5, 5, 6)),
        ("vstr s4,[sp,#0x10]",      lambda h, L: vstr(4, SP, 0x10)),
        ("vstr s5,[sp,#0x14]",      lambda h, L: vstr(5, SP, 0x14)),
        ("vldr s17,[sp,#0x18]  (original)", lambda h, L: HOOKS["C"][1]),
        ("b 0x%x" % RET["C"],       lambda h, L: branch(h, RET["C"])),
    ]
    return A, B, C


def build_blob(base, yshift=0.0, A=24.0, B=24.0 / 0.55, uv=0.5):
    """Return (list of (address, word, text), cave addresses dict).
       The caves and the game's metrics function read PRIVATE copies of tables A and B that live in the
       cave pool, so the game's own tables at 0x57D130 stay untouched for every other reader."""
    CA, CB, CC = build_items()
    addr_A = base
    addr_B = addr_A + 4 * len(CA)
    addr_C = addr_B + 4 * len(CB)
    pool = addr_C + 4 * len(CC)
    L = {"tabA": pool, "tabB": pool + 4, "half": pool + 8, "dytabp": pool + 12, "dytab": pool + 16,
         "privA": pool + 32, "privB": pool + 40}
    out = []
    for start, items in ((addr_A, CA), (addr_B, CB), (addr_C, CC)):
        for i, (txt, fn) in enumerate(items):
            here = start + 4 * i
            out.append((here, fn(here, L), txt))
    fw = lambda v: struct.unpack("<I", struct.pack("<f", v))[0]
    out.append((pool, L["privA"], ".word privA   (address of the private table A below)"))
    out.append((pool + 4, L["privB"], ".word privB   (address of the private table B below)"))
    out.append((pool + 8, struct.unpack("<I", struct.pack("<f", uv))[0], ".float %g  [param] (UV size factor = 256 / cache size)" % uv))
    out.append((pool + 12, pool + 16, ".word dytab   (address of the shift table below)"))
    for i, v in enumerate((0.0, yshift, 0.0, 0.0)):
        out.append((pool + 16 + 4 * i, fw(v),
                    ".float %g   (dytab[%d]: pixels to move text of font slot %d UP)" % (v, i, i)))
    for name, vals in (("privA", (13.0, A)), ("privB", (13.0, B))):
        for i, v in enumerate(vals):
            out.append((L[name] + 4 * i, fw(v), ".float %g   (%s[%d] [param])" % (v, name, i)))
    return out, {"A": addr_A, "B": addr_B, "C": addr_C, "privA": L["privA"], "privB": L["privB"],
                 "dytab1": L["dytab"] + 4}


def load(path):
    with open(path, "rb") as f:
        return bytearray(f.read())


def cmd_apply(args):
    data = load(args.input)
    base = args.base
    cave = args.cave

    if cave % 4:
        sys.exit("ERROR: cave address must be a multiple of 4")

    def off(a, n=4):
        o = a - base
        if o < 0 or o + n > len(data):
            sys.exit("ERROR: address 0x%X is outside the file (file covers 0x%X - 0x%X)."
                     " Is --base right?" % (a, base, base + len(data)))
        return o

    def rd(a):
        return struct.unpack_from("<I", data, off(a))[0]

    def wr(a, w):
        struct.pack_into("<I", data, off(a), w)

    A = args.A
    B = args.B if args.B is not None else A / args.ratio
    cache = getattr(args, "cache", 32)
    blob, caves = build_blob(cave, args.yshift, A, B, CACHE_UV[cache])
    blob_end = max(a for a, _, _ in blob) + 4
    print("cave A at 0x%X, B at 0x%X, C at 0x%X, ends 0x%X (size 0x%X)"
          % (caves["A"], caves["B"], caves["C"], blob_end, blob_end - cave))
    print("vertical shift (slot 1) at 0x%X = %g px;  private font tables: A at 0x%X, B at 0x%X  (A[1]=%g, B[1]=%g, scale %.4f)"
          % (caves["dytab1"], args.yshift, caves["privA"], caves["privB"], A, B, A / B))
    lay0 = detect_layout(data)
    if lay0 is None:
        sys.exit("ERROR: the game's font tables at 0x57D130 / 0x50BA2C are not the original values.\n"
                 "Start again from the ORIGINAL code.bin (apply now keeps the game's own tables untouched).")

    # ---- check everything first, change nothing yet -------------------------
    problems = []
    plan = []   # (address, new word, description)

    for a, old, new, desc in (TABLE_PATCHES if cache == 32 else TABLE_PATCHES_64):
        cur = rd(a)
        if cur == old:
            plan.append((a, new, desc))
        elif cur == new:
            print("  already applied: 0x%X %s" % (a, desc))
        else:
            problems.append("0x%X: expected %08X (or already %08X), found %08X  [%s]"
                            % (a, old, new, cur, desc))

    for key, (a, orig, name) in HOOKS.items():
        cur = rd(a)
        newb = branch(a, caves[key])
        if cur == orig:
            plan.append((a, newb, "hook %s in %s -> cave %s" % (key, name, key)))
        elif cur == newb:
            print("  already applied: hook %s at 0x%X" % (key, a))
        else:
            problems.append("hook 0x%X: expected %08X, found %08X" % (a, orig, cur))

    for a, expect, new, desc in ((0x1A0CF8, TAB_A, caves["privA"], "FUN_001a0b4c reads its A table from the private copy"),
                                 (0x1A0D00, TAB_B, caves["privB"], "FUN_001a0b4c reads its B table from the private copy")):
        cur = rd(a)
        if cur == expect:
            plan.append((a, new, desc))
        elif cur == new:
            print("  already applied: 0x%X %s" % (a, desc))
        else:
            problems.append("0x%X: expected the table pointer %08X, found %08X  [%s]" % (a, expect, cur, desc))

    for a, w, txt in blob:
        cur = rd(a)
        if cur == w:
            continue
        if cur != 0:
            problems.append("cave space at 0x%X is not empty (found %08X). "
                            "Pick another address." % (a, cur))
            break
        plan.append((a, w, "cave: " + txt))

    if problems:
        print("\nNOTHING WAS WRITTEN. Problems found:")
        for p in problems:
            print("  -", p)
        return 1

    for a, w, desc in plan:
        wr(a, w)

    with open(args.output, "wb") as f:
        f.write(data)

    print("\nWrote %s.  Changes made:" % args.output)
    for a, w, desc in sorted(plan):
        print("  0x%08X  %s  %08X  %s" % (a, struct.pack("<I", w).hex(" "), w, desc))
    print("\nCave listing (compare with Ghidra's disassembly):")
    for a, w, txt in blob:
        print("  0x%08X  %s  %s" % (a, struct.pack("<I", w).hex(" "), txt))
    return 0

TEXT_ADDR, TEXT_BYTES = 0x100000, 0x406000

RO_ADDR, RO_BYTES = 0x506000, 0x4B000          # 75 pages (code size 0x4ADF8, padded)

DATA_ADDR = 0x551000

A_TABLE = 0x57D130       # per-slot "target size" floats  {13.0, 12.0}

B_TABLE = 0x57D138       # per-slot "native size" floats  {13.0, 12.0}

EXTRA_TABLE = 0x50BA2C   # per-slot extra advance (ints)  {1, 0, -1, 0}


class Layout:
    def __init__(self, ro_bytes):
        self.ro_bytes = ro_bytes

    def off(self, addr):
        if TEXT_ADDR <= addr < RO_ADDR:
            return addr - TEXT_ADDR
        if RO_ADDR <= addr < DATA_ADDR:
            return TEXT_BYTES + (addr - RO_ADDR)
        if addr >= DATA_ADDR:
            return TEXT_BYTES + self.ro_bytes + (addr - DATA_ADDR)
        raise ValueError("address 0x%X is below the text segment" % addr)


def detect_layout(data):
    """Find which file layout puts the font tables at the right addresses."""
    for ro in (RO_BYTES, 0x4ADF8):
        lay = Layout(ro)
        try:
            o = lay.off(A_TABLE)
            e = lay.off(EXTRA_TABLE)
            if o + 16 > len(data) or e + 16 > len(data):
                continue
            a0, a1, b0, b1 = struct.unpack_from("<4f", data, o)
            x0, x1, x2, x3 = struct.unpack_from("<4i", data, e)
            if a0 == 13.0 and b0 == 13.0 and x0 == 1 and x2 == -1:
                return lay
        except ValueError:
            pass
    return None


def need_layout(data):
    lay = detect_layout(data)
    if lay is None:
        sys.exit("ERROR: could not find the font tables (13.0, 12.0, 13.0, ... at 0x57D130 and "
                 "1, 0, -1, 0 at 0x50BA2C) in this file.\n"
                 "Check your code.bin file. Nothing was written.")
    if lay.ro_bytes != RO_BYTES:
        print("note: this file uses an unpadded read-only segment (0x%X)." % lay.ro_bytes)
    return lay


def cmd_params(args):
    data = load(args.input)
    lay = need_layout(data)

    plan = []
    lit_a = struct.unpack_from("<I", data, lay.off(0x1A0CF8))[0]
    lit_b = struct.unpack_from("<I", data, lay.off(0x1A0D00))[0]
    private = lit_a != A_TABLE
    if private:
        print("This code.bin uses the private font tables (A at 0x%X, B at 0x%X);"
              " the game's own tables stay original." % (lit_a, lit_b))
        if args.B is None and args.ratio is None:
            A = B = None
        else:
            A = args.A
            B = args.B if args.B is not None else A / args.ratio
    else:
        if args.B is None and args.ratio is None:
            sys.exit("ERROR: give --ratio (e.g. 0.53) or --B")
        A = args.A
        B = args.B if args.B is not None else A / args.ratio

    def f32(a):
        return struct.unpack_from("<f", data, lay.off(a))[0]

    def i32(a):
        return struct.unpack_from("<i", data, lay.off(a))[0]

    def u32(a):
        return struct.unpack_from("<I", data, lay.off(a))[0]

    # slot 1 entries
    if private and A is not None:
        plan.append(("private A[1] (clamp size / numerator)", lit_a + 4, "f", f32(lit_a + 4), A))
        plan.append(("private B[1] (native size / divisor)", lit_b + 4, "f", f32(lit_b + 4), B))
    elif not private:
        plan.append(("A[1] (clamp size / numerator)", A_TABLE + 4, "f", f32(A_TABLE + 4), A))
        plan.append(("B[1] (native size / divisor)", B_TABLE + 4, "f", f32(B_TABLE + 4), B))
    plan.append(("extra[1] (tracking, px before scaling)", EXTRA_TABLE + 4, "i",
                 i32(EXTRA_TABLE + 4), args.tracking))
    if args.round:
        for a in (0x1A0CC4, 0x1A0CE4):
            cur = u32(a)
            if cur not in (0xEEBD0AC1, 0xEEBD0A41):
                sys.exit("ERROR: unexpected word %08X at 0x%X (expected EEBD0AC1 = vcvt.s32.f32 s0,s2)"
                         % (cur, a))
            plan.append(("round-to-nearest (vcvtr) at 0x%X" % a, a, "u", cur, 0xEEBD0A41))

    for name, a, t, old, new in plan:
        o = lay.off(a)
        if t == "f":
            struct.pack_into("<f", data, o, new)
        elif t == "i":
            struct.pack_into("<i", data, o, new)
        else:
            struct.pack_into("<I", data, o, new)
        fmt = (lambda v: "%g" % v) if t == "f" else ((lambda v: "%d" % v) if t == "i" else (lambda v: "%08X" % v))
        extra = ""
        if t == "f":
            extra = "  (word %08X)" % struct.unpack("<I", struct.pack("<f", new))[0]
        print("  0x%06X (file 0x%06X)  %-42s %s -> %s%s" % (a, o, name, fmt(old), fmt(new), extra))

    with open(args.output, "wb") as f:
        f.write(data)
    if A is not None:
        print("ratio A/B = %.4f, clamp size = %d  ->  wrote %s" % (A / B, int(A), args.output))
    else:
        print("wrote %s" % args.output)
    return 0

CAVE      = 0x505A78   # Free space in code.bin where the patch code is stored

YSHIFT    = 2.5        # Pixels the text is moved UP

TRACKING  = 0

ROUND     = True      # Round advances instead of truncating

PROFILES  = {          # name: (A, ratio, cache cell)
    "normal": (24.0, 0.55, 32),
    "x4":     (48.0, 0.27, 64),
}


def one_shot():
    import argparse, hashlib, io, os, contextlib, tempfile
    ap = argparse.ArgumentParser(description="Patch code.bin for the modded font.")
    ap.add_argument("input", help="original code.bin")
    ap.add_argument("output", nargs="?", help="patched file name (default: code_patched.bin)")
    ap.add_argument("--x4", action="store_true", help="TEST: Uses the settings for an experimental 4x font (64x64 glyph cache)")
    ap.add_argument("--yshift", type=float, default=YSHIFT)
    ap.add_argument("--round", action="store_true", default=ROUND)
    a = ap.parse_args()
    A_VALUE, RATIO, CACHE = PROFILES["x4" if a.x4 else "normal"]
    out = a.output or os.path.join(os.path.dirname(os.path.abspath(a.input)), "code_patched.bin")
    if os.path.abspath(out) == os.path.abspath(a.input):
        sys.exit("ERROR: the output must be a different file; original is left untouched.")
    raw = open(a.input, "rb").read()
    print("input : %s  (sha256 %s...)   profile: %s" % (a.input, hashlib.sha256(raw).hexdigest()[:16], "x4" if a.x4 else "normal"))
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".bin")
    tmp.close()
    quiet = io.StringIO()
    try:
        with contextlib.redirect_stdout(quiet):
            r1 = cmd_apply(argparse.Namespace(input=a.input, output=tmp.name, cave=CAVE, base=0x100000,
                                              yshift=a.yshift, A=A_VALUE, ratio=RATIO, B=None, cache=CACHE))
        if r1:
            raise SystemExit("ERROR: this code.bin cannot be patched (Wrong code.bin file)")
        with contextlib.redirect_stdout(quiet):
            r2 = cmd_params(argparse.Namespace(input=tmp.name, output=out, A=A_VALUE, ratio=RATIO, B=None,
                                               tracking=TRACKING, **{"round": a.round}))
        if r2:
            raise SystemExit("ERROR: could not write the font parameters")
    except SystemExit as e:
        print(quiet.getvalue().strip()[-700:])
        sys.exit(e.code if isinstance(e.code, str) else "ERROR: patching failed")
    finally:
        os.unlink(tmp.name)
    print("output: %s" % out)
    print("Done. Place the new code.bin in the exefs folder. Don't forget to build mod.cpk with the font upscale mod.")


if __name__ == "__main__":
    one_shot()
