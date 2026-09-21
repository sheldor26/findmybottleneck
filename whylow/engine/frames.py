"""What set the pace, frame by frame.

The primitive is Intel PresentMon's: for each presented frame it reports how
long the GPU was busy on it and how long the CPU spent generating it. If the
frame took much longer than the GPU was busy, the GPU was not what held you
back — something else set the pace, and the interesting question is what.

Every threshold in this file is judgement, not a published rule, and is named
here rather than buried in a comparison.
"""

from __future__ import annotations

import statistics
from typing import Dict, List, Optional, Tuple

from ..trace import Frame
from ..verdict import Verdict

# A frame whose GPU-busy time fills this much of it was paced by the GPU.
GPU_BOUND = 0.95
# Likewise for the CPU generating the frame.
CPU_BOUND = 0.95
# Neither one filled the frame: this much unexplained time makes it a stall.
STALL_SHARE = 0.20
# A frame this many times the median is a hitch worth explaining on its own.
HITCH_FACTOR = 2.0
# Frame caps people actually use, plus common refresh rates.
COMMON_CAPS = (30, 60, 72, 75, 90, 100, 120, 144, 165, 180, 240, 280, 360)
# How close the median has to sit to a cap, and how steady the frames have to
# be, before calling it a cap rather than a coincidence.
CAP_TOLERANCE = 0.03
CAP_STEADINESS = 0.08


def classify(frame: Frame) -> str:
    if frame.frame_time <= 0:
        return "unknown"
    if frame.gpu_busy / frame.frame_time >= GPU_BOUND:
        return "gpu"
    if frame.cpu_busy / frame.frame_time >= CPU_BOUND:
        return "cpu"
    unexplained = frame.frame_time - max(frame.gpu_busy, frame.cpu_busy)
    if unexplained / frame.frame_time >= STALL_SHARE:
        return "stall"
    return "mixed"


def percentiles(values: List[float]) -> Dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values)

    def at(fraction: float) -> float:
        index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
        return ordered[index]

    return {
        "median_ms": round(statistics.median(ordered), 2),
        "p99_ms": round(at(0.99), 2),
        "p999_ms": round(at(0.999), 2),
        "median_fps": round(1000.0 / statistics.median(ordered), 1) if statistics.median(ordered) else 0.0,
        "low1_fps": round(1000.0 / at(0.99), 1) if at(0.99) else 0.0,
        "low01_fps": round(1000.0 / at(0.999), 1) if at(0.999) else 0.0,
    }


def looks_capped(frames: List[Frame]) -> Optional[Tuple[int, float]]:
    """Is the frame rate being held at a cap rather than by a component?

    A cap leaves both the CPU and the GPU with time to spare and produces
    unusually even frames. Reporting "your CPU is limiting you" to someone
    sitting on a 60 fps cap is the kind of confident wrong answer this tool
    exists to avoid.
    """
    times = [f.frame_time for f in frames if f.frame_time > 0]
    if len(times) < 30:
        return None
    median = statistics.median(times)
    if median <= 0:
        return None
    spread = statistics.median([abs(t - median) for t in times]) / median
    if spread > CAP_STEADINESS:
        return None
    fps = 1000.0 / median
    for cap in COMMON_CAPS:
        if abs(fps - cap) / cap <= CAP_TOLERANCE:
            headroom = statistics.median([
                1 - (max(f.gpu_busy, f.cpu_busy) / f.frame_time) for f in frames if f.frame_time > 0
            ])
            if headroom > 0.1:
                return cap, headroom
    return None


def analyse(frames: List[Frame]) -> Tuple[Optional[Verdict], List[Verdict], Dict[str, float], List[int]]:
    """The pace verdict, the runners-up, the frame statistics, and the hitches."""
    usable = [f for f in frames if f.frame_time > 0]
    if len(usable) < 10:
        return None, [], {}, []

    stats = percentiles([f.frame_time for f in usable])
    labels = [classify(f) for f in usable]
    counts: Dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1

    median_gpu = statistics.median([f.gpu_busy for f in usable])
    median_cpu = statistics.median([f.cpu_busy for f in usable])
    median_frame = stats["median_ms"]

    cap = looks_capped(usable)
    if cap:
        capped_at, headroom = cap
        verdict = Verdict(
            limiter="frame-cap",
            headline=f"Nothing is limiting you: the frame rate is being held at {capped_at} fps",
            share=1.0,
            evidence=[
                f"median frame time {median_frame} ms, which is {stats['median_fps']} fps",
                f"frames are unusually even, and {round(headroom * 100)}% of each one is spare time",
                "the app asked to sync to the display" if any(f.sync_interval for f in usable)
                else "no sync was requested, so this is an in-game or driver cap",
            ],
            fix="Raise or remove the cap, or turn off vertical sync, and measure again. Until then "
                "no component is being asked for more than it is giving.",
            heuristic=True,
        )
        return verdict, [], stats, hitches(usable, median_frame)

    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    top, top_count = ranked[0]
    share = top_count / len(usable)

    headline = {
        "gpu": "Your graphics card is setting the pace",
        "cpu": "Your processor is setting the pace",
        "stall": "Neither the CPU nor the GPU was busy — something stalled the frames",
        "mixed": "It changes from frame to frame, with no single component in control",
        "unknown": "The frames could not be attributed",
    }[top]

    evidence = [
        f"{round(share * 100)}% of {len(usable)} frames",
        f"median frame time {median_frame} ms · GPU busy {round(median_gpu, 2)} ms · CPU busy {round(median_cpu, 2)} ms",
        f"1% low {stats['low1_fps']} fps against a median of {stats['median_fps']} fps",
    ]

    fix = {
        "gpu": "This is the normal, healthy state — the component you paid the most for is the one "
               "working hardest. To go faster: lower graphics settings or resolution, or buy a faster card.",
        "cpu": "Lowering the resolution will not help: the GPU already has time to spare. Look at "
               "settings that cost CPU — draw distance, crowd density, shadows — and at the "
               "configuration findings below, because a processor that looks slow is often a "
               "processor waiting for memory.",
        "stall": "The frames were waiting on something that is neither the CPU nor the GPU. The "
                 "hitch breakdown below names what was happening at the time.",
        "mixed": "No single component dominates, which usually means the load itself changes — "
                 "different scenes, different limits. Capture during the part that feels worst.",
        "unknown": "",
    }[top]

    verdict = Verdict(
        limiter=top, headline=headline, share=share, evidence=evidence, fix=fix,
        source="gpu_busy",
    )

    secondary = [
        Verdict(limiter=label, headline=f"{label} on {round(count / len(usable) * 100)}% of frames",
                share=count / len(usable))
        for label, count in ranked[1:]
        if count / len(usable) >= 0.1
    ]
    return verdict, secondary, stats, hitches(usable, median_frame)


def hitches(frames: List[Frame], median_frame: float) -> List[int]:
    """Indices of frames that took long enough to be felt."""
    threshold = median_frame * HITCH_FACTOR
    return [i for i, f in enumerate(frames) if f.frame_time >= threshold]
