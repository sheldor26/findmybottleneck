"""Everything that reaches RTSS's on-screen display.

RivaTuner Statistics Server (RTSS) is the engine behind MSI Afterburner's and
HWiNFO's overlays — it does the actual DirectX/Vulkan/OpenGL hooking, and it
publishes a small, versioned shared-memory block (``RTSSSharedMemoryV2``)
that any other process can write custom text into. Third-party tools feed
that block; none of them re-implement the hook. This does the same: it never
draws anything itself, it writes a short status string into a slot RTSS
already knows how to render.

Split the same way the rest of this project splits real I/O from reasoning
about it: the byte-level layout is read and computed by pure functions,
tested against a synthetic buffer shaped like the real one. Only
``RTSSWriter`` touches the actual OS-level shared memory, and that half is
untested here — like the rest of this project's Windows collector, it is
written against RTSS's own documented layout, not yet run against a live
RTSS instance.

Layout, offsets and version gates come from Unwinder's (Guru3D) publicly
distributed ``RTSSSharedMemory.h`` (shipped in RTSS's own install under
``.\\SDK``), cross-checked against two independently hosted mirrors and the
reference C++/CLI implementation, RTSSSharedMemoryNET. RTSS's own header
warns: "next fields should never be accessed directly, use the offsets to
access them in order to provide compatibility with future versions" — so
every slot address here is computed from the header's own offset fields at
read time, never from a hardcoded struct size.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from typing import List, Optional, Tuple

MAPPING_NAME = "RTSSSharedMemoryV2"
SIGNATURE = 0x53535452  # 'RTSS' packed little-endian as a DWORD

# The version that added szOSDEx (a 4095-character buffer). Below this, only
# the 255-character szOSD field exists.
VERSION_OSD_EX = 0x00020007
# Below this, the shared memory is the v1 single-slot layout this module does
# not understand — a v1 header read with v2 offsets is wrong, not just old.
MIN_VERSION = 0x00020000

HEADER_FORMAT = "<9I"  # 9 DWORDs: see RTSSHeader field order below
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

# Byte layout within one RTSS_SHARED_MEMORY_OSD_ENTRY, from the same header.
OSD_TEXT_OFFSET = 0        # char szOSD[256]
OSD_TEXT_MAX = 255
OSD_OWNER_OFFSET = 256      # char szOSDOwner[256]
OSD_OWNER_MAX = 255
OSD_EX_TEXT_OFFSET = 512    # char szOSDEx[4096], v2.7+
OSD_EX_TEXT_MAX = 4095

# The smallest an entry can be and still hold every field this module writes,
# per version. A header that advertises a version but too small an entry size
# for it is corrupt or lying — trusting it is exactly how a truncated write
# spills into the next slot.
MIN_ENTRY_SIZE_LEGACY = OSD_OWNER_OFFSET + OSD_OWNER_MAX + 1       # 512
MIN_ENTRY_SIZE_EX = OSD_EX_TEXT_OFFSET + OSD_EX_TEXT_MAX + 1       # 4608

# Real RTSS ships exactly 8 OSD slots. This is a generous upper bound against
# a corrupt or hostile value, not a real limit — without it, a header lying
# about an enormous array size turns find_slot's scan into a near-infinite
# loop over the same few bytes.
MAX_OSD_ARR_SIZE = 64

# The header itself grows across versions (dwBusy in 2.14, capture-stat
# fields in 2.15) — that is the entire reason RTSS hands clients an offset
# field instead of a fixed struct size, so this can't require
# osd_arr_offset == HEADER_SIZE. But it can't grow arbitrarily either: a
# header claiming the OSD array starts hundreds of bytes further out than
# any real version does is not "a newer version this module doesn't know
# about", it's a header pointing the array somewhere else entirely — exactly
# the shape that let a corrupt-but-in-mapping-bounds offset redirect a write
# from slot 1 into the middle of slot 2 without ever exceeding len(buf).
MAX_HEADER_GROWTH = 256

# Slot 0 is conventionally reserved (RTSS's own reference client starts
# scanning at index 1); this project follows the same convention rather than
# inventing its own.
FIRST_USABLE_SLOT = 1


@dataclass
class RTSSHeader:
    version: int
    app_entry_size: int
    app_arr_offset: int
    app_arr_size: int
    osd_entry_size: int
    osd_arr_offset: int
    osd_arr_size: int
    osd_frame: int


def parse_header(buf: bytes) -> Optional[RTSSHeader]:
    """Read RTSS's shared-memory header. ``None`` if the buffer is too small,
    the signature doesn't match, or the geometry it advertises is not one
    this module can safely write against — a missing or corrupt mapping is
    not a crash, the same instinct as ``trace.missing``.

    This geometry check is the one thing standing between a corrupt or
    unexpected header and a write that lands in the wrong slot: every offset
    computed later in this module assumes ``osd_entry_size`` is at least big
    enough for the fields this module writes, that ``osd_arr_size`` is a real
    slot count rather than a huge or zero value, and that ``osd_arr_offset``
    actually points at the OSD array rather than somewhere else inside the
    mapping — the last one matters because a wrong-but-in-bounds offset can
    redirect a "safe" write straight into a neighbouring slot without ever
    exceeding the mapping's real length. None of this is checked anywhere
    else, on purpose — reject bad geometry once, here, rather than guard
    every downstream offset calculation against it separately.

    The strongest of these checks is not a magic-number bound at all: RTSS's
    own layout places `arrApp` immediately after `arrOSD`, so a genuine
    header's `app_arr_offset` always equals `osd_arr_offset + osd_arr_size *
    osd_entry_size` — two independently-advertised fields that agree by
    construction in a real header. This makes a *single*-field corruption
    (any one of `osd_arr_offset`, `osd_arr_size`, `osd_entry_size` changed on
    its own) fail the check, which an offset-only slack bound cannot do.

    It does not, and cannot, prove the geometry is genuine: `/trio-auditor`
    round 4 (Codex) demonstrated a header where `osd_arr_offset`,
    `osd_arr_size` and `osd_entry_size` are corrupted *together*, chosen so
    the same equation still holds while every slot address computed from
    them is wrong — an algebraic identity checked from inside the very data
    that might be corrupt can always be satisfied by a corruption that
    changes enough of it in a coordinated way. No amount of additional
    self-consistency checking closes this; it would need a reference outside
    the shared memory itself, which this module does not have (there is no
    live RTSS to check against — see the module docstring). What this check
    does do is turn a single accidental bit-flip or partial write — the
    realistic shape of a corrupt-but-not-adversarial header, which is this
    project's actual threat model — from "silently accepted" into "rejected
    with overwhelming probability", by requiring three independent fields to
    misalign in a way that happens to still add up. `MAX_HEADER_GROWTH`
    remains as a cheap secondary bound.
    """
    if len(buf) < HEADER_SIZE:
        return None
    (signature, version, app_entry_size, app_arr_offset, app_arr_size,
     osd_entry_size, osd_arr_offset, osd_arr_size, osd_frame) = struct.unpack(
        HEADER_FORMAT, buf[:HEADER_SIZE])
    if signature != SIGNATURE:
        return None
    if version < MIN_VERSION:
        return None
    if not (HEADER_SIZE <= osd_arr_offset <= HEADER_SIZE + MAX_HEADER_GROWTH):
        return None
    if not (0 < osd_arr_size <= MAX_OSD_ARR_SIZE):
        return None
    min_entry_size = MIN_ENTRY_SIZE_EX if version >= VERSION_OSD_EX else MIN_ENTRY_SIZE_LEGACY
    if osd_entry_size < min_entry_size:
        return None
    if app_entry_size <= 0 or app_arr_size <= 0:
        return None
    if app_arr_offset != osd_arr_offset + osd_arr_size * osd_entry_size:
        return None
    return RTSSHeader(
        version=version, app_entry_size=app_entry_size,
        app_arr_offset=app_arr_offset, app_arr_size=app_arr_size,
        osd_entry_size=osd_entry_size, osd_arr_offset=osd_arr_offset,
        osd_arr_size=osd_arr_size, osd_frame=osd_frame,
    )


def _slot_base(header: RTSSHeader, slot_index: int) -> int:
    return header.osd_arr_offset + slot_index * header.osd_entry_size


def _read_cstring(buf: bytes, offset: int, max_len: int) -> str:
    raw = buf[offset:offset + max_len]
    return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace")


def find_slot(buf: bytes, header: RTSSHeader, owner: str) -> Optional[int]:
    """The slot to write to: one we already own, or else the first free one.

    Reusing our own slot across calls means a running overlay updates the
    same line instead of leaking a new claimed slot every tick.
    """
    free_slot = None
    for index in range(FIRST_USABLE_SLOT, header.osd_arr_size):
        base = _slot_base(header, index)
        if base + header.osd_entry_size > len(buf):
            break
        existing_owner = _read_cstring(buf, base + OSD_OWNER_OFFSET, OSD_OWNER_MAX)
        if existing_owner == owner:
            return index
        if not existing_owner and free_slot is None:
            free_slot = index
    return free_slot


def build_writes(header: RTSSHeader, slot_index: int, owner: str, text: str) -> List[Tuple[int, bytes]]:
    """What to write, and where. Pure — no I/O, easy to check for overflow.

    Every write is truncated to its field's real maximum before the
    terminating NUL is appended, so a long status line can never spill past
    its slot into the next one, or into RTSS's own bookkeeping.
    """
    base = _slot_base(header, slot_index)
    owner_bytes = owner.encode("ascii", errors="replace")[:OSD_OWNER_MAX] + b"\x00"
    writes = [(base + OSD_OWNER_OFFSET, owner_bytes)]

    if header.version >= VERSION_OSD_EX:
        text_bytes = text.encode("ascii", errors="replace")[:OSD_EX_TEXT_MAX] + b"\x00"
        writes.append((base + OSD_EX_TEXT_OFFSET, text_bytes))
    else:
        text_bytes = text.encode("ascii", errors="replace")[:OSD_TEXT_MAX] + b"\x00"
        writes.append((base + OSD_TEXT_OFFSET, text_bytes))

    return writes


def build_clear(header: RTSSHeader, slot_index: int) -> List[Tuple[int, bytes]]:
    """Zero a slot's owner and text — used on shutdown so a killed overlay
    doesn't leave stale text glued to someone's game."""
    return build_writes(header, slot_index, owner="", text="")


def should_clear(buf: bytes, header: RTSSHeader, slot_index: int, owner: str) -> bool:
    """Only clear a slot that is still ours, going by its current owner
    string. See `RTSSWriter`'s docstring for what this does and does not
    guarantee — it narrows the window in which shutdown can be destructive,
    it does not close it."""
    base = _slot_base(header, slot_index)
    if base + header.osd_entry_size > len(buf):
        return False
    return _read_cstring(buf, base + OSD_OWNER_OFFSET, OSD_OWNER_MAX) == owner


# Header field offset of dwOSDFrame: the 9th DWORD, 0-indexed as the 8th.
OSD_FRAME_OFFSET = 8 * 4


def bump_frame(header: RTSSHeader) -> Tuple[int, bytes]:
    """The write that actually commits a change. RTSS has no other IPC signal
    for "the OSD text changed, redraw it" — the client just increments this
    counter and RTSS's hook picks it up on its own poll."""
    return (OSD_FRAME_OFFSET, struct.pack("<I", (header.osd_frame + 1) & 0xFFFFFFFF))


def format_status(verdict, stats: dict, frame_count: int, min_frames: int = 10) -> str:
    """One short line for the OSD, built the same way report.py's headlines
    are: plain language, the number that produced it, nothing that needs a
    legend."""
    if verdict is None or not stats:
        return f"fmb: warming up... ({frame_count}/{min_frames} frames)"

    fps = stats.get("median_fps")
    low1 = stats.get("low1_fps")
    fps_text = f"{fps}fps" if fps is not None else "?fps"
    low_text = f" (1% low {low1})" if low1 is not None else ""

    if verdict.limiter == "frame-cap":
        return f"fmb: capped at {fps_text} - nothing is limiting you"
    if verdict.limiter == "gpu":
        return f"fmb: GPU-bound {round(verdict.share * 100)}% | {fps_text}{low_text}"
    if verdict.limiter == "cpu":
        return f"fmb: CPU-bound {round(verdict.share * 100)}% | {fps_text}{low_text}"
    if verdict.limiter == "stall":
        return f"fmb: stalled | {fps_text}{low_text}"
    return f"fmb: mixed | {fps_text}{low_text}"


class RTSSWriter:
    """The thin, real part. Untested against a live RTSS instance — see the
    module docstring.

    Two known, disclosed gaps, neither fully closed here: RTSS v2.14+
    documents a ``dwBusy`` spin-lock bit for writers to hold while updating
    shared memory, which this does not implement (neither of the two
    reference implementations found during research did either, and
    implementing a locking protocol from documentation alone, with no way to
    test it against a live RTSS, risks acquiring the lock and never
    releasing it — freezing the OSD for every client, a worse failure than
    the one being avoided). A torn read of a single update is possible;
    self-correcting a moment later, at the next `push()`, not a persistent
    corruption.

    And slot release has a real, if narrow, TOCTOU window: `close()` reads
    the slot's current owner (`should_clear`) and only then writes the
    clear — if another writer takes over that exact slot in between (only
    plausible in the instant after this owner's own text was last written,
    since a still-owned slot is never offered to a different owner by
    `find_slot`), `close()` would still erase it. `owner` defaults to
    `f"findmybottleneck:{os.getpid()}"` rather than a fixed string, which
    rules out two separate *processes* (two `overlay` runs against the same
    RTSS instance) colliding on the same owner — the case that would happen
    constantly rather than rarely. It does not, on its own, guarantee two
    `RTSSWriter` objects constructed *within the same process* get different
    owners (both would compute the same PID-based default) — nothing in this
    codebase currently constructs more than one per process, so this is a
    latent, not exercised, gap rather than a live one. Neither this default,
    nor anything else here, makes the read-then-write atomic against a
    different writer's process. Recorded as such in DECISIONS.md: a bounded,
    disclosed concurrency gap, not the same bucket as the rest of the
    collector's simply-unverified Windows calls.
    """

    def __init__(self, owner: Optional[str] = None):
        self.owner = owner or f"findmybottleneck:{os.getpid()}"
        self._mmap = None
        self._slot: Optional[int] = None

    def open(self) -> bool:
        import mmap
        try:
            # length=0 asks Windows to open the EXISTING named mapping at its
            # real size, rather than creating a new one — this is the one
            # behaviour in this module that can only be confirmed on a real
            # Windows machine with RTSS actually running.
            self._mmap = mmap.mmap(-1, 0, tagname=MAPPING_NAME)
        except (OSError, ValueError):
            self._mmap = None
            return False
        if parse_header(self._read()) is None:
            self._mmap.close()
            self._mmap = None
            return False
        return True

    def _read(self) -> bytes:
        self._mmap.seek(0)
        return self._mmap.read()

    def _write(self, writes: List[Tuple[int, bytes]]) -> bool:
        """Best-effort: a write that would land outside the mapping, or that
        the OS refuses, is skipped rather than allowed to crash a live
        overlay session over one bad tick."""
        ok = True
        for offset, data in writes:
            if offset < 0 or offset + len(data) > len(self._mmap):
                ok = False
                continue
            try:
                self._mmap.seek(offset)
                self._mmap.write(data)
            except (ValueError, OSError):
                ok = False
        return ok

    def push(self, text: str) -> bool:
        if self._mmap is None:
            return False
        header = parse_header(self._read())
        if header is None:
            return False
        slot = find_slot(self._read(), header, self.owner)
        if slot is None:
            return False
        self._slot = slot
        wrote = self._write(build_writes(header, slot, self.owner, text))
        self._write([bump_frame(header)])
        return wrote

    def close(self) -> None:
        if self._mmap is None:
            return
        buf = self._read()
        header = parse_header(buf)
        if header is not None and self._slot is not None and should_clear(buf, header, self._slot, self.owner):
            self._write(build_clear(header, self._slot))
            self._write([bump_frame(header)])
        self._mmap.close()
        self._mmap = None

    def __enter__(self) -> "RTSSWriter":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()
