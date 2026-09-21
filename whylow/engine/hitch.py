"""Why each hitch happened.

This is the part nobody does. Every tool in this category stops at the frame
time graph: here is the spike, good luck. What follows is an attempt to say
what was happening at the moment of the spike — and, just as importantly, to
say "unexplained" when nothing in the capture accounts for it.

The honesty problem is real and is handled explicitly. The frame data is
per-frame; the counters are sampled about once a second. So a counter can only
ever be *coincident* with a hitch, never proven to have caused it. Every cause
here is labelled as coincident evidence, and a cause that matches a single
sample is weaker than one that matches several.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..trace import Frame, Trace

# How far from the hitch a sample may be and still count as coincident.
WINDOW_S = 1.0
DISK_STALL_S = 0.020
LOW_MEMORY_MB = 1024
PAGES_SPIKE = 1000


def _nearest(samples, when: float, window: float = WINDOW_S):
    return [s for s in samples if abs(getattr(s, "time", -1e9) - when) <= window]


def explain(trace: Trace, hitch_indices: List[int], frames: List[Frame]) -> List[Dict]:
    out: List[Dict] = []
    for index in hitch_indices:
        frame = frames[index]
        causes: List[Dict] = []

        for sample in _nearest(trace.gpu, frame.time):
            for flag, value in (sample.throttle or {}).items():
                if value:
                    causes.append({"cause": f"gpu throttled ({flag})", "detail": f"at t={round(sample.time, 1)}s"})
            if sample.memory_used and sample.memory_total and sample.memory_used / sample.memory_total >= 0.98:
                causes.append({"cause": "graphics memory full",
                               "detail": f"{round(sample.memory_used)} of {round(sample.memory_total)} MiB"})

        for sample in _nearest(trace.disk, frame.time):
            worst = max(sample.read_latency or 0, sample.write_latency or 0)
            if worst >= DISK_STALL_S:
                causes.append({"cause": "disk stall", "detail": f"{round(worst * 1000)} ms average latency"})

        for sample in _nearest(trace.memory, frame.time):
            if sample.available_mb is not None and sample.available_mb < LOW_MEMORY_MB:
                causes.append({"cause": "system memory nearly full",
                               "detail": f"{round(sample.available_mb)} MiB available"})
            if sample.pages_per_sec and sample.pages_per_sec > PAGES_SPIKE:
                causes.append({"cause": "paging", "detail": f"{round(sample.pages_per_sec)} pages/sec"})
            if sample.gpu_shared_mb and sample.gpu_shared_mb > 0:
                causes.append({"cause": "graphics memory spilled to system memory",
                               "detail": f"{round(sample.gpu_shared_mb)} MiB"})

        # The frame's own numbers say which side was idle while it waited.
        if frame.gpu_busy / frame.frame_time < 0.5 and frame.cpu_busy / frame.frame_time < 0.5:
            causes.append({"cause": "both CPU and GPU were idle during this frame",
                           "detail": f"frame {round(frame.frame_time, 1)} ms, "
                                     f"gpu busy {round(frame.gpu_busy, 1)} ms, "
                                     f"cpu busy {round(frame.cpu_busy, 1)} ms"})

        out.append({
            "at_s": round(frame.time, 2),
            "frame_ms": round(frame.frame_time, 1),
            "causes": _dedupe(causes),
        })
    return out


def _dedupe(causes: List[Dict]) -> List[Dict]:
    seen = {}
    for cause in causes:
        seen.setdefault(cause["cause"], cause)
    return list(seen.values())


def summarise(hitches: List[Dict]) -> Dict:
    """How many hitches, and how many of them anything accounts for."""
    if not hitches:
        return {"count": 0, "explained": 0, "by_cause": {}}
    by_cause: Dict[str, int] = {}
    explained = 0
    for hitch in hitches:
        if hitch["causes"]:
            explained += 1
        for cause in hitch["causes"]:
            by_cause[cause["cause"]] = by_cause.get(cause["cause"], 0) + 1
    return {
        "count": len(hitches),
        "explained": explained,
        "unexplained": len(hitches) - explained,
        "by_cause": dict(sorted(by_cause.items(), key=lambda kv: kv[1], reverse=True)),
    }
