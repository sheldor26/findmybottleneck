"""Check a verdict against the resolution-drop test.

Lowering resolution or in-game settings and measuring again is the community's
own gold standard for settling CPU-versus-GPU: a CPU-bound machine barely
moves, because the GPU already had time to spare and giving it less to do
changes nothing. A GPU-bound machine speeds up, because the component that was
full now has room. This module never touches hardware — it takes two already
captured traces and says whether the second one agrees with the first.

This is a heuristic check, not a proof. The 8% tolerance is judgement, the
same way every other threshold in this package is, and this checks arithmetic,
not intent: it has no way to know whether the person actually lowered
anything between the two captures, only whether the frame rate moved. Each
result names the specific way that blind spot could fool it for that
particular outcome — the risk is not the same shape for every branch (see
below), so there is no single disclaimer that is true of all of them.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import List

from ..trace import Trace
from . import frames as frames_engine

# Below this change in median fps, the frame rate is treated as unchanged.
# Judgement, not a published rule — the same convention as every threshold
# elsewhere in engine/.
FPS_UNCHANGED_TOLERANCE = 0.08


@dataclass
class CompareResult:
    headline: str
    agreement: str  # "confirms" | "contradicts" | "inconclusive"
    fps_change_pct: float
    evidence: List[str] = field(default_factory=list)
    note: str = ""


def _raw_median_fps(trace: Trace) -> float:
    """The median fps, computed from unrounded frame times.

    frames.percentiles() rounds median_fps to one decimal place for display —
    fine for a report, but comparing two already-rounded numbers against an 8%
    tolerance can flip the verdict right at the boundary. This recomputes the
    median directly from the frame times so the comparison is exact.
    """
    times = [f.frame_time for f in trace.frames if f.frame_time > 0]
    if not times:
        return 0.0
    median_ms = statistics.median(times)
    return 1000.0 / median_ms if median_ms else 0.0


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

    before_fps_raw = _raw_median_fps(before)
    after_fps_raw = _raw_median_fps(after)
    change = (after_fps_raw - before_fps_raw) / before_fps_raw if before_fps_raw else 0.0

    evidence = [
        f"before: {before_verdict.headline.lower()} ({before_stats['median_fps']} fps median)",
        f"after:  {after_verdict.headline.lower()} ({after_stats['median_fps']} fps median)",
        f"fps change: {change:+.1%}",
    ]

    limiter = before_verdict.limiter
    after_limiter = after_verdict.limiter

    # Both sides have to be a plain gpu/cpu verdict for the comparison to mean
    # anything — an "after" capture that landed on a cap, a stall, or a mixed
    # result invalidates the experiment just as surely as a bad "before" would.
    if limiter == "frame-cap" or after_limiter == "frame-cap":
        side = "before" if limiter == "frame-cap" else "after"
        return CompareResult(
            headline=f"The {side} capture was a frame cap, so this test does not apply",
            agreement="inconclusive",
            fps_change_pct=change,
            evidence=evidence,
            note="Remove the cap or turn off vertical sync, capture again, and compare from there.",
        )

    inapplicable = ("mixed", "unknown", "stall")
    if limiter in inapplicable or after_limiter in inapplicable:
        side, bad_limiter = (("before", limiter) if limiter in inapplicable
                             else ("after", after_limiter))
        return CompareResult(
            headline=f"The {side} capture was '{bad_limiter}', which this test cannot confirm or contradict",
            agreement="inconclusive",
            fps_change_pct=change,
            evidence=evidence,
            note="This test only checks a gpu or cpu verdict on both sides. A stall or mixed result "
                 "needs the hitch breakdown, not a resolution change.",
        )

    unchanged = abs(change) < FPS_UNCHANGED_TOLERANCE

    if limiter == "cpu":
        if unchanged:
            return CompareResult(
                headline="Consistent with a CPU-bound machine: the frame rate barely moved",
                agreement="confirms",
                fps_change_pct=change,
                evidence=evidence,
                note=f"The GPU already had time to spare, so giving it less to do changed little "
                     f"(within the {round(FPS_UNCHANGED_TOLERANCE * 100)}% tolerance this test uses "
                     f"for 'unchanged', which is judgement, not a measured limit). But this is also "
                     f"exactly what two identical captures — nothing actually lowered between them — "
                     f"would show, so on its own this does not prove anything was changed.",
            )
        return CompareResult(
            headline=f"Contradicted: the frame rate moved {change:+.1%} after lowering the load",
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
                headline=f"Consistent with a GPU-bound machine: the frame rate rose {change:+.1%}",
                agreement="confirms",
                fps_change_pct=change,
                evidence=evidence,
                note=f"The component that was full now has room, which is why lowering the load "
                     f"raised fps beyond this test's {round(FPS_UNCHANGED_TOLERANCE * 100)}% "
                     f"tolerance for 'unchanged' — judgement, not a measured limit. This does mean "
                     f"something changed between the two captures, but not necessarily the thing you "
                     f"intended: a lighter section of the same scene raises fps just as well as a "
                     f"lower resolution does.",
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
