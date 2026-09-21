# Mistakes

> Every time something breaks, it gets an entry here — what happened, why it
> was possible, and the guardrail that makes it impossible to repeat.
>
> An entry without a guardrail is just a complaint. Newest first.
>
> Add entries with: `node .bitacora/cli.mjs new mistake "Title" --tags area,failure-mode`
<!-- bitacora:entry
id: M-0001
date: 2026-09-21
tags: [engine, testing]
severity: high
-->
### Two judgement-only engine rules shipped with false negatives no test caught

**What happened.** `engine/config.py::cpu_throttling` and `engine/compare.py` were written, tested
(42 passing tests, each rule with a firing case and a silent neighbour, the
project's own convention) and pushed to `origin/master` in commits f8beaa7 and
36f52ad. A `/trio-auditor` pass (Codex + Gemini, run independently and in
parallel, neither seeing the other's output) run immediately afterward found,
between them, six real defects the test suite had not caught: (1)
`cpu_throttling` gated on whole-processor `% Processor Time`, which a game
bound to one or two threads on an 8+ core machine never pushes past 80% even
while those threads are pinned and throttled; (2) the same rule's absolute
floor could never catch a processor that loses turbo boost and settles back
at (not below) its own rated clock; (3) `compare.py` computed its 8%-tolerance
fps comparison from already-rounded `median_fps` values, which can flip the
confirm/contradict verdict right at the boundary; (4) `compare.py`'s
"Confirmed:" wording didn't disclose that the tolerance is judgement or that
two identical captures would read as confirmed without having tested
anything; (5) the `cpu_performance` source citation was cited as if it
supported a specific throttling diagnosis when it only supports the counter's
general mechanics — the finding needed `heuristic=True`, which it didn't
have; (6) a dead helper, `_median_fps()`, left over from an earlier draft of
`compare.py`. Both auditors independently returned NO-GO on the first pass.

After all six were fixed, a **second** `/trio-auditor` pass (same two
auditors, same independence) caught a seventh, in the fix itself: the
rewritten `compare()` still dispatched on `before_verdict.limiter` alone and
never checked `after_verdict.limiter`, so a first capture that was cleanly
cpu-bound but a second capture that happened to land on a frame cap, a stall,
or a mixed result would be scored anyway instead of returned as
inconclusive — the exact failure `compare()` was written to avoid, just on
the other side of the comparison. Gemini's pass gave GO; Codex's did not,
and was the one that found it.

**Root cause.** Every test written for these rules — and the first round of fixes — was written
by the same reasoning that designed them, so a test could confirm the code
did what it was intended to do without ever revealing that the intent itself
had a gap. Unit tests check "does this code implement this rule correctly";
they cannot check "is this rule the right rule," and that blind spot survives
a first fix as readily as it survives a first draft — the second-pass finding
above is proof of that, not a fluke. Nothing between commit and push required
a reviewer who did not write the rule, for this kind of judgement-heavy code
(as opposed to code with a single objectively correct behaviour, where tests
alone are a reasonable bar).

**Guardrail.** Any new `engine/` rule that introduces a judgement threshold (a percentage, a
share, a tolerance — anything commented "judgement, not a published rule" by
this project's own convention) gets a `/trio-auditor` pass before it is
considered done, not just a green test suite — and after fixes are applied
for a NO-GO, a second pass runs against the fixed code before calling it done,
not just against the original defect list. The auditor's brief must
explicitly ask it to hunt for the shape of false negative a same-author test
suite is structurally blind to: a gate defined on the wrong granularity
(aggregate vs. per-component), a threshold defined in absolute terms when the
failure mode is relative (a decline from a moving baseline), and — as the
second pass showed — an asymmetric check that only validates one side of a
two-sided comparison. This is already how `cpu_throttling` was fixed — see
`engine/config.py::_busy_signal` (per-core, not aggregate) and
`_declining_trend` (relative to the capture's own earlier samples) — and how
`compare()`'s dispatch was fixed to check both `before_verdict.limiter` and
`after_verdict.limiter`.

