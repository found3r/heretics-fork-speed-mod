# Copyright (C) 2026 found3r
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""
Heretic's Fork - "Ludicrous speed" mod.

Adds a 4th speed button to the in-game HUD, right of fast-forward, that runs the
game at 10.5x (3x the vanilla 3.5x fast-forward). Pause / 1x / 3.5x are unchanged.

The game is GameMaker compiled with YYC (native code), so this patches both files:

  Heretics Fork.exe
    * obj_speed_controller Create: speed_vals = [1, 3.5]  ->  [1, 3.5, 10.5]
      (speed_up / force_set_speed / button highlight all use array_length, so the
      new tier needs no other logic changes; keyboard speed-up also reaches it)
    * new room-instance PreCreate function (spd_index = 3, frame_index = 3) that
      wraps the vanilla fast-forward button's PreCreate, registered as GML code
      id 5461 by relocating the YYC function table into a new .hfmod section
      (.reloc and .pdata are moved there too, so ASLR and unwinding keep working)

  data.win  (everything new is appended to the end; only a few pointers change in place)
    * new obj_button_ff instance in rm_defend, on the UI layer, using code id 5461
    * spr_buttons_FF gets a 4th frame (triple chevron built from the FF art)
      drawn into a free spot of texture page 15

Usage:
    python hf_speedmod.py install   [game_dir]
    python hf_speedmod.py uninstall [game_dir]
    python hf_speedmod.py status    [game_dir]

Only the exact Steam build this was made for is accepted; anything else is
refused untouched. Steam "Verify integrity of game files" also fully reverts it.
"""
import bz2
import hashlib
import json
import mmap
import os
import shutil
import struct
import sys

DEFAULT_DIR = r"C:\Program Files (x86)\Steam\steamapps\common\Heretic's Fork"
EXE_NAME = "Heretics Fork.exe"
EXE_SHA256 = "9bed3e691f4c04f9e7f9de7887921f0ef0df0a3ee67b44adb82751bcf2975e26"
DATA_SIZE = 1213845386
EXE_BACKUP = EXE_NAME + ".speedmod.bak"
DATA_UNDO = "data.win.speedmod.undo.json"

NEW_SPEED = 10.5          # 3 x vanilla fast-forward (3.5)
BUTTON_X, BUTTON_Y = 493, 66   # vanilla buttons sit at x = 349 / 397 / 445, y = 66
ICON_XY = (1346, 1638)    # free 28x24 spot on texture page 15
UNLISTED_TPAG_PAGE_SHIFT = 1   # observed in-game: see plan_data()

# --- exe facts (this build only) -------------------------------------------------
IMAGE_BASE = 0x140000000
GML_TABLE = 0x141782970          # YYC {name, func, var} table, 24-byte entries
GML_COUNT = 0x1555               # 5461 entries
TABLE_LEA = 0x140C0018E          # lea rax, [rip + GML_TABLE]
COUNT_MOV = 0x140C001AB          # mov dword [rcx + 0x14], GML_COUNT
CAVE_SITE = 0x1409C1A7A          # mov r9d, 2   (arg count of the speed_vals array literal)
CAVE_RET = 0x1409C1A80
FF_PRECREATE = 0x14048BCD0       # gml_RoomCC_rm_defend_3_PreCreate (vanilla FF button)
SLOT_FRAME_INDEX = 0x1417C1AD0
SLOT_SPD_INDEX = 0x1417C1AA0
NEW_CODE_ID = GML_COUNT          # index of the appended table entry
NEW_FUNC_NAME = b"gml_RoomCC_rm_defend_hfmod_PreCreate\0"


def align(v, a):
    return (v + a - 1) // a * a


def rel32(src_next, dst):
    return struct.pack("<i", dst - src_next)


# =================================================================================
# GameMaker texture format: '2zoq' = bz2 wrapper around 'fioq' (pre-release QOI variant
# with two's-complement diff fields)
# =================================================================================
def _sx(v, bits):
    return v - (1 << bits) if v >= (1 << (bits - 1)) else v


def qoi_decode(buf):
    assert buf[:4] == b"fioq", "unexpected texture format"
    w, h, ln = struct.unpack_from("<HHI", buf, 4)
    pos, end = 12, 12 + ln
    px = bytearray(w * h * 4)
    r = g = b = 0
    a = 255
    run = 0
    index = [0] * 256
    for o in range(0, w * h * 4, 4):
        if run > 0:
            run -= 1
        elif pos < end:
            b1 = buf[pos]
            pos += 1
            if (b1 & 0xC0) == 0x00:
                i = b1 << 2
                r, g, b, a = index[i], index[i + 1], index[i + 2], index[i + 3]
            elif (b1 & 0xE0) == 0x40:
                run = b1 & 0x1F
            elif (b1 & 0xE0) == 0x60:
                run = (((b1 & 0x1F) << 8) | buf[pos]) + 32
                pos += 1
            elif (b1 & 0xC0) == 0x80:
                r = (r + _sx((b1 >> 4) & 3, 2)) & 255
                g = (g + _sx((b1 >> 2) & 3, 2)) & 255
                b = (b + _sx(b1 & 3, 2)) & 255
            elif (b1 & 0xE0) == 0xC0:
                b2 = buf[pos]
                pos += 1
                r = (r + _sx(b1 & 0x1F, 5)) & 255
                g = (g + _sx(b2 >> 4, 4)) & 255
                b = (b + _sx(b2 & 15, 4)) & 255
            elif (b1 & 0xF0) == 0xE0:
                m = (b1 << 16) | (buf[pos] << 8) | buf[pos + 1]
                pos += 2
                r = (r + _sx((m >> 15) & 31, 5)) & 255
                g = (g + _sx((m >> 10) & 31, 5)) & 255
                b = (b + _sx((m >> 5) & 31, 5)) & 255
                a = (a + _sx(m & 31, 5)) & 255
            else:
                if b1 & 8:
                    r = buf[pos]
                    pos += 1
                if b1 & 4:
                    g = buf[pos]
                    pos += 1
                if b1 & 2:
                    b = buf[pos]
                    pos += 1
                if b1 & 1:
                    a = buf[pos]
                    pos += 1
            i = ((r ^ g ^ b ^ a) & 63) << 2
            index[i], index[i + 1], index[i + 2], index[i + 3] = r, g, b, a
        px[o], px[o + 1], px[o + 2], px[o + 3] = r, g, b, a
    if pos != end:
        raise ValueError("texture decode desynced")
    return w, h, px


def qoi_encode(w, h, px):
    """Uses only INDEX / RUN / COLOR ops, which are exact and need no diff math."""
    out = bytearray()
    index = [None] * 64
    prev = (0, 0, 0, 255)
    run = 0

    def flush():
        nonlocal run
        while run > 0:
            if run <= 32:
                out.append(0x40 | (run - 1))
                run = 0
            else:
                k = min(run, 0x1FFF + 33) - 33
                out.append(0x60 | (k >> 8))
                out.append(k & 255)
                run -= k + 33

    for o in range(0, w * h * 4, 4):
        cur = (px[o], px[o + 1], px[o + 2], px[o + 3])
        if cur == prev:
            run += 1
            continue
        flush()
        hsh = (cur[0] ^ cur[1] ^ cur[2] ^ cur[3]) & 63
        if index[hsh] == cur:
            out.append(hsh)
        else:
            op = 0xF0
            extra = bytearray()
            for bit, c, p in ((8, cur[0], prev[0]), (4, cur[1], prev[1]), (2, cur[2], prev[2]), (1, cur[3], prev[3])):
                if c != p:
                    op |= bit
                    extra.append(c)
            out.append(op)
            out += extra
        index[hsh] = cur
        prev = cur
    flush()
    return b"fioq" + struct.pack("<HHI", w, h, len(out)) + bytes(out)


def build_texture(blob):
    """Return a new '2zoq' blob for page 15 with the triple-chevron icon added."""
    assert blob[:4] == b"2zoq"
    w, h, px = qoi_decode(bz2.decompress(blob[12:]))
    ix, iy = ICON_XY

    def get(x, y):
        o = (y * w + x) * 4
        return px[o:o + 4]

    # the vanilla FF icon is 22x24 at (1033, 1636); its right chevron (cols 10..21) is a full triangle
    stamp = [[get(1033 + 10 + x, 1636 + y) for x in range(12)] for y in range(24)]
    for y in range(-2, 26):            # the target area (plus padding) must be empty
        for x in range(-2, 30):
            if get(ix + x, iy + y)[3] != 0:
                raise RuntimeError("icon target area on texture page is not empty")
    for k in range(3):                 # chevrons at x = 0, 8, 16 -> 28 px wide
        for y in range(24):
            for x in range(12):
                c = stamp[y][x]
                if c[3]:
                    o = ((iy + y) * w + ix + k * 8 + x) * 4
                    px[o:o + 4] = c
    qoi = qoi_encode(w, h, px)
    assert qoi_decode(qoi)[2] == px, "texture re-encode self-check failed"
    return b"2zoq" + struct.pack("<HHI", w, h, len(qoi)) + bz2.compress(qoi, 9)


# =================================================================================
# exe
# =================================================================================
class PE:
    def __init__(self, data):
        self.d = bytearray(data)
        self.e = struct.unpack_from("<I", self.d, 0x3C)[0]
        self.opt = self.e + 24
        self.nsec = struct.unpack_from("<H", self.d, self.e + 6)[0]
        optsz = struct.unpack_from("<H", self.d, self.e + 20)[0]
        self.sec_table = self.opt + optsz
        self.secs = []
        for i in range(self.nsec):
            so = self.sec_table + 40 * i
            name = self.d[so:so + 8].rstrip(b"\0").decode()
            vs, va, rs, ra = struct.unpack_from("<IIII", self.d, so + 8)
            self.secs.append((name, va, vs, ra, rs))

    def off(self, va):
        rva = va - IMAGE_BASE
        for _, sva, vs, ra, rs in self.secs:
            if sva <= rva < sva + max(vs, rs):
                return ra + rva - sva
        raise KeyError(hex(va))

    def directory(self, i):
        return struct.unpack_from("<II", self.d, self.opt + 112 + 8 * i)

    def set_directory(self, i, rva, size):
        struct.pack_into("<II", self.d, self.opt + 112 + 8 * i, rva, size)


def patch_exe(orig):
    pe = PE(orig)
    if any(s[0] == ".hfmod" for s in pe.secs):
        raise RuntimeError("exe already patched")
    size_of_image = struct.unpack_from("<I", pe.d, pe.opt + 56)[0]
    sec_rva = size_of_image
    S = IMAGE_BASE + sec_rva
    raw_ptr = len(pe.d)
    assert raw_ptr % 0x200 == 0 and pe.secs[-1][3] + pe.secs[-1][4] == raw_ptr, "unexpected exe layout"

    def expect(va, data):
        o = pe.off(va)
        if bytes(pe.d[o:o + len(data)]) != data:
            raise RuntimeError(f"unexpected bytes at {va:#x}; wrong game build?")

    expect(CAVE_SITE, bytes.fromhex("41b902000000"))
    expect(COUNT_MOV, bytes.fromhex("c74114") + struct.pack("<I", GML_COUNT))
    expect(TABLE_LEA, bytes.fromhex("488d05") + rel32(TABLE_LEA + 7, GML_TABLE))

    sec = bytearray(0x200)
    relocs = []                       # RVAs holding absolute 64-bit pointers

    # --- 0x000: PreCreate wrapper: vanilla FF button setup, then spd_index = frame_index = 3
    f = S + 0x000
    code = bytearray(b"\x53\x48\x83\xec\x20\x48\x89\xcb")      # push rbx; sub rsp,20h; mov rbx,rcx
    code += b"\xe8" + rel32(f + len(code) + 5, FF_PRECREATE)     # call FF PreCreate(self, other)
    three = struct.pack("<d", 3.0)
    for slot in (SLOT_FRAME_INDEX, SLOT_SPD_INDEX):
        code += b"\x48\x8b\x03"                                  # mov rax,[rbx]
        code += b"\x8b\x15" + rel32(f + len(code) + 6, slot)     # mov edx,[rip+slot]
        code += b"\x48\x89\xd9\xff\x50\x10"                      # mov rcx,rbx; call [rax+10h]  (var ref for write)
        code += b"\xc7\x40\x0c\x00\x00\x00\x00"                  # kind = REAL (old value was REAL 2.0)
        code += b"\x48\xb9" + three + b"\x48\x89\x08"            # mov rcx,3.0; mov [rax],rcx
    code += b"\x48\x83\xc4\x20\x5b\xc3"                          # add rsp,20h; pop rbx; ret
    func_len = len(code)
    sec[0:func_len] = code

    # --- 0x080: cave: speed_vals literal gets a 3rd element
    c = S + 0x080
    rv = S + 0x0C0
    cave = bytearray(b"\x41\xb9\x03\x00\x00\x00")                # mov r9d,3
    cave += b"\x48\x8d\x05" + rel32(c + len(cave) + 7, rv)       # lea rax,[rip+rv]
    cave += b"\x48\x89\x45\xf7"                                  # mov [rbp-9],rax   (args[2])
    cave += b"\xe9" + rel32(c + len(cave) + 5, CAVE_RET)         # jmp back
    sec[0x80:0x80 + len(cave)] = cave

    # --- 0x0C0: RValue{10.5, flags 0, kind REAL}
    struct.pack_into("<dII", sec, 0xC0, NEW_SPEED, 0, 0)
    # --- 0x0D0: YYVAR for the new function {name, id}; ids are 100000 + index
    struct.pack_into("<QQ", sec, 0xD0, S + 0xE0, 100000 + NEW_CODE_ID)
    relocs.append(sec_rva + 0xD0)
    sec[0xE0:0xE0 + len(NEW_FUNC_NAME)] = NEW_FUNC_NAME
    # --- 0x120: UNWIND_INFO for the wrapper (push rbx @1, sub rsp,20h @5)
    sec[0x120:0x128] = bytes([0x01, 0x05, 0x02, 0x00, 0x05, 0x32, 0x01, 0x30])

    # --- 0x200: relocated GML function table + new entry
    tab_off = len(sec)
    t = pe.off(GML_TABLE)
    sec += pe.d[t:t + 24 * GML_COUNT]
    sec += struct.pack("<QQQ", S + 0xE0, f, S + 0xD0)
    for i in range(GML_COUNT + 1):
        for k in (0, 8, 16):
            relocs.append(sec_rva + tab_off + 24 * i + k)

    # --- exception directory (.pdata) + entry for the wrapper
    sec += b"\0" * (align(len(sec), 16) - len(sec))
    pdata_off = len(sec)
    prva, psz = pe.directory(3)
    po = pe.off(IMAGE_BASE + prva)
    sec += pe.d[po:po + psz]
    sec += struct.pack("<III", sec_rva, sec_rva + func_len, sec_rva + 0x120)
    pdata_size = psz + 12

    # --- base relocations: original blocks + ours
    sec += b"\0" * (align(len(sec), 16) - len(sec))
    reloc_off = len(sec)
    rrva, rsz = pe.directory(5)
    ro = pe.off(IMAGE_BASE + rrva)
    sec += pe.d[ro:ro + rsz]
    pages = {}
    for r in relocs:
        pages.setdefault(r & ~0xFFF, []).append(r & 0xFFF)
    for page in sorted(pages):
        ents = [0xA000 | o for o in sorted(pages[page])]
        if len(ents) % 2:
            ents.append(0)
        sec += struct.pack("<II", page, 8 + 2 * len(ents)) + struct.pack(f"<{len(ents)}H", *ents)
    reloc_size = len(sec) - reloc_off

    vsize = len(sec)
    sec += b"\0" * (align(len(sec), 0x200) - len(sec))

    # --- headers
    if pe.sec_table + 40 * (pe.nsec + 1) > struct.unpack_from("<I", pe.d, pe.opt + 60)[0]:
        raise RuntimeError("no room for a new section header")
    sh = struct.pack("<8sIIIIIIHHI", b".hfmod", vsize, sec_rva, len(sec), raw_ptr, 0, 0, 0, 0, 0xE0000060)
    o = pe.sec_table + 40 * pe.nsec
    pe.d[o:o + 40] = sh
    struct.pack_into("<H", pe.d, pe.e + 6, pe.nsec + 1)
    struct.pack_into("<I", pe.d, pe.opt + 56, align(sec_rva + vsize, 0x1000))
    pe.set_directory(3, sec_rva + pdata_off, pdata_size)
    pe.set_directory(5, sec_rva + reloc_off, reloc_size)

    # --- code patches
    o = pe.off(CAVE_SITE)
    pe.d[o:o + 6] = b"\xe9" + rel32(CAVE_SITE + 5, c) + b"\x90"
    o = pe.off(TABLE_LEA)
    pe.d[o + 3:o + 7] = rel32(TABLE_LEA + 7, S + tab_off)
    o = pe.off(COUNT_MOV)
    pe.d[o + 3:o + 7] = struct.pack("<I", GML_COUNT + 1)

    return bytes(pe.d + sec)


# =================================================================================
# data.win
# =================================================================================
class DataWin:
    def __init__(self, path):
        self.f = open(path, "r+b")
        self.m = mmap.mmap(self.f.fileno(), 0)
        self.chunks = {}
        self.order = []
        pos, size = 8, self.u32(4)
        while pos < size + 8:
            name = self.m[pos:pos + 4].decode()
            ln = self.u32(pos + 4)
            self.chunks[name] = (pos, ln)
            self.order.append(name)
            pos += 8 + ln

    def u32(self, o):
        return struct.unpack_from("<I", self.m, o)[0]

    def i32(self, o):
        return struct.unpack_from("<i", self.m, o)[0]

    def string(self, p):
        return self.m[p:p + self.u32(p - 4)].decode("utf-8", "replace") if p else None

    def plist(self, o):
        return [self.u32(o + 4 + 4 * i) for i in range(self.u32(o))]

    def chunk_list(self, name):
        return self.plist(self.chunks[name][0] + 8)

    def close(self):
        self.m.flush()
        self.m.close()
        self.f.close()


def plan_data(dw):
    """Locate everything and build the bytes to append + in-place pointer writes."""
    end = len(dw.m)
    if end != DATA_SIZE or dw.order[-1] != "AUDO":
        raise RuntimeError("unexpected data.win (size/layout); wrong game build or already patched?")
    gen8 = dw.chunks["GEN8"][0] + 8
    last_obj = dw.u32(gen8 + 12)

    objs = dw.chunk_list("OBJT")
    ff_obj = next(i for i, p in enumerate(objs) if dw.string(dw.u32(p)) == "obj_button_ff")
    sprite_idx = dw.i32(objs[ff_obj] + 4)

    rooms = dw.chunk_list("ROOM")
    room = next(p for p in rooms if dw.string(dw.u32(p)) == "rm_defend")
    inst_list = dw.u32(room + 12 * 4)
    insts = dw.plist(inst_list)
    tmpl = next(p for p in insts if dw.i32(p + 8) == ff_obj and dw.i32(p + 44) == 2202)
    tmpl_id = dw.u32(tmpl + 12)

    used_ids = set()
    for r in rooms:
        for p in dw.plist(dw.u32(r + 12 * 4)):
            used_ids.add(dw.u32(p + 12))
    new_id = last_obj
    if new_id in used_ids:
        raise RuntimeError("instance id collision")

    layer_list = dw.u32(room + 23 * 4)
    layers = dw.plist(layer_list)
    ui_slot, ui_layer = next((i, p) for i, p in enumerate(layers)
                             if dw.u32(p + 8) == 2 and tmpl_id in [dw.u32(p + 52 + 4 * k) for k in range(dw.u32(p + 48))])
    if dw.u32(ui_layer + 44) != 0:       # effect property count; non-zero would shift the id list
        raise RuntimeError("unexpected UI layer layout")
    n_ids = dw.u32(ui_layer + 48)
    ids = [dw.u32(ui_layer + 52 + 4 * k) for k in range(n_ids)]

    sprites = dw.chunk_list("SPRT")
    spr = sprites[sprite_idx]
    if dw.string(dw.u32(spr)) != "spr_buttons_FF" or dw.u32(spr + 21 * 4) != 3:
        raise RuntimeError("unexpected button sprite")
    frames = [dw.u32(spr + 4 * (22 + k)) for k in range(3)]
    tpag = struct.unpack_from("<11H", dw.m, frames[2])
    if tpag != (1033, 1636, 22, 24, 5, 4, 22, 24, 32, 32, 15):
        raise RuntimeError("unexpected FF icon")
    spr_tail = dw.u32(spr + 25 * 4)      # mask count after the frame list
    if spr_tail != 0:
        raise RuntimeError("unexpected sprite mask data")

    textures = dw.chunk_list("TXTR")
    tex = textures[15]
    tex_len, tex_ptr = dw.u32(tex + 8), dw.u32(tex + 24)

    print("  rebuilding texture page 15 (takes a few seconds)...")
    new_tex = build_texture(dw.m[tex_ptr:tex_ptr + tex_len])

    # ---- build the appended block
    base = align(end, 16)
    blob = bytearray(base - end)

    def here():
        return end + len(blob)

    def put(b, al=4):
        blob.extend(b"\0" * (align(here(), al) - here()))
        p = here()
        blob.extend(b)
        return p

    inst = bytearray(dw.m[tmpl:tmpl + 48])
    struct.pack_into("<ii", inst, 0, BUTTON_X, BUTTON_Y)
    struct.pack_into("<I", inst, 12, new_id)
    struct.pack_into("<i", inst, 44, NEW_CODE_ID)
    new_inst = put(inst)
    new_inst_list = put(struct.pack(f"<I{len(insts) + 1}I", len(insts) + 1, *insts, new_inst))

    pos = ids.index(tmpl_id) + 1
    new_ids = ids[:pos] + [new_id] + ids[pos:]
    layer_hdr = bytes(dw.m[ui_layer:ui_layer + 48])
    new_layer = put(layer_hdr + struct.pack(f"<I{len(new_ids)}I", len(new_ids), *new_ids))

    sx, sy = ICON_XY
    # The runner shifts the page id of every entry in the TPAG list at load time; ours is not
    # in that list, so a raw 15 is drawn from page 14. 16 lands on page 15.
    new_tpag = put(struct.pack("<11H", sx, sy, 28, 24, 2, 4, 28, 24, 32, 32, 15 + UNLISTED_TPAG_PAGE_SHIFT))
    spr_hdr = bytes(dw.m[spr:spr + 21 * 4])
    new_spr = put(spr_hdr + struct.pack("<I4II", 4, *frames, new_tpag, 0))

    new_tex_ptr = put(new_tex, 128)
    blob.extend(b"\0" * (align(here(), 16) - here()))

    audo_hdr = dw.chunks["AUDO"][0]
    writes = [
        (4, struct.pack("<I", dw.u32(4) + len(blob))),                         # FORM size
        (audo_hdr + 4, struct.pack("<I", dw.chunks["AUDO"][1] + len(blob))),   # AUDO grows over the tail
        (gen8 + 12, struct.pack("<I", last_obj + 1)),                          # GEN8.LastObj
        (room + 12 * 4, struct.pack("<I", new_inst_list)),
        (layer_list + 4 + 4 * ui_slot, struct.pack("<I", new_layer)),
        (dw.chunks["SPRT"][0] + 8 + 4 + 4 * sprite_idx, struct.pack("<I", new_spr)),
        (tex + 8, struct.pack("<I", len(new_tex))),
        (tex + 24, struct.pack("<I", new_tex_ptr)),
    ]
    return bytes(blob), writes


# =================================================================================
# commands
# =================================================================================
def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def install(game):
    exe, data = os.path.join(game, EXE_NAME), os.path.join(game, "data.win")
    if os.path.exists(os.path.join(game, DATA_UNDO)) or os.path.exists(os.path.join(game, EXE_BACKUP)):
        raise SystemExit("Already installed (backup files exist). Run 'uninstall' first.")
    if sha256(exe) != EXE_SHA256:
        raise SystemExit("Heretics Fork.exe is not the build this mod was made for. Nothing changed.")

    print("Patching exe in memory...")
    new_exe = patch_exe(open(exe, "rb").read())

    print("Planning data.win changes...")
    dw = DataWin(data)
    try:
        blob, writes = plan_data(dw)
        undo = {"size": len(dw.m), "writes": [[o, dw.m[o:o + len(b)].hex()] for o, b in writes]}
    finally:
        dw.close()

    print("Writing backups...")
    shutil.copy2(exe, os.path.join(game, EXE_BACKUP))
    with open(os.path.join(game, DATA_UNDO), "w") as f:
        json.dump(undo, f)

    print("Writing data.win...")
    with open(data, "r+b") as f:
        f.seek(undo["size"])
        f.write(blob)
        for o, b in writes:
            f.seek(o)
            f.write(b)
    print("Writing exe...")
    with open(exe, "wb") as f:
        f.write(new_exe)
    print(f"Installed: new {NEW_SPEED:g}x button added right of fast-forward.")


def uninstall(game):
    exe, data = os.path.join(game, EXE_NAME), os.path.join(game, "data.win")
    bak, undo_path = os.path.join(game, EXE_BACKUP), os.path.join(game, DATA_UNDO)
    if not (os.path.exists(bak) and os.path.exists(undo_path)):
        raise SystemExit("Not installed (no backup files found).")
    if sha256(bak) != EXE_SHA256:
        raise SystemExit("Exe backup does not match the expected original; use Steam 'Verify integrity'.")
    undo = json.load(open(undo_path))
    with open(data, "r+b") as f:
        for o, h in undo["writes"]:
            f.seek(o)
            f.write(bytes.fromhex(h))
        f.truncate(undo["size"])
    shutil.copy2(bak, exe)
    os.remove(bak)
    os.remove(undo_path)
    print("Uninstalled; original files restored.")


def status(game):
    installed = os.path.exists(os.path.join(game, DATA_UNDO))
    print("installed" if installed else "not installed")


def find_game_dir():
    """Steam can keep games in any library folder, so read them all from libraryfolders.vdf."""
    roots = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            roots.append(winreg.QueryValueEx(k, "SteamPath")[0])
    except OSError:
        pass
    roots.append(os.path.dirname(os.path.dirname(os.path.dirname(DEFAULT_DIR))))
    libraries = []
    for root in roots:
        libraries.append(root)
        vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
        if os.path.exists(vdf):
            for line in open(vdf, encoding="utf-8", errors="replace"):
                parts = line.strip().split('"')
                if len(parts) >= 4 and parts[1] == "path":
                    libraries.append(parts[3].replace("\\\\", "\\"))
    for lib in libraries:
        game = os.path.join(lib, "steamapps", "common", "Heretic's Fork")
        if os.path.exists(os.path.join(game, EXE_NAME)):
            return os.path.normpath(game)
    raise SystemExit("Could not find Heretic's Fork in your Steam libraries.\n"
                     "Pass the game folder explicitly, e.g.:  hf_speedmod.py install \"D:\\Games\\Heretic's Fork\"")


def game_running():
    try:
        import subprocess
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {EXE_NAME}", "/NH"],
                             capture_output=True, text=True).stdout
        return EXE_NAME.lower() in out.lower()
    except OSError:
        return False


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("install", "uninstall", "status"):
        print(__doc__)
        raise SystemExit(2)
    game = sys.argv[2] if len(sys.argv) > 2 else find_game_dir()
    print(f"Game folder: {game}")
    if sys.argv[1] != "status" and game_running():
        raise SystemExit("Heretic's Fork is running. Close the game and try again.")
    try:
        {"install": install, "uninstall": uninstall, "status": status}[sys.argv[1]](game)
    except PermissionError as e:
        raise SystemExit(f"No permission to write game files ({e.filename}).\n"
                         "Right-click the .bat file and choose 'Run as administrator'.")


if __name__ == "__main__":
    main()
