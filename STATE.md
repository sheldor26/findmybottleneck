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

## In flight

- **The collector has never run.** It is written against documented flags and
  field names and parses text shaped like the real output, but no line of it
  has executed on a Windows machine. That is the one thing a test suite here
  cannot settle.
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
5. A wrong-GPU finding (laptop rendering on integrated graphics instead of
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
