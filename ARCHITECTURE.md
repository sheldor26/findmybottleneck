# Architecture

> How this project is built, and the reasoning that is too structural to live
> in a code comment. If a section here is longer than a screen, it probably
> wants to be a `DECISIONS.md` entry instead.

## Shape

```
findmybottleneck/collect/windows.py   every subprocess: nvidia-smi, typeperf, CIM, PresentMon
findmybottleneck/trace.py             the trace: what a capture writes and a verdict reads
findmybottleneck/engine/frames.py     what set the pace, frame by frame
findmybottleneck/engine/config.py     what is wrong with the machine (memory, throttling, VRAM, PCIe)
findmybottleneck/engine/hitch.py      why each spike happened
findmybottleneck/engine/compare.py    a second trace confirms or contradicts the first verdict
findmybottleneck/engine/__init__.py   judge(trace) -> Report
findmybottleneck/verdict.py           Verdict, Finding, Report
findmybottleneck/report.py            everything that reaches the terminal
findmybottleneck/data/sources.json    the published sentence behind every rule
```

The line that matters runs between `collect/` and `engine/`. They never call
each other. A capture writes a file; a verdict reads one.

## Data

A trace is one JSON file: hardware read once, then four sample streams (frames,
GPU, CPU, disk and memory) each carrying seconds since the capture began, plus
a `missing` map recording what the collector could not read and why.

`missing` is the part that is easy to leave out and shouldn't be. A verdict has
to be able to say "I did not measure this", so the gaps travel with the data
rather than being inferred from an absence.

Nothing else is written. No database, no state directory, no history.

## Boundaries

- `collect/` never judges. It reads, parses, and records its own failures.
- `engine/` never reads hardware, never shells out, never reaches the network.
  Everything it concludes comes from the trace it was handed.
- Column names are read from the CSV header, never assumed by position —
  PresentMon renamed every metric between v1 and v2, and both spellings are
  accepted.
- Which field name a driver supports is probed, not assumed:
  `clocks_event_reasons` on newer drivers, `clocks_throttle_reasons` on older.
- A metric that is only meaningful under load is judged only from samples taken
  under load. When there are none, the answer is "not judged", never "fine".

## Conventions

- Python 3.9+, standard library only, in the tool and in the tests.
- Every threshold lives at the top of its module with a name and a sentence
  saying it is judgement. A verdict built on one prints itself as a judgement.
- Every rule with a published source cites it from `data/sources.json`. A rule
  without one is marked `heuristic` and says so in its own output.
- Every check has the case that fires it and the neighbouring case that must
  stay silent. `temperature` on an older card, a narrow link at idle, a
  throttle in one sample of fifty: the silent cases are the tests that matter.
- The record — this file, the logbook, code comments, commit messages — is
  written in English.
