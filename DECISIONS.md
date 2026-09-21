# Decisions

> Architecture decisions, lightweight. One entry per choice that would be
> expensive to reverse, or that a future reader would otherwise second-guess.
>
> The point is not the decision — it is the *context*, so that when the context
> changes the decision can be revisited honestly. Newest first.
>
> Add entries with: `node .bitacora/cli.mjs new decision "Title" --tags area`

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
