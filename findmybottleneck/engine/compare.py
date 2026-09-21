"""Check a verdict against the resolution-drop test.

Lowering resolution or in-game settings and measuring again is the community's
own gold standard for settling CPU-versus-GPU: a CPU-bound machine barely
moves, because the GPU already had time to spare and giving it less to do
changes nothing. A GPU-bound machine speeds up, because the component that was
full now has room. This module never touches hardware — it takes two already
captured traces and says whether the second one agrees with the first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from ..trace import Trace
from . import frames as frames_engine

# Below this change in median fps, the frame rate is treated as unchanged.
FPS_UNCHANGED_TOLERANCE = 0.08


@dataclass
class CompareResult:
    headline: str
    agreement: str  # "confirms" | "contradicts" | "inconclusive"
    fps_change_pct: float
    evidence: List[str] = field(default_factory=list)
    note: str = ""


def _median_fps(trace: Trace) -> Optional[float]:
    _, _, stats, _ = frames_engine.analyse(trace.frames)
    return stats.get("median_fps") if stats else None


def compare(before: Trace, after: Trace) -> CompareResult:
    before_verdict, _, before_stats, _ = frames_engine.analyse(before.frames)
    after_verdict, _, after_stats, _ = frames_engine.analyse(after.frames)

    if not before_verdict or not after_verdict:
        return CompareResult(
            headline="Not enough frames in one of the two captures to compare them",
            agreement="inconclusive",
            fps_change_pct=0.0,
            note="both captures need at least 10 usable frames",
        )

    before_fps = before_stats["median_fps"]
    after_fps = after_stats["median_fps"]
    change = (after_fps - before_fps) / before_fps if before_fps else 0.0

    evidence = [
        f"before: {before_verdict.headline.lower()} ({before_fps} fps median)",
        f"after:  {after_verdict.headline.lower()} ({after_fps} fps median)",
        f"fps change: {change:+.0%}",
    ]

    limiter = before_verdict.limiter

    if limiter == "frame-cap":
        return CompareResult(
            headline="The first capture was a frame cap, so this test does not apply",
            agreement="inconclusive",
            fps_change_pct=change,
            evidence=evidence,
            note="Remove the cap or turn off vertical sync, capture again, and compare from there.",
        )

    if limiter in ("mixed", "unknown", "stall"):
        return CompareResult(
            headline=f"The first capture was '{limiter}', which this test cannot confirm or contradict",
            agreement="inconclusive",
            fps_change_pct=change,
            evidence=evidence,
            note="This test only checks a gpu or cpu verdict. A stall or mixed result needs the "
                 "hitch breakdown, not a resolution change.",
        )

    unchanged = abs(change) < FPS_UNCHANGED_TOLERANCE

    if limiter == "cpu":
        if unchanged:
            return CompareResult(
                headline="Confirmed: lowering the load did not move the frame rate",
                agreement="confirms",
                fps_change_pct=change,
                evidence=evidence,
                note="This is exactly what a CPU-bound machine looks like — the GPU already had "
                     "time to spare, so giving it less to do changed nothing.",
            )
        return CompareResult(
            headline=f"Contradicted: the frame rate moved {change:+.0%} after lowering the load",
            agreement="contradicts",
            fps_change_pct=change,
            evidence=evidence,
            note="A CPU-bound verdict should not have moved this much. Treat the first verdict as "
                 "unconfirmed — the two captures may not have been comparable (different scene, "
                 "different load).",
        )

    if limiter == "gpu":
        if not unchanged and change > 0:
            return CompareResult(
                headline=f"Confirmed: the frame rate rose {change:+.0%} after lowering the load",
                agreement="confirms",
                fps_change_pct=change,
                evidence=evidence,
                note="This is exactly what a GPU-bound machine looks like — the component that was "
                     "full now has room.",
            )
        return CompareResult(
            headline="Contradicted: lowering the load did not raise the frame rate",
            agreement="contradicts",
            fps_change_pct=change,
            evidence=evidence,
            note="A GPU-bound verdict should have scaled with the load. Treat the first verdict as "
                 "unconfirmed — the two captures may not have been comparable (different scene, "
                 "different load).",
        )

    return CompareResult(
        headline="Could not compare these two captures",
        agreement="inconclusive",
        fps_change_pct=change,
        evidence=evidence,
    )
