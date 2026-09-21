# whylow

Your frame rate is low. Every tool on your screen shows you numbers: GPU 99%,
CPU 34%, 61 °C. None of them tell you what to do about it.

So what actually happens is this — you screenshot HWiNFO, post it on a forum,
and wait twelve hours for a stranger to interpret it. Asked directly whether
any software can name a bottleneck, the answer on Tom's Hardware is one word:
**no**.

`whylow` records thirty seconds while you play, then says what set the pace and
shows the numbers that produced the answer.

```
whylow check
whylow capture cs2.exe
whylow explain
```

## What it says

```
Your processor is setting the pace

  · 87% of 3412 frames
  · median frame time 11.4 ms · GPU busy 6.2 ms · CPU busy 11.1 ms
  · 1% low 54 fps against a median of 88 fps

  Lowering the resolution will not help: the GPU already has time to spare.

Frames
  median 11.4 ms (88.0 fps) · 1% low 54.0 fps · 0.1% low 31.0 fps
  the lows are what you feel. A high average hides them.

What is wrong with the machine

high   Your memory is running at 2133 MHz, and the modules are rated for 6000
         2 of 2 modules below their rating
         Enable the memory profile in your BIOS — XMP on Intel boards, EXPO on
         AMD. It is one setting, it costs nothing, and this is the most common
         reason a fast machine feels slow.
         "ConfiguredClockSpeed: The configured clock speed of the memory
         device, in megahertz (MHz)"
         https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/win32-physicalmemory
```

A processor that looks slow is very often a processor waiting for memory that
never had its profile enabled. That is the single most common real cause in the
wild, and it is invisible to every "bottleneck calculator" on the internet
because none of them ever touch your machine.

## It will not tell you there is no bottleneck

Something always sets the pace. But if you are sitting behind a 60 fps cap,
both your components are idle and the honest answer is not "your CPU is the
problem" — it is:

```
Nothing is limiting you: the frame rate is being held at 60 fps
```

That case is handled first, because telling someone their processor is holding
them back while they sit behind a cap they forgot they set is the most
embarrassing wrong answer this tool could give.

## And it will not judge what it did not measure

A graphics card narrows its PCIe link when idle to save power. Read it at rest
and it says x1 — which looks exactly like a badly seated card. So the link is
judged only from samples taken while the GPU was actually working, and when
there are none:

```
note   The PCIe link was not judged, because the GPU was never busy during the capture
```

Not "fine". Not "broken". Not judged. Every clean run ends with a **Not
measured** list, because "nothing found" is a claim about coverage.

## Hitches

For every frame that took more than twice the median, whylow says what else was
happening at that moment — a throttle, a disk stall, paging, graphics memory
spilling into system RAM — and how many hitches nothing accounts for.

This is labelled as coincidence, not causation, and the reason is honest: frame
data arrives per frame, the counters arrive about once a second. A 95 ms frame
next to a 45 ms disk read happened at the same time. That is what whylow
claims, and no more.

No tool in this category attempts this at all. They stop at the frame time
graph.

## A trace is a file you can send

A capture writes one JSON file, and everything that reasons about performance
reads that file — including on a different machine, a different operating
system, or someone else's laptop.

```
whylow explain someone-elses-trace.json
```

That is the screenshot people already post on forums, except a program can read
it.

## What you need

- **Windows**, to capture. `explain` runs anywhere.
- **An NVIDIA card**, for now, for the telemetry: throttle reasons, power,
  clocks, PCIe link. There is no command-line equivalent that ships with the
  AMD or Intel consumer driver, so on those machines whylow reads frames and
  Windows counters and says what it could not read.
- **[PresentMon](https://github.com/GameTechDev/PresentMon)** (MIT), which is
  the only thing that can attribute a frame to the CPU or the GPU. Put
  `PresentMon.exe` on your PATH or pass `--presentmon`.
- **One Administrator command, once.** PresentMon needs your user in the
  Performance Log Users group:

  ```
  net localgroup "Performance Log Users" "%USERNAME%" /add
  ```

  Sign out and back in. Nothing needs Administrator after that, and `whylow
  check` prints this for you if it is missing.

Python 3.9 or newer. No dependencies.

## Where the method comes from

The per-frame attribution is Intel PresentMon's `GPU Busy`, which is the method
the enthusiast community converged on: if the frame took much longer than the
GPU was busy, the GPU is not what held you back. whylow does not reinvent the
measurement — it adds the attribution and the fix.

Throttle reasons come from NVML's documented event reasons, memory speed from
SMBIOS through CIM, disk latency from Windows performance counters. Every
finding prints its source. A rule with no published sentence behind it is
printed as `(heuristic)` and never dressed up as more.

## License

MIT
