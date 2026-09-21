# Decisions

> Architecture decisions, lightweight. One entry per choice that would be
> expensive to reverse, or that a future reader would otherwise second-guess.
>
> The point is not the decision — it is the *context*, so that when the context
> changes the decision can be revisited honestly. Newest first.
>
> Add entries with: `node .bitacora/cli.mjs new decision "Title" --tags area`

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

<!-- bitacora:entry
id: D-0011
date: 2026-09-21
tags: [design]
-->
### Attempted a wrong-GPU finding, and pulled it before shipping

**Context.** Juan pointed at a folder of PC-gaming-bottleneck YouTube transcripts to see
whether they held anything the project's own research had missed. Most of it
was the same ground already covered, or content-farm filler recommending the
exact calculator sites D-0007 rejects. One thing recurred across several
transcripts and looked genuinely new: a laptop can silently render a game on
integrated graphics instead of the discrete card, and `collect/windows.py`
already reads the discrete card via `nvidia-smi` regardless of which GPU
actually rendered the frame — a trace from a misconfigured laptop would show
a card that stayed idle the whole capture, data this project was already
gathering and not reading for this.

**Decision.** Built `engine/config.py::wrong_gpu`, and ran it through the M-0001 guardrail
(a `/trio-auditor` pass before shipping, not after) three times. Each pass
found a real defect the previous fix hadn't closed: (1) a missing
utilisation reading treated as 0%, and idle share computed over the whole
capture rather than while frames were recorded; (2) fixed with a `None`
filter and a `[min, max]` frame-time window — which the next pass showed
compares two collectors' clocks that do not share an origin
(`_parse_gpu`'s samples are indexed `0, 0.5, 1.0, …` from collector start;
`_parse_presentmon`'s frames are indexed from zero at the *first captured
frame*), so the "same window" the code assumes is not actually the same
window in a real trace; and, independently, that the window swallows any
mid-capture gap (a loading screen, an alt-tab) as if it were idle time in the
supposedly-active period; (3) a GPU-busy floor meant to separate a real
stall from a wrong-GPU laptop, which both auditors independently showed
cuts the wrong way too: a light or capped game genuinely running on the
wrong GPU can render fast enough that its own GPU-busy share never crosses
the floor, silently suppressing the one case the rule exists to catch.

Three rounds, three genuinely different failure shapes, each deeper than the
last rather than converging — the opposite of what a healthy fix-and-reverify
cycle looks like (contrast D-0008 and D-0010, each closed in one or
two rounds). That pattern is itself the signal: the rule was not sound
enough to fix incrementally, so it was deleted — function, constants, tests,
and its `judge()` wiring — rather than attempting a fourth patch.

**Consequences.** This is `engine/config.py`'s first pulled finding, and the guardrail did
exactly its job: catching a rule that looked reasonable on a first read
before it reached a real trace, not after. The underlying idea is not wrong
— a laptop rendering on the wrong GPU is real and `collect/windows.py`'s data
could in principle show it — but doing so honestly needs the collector's
GPU and frame samples to share a real, common clock, which they do not today
(D-0002's boundary means `engine/` cannot fix this by reading harder; it is
a `collect/` problem). Revisiting this after the collector has an actual
Windows capture to check timestamps against (`STATE.md`'s **In flight** item)
is the earliest this should be tried again.

<!-- bitacora:entry
id: D-0010
date: 2026-09-21
tags: [design]
-->
### Judge CPU throttling the same way GPU throttling is judged

**Context.** Juan asked to look at how other tools detect bottlenecks and find something
this project wasn't doing yet. `engine/config.py::throttling` already checks
whether the GPU is being held back by power or temperature, cited from
nvidia-smi's own clocks-event-reasons. Nothing here ever asked the same
question of the processor, even though `collect/windows.py` already requests
`\Processor Information(_Total)\% Processor Performance` and `trace.py`
already carries it on every `CpuSample` — the data was collected and unused.
A Microsoft support article on why Task Manager can show CPU usage over 100%
gives the exact mechanism in one sentence: "a processor that's running 100%
of the time and clocked down to 50% frequency performs only half the work."

**Decision.** Add `engine/config.py::cpu_throttling`, gated the same way the PCIe link
finding is gated (D-0004): only samples where `% Processor Time` is at or
above 80% count, because a low `% Processor Performance` at idle is the
processor correctly downclocking to save power, not a fault. Within busy
samples, a share below 85% of nominal clock is reported as the processor
being held back — laptop power plans capping "Maximum processor state" and
thermal throttling being the two real-world causes named in the fix text.

**Consequences.** This is the CPU-side twin of the GPU throttling finding, and it was buildable
today because the collector already gathered the counter — the gap was only
in `engine/`, not in `collect/`. It also closes a real blind spot: a "your
processor is setting the pace" verdict previously had no way to say *why* the
processor was slow, and "waiting on memory" (D-0004's neighbour, the memory
Finding) was the only explanation this tool could offer. Now a throttled
clock is a second, independent explanation, with its own evidence. The
research also turned up two bigger, unbuilt ideas worth keeping — DPC/ISR
latency (the LatencyMon method, needs an ETW capture this project doesn't
have) and per-process `GPU Engine` counters (catching a background app
stealing the 3D engine) — both left for `STATE.md`'s **Next**, not built now,
because both need new collection code rather than reading data already in
hand.

<!-- bitacora:entry
id: D-0009
date: 2026-09-21
tags: [design]
-->
### Skip the shared-VRAM counter - Microsoft documents it as unreliable

**Context.** `STATE.md` named the `GPU Process Memory\Shared Usage` performance counter
as the way to read spilled graphics memory without native code or admin
rights, with the caveat that Microsoft publishes no reference for the counter
set. A web search to fill that gap surfaced a Microsoft Learn support article
titled "GPU Process Memory counters report incorrect value", describing known
memory-leak-shaped bugs in that exact counter set on affected Windows
versions, with Task Manager or WPA as the only reliable alternatives — neither
of which this tool can shell out to and parse the way it does `nvidia-smi` or
`typeperf`.

**Decision.** Do not implement the shared-VRAM counter. `trace.py` already carries a
`gpu_shared_mb` field and `engine/config.py` and `engine/hitch.py` already
know what to do with it if it is ever populated, so nothing here is wasted —
but the collector will not read it until a source exists that can be trusted
the way every other rule in this project is trusted enough to cite.

**Consequences.** This closes out `STATE.md`'s Next #3 without building a finding that could
itself be the false accusation this tool exists to avoid — the exact trap
D-0004 names for the PCIe link. The cost is that VRAM overflow is still
only detected from `nvidia-smi`'s dedicated-memory reading
(`engine/config.py::vram`), which misses the case where the driver has
already started spilling and dedicated memory looks fine. If a reliable
source for the counter set appears later, the schema is ready for it.

<!-- bitacora:entry
id: D-0008
date: 2026-09-21
tags: [design]
-->
### Automate the resolution-drop test as a compare command

**Context.** `STATE.md` named the resolution-drop test as Next #2 and called it "the
community's own gold standard for settling CPU-versus-GPU" while noting
"nothing automates it." A web search across several sources confirmed the
method and its interpretation: drop resolution or in-game settings, capture
again, and if the frame rate barely moves the CPU was always the limit —
giving the GPU less to do changed nothing because it never had the whole
frame anyway. If the frame rate rises, the GPU was the limit. A single
capture's per-frame attribution (D-0006, `engine/frames.py`) is an
inference from one sample of reality; this test is the same claim checked
experimentally, against a second sample.

**Decision.** Add `engine/compare.py`, taking two already-captured traces and returning
whether the second confirms or contradicts the first verdict, with an 8%
fps-change tolerance named as judgement the same way every other threshold in
`engine/` is. It reuses `frames.analyse` rather than re-deriving the verdict,
and it says "inconclusive" for frame-cap, mixed, stall or unknown verdicts
rather than forcing a confirm/contradict answer the test cannot actually give.
Wired in as `findmybottleneck compare <before> <after>`, and surfaced as a
one-line hint directly under a gpu/cpu verdict in `report.render()` so the
feature is discoverable from the screen people already read, not only from
`--help`.

**Consequences.** A verdict is no longer only as strong as one capture's inference — someone can
now falsify it, which is the difference between an opinion and a claim that
can be argued with, in the same spirit as D-0006 labelling coincidence
rather than proof. The cost is a workflow: this only helps someone who
captures twice and remembers to lower something between the two runs, and the
tool has no way to detect whether they actually did. If they didn't, and both
captures are the same scene at the same settings, "contradicts" or "confirms"
would follow from noise rather than from anything real. The wording is honest
about this ("may not have been comparable") but cannot enforce it.

<!-- bitacora:entry
id: D-0007
date: 2026-09-21
tags: [naming]
-->
### Take the name the search term owns, and earn it

**Context.** The tool was built as `whylow`. The word "bottleneck" is owned by a dozen
sites that ask for two model numbers and return a percentage with no
denominator — the exact thing this tool exists to be the opposite of. A
thread on Linus Tech Tips settles it as its accepted answer: "Bottleneck
calculators exist to sell more hardware, not to be an actually useful
advisement tool." Taking the word risks being read as one of them before
anyone sees the output.

**Decision.** The name is findmybottleneck, with `fmb` as the short command, and the
positioning is explicit rather than implied: the README has a section named
"Not a bottleneck calculator" that says what those sites structurally cannot
see — memory at a fallback speed, a card on its power limit, a link at x4 —
and the package description leads with "measured on your machine while you
play, not calculated from a spec sheet".

**Consequences.** The tool now appears where people actually search, and the comparison it
invites is one it wins on the merits, because the differentiator is that it
touches the machine at all. The risk is real and accepted: a reader who
recognises the word may dismiss it before reading, so the first screen of the
README has to do the work of separating them. If that turns out to cost more
than the search term is worth, the package can be renamed and the old name
kept as an alias — nothing about the code depends on it.

<!-- bitacora:entry
id: D-0006
date: 2026-09-21
tags: [design]
-->
### A hitch cause is coincident evidence, never proof

**Context.** Frame data arrives per frame. The counters arrive about once a second. So when
a 95 ms frame lands next to a disk read averaging 45 ms, the honest statement
is that they happened at the same time — not that one caused the other.

**Decision.** Every hitch cause is labelled coincident, the summary separates hitches that
have something to blame from those that do not, and "unexplained" is printed as
a result rather than hidden. A cause matching one sample is presented as weaker
than one matching several.

**Consequences.** The tool can claim the ground nobody has claimed — no tool in this category
attempts per-hitch attribution at all — without claiming more than the sampling
rate supports. The cost is a wordier answer than a confident one, and the
permanent temptation to promote a correlation once it has been right a few
times.

<!-- bitacora:entry
id: D-0005
date: 2026-09-21
tags: [design]
-->
### Shell out to what is already installed

**Context.** The data needs vendor telemetry, Windows performance counters, frame timings
and SMBIOS. Reading those properly means native code, a driver, or a bundled
runtime — and a diagnostic tool people install while already annoyed cannot ask
for that.

**Decision.** Everything is a subprocess against something already present: nvidia-smi ships
with the driver, typeperf ships with Windows, CIM answers through PowerShell.
PresentMon is the only download, it is MIT licensed, and it is named as a
download rather than bundled. No dependencies, no compilation.

**Consequences.** It installs in seconds and every reading is reproducible by hand, which matters
for a tool whose output is an accusation. The cost is parsing text meant for
humans, per-vendor blindness — on AMD and Intel there is no command-line
telemetry at all, so those machines get frames and counters and nothing else —
and column names that have to be read from the header rather than assumed.

<!-- bitacora:entry
id: D-0004
date: 2026-09-21
tags: [design]
-->
### Refuse to judge a metric measured at idle

**Context.** A graphics card narrows its PCIe link when idle to save power. Read the link
with nothing running and it says x1, which looks exactly like a card seated
badly. This is a known trap: the forums are full of people told they have a
broken slot by a tool that read a sleeping card.

**Decision.** The link is judged only from samples where GPU utilisation was at least 50%. If
no such sample exists, the finding is not "your link is fine" and not "your
link is broken" — it is a note saying the link was not judged, and why.

**Consequences.** The tool cannot produce the specific false positive that the category is
mocked for. The same shape will apply to every future metric that is only
meaningful under load, so it is a rule rather than a special case. The cost is
that a capture taken at the wrong moment yields less, which is correct and has
to be explained rather than hidden.

<!-- bitacora:entry
id: D-0003
date: 2026-09-21
tags: [design]
-->
### A frame cap is a verdict, not a silence

**Context.** Something always sets the pace — the enthusiast forums say it more bluntly than
any documentation: there is no such thing as no bottleneck. But a machine
sitting at a 60 fps cap has both components idle, and every rule that looks for
a busy component will either pick the larger number and be confidently wrong,
or find nothing and say so.

**Decision.** A capture whose frames are unusually even, land within 3% of a common cap, and
leave spare time on both sides is reported as a cap, by name, with the fps.
The headline is that nothing is limiting the machine.

**Consequences.** The single most embarrassing wrong answer this tool could give — telling
someone their processor is holding them back while they sit behind a cap they
forgot they set — is now the one case it handles first. The cost is three
thresholds that are judgement rather than measurement, so the verdict prints
itself as a judgement.

<!-- bitacora:entry
id: D-0002
date: 2026-09-21
tags: [design]
-->
### The collector and the engine meet only in a trace file

**Context.** The machine this is built for runs Windows with a dedicated graphics card. The
machine it is written on does not. Worse, the interesting failures — a card
throttling, memory at a fallback speed, a link negotiated narrow — cannot be
made to happen on demand even on the right machine.

**Decision.** Capture writes one JSON file and stops. Everything that reasons about
performance reads that file and never touches hardware. The reasoning is tested
against traces; the reading is tested against text shaped like what the real
tools emit.

**Consequences.** The half that can be wrong in an interesting way — the reasoning — is testable
anywhere, and the untested remainder shrinks to "do the three programs run and
produce that text", which one real capture settles. It also makes a trace
something a person can send: today someone with a stutter posts a screenshot of
an overlay and waits for a stranger to interpret it, and a trace is that
screenshot except a program can read it. The cost is a schema to keep
compatible, and the discipline of never letting the engine reach for a live
reading it is missing.
