# Decisions

> Architecture decisions, lightweight. One entry per choice that would be
> expensive to reverse, or that a future reader would otherwise second-guess.
>
> The point is not the decision — it is the *context*, so that when the context
> changes the decision can be revisited honestly. Newest first.
>
> Add entries with: `node .bitacora/cli.mjs new decision "Title" --tags area`

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
