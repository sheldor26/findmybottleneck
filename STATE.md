# State

updated: 2026-09-21

> A snapshot of where this project is right now — the file a new session reads
> first. It answers "what exists, what is half-done, what is next".
>
> It is not a diary. When this file starts telling stories, the stories belong
> in the logbook. `doctor` enforces that with a line budget.

## Shipped

- The trace format, and the split that makes this project testable: a capture
  writes JSON, and everything that reasons reads JSON.
- Frame attribution from PresentMon's per-frame `MsGPUBusy` / `MsCPUBusy`:
  GPU-paced, CPU-paced, stalled, mixed, and — handled first — a frame cap,
  which is reported by name instead of being mistaken for a bottleneck.
- Frame statistics in the terms the audience already uses: median, 1% low,
  0.1% low.
- Configuration findings: memory below its rated speed, a single module or a
  single populated channel, GPU throttling by power limit or temperature with
  the measured watts and degrees, CPU throttling (rated clock vs measured
  clock under load, judged from the busiest core rather than the whole-CPU
  average, plus a declining-trend check for a clock that settles at or above
  its own rating — the load-gated pattern is the PCIe link's, D-0004, not the
  GPU throttling check's, which does not gate on load), graphics memory full
  and spilling, and a PCIe link narrower or slower than the card supports.
- Per-hitch attribution: for every frame past twice the median, what else was
  happening at that moment, labelled coincident rather than causal, with
  "unexplained" printed as a result.
- `findmybottleneck check`, which says what this machine can and cannot be read for and
  prints the one command that adds the user to Performance Log Users.
- A Windows collector that shells out to nvidia-smi, typeperf, PowerShell CIM
  and PresentMon, records every probe that fails into the trace, and bundles
  nothing.
- 50 tests, standard library only: the engine against synthetic traces, the
  parsers against text shaped like what the real tools emit.
- `findmybottleneck compare <before> <after>`, automating the community's own
  resolution-drop test: a second capture either confirms or contradicts the
  first verdict, with an honest "inconclusive" for frame-cap, mixed, stall or
  unknown results. Surfaced as a hint under any gpu/cpu verdict so it is
  discoverable without reading `--help`.
- `findmybottleneck overlay <game.exe>`, pushing a live rolling verdict (not
  raw FPS/GPU%/CPU%, which every other overlay already shows) into RTSS's
  on-screen display — the same shared-memory API MSI Afterburner and HWiNFO
  write into, rather than a renderer built from scratch (D-0012). `rtss.py`
  is the new module; its byte-level protocol code is unit tested, its actual
  OS-level shared memory access is not (see **In flight**).
- `findmybottleneck gui` — a desktop window (`gui/app.py` +
  `gui/report_view.py`), optional (`pip install findmybottleneck[gui]`,
  D-0013), the project's first GUI and first new dependency. Three tabs —
  Check, Capture (with a progress bar), Report (as cards, not monospace) —
  driving the exact same `collect.windows.check_status`/`run_capture` and
  `engine.judge` the CLI already calls; `check()`/`capture()` were each split
  into a data-returning core and a thin CLI printer so both callers share one
  implementation.
- `findmybottleneck capture` now waits for the target process to actually
  appear before starting the timed recording, instead of counting down blind
  (D-0014), and can launch it itself with `--launch <path>` so "is the game
  actually running" is guaranteed rather than hoped for. `--wait-timeout`
  (default 120s, `0` disables it) bounds the wait; on timeout `capture`
  proceeds exactly as it already does when PresentMon or the target is
  missing — recording what it can and marking frame attribution `missing`
  with the reason, not aborting. The GUI's `run_capture` call now wires
  `on_wait` too (M-0003 — it previously said "recording" while it was still
  waiting for the process to appear), and the Capture tab now has a
  "Launch" field + Browse button that passes `launch` the same way the CLI's
  `--launch` does.
- `capture --seconds 0` (CLI) or an empty/`0` Seconds field (GUI) records
  until the target process exits instead of a fixed window (D-0015) — more
  samples across a whole play session instead of a fixed 30s slice, so the
  1% low, throttle checks and hitch breakdown all get steadier the longer
  someone plays. Needs a target process to detect the end by; without one it
  falls back to 30s. The GUI shows an indeterminate progress bar while
  unbounded instead of a fraction it cannot compute.

## In flight

- **The collector has run for real for the first time this session** — the
  Windows machine used to build `--launch`/auto-wait above has an NVIDIA
  card and a real PresentMon 2.6.0 binary. It immediately surfaced a real bug
  (M-0002, now fixed): PresentMon 2.6.0 rejects `--no_top`, which both
  `capture` and `overlay` were passing; PresentMon 2.x's actual flag is
  `--no_console_stats`. nvidia-smi and PresentMon's own `--help`/argument
  parsing are now confirmed against a real binary. Still unconfirmed by a
  real capture: the synthetic smoke test used `notepad.exe` (launched, then
  killed) as a stand-in target — it never presents a frame, so it says
  nothing about whether `_parse_presentmon`'s column names match a real
  capture's CSV, and `typeperf` wrote nothing in that same run (unexplained —
  worth checking who cwd/permissions were on that machine before assuming
  it's a real bug, since `findmybottleneck check` on the same machine reports
  `typeperf: present`). Item 1 below (a real game, not a stand-in) is what
  actually settles this.
- **`rtss.py` has never talked to a real RTSS.** Its byte-offset math is unit
  tested against a synthetic buffer, and `parse_header` rejects corrupt or
  undersized geometry, including a single corrupted offset/size/count field,
  via a cross-consistency check between two independently-advertised header
  fields rather than a guessed slack bound (four `/trio-auditor` rounds,
  pre-ship, each catching what the previous fix missed — D-0012). That check
  has a stated, deliberate limit: it cannot prove the geometry is genuine
  against a header with several fields corrupted *together* in a way chosen
  to keep the equation true — round four demonstrated exactly that, and
  closing it for real needs a live RTSS to check against, which isn't
  available here. It does close the realistic case, a single accidental
  corrupted field. Separately: whether `mmap.mmap(-1, 0, tagname=...)`
  actually opens RTSS's real shared memory at its real size, and whether a
  written OSD slot actually renders, is still unverified. Also separate: no
  `dwBusy` spin-lock (RTSS v2.14+), and `close()`'s read-then-clear has a
  real if narrow TOCTOU window against another writer's process — disclosed,
  not fixed. These are all bounded concurrency/proof-limit gaps (D-0012) — a
  torn or overwritten status line, self-correcting on the next refresh — not
  the unbounded cross-slot corruption the geometry validation closes.
- **The GUI has never been looked at.** It imports and builds without error,
  and its Report tab was checked against `report.py`'s own structure, but no
  one has actually seen the window — the user declined screen-control access
  when it was offered this session. Colours, layout, hover behaviour, whether
  the progress bar reads well, and now the new Launch field and the
  indeterminate bar for unbounded capture: all unverified. `python3 -m
  findmybottleneck gui` (after `pip install findmybottleneck[gui]`) is the
  one thing that would settle it.
- **`--seconds 0` / unbounded capture (D-0015) has never run against a real
  game.** The argument-building (dropping `-sc`/`--timed`) and the
  stop-on-exit loop are straightforward extensions of code already proven
  against real PresentMon/typeperf this session, but nobody has watched an
  hour-long trace actually get written and parsed end to end — whether the
  CSVs stay well-formed at that row count, and whether `duration_s` ends up
  reasonable, is unconfirmed.

## Next

1. One real capture on the desktop with the NVIDIA card: `findmybottleneck check`, then
   `findmybottleneck capture <game.exe> --keep-csv`. The raw CSVs are the point — the
   headers will say whether the column names, the throttle field name and the
   typeperf counter paths are what the documentation claims.
2. One real `findmybottleneck overlay <game.exe>` session against a running RTSS —
   the other half of item 1's "never run for real". Settles whether
   `RTSSWriter.open()` actually finds the mapping, whether the written text
   actually appears on screen, and whether RTSS's version on a real install
   is old enough that `szOSDEx` (v2.7+) is never used in practice.
3. DPC/ISR latency (the method LatencyMon uses): a driver holding the CPU in
   an interrupt handler too long causes exactly the stutter this tool already
   tries to explain in the hitch breakdown, and today it cannot see it —
   `engine/hitch.py` only looks at GPU, disk and memory samples. Needs an ETW
   kernel-logger capture (`xperf` or the Windows Performance Recorder), which
   is a new collection dependency, not just a new engine rule.
4. Per-process `GPU Engine` counters, to catch a background app (a browser,
   Discord, OBS) competing for the 3D engine while the game runs. The counter
   is readable via typeperf without admin rights, but the instances are keyed
   by PID and engine type and nothing here parses indexed typeperf instances
   yet — `_parse_typeperf` only reads `(_Total)` and fixed per-core columns.
5. Actually look at the GUI: run `pip install findmybottleneck[gui]` then
   `python3 -m findmybottleneck gui`, click through all three tabs, and fix
   whatever doesn't look or behave right. Nobody has seen it render yet.
6. A wrong-GPU finding (laptop rendering on integrated graphics instead of
   the discrete card this trace reads) was attempted and pulled — see
   D-0011. It needs `collect/windows.py`'s GPU samples and PresentMon's
   frames to share a real, common clock, which they do not today
   (`_parse_gpu` indexes samples from collector start; `_parse_presentmon`
   indexes frames from the first captured frame). Worth retrying once the
   real Windows capture (item 1 above) shows what timestamps the two
   collectors actually produce.

## Known rough edges

- NVIDIA only for telemetry. There is no command-line equivalent that ships
  with the AMD or Intel consumer driver — `amd-smi` is part of ROCm and
  documents Linux, `xpu-smi` is a separate install that documents data-centre
  cards and needs Administrator. On those machines findmybottleneck gets frames and
  Windows counters and nothing else, and has to say so.
- The counters are sampled about once a second and a hitch lasts milliseconds,
  so hitch causes are coincidence, not causation. `typeperf` does not accept a
  sub-second interval.
- PresentMon needs the user in Performance Log Users, which needs one
  Administrator command. Without it there is no frame attribution at all, which
  is the core of the product.
- The frame-cap detector, the stall threshold and the disk-stall threshold are
  judgement. They are named in one place per module and printed as judgement,
  but they are the most likely source of a wrong verdict.
- The shared-VRAM counter is shelved, not planned. Microsoft's own docs
  describe the `GPU Process Memory` counter set as reporting incorrect
  values on affected Windows versions — see D-0009. `trace.py` already
  has a field ready for it (`gpu_shared_mb`) if a trustworthy source appears.
