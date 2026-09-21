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
  the measured watts and degrees, graphics memory full and spilling, and a
  PCIe link narrower or slower than the card supports — judged only from
  samples taken while the GPU was busy.
- Per-hitch attribution: for every frame past twice the median, what else was
  happening at that moment, labelled coincident rather than causal, with
  "unexplained" printed as a result.
- `findmybottleneck check`, which says what this machine can and cannot be read for and
  prints the one command that adds the user to Performance Log Users.
- A Windows collector that shells out to nvidia-smi, typeperf, PowerShell CIM
  and PresentMon, records every probe that fails into the trace, and bundles
  nothing.
- 32 tests, standard library only: the engine against synthetic traces, the
  parsers against text shaped like what the real tools emit.

## In flight

- **The collector has never run.** It is written against documented flags and
  field names and parses text shaped like the real output, but no line of it
  has executed on a Windows machine. That is the one thing a test suite here
  cannot settle.

## Next

1. One real capture on the desktop with the NVIDIA card: `findmybottleneck check`, then
   `findmybottleneck capture <game.exe> --keep-csv`. The raw CSVs are the point — the
   headers will say whether the column names, the throttle field name and the
   typeperf counter paths are what the documentation claims.
2. Automate the resolution-drop test. Lowering the resolution and measuring
   again is the community's own gold standard for settling CPU-versus-GPU, it
   is entirely manual today, and nothing automates it. It would turn the
   verdict from an inference into an experiment.
3. The shared-VRAM counter. The documented route needs native code; the
   `GPU Process Memory\Shared Usage` performance counter appears to expose it
   without privileges but Microsoft publishes no reference for the counter set,
   so the names must be enumerated at runtime rather than hardcoded.

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
