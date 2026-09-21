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
  D-0013), the project's first GUI. Four pages — Check, System, Capture,
  Report — behind a left sidebar nav, dark background, one orange accent
  (Juan asked for something closer to a dashboard reference he shared).
  Report opens with a stat-tile row (Verdict/Median FPS/1% Low/Findings).
  System (D-0016) reads GPU/CPU/memory/motherboard/RAM-slot identity plus
  live GPU sensors (`gpu_status()`, D-0016) and CPU temperature
  (`cpu_temperature()`, opt-in `[sensors]` extra over `pythonnet` +
  Administrator, D-0017) standalone, not tied to a capture. Motherboard/
  RAM-slot detail (D-0018) answers "can I add a stick, and which one" —
  free slots, exact part to match — without ever claiming a maximum
  *supported* speed, which no Windows API reports; a `/code-review` this
  session caught those two extra queries running on every `capture`, not
  just System, and D-0019 split them into their own `motherboard_status()`
  so `capture` pays for exactly what it needs.
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
  samples across a whole play session for a steadier 1% low/throttle/hitch
  read. Needs a target process to detect the end by, else falls back to 30s.
  GUI shows an indeterminate progress bar while unbounded.
- Eight companion features (D-0020), each compiled/tested, several
  smoke-tested with synthetic data: `config_audit()`/`disk_health()`
  (power plan, HAGS, Game Mode, Memory Integrity/HVCI, SMART health) as a
  second section in `check()`/Check page, clearly separate from tool
  pass/fail; `capture --notes`/GUI Notes field, shown in both report
  renderers; a GUI compare wizard (Save/Compare-to-baseline buttons on
  Capture, reusing `engine.compare.compare()`); `engine/history.py` + new
  `findmybottleneck history <folder>` — trend, GPU-temp-rise, and
  build-change-anchored regression notes across a folder of traces;
  per-process `GPU Engine` counters → a `background_gpu` finding (asks
  `typeperf -q` before adding the counter, since one bad path fails the
  whole typeperf call); `overlay --log <path>`, the same rolling verdict
  already computed, appended with a timestamp for after-the-fact lookup.
  Two ideas skipped with a stated reason: an "upgrade calculator" would
  have contradicted `engine/frames.py`'s already-deliberate CPU-bound fix
  text (steers to memory config, not "buy a CPU") and `report.py`'s own
  disclaimer; a stutter-triggered "clip mode" turned out to already be
  covered by D-0015's unbounded capture plus `engine/hitch.py`'s existing
  retroactive attribution.

## In flight

- **The collector has run for real for the first time** — against a real
  NVIDIA card and PresentMon 2.6.0, surfacing M-0002 (now fixed: PresentMon
  2.x wants `--no_console_stats`, not `--no_top`). Still unconfirmed: a real
  game's PresentMon CSV column names (the smoke test used `notepad.exe`,
  which never presents a frame), and why `typeperf` wrote nothing in that
  same run despite `check` reporting it present. Item 1 below settles both.
- **`rtss.py` has never talked to a real RTSS.** Byte-offset math and header
  geometry validation are unit tested against synthetic buffers only
  (four `/trio-auditor` rounds, pre-ship — D-0012, which also states its
  deliberate proof limit). Whether `mmap.mmap(-1, 0, tagname=...)` actually
  opens RTSS's real shared memory, and whether a written OSD slot actually
  renders on screen, is unverified — needs a live RTSS, unavailable here.
- **The GUI has now been looked at, on macOS** — reworked to a dark
  sidebar-nav shell with an orange accent and a Report stat-tile row (Juan
  asked for something closer to a dashboard reference he shared). Confirmed
  rendering correctly via `app_screenshot`: Check (live, real button click),
  Report (stat tiles + cards, via a synthetic trace), and System's disabled
  non-Windows state (see L-0001 for why this needed a throwaway
  `python-tk@3.12` venv — the Mac's default `python3` has Tk 8.5, which
  renders CustomTkinter fully blank with no error). Unconfirmed: Capture's
  visuals, what System shows with real GPU/CPU/memory/sensor data, and
  everything on Windows, the GUI's real target, which has never run at all.
- **CPU temperature (D-0017) and the motherboard/RAM-slot reads (D-0018)
  are both unverified against real hardware** — only that their failure
  paths degrade to `missing` instead of crashing (confirmed on macOS for
  the former; the latter's PowerShell queries weren't reachable to test
  here at all). Whether a real board returns sensible values for
  `Win32_BaseBoard`/`Win32_PhysicalMemoryArray`, and whether
  `cpu_temperature()` finds a Package/Tctl/Tdie sensor under Administrator,
  is open until item 1 below happens for real.
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
4. Look at the GUI on Windows: `pip install findmybottleneck[gui]` then
   `python3 -m findmybottleneck gui`, click through all four pages with real
   hardware and a real capture, and fix whatever doesn't look or behave
   right. Only confirmed on macOS so far, and Capture's visuals not even
   there yet.
5. A wrong-GPU finding (laptop rendering on integrated graphics instead of
   the discrete card this trace reads) was attempted and pulled — see
   D-0011. It needs `collect/windows.py`'s GPU samples and PresentMon's
   frames to share a real, common clock, which they do not today
   (`_parse_gpu` indexes samples from collector start; `_parse_presentmon`
   indexes frames from the first captured frame). Worth retrying once the
   real Windows capture (item 1 above) shows what timestamps the two
   collectors actually produce.

## Known rough edges

- NVIDIA only for GPU telemetry. No command-line equivalent ships with the
  AMD or Intel consumer driver (`amd-smi` is ROCm/Linux, `xpu-smi` targets
  data-centre cards and needs Administrator) — those machines get frames
  and Windows counters and nothing else, and findmybottleneck says so.
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
