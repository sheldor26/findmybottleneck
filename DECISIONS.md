# Decisions

> Architecture decisions, lightweight. One entry per choice that would be
> expensive to reverse, or that a future reader would otherwise second-guess.
>
> The point is not the decision — it is the *context*, so that when the context
> changes the decision can be revisited honestly. Newest first.
>
> Add entries with: `node .bitacora/cli.mjs new decision "Title" --tags area`

<!-- bitacora:entry
id: D-0020
date: 2026-09-21
tags: [gui, collect, engine]
-->
### Ship 8 of the 12 approved companion-tool ideas; skip 4 for real reasons, not just less rigor

**Context.** Juan asked for 20 companion-tool ideas, then approved building 14 of them (excluding
a VRR detector with no Windows API and a shareable-card feature needing
Pillow, both declined upfront), accepting less test rigor than the rest of
this session in exchange for speed. Working through them individually
surfaced that "less rigor" and "build it anyway" are not always compatible
— two ideas hit a hard floor this project does not cross regardless of
time pressure.

**Decision.** Shipped, each with real (if lighter than usual) verification: **config_audit()** +
**disk_health()** (power plan, HAGS, Game Mode, Memory Integrity/HVCI via
registry+CIM, `Get-PhysicalDisk` SMART health) — new `CheckItem`-shaped
probes, printed as a second, clearly separate section in `check()` and the
GUI's Check page so a "flag" never touches the tool-availability pass/fail
logic `check_status()` already owns. **`--notes`** on `capture` and a
`Trace.notes` field, shown in both report renderers. A **compare wizard**
on the GUI's Capture page (Save as baseline / Compare to baseline buttons)
— pure UI, reusing `engine.compare.compare()` exactly as `findmybottleneck
compare` already does. **`engine/history.py`** (new module): scans a
folder of trace JSON for `trend_note` (1% low, earlier half vs later half),
`thermal_trend_note` (GPU temp rise, hedged as "not proof of anything on
its own"), and `os_change_note` (anchored to an actual recorded
`Hardware.os_build` transition, not an arbitrary split) — a new
`findmybottleneck history <folder>` command. **Per-process GPU Engine
counters** (D-0005-consistent: `typeperf -q` asks whether
`\GPU Engine(*)\Utilization Percentage` exists before ever adding it to
the real capture command, since one bad counter path can fail the whole
typeperf invocation): a new `background_gpu` finding names a non-target
process using a real share of the 3D engine, flowing through the existing
`report.findings` pipeline into both renderers for free. **`overlay
--log <path>`**: the same rolling verdict `overlay` already computes,
appended to a file with a timestamp — so a stutter noticed mid-match can
be looked up after the fact.

Two ideas were skipped with a stated reason, not silently dropped: (1) **"what upgrade helps"** —
already substantially covered by `engine/frames.py`'s existing verdict fix
text (GPU-bound → "buy a faster card"; CPU-bound → deliberately steers to
memory configuration *instead of* "buy a CPU", the project's own
documented stance on the most common real cause). Building a new "buy
this" calculator on top would have contradicted that already-reasoned
design and `report.py`'s own closing disclaimer ("does not know whether
the component is worth replacing"). (2) **Clip mode** (auto-capture only
near a frame-rate drop) — turned out to already be covered: D-0015's
`--seconds 0` already records an entire session, and `engine/hitch.py`
already finds and attributes every hitch in it retroactively; building a
trigger-based recorder would have been new infrastructure duplicating
what unbounded capture plus existing hitch detection already deliver.

**Consequences.** Eight real, working additions, each independently compiled/tested (73
tests green throughout) and several smoke-tested with synthetic data
(`_parse_gpu_engine`'s PID/engtype regex, `background_gpu`'s filtering,
`engine/history.py`'s trend/thermal/build-change detectors) since none of
this session's new collection logic has unit-test coverage yet — matching
this project's existing boundary (parsers are tested against synthetic
text, subprocess orchestration is not) rather than a gap unique to this
batch. `hardware()` gained one more PowerShell call (`Win32_OperatingSystem`
for `os_build`) on top of D-0019's just-completed trim — deliberately, since
unlike motherboard/RAM-slot data, `os_build` has to be on *every* capture
for `os_change_note` to ever detect a transition; this is the "genuinely
needed every time" case D-0019's fix was drawing a line around, not a
regression of it. Nothing here has run against real hardware — same
disclosed-gap shape as the rest of this session's Windows-only work.

<!-- bitacora:entry
id: D-0019
date: 2026-09-21
tags: [collect, gui]
-->
### Split motherboard/RAM-slot reads out of hardware() so capture never pays their cost

**Context.** A `/code-review` this session (medium effort, 8 finder angles) flagged that D-0018's
two new CIM queries (`Win32_BaseBoard`, `Win32_PhysicalMemoryArray`) were
added directly inside `hardware()`, which `run_capture()` calls on every
capture — not only from the System page that actually wants motherboard/
RAM-slot data. Confirmed by reading the code, not just trusting the
finder: `run_capture` calls `hw, hw_missing = hardware(); missing.update
(hw_missing)`, so every `findmybottleneck capture` was paying two extra
PowerShell spawns (~100-300ms each) for data it never displays, and a
probe failure there ("motherboard identity did not answer") would show up
in that capture's own `Trace.missing` → Report "Not measured" section,
next to genuinely capture-relevant gaps like frame attribution — noise
unrelated to diagnosing what set a game's pace.

**Decision.** Moved both queries out of `hardware()` into a new `motherboard_status()`
(`collect/windows.py`), returning `(motherboard, memory_slots_total,
memory_max_capacity_gb, missing)` — the same "one reading, for the System
page, not tied to a capture" shape `gpu_status()`/`cpu_temperature()`
already established this session. The GUI's `_on_read_system` calls it
alongside `hardware()`/`gpu_status()`/`cpu_temperature()` and assigns the
three fields onto the `Hardware` object it already has, so
`_show_system_results` needed no changes — it still just reads
`hw.motherboard`/`hw.memory_slots_total`/`hw.memory_max_capacity_gb`.

**Consequences.** `capture` is back to exactly the PowerShell call count it had before D-0018
(3, not 5), and a capture's "Not measured" section can no longer contain
motherboard/RAM-slot-layout gaps that have nothing to do with a game's
bottleneck. The cost: `Hardware`'s three upgrade-guidance fields are now
populated by two different functions instead of one, so a future caller
that wants a fully-populated `Hardware` for some other purpose has to
remember to call both `hardware()` and `motherboard_status()` and merge
them — acceptable today since the only caller that wants both is the
System page, which already does.

<!-- bitacora:entry
id: D-0018
date: 2026-09-21
tags: [gui, collect]
-->
### Show upgrade-relevant hardware detail (motherboard, free RAM slots, exact part) without inventing what Windows can't read

**Context.** Juan's ask was concrete: someone who wants to add a stick of RAM to a second
slot should be told what to buy to match, and what the board can take. The
tempting version of this — "your motherboard supports up to N MHz, buy
that" — cannot be built honestly: no WMI class, and no Windows API at all,
reports a motherboard's maximum *supported speed*. That number lives only
in the manufacturer's QVL page or the board's manual. Fabricating it, or
inferring it from the currently-installed speed, would violate this
project's own non-negotiable against invented data and its whole
`report.py`/`report_view.py` convention of naming what was not measured
rather than guessing.

**Decision.** `hardware()` gained three real, WMI-backed reads instead: `Win32_BaseBoard`
(manufacturer + product → `Hardware.motherboard`, so the user can look up
that exact board's QVL themselves), `Win32_PhysicalMemoryArray`
(`MemoryDevices` → `memory_slots_total`, `MaxCapacity` →
`memory_max_capacity_gb` — the largest *total* the board supports, a real
field, not the same claim as "fastest speed supported"), and a `Capacity`
column added to the existing `Win32_PhysicalMemory` query
(`capacity_gb` per module). The System page turns slots-total minus
modules-installed into "N free — room to add a module" (or "no free slots
— replacing, not adding"), and prints each module's exact manufacturer/part
number as what to buy to match. A new italic line states plainly that
maximum supported RAM speed cannot be read here and points at the board's
own QVL/manual — the same "not measured" honesty the Report tab already
has, extended to a page most people will read specifically to go shopping.

**Consequences.** The one scenario Juan asked about — "can I add a stick, and which one" — is
now answerable from data this project actually has, with no new
dependency (three more CIM queries, same pattern as everything else in
`hardware()`). The line this deliberately does not cross: findmybottleneck
still will not tell anyone what speed to buy, only what they already have
and how many slots are open — a real product-scope decision, not a gap to
quietly fill later with a hardcoded motherboard-spec database (a
meaningfully bigger, separately-decided feature: sourcing and maintaining
compatibility data for motherboards this project cannot verify against
real hardware). Untested against a real motherboard, same disclosed-gap
shape as the rest of this session's Windows-only work.

<!-- bitacora:entry
id: D-0017
date: 2026-09-21
tags: [collect, dependency]
-->
### CPU temperature via LibreHardwareMonitor's Python binding, as an opt-in sensors extra

**Context.** Juan asked for CPU temperature on the System page. Unlike everything else this
project reads, there is no command-line tool already on Windows that
exposes it (D-0005's whole pattern — shell out to what's there — has
nothing to shell out to here). The real options were: build and maintain
our own kernel-mode driver (ruled out in conversation — needs an EV
code-signing certificate, harder to qualify for as an individual than a
registered company, plus ongoing Windows Hardware Dev Center submission,
plus fighting Virtualization-Based Security on modern Windows, for a
project this size); or depend on LibreHardwareMonitor, which already
maintains its own signed driver. Discovered mid-conversation: `HardwareMonitor`
(PyPI, BSD-3-Clause, github.com/snip3rnick/PyHardwareMonitor) is a thin
pre-built Python layer over `LibreHardwareMonitorLib` via `pythonnet` —
no C# to write or compile ourselves, just a pip install.

**Decision.** New optional extra, `pip install findmybottleneck[sensors]` (`HardwareMonitor`,
which pulls `pythonnet`) — separate from `[gui]`, so installing one never
pulls the other. `collect.windows.cpu_temperature()` opens a
`HardwareMonitor.Hardware.Computer` with only `IsCpuEnabled = True` (not
motherboard/GPU/etc — GPU already comes from nvidia-smi, and enabling more
than needed only enlarges what can go wrong), reads every `Temperature`
sensor under the CPU hardware node, and prefers whichever reads as
"Package"/"Tctl"/"Tdie" over a per-core maximum. Not called from
`hardware()` or `check()` — capture and check must keep working without
Administrator, which this needs (LibreHardwareMonitor's own driver, not
one this project ships or signs); it is a fifth caller pattern, only from
the GUI's System page, opt-in in both the install and the privilege it
asks for. The import and the sensor read are both wrapped in a broad
`except Exception`, not just `ImportError` — see M-0004, found while
verifying this on a machine with the package installed but no .NET
runtime, which is exactly the "installed but broken" case a normal
import-only try/except misses.

**Consequences.** CPU temperature becomes readable without anyone hand-writing or signing a
driver, and the dependency is genuinely optional — nothing about capture,
check, overlay, or the GUI's other three pages changes for someone who
never installs `[sensors]`. The cost: this is the project's first
dependency that is not pure Python and not "a tool already on the target
machine" — it needs .NET Framework 4.7+ present (usually true on Windows
10/11, not guaranteed), and it needs Administrator, which nothing else in
this project has ever required. Untested against real hardware (same
disclosed-gap shape as the rest of this session): confirmed on macOS only
that the import/interop failure path degrades to `missing` instead of
crashing, never that a real Windows machine with Administrator actually
returns a CPU temperature.

<!-- bitacora:entry
id: D-0016
date: 2026-09-21
tags: [gui]
-->
### Add a System tab that calls hardware() standalone, not only inside a capture

**Context.** Juan asked for a tab showing PC component details. `collect.windows.hardware()`
already reads exactly that — GPU name/driver, CPU name/core counts, RAM
module speeds/manufacturer/channels, OS — but it had only ever been called
as a side effect of `run_capture`, buried inside a 30-second recording.

**Decision.** New "System" page in the sidebar (Check → System → Capture → Report), calling
`hardware()` directly on a button press, on the same background-thread +
`self.after(0, ...)` pattern `_on_check` already uses. No new collection
code: `hardware()`'s signature and return shape (`Tuple[Hardware, Dict[str,
str]]`) were untouched, so the GUI is just a fourth caller of a function
that already existed for a different reason. Grouped into GPU/CPU/Memory/OS
sections with a "Not read" section underneath for whatever `hardware()`'s
own `missing` dict reports, mirroring the Check tab's ok/miss language
rather than inventing a new one. Disabled on non-Windows with the same
"Needs Windows" pattern Capture already uses, since `hardware()` shells out
to nvidia-smi and Windows CIM classes.

**Consequences.** No engine or collector changes — this is presentation
only, reusing `hardware()` exactly as `run_capture` already calls it, so a
future change to what hardware is read (a new field, a new probe) reaches
both callers for free. The cost: `hardware()` now runs standalone, outside
a capture, which was never exercised before — its own probes (nvidia-smi,
two PowerShell CIM queries) are unit-tested for parsing but not for
"running twice in one session back to back," which is now a real usage
pattern this adds and has not been verified against real hardware
(same disclosed-gap shape as the rest of this session's GUI work).

<!-- bitacora:entry
id: D-0015
date: 2026-09-21
tags: [capture, gui]
-->
### Capture until the target process exits instead of a fixed window

**Context.** `capture` has always recorded a fixed `--seconds` window (30s
by default). That is enough to name what set the pace right now, but the
user's own framing was: the longer you sample, the steadier the 1% low,
the throttle checks, and the hitch breakdown get — and asking someone to
guess how many seconds their play session will last, upfront, is the wrong
question. D-0014 (this session, earlier) already added the machinery this
needs: `_process_running`/`_wait_for_target` to poll `tasklist` for a named
process, already used to wait for a game to *start*. The same primitive
answers "has it *stopped*".

**Decision.** `--seconds 0` (CLI) or an empty/`0` Seconds field (GUI) means
no fixed window: `run_capture` polls `_process_running(target)` every 0.5s
and stops recording when the game closes, instead of racing a clock. This
needs a target process — it falls back to a 30s capture with a `missing`
entry if neither `process` nor `launch` is given, since there is nothing to
detect the end of otherwise. The three collectors adapt the same way:
`nvidia-smi` already polls until killed regardless of duration; `typeperf`
drops its `-sc <count>` argument (a fixed sample count) so it logs until
terminated; PresentMon drops `--timed`/`--terminate_after_timed` so it
keeps writing until this process explicitly terminates it, mirroring how
the other two workers are already stopped. `duration_s` in the resulting
trace becomes the actual measured elapsed time, not the requested one — the
distinction `report.py`/the engine already handle for `missing`, extended
the same way here (say what actually happened, not what was asked for).
Same session, this also closed a related but separate GUI-only bug
(M-0003): the GUI wasn't wiring `run_capture`'s `on_wait` callback at all,
so it showed "recording" during the wait-for-the-game-to-appear phase; that
fix, and adding a "Launch" `.exe` picker to the Capture tab (the GUI half
of D-0014's `--launch`, which only the CLI had until now), landed in the
same pass as this feature because all three touch the same capture flow
end to end.

**Consequences.** A long capture is now one command instead of a guess at
`--seconds`, and the statistics engine already reasons over "however many
frames there are" rather than assuming exactly 30s of them, so nothing in
`engine/` needed to change. The cost: an hour-long session at `typeperf`'s
1s interval and PresentMon's per-frame rate is tens of thousands of CSV
rows — untested against a real multi-hour run, same disclosed-gap pattern
as the rest of this session's work (see **In flight** in STATE.md). There
is deliberately no safety cap on how long unbounded mode can run — it stops
when the game does, full stop; if a runaway capture from a game left open
for days turns out to be a real problem, that's a new, separately-decided
cap, not assumed here.

<!-- bitacora:entry
id: D-0014
date: 2026-09-21
tags: [collect, capture]
-->
### Wait for the target process before recording, and let capture launch it

**Context.** Juan asked for `capture` to start recording automatically once it detects the
game is open, and to optionally launch the game itself so that is guaranteed
rather than hoped for. Before this, `capture <game.exe>` started its 30s
countdown the instant it ran — timing it against alt-tabbing into a loading
game, or typing the command a beat too early, produced a trace with an
empty or near-empty frame set and no real signal about why.

**Decision.** `run_capture` gained two independent, composable additions, both built on the
GUI's existing `tasklist`-shelling pattern (D-0005) rather than a new
dependency (`psutil` would be the obvious alternative — rejected under
CLAUDE.md's "no new dependency without asking" for something `tasklist`
already answers): (1) an optional `launch` path, `Popen`'d before recording,
with the target process name defaulted from its filename when `process`
isn't given explicitly; (2) whenever a target name is known, recording waits
(polling once a second, `--wait-timeout`, default 120s) for that name to
actually appear in the process list before starting the timed capture,
instead of starting blind. If the process never appears in time, `capture`
does not abort — it proceeds exactly as it already does when PresentMon or
the target is missing, recording hardware/counters and marking frame
attribution as `missing` with the reason, honest rather than a hard failure.
The wait is skipped entirely when there is no PresentMon to attribute frames
with anyway (waiting buys nothing in that case) or when `--wait-timeout 0` is
passed.

**Consequences.** `findmybottleneck capture cs2.exe` now works whether cs2.exe is already
running or about to be started by hand, and `--launch <path>` covers the
"make sure it's running" case directly. The cost: `--launch` only starts the
process and does not manage its lifecycle (it is not terminated after the
capture, and a launcher stub — Steam launching a different real game
process — is not resolved automatically; the caller can still pass an
explicit `process` name to attribute frames to the real child process while
`--launch`ing the stub). The wait loop adds up to `--wait-timeout` seconds of
real wall-clock time before recording starts, which `capture`'s own status
line reports so it never looks hung. `run_capture` is shared with the GUI
(D-0013); the GUI does not pass `launch` or `on_wait` yet, so its existing
flow (pick an already-running process from the dropdown) is unaffected — the
wait resolves on the very first check when the process is already up.

<!-- bitacora:entry
id: D-0013
date: 2026-09-21
tags: [design]
-->
### Add a desktop GUI, opt-in, on top of the same functions the CLI already calls

**Context.** Juan asked for a modern interface — buttons with hover, intuitive, not the
terminal. Asked to clarify the format (a browser report, a web dashboard, or
a native app) and then the framework, he chose a native desktop window built
with CustomTkinter: modern flat look and hover states out of the box, a
small dependency, and light enough to eventually bundle as a single .exe for
this tool's actual audience — someone who wants to play, not install Python.
Both were his calls, not decided unilaterally, matching this project's own
non-negotiable #1.

**Decision.** Add `findmybottleneck/gui/` (`app.py` for the window, `report_view.py` for
rendering) as a new dependency behind an optional extra —
`pip install findmybottleneck[gui]` — with `customtkinter` absent from the
base `dependencies` list and imported lazily, only inside the `gui`
subcommand's handler in `cli.py`. Running `capture`/`explain`/`compare`/
`overlay`/`check` never touches it. The window covers the full flow (check
this machine, start a capture with a progress bar, see the verdict as
cards) by calling the exact same functions the CLI does — nothing new was
built underneath it. `collect/windows.py::check()` and `capture()` were each
split into a data-returning core (`check_status()`, `run_capture()`) and a
thin CLI wrapper that prints exactly what it always printed; the GUI is a
second caller of the same core, driving its own progress bar instead of a
`\r`-overwritten countdown, the same split `run_overlay` already used for
its own progress reporting. `gui/report_view.py` mirrors `report.py`'s
structure (verdict headline, evidence, findings in the same severity order,
"not measured") so the two renderers can't drift apart in content, only in
medium — neither computes anything; both read the same `Report`.

**Consequences.** This is the project's first GUI and its first new dependency ever. The
CLI's zero-dependency identity survives intact for anyone who never asked
for a window — the `[gui]` extra is the whole difference, and it's the
only place `customtkinter` is even imported. Splitting `check()`/`capture()`
cost a small refactor but no duplicated subprocess logic: the GUI's Capture
tab and the CLI's `capture` command are two thin callers of one
`run_capture()`, so a future change to how nvidia-smi/typeperf/PresentMon
are driven only has to happen once. The cost carried forward, honestly: the
GUI's Capture tab reuses `run_capture`'s exact subprocess path, which is the
same path `STATE.md` already says has never run on a real Windows machine —
this doesn't add a second untested integration, but it doesn't remove the
existing one either. The GUI itself (window layout, colours, hover
behaviour) could not be screenshotted or interacted with in this session —
the user declined screen-control access when offered — so it is verified by
import (it builds without error) and by code review against
`report.py`/`verdict.py`'s structure, not by looking at it. A real visual
pass — does it actually look modern, do the cards read well, does the
progress bar behave — is still owed and belongs in `STATE.md`'s **Next**,
not assumed away.

<!-- bitacora:entry
id: D-0012
date: 2026-09-21
tags: [design]
-->
### Feed RTSS's overlay instead of building one

**Context.** Juan asked for an "overlay estilo FPS monitor". The same transcripts folder that
led to D-0010 also confirmed what everyone in that space actually uses for
this: RivaTuner Statistics Server (RTSS) — the engine MSI Afterburner and
HWiNFO both write into via its public shared-memory API, not something they
each reimplement. Building a real overlay from scratch means hooking
DirectX/Vulkan/OpenGL inside someone else's game process — a project the
size of RTSS itself (which has been doing exactly that since 2011), fragile
across graphics-API versions, and redundant with software the audience this
tool targets almost certainly already has installed for free. Given three
options (feed RTSS, a non-overlay live terminal dashboard, build a renderer
from scratch), Juan chose feeding RTSS.

**Decision.** Add `rtss.py`, a from-scratch Python implementation of RTSS's shared-memory
client protocol (`RTSSSharedMemoryV2`), researched from Unwinder's publicly
distributed `RTSSSharedMemory.h` (cross-checked against two independent
mirrors and a reference C++/CLI implementation — no prior Python port was
found anywhere). Split the same way the rest of this project splits real I/O
from reasoning about it: `parse_header`/`find_slot`/`build_writes` are pure
functions tested against a synthetic buffer shaped like the real shared
memory; only `RTSSWriter` touches the actual OS-level mapping, and that half
is untested here, exactly like the rest of the Windows collector.
`collect/windows.py::run_overlay` reuses `find_presentmon` and
`_parse_presentmon` unchanged, running PresentMon open-ended instead of
timed, and feeds `engine/frames.py::analyse`'s existing rolling verdict — not
raw FPS/GPU%/CPU%, which RTSS/Afterburner/HWiNFO already show — into the OSD
every `--refresh-ms`. `findmybottleneck check` gained one more line (RTSS
found or not), informational only, since it is not needed for `capture` or
`explain`.

**Consequences.** The differentiator survives into the live view: every other overlay in this
space shows numbers, this one is the only one that says *why* — "GPU-bound",
"CPU-bound", a frame cap — recomputed from a short rolling window instead of
once per capture. `rtss.py`'s byte-offset arithmetic is a different kind of
risk than everything else in `engine/`: a mistake there is not a wrong
verdict, it is memory corruption in a shared segment RTSS itself and every
other app writing into it also depends on — the reason it got a
correctness-focused `/trio-auditor` pass (M-0001's guardrail) before being
called done, rather than only unit tests.

Three `/trio-auditor` rounds were needed, and each one found something real
the previous fix had missed — worth recording precisely, not smoothed into
"eventually passed."

Round one: `parse_header` trusted whatever `osd_entry_size`/`osd_arr_size`
the shared memory advertised, which meant a corrupt or unexpected header
(`entry_size == 0`, an absurd slot count, an entry too small for the version
it claims) could turn `find_slot`'s scan into a nine-figure loop, or make
`build_writes` compute an offset past a slot's real boundary and into the
next one; `RTSSWriter._write` had no bounds check or exception handling
around a write landing outside the mapped view; `close()` cleared its cached
slot unconditionally. Fixed by validating geometry once in `parse_header` (a
header that fails is treated exactly like a missing mapping), catching write
failures in `_write`, and adding `should_clear` to recheck ownership before
clearing.

Round two: `parse_header` validated `osd_entry_size`/`osd_arr_size` but not
`osd_arr_offset` itself — an offset that is wrong but still lands inside the
mapping (e.g. pointing 1000 bytes past the real array) sailed through every
check and would have redirected a "safe" write into a neighbouring slot
without ever exceeding the mapping's real length. First attempt: a fixed
slack bound, `MAX_HEADER_GROWTH`, on how far past the header the array could
legitimately start. Also: `should_clear` only distinguished owner strings,
not writer *processes* — two `overlay` runs sharing the fixed string
`"findmybottleneck"` racing for the same slot could have one erase the
other's just-written line. Fixed by qualifying the default owner with the
process's own PID.

Round three found the slack bound from round two still insufficient — a
carefully-shifted offset within that bound could still land exactly on a
neighbouring slot's boundary, so an arbitrary number was never going to be
enough. Replaced with an invariant instead of a guessed constant: RTSS's own
layout places `arrApp` immediately after `arrOSD`, so a genuine header's
`app_arr_offset` always equals `osd_arr_offset + osd_arr_size *
osd_entry_size` — two independently-advertised fields that agree by
construction in a real header, which rejects any *single*-field corruption
of the three involved. Round three also caught this entry's own draft
overclaiming ("both auditors gave GO") before the round that would have
justified saying so had even returned, and a class-docstring claim that the
PID-based owner makes "each running instance" distinct, when it only
distinguishes *processes* — two `RTSSWriter` objects built in the same
process would still share a default owner (nothing in this codebase does
that today, but the docstring said more than the code guarantees). Both
corrected to say only what is true.

Round four (Codex) showed the consistency check itself is not a closed
proof: a header with `osd_arr_offset`, `osd_arr_size` and `osd_entry_size`
corrupted *together*, chosen so the same equation still holds, passes every
check here while every slot address computed from it is wrong. This is not
a bug to patch — it is the actual limit of what an algebraic
self-consistency check, run entirely on data that might itself be the thing
that's corrupt, can prove. Closing it for real would need a reference
outside the shared memory (a live RTSS to check against), which this module
does not have and cannot get without a real Windows machine running RTSS.
The decision here, after four rounds, is to stop patching and say precisely
what the check does and does not guarantee, in the code and in this entry,
rather than attempt a fifth algebraic refinement of an approach that cannot
reach a proof by construction: it turns a single accidental bit-flip or
partial write — a corrupt-but-not-adversarial header, this project's actual
threat model, the same one D-0004 and D-0009 already draw the line at — from
silently accepted into rejected with overwhelming probability. It does not,
and structurally cannot, defend against a header deliberately and
coherently rewritten across three fields at once. That is a materially
different, much narrower claim than "closes the wrong-in-bounds-offset
class", and the module docstring now says exactly that, not the stronger
thing.

What is still open, honestly, not softened: `close()`'s read-then-write
(`should_clear` then `build_clear`) remains a real TOCTOU window against a
*different process* — if another writer takes over this exact slot in the
instant between the check and the clear, `close()` would still erase it.
This is the same category as the missing `dwBusy` spin-lock (RTSS v2.14+
documents one; this does not implement it — a guessed-at locking protocol,
unverifiable against a real RTSS, risks acquiring a lock and never releasing
it, which freezes the OSD for every client, a worse failure than the one
being avoided). Both are disclosed, bounded concurrency gaps — a momentarily
torn or overwritten status line, self-correcting a moment later — not the
unbounded, silent cross-slot corruption the geometry-validation fix closes.
That distinction, not "everything here is fixed," is what `STATE.md` and
this entry are careful to state.

Like the rest of the collector, none of this has run against a real, live
RTSS instance — built against RTSS's own documented layout, the same posture
`STATE.md` already carries for `capture`, now extended to a second untested
integration, with the concurrency caveats above on top of that, not instead
of it.


## Archived

Older entries, one line each. `recall` still searches them in full.

- `D-0011` Attempted a wrong-GPU finding, and pulled it before shipping — [design] → `docs/bitacora-archive/decisions-2026.md`
- `D-0010` Judge CPU throttling the same way GPU throttling is judged — [design] → `docs/bitacora-archive/decisions-2026.md`
- `D-0009` Skip the shared-VRAM counter - Microsoft documents it as unreliable — [design] → `docs/bitacora-archive/decisions-2026.md`
- `D-0008` Automate the resolution-drop test as a compare command — [design] → `docs/bitacora-archive/decisions-2026.md`
- `D-0007` Take the name the search term owns, and earn it — [naming] → `docs/bitacora-archive/decisions-2026.md`
- `D-0006` A hitch cause is coincident evidence, never proof — [design] → `docs/bitacora-archive/decisions-2026.md`
- `D-0005` Shell out to what is already installed — [design] → `docs/bitacora-archive/decisions-2026.md`
- `D-0004` Refuse to judge a metric measured at idle — [design] → `docs/bitacora-archive/decisions-2026.md`
- `D-0003` A frame cap is a verdict, not a silence — [design] → `docs/bitacora-archive/decisions-2026.md`
- `D-0002` The collector and the engine meet only in a trace file — [design] → `docs/bitacora-archive/decisions-2026.md`
