# Mistakes

> Every time something breaks, it gets an entry here — what happened, why it
> was possible, and the guardrail that makes it impossible to repeat.
>
> An entry without a guardrail is just a complaint. Newest first.
>
> Add entries with: `node .bitacora/cli.mjs new mistake "Title" --tags area,failure-mode`
<!-- bitacora:entry
id: M-0004
date: 2026-09-21
tags: [collect, failure-mode]
severity: medium
-->
### cpu_temperature() only caught ImportError; pythonnet's real failure mode is RuntimeError

**What happened.** The first version of `cpu_temperature()` (D-0017) wrapped
`from HardwareMonitor.Hardware import ...` in `except ImportError` only, on
the assumption that "the package isn't installed" was the only way this
import could fail — matching every other optional-dependency import in this
codebase (`customtkinter` in the GUI). Tested on this Mac with the package
actually installed but no .NET runtime present, the import raised
`RuntimeError` from deep inside `pythonnet`'s own `import clr` — uncaught,
it would have propagated out of the background thread `_on_read_system`
runs in, silently killing that thread before `self.after(...)` ever fired,
leaving the System page's button stuck on "Reading…" forever with no error
shown.

**Root cause.** `HardwareMonitor`/`pythonnet` is an interop boundary to a
different runtime (.NET/Mono), not a plain Python import — its failure
modes (no runtime found, wrong runtime version, a native library that
won't `dlopen`) surface as whatever exception pythonnet's own loader
chooses to raise, not consistently `ImportError`. Copying the
`except ImportError`-only pattern from `customtkinter` (a pure-Python
import that really can only fail that one way) assumed the two
dependencies fail the same way without checking.

**Guardrail.** Any optional dependency that bridges to a non-Python runtime
(pythonnet/CLR, a native extension that loads a system library, etc.) gets
a broad `except Exception` around its import and its first real call, not
just `except ImportError` — and that broad catch must be exercised once
against an environment where the dependency is installed but cannot
actually run (not just uninstalled), the way this was caught: `pip install
HardwareMonitor` in a venv with no .NET runtime, then call the function and
confirm it returns `(None, missing)` instead of raising.

<!-- bitacora:entry
id: M-0003
date: 2026-09-21
tags: [gui, capture]
severity: medium
-->
### GUI capture label said 'recording' during the wait-for-process phase

**What happened.** The user started a capture from the GUI for `cs2.exe`
while it was not actually running. `run_capture` (D-0014) correctly polled
`tasklist` and waited for it to appear, but the GUI's `_on_start_capture`
set the progress label to `"recording {seconds}s of {process} — play
normally"` immediately on click and never wired `run_capture`'s `on_wait`
callback. So for however long the wait lasted (up to the 120s default
timeout), the window told the user a capture was in progress when nothing
had been recorded yet — indistinguishable from the tool being stuck or
broken. The CLI (`collect/windows.py::capture`) already had this right: it
prints `"waiting for X to start…"` via `on_wait` before switching to a
countdown, so the bug was GUI-only, introduced when D-0014 added the wait
but only threaded `on_wait` through the CLI caller.

**Root cause.** `run_capture` exposes two distinct phases through two
different callbacks (`on_wait`, `on_progress`), and it is a caller's
responsibility to render both. The GUI caller was written against the
pre-D-0014 signature (progress only) and not updated when D-0014 added
`on_wait` — nothing forced the second caller to catch up when the first one
changed.

**Guardrail.** Both `run_capture` callers (`collect/windows.py::capture` for
the CLI, `gui/app.py::App._on_start_capture` for the GUI) must wire both
`on_wait` and `on_progress`, not just one. There are only ever two callers
of `run_capture`; before adding a third callback to its signature, or
touching this pair, grep both `collect/windows.py` and `gui/app.py` for
`run_capture(` and confirm every optional callback the function accepts is
passed by both — a caller that silently no-ops on a new callback is exactly
how this happened.

<!-- bitacora:entry
id: M-0002
date: 2026-09-21
tags: [collect, presentmon]
severity: high
-->
### PresentMon 2.6.0 rejects the --no_top flag capture/overlay pass it

**What happened.** `run_capture` and `run_overlay` both invoke PresentMon with `--no_top`, a flag
meant to suppress its live console table. Running `capture` for real for the
first time (adding `--launch`/auto-wait to `capture`, this session) against a
real PresentMon 2.6.0 binary, downloaded fresh from the project's own README
link, PresentMon exited immediately with `error: unrecognized option
'--no_top'.` — every capture and every overlay session failed before writing
a single frame, silently recorded as "PresentMon exited with an error" in
`missing`, which is easy to read as a permissions problem rather than a
wrong flag. Fixed to `--no_console_stats`, PresentMon's 2.x name for the same
thing per its own `--help` output.

**Root cause.** STATE.md already named this precisely under "In flight": *"The collector has
never run… no line of it has executed on a Windows machine."* The flag was
written against memory/documentation of PresentMon's CLI, not against a real
binary's `--help`, and PresentMon has no stable versioned flag API — `--help`
on 2.6.0 lists neither `--no_top` nor any deprecation notice for it, it is
simply gone. Nothing in the test suite could have caught this: `_parse_presentmon`
is tested against synthetic CSV text, never against the subprocess invocation
itself, by design (D-0005's tests-the-parser-not-the-shelling-out split).

**Guardrail.** Before changing or adding a PresentMon (or any shelled-out tool's) command-line
flag, run that exact binary with `--help` and confirm the flag is listed —
do not carry a flag forward from memory, an older version's docs, or a
different PresentMon build. `findmybottleneck check` already prints the
resolved PresentMon path; the check before touching its invocation is one
command: `<presentmon-path> --help`.

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

