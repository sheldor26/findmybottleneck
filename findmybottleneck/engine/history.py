"""Trend across multiple captures — the same Trace/Report objects, read
from a folder instead of one file, so a decline shows up before anyone
complains about it. No database: a folder of trace JSON files, already
the format `capture` writes, is the whole storage layer.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from ..trace import Trace
from . import judge


@dataclass
class HistoryEntry:
    path: Path
    captured_at: str
    target: str
    notes: str
    median_fps: Optional[float]
    low1_fps: Optional[float]
    limiter: Optional[str]
    median_gpu_temp: Optional[float]
    os_build: Optional[str]


def scan(folder: Path) -> List[HistoryEntry]:
    """Every readable trace JSON directly inside `folder`, oldest first. A
    file that isn't a findmybottleneck trace (wrong schema, not JSON, a
    stray file) is skipped rather than treated as an error — a folder of
    mixed files, or of traces from an older schema, is the common case."""
    entries: List[HistoryEntry] = []
    for path in sorted(folder.glob("*.json")):
        try:
            trace = Trace.read(path)
        except (OSError, ValueError, KeyError):
            continue
        report = judge(trace)
        temps = [s.temperature for s in trace.gpu if s.temperature is not None]
        entries.append(HistoryEntry(
            path=path, captured_at=trace.captured_at, target=trace.target, notes=trace.notes,
            median_fps=report.frames.get("median_fps") if report.frames else None,
            low1_fps=report.frames.get("low1_fps") if report.frames else None,
            limiter=report.verdict.limiter if report.verdict else None,
            median_gpu_temp=round(statistics.median(temps), 1) if temps else None,
            os_build=trace.hardware.os_build,
        ))
    entries.sort(key=lambda e: e.captured_at)
    return entries


def trend_note(entries: List[HistoryEntry]) -> str:
    """One honest sentence about direction, comparing the earlier half of
    these captures' 1% lows against the later half — the number that is
    "what you feel", same as everywhere else in this project. Empty string
    when there isn't enough data to say anything, rather than a trend line
    drawn through noise."""
    lows = [e.low1_fps for e in entries if e.low1_fps is not None]
    if len(lows) < 4:
        return ""
    half = len(lows) // 2
    earlier, later = statistics.median(lows[:half]), statistics.median(lows[half:])
    if earlier <= 0:
        return ""
    change = (later - earlier) / earlier
    if change <= -0.10:
        return (f"1% low declined {abs(round(change * 100))}% from the earlier half of these "
                f"captures to the later half — {round(earlier)} fps to {round(later)} fps.")
    if change >= 0.10:
        return (f"1% low improved {round(change * 100)}% from the earlier half of these "
                f"captures to the later half — {round(earlier)} fps to {round(later)} fps.")
    return f"1% low has stayed roughly steady across these captures, around {round(later)} fps."


# A GPU running this much hotter across these captures is worth a look —
# dust, degraded thermal paste, a fan curve that changed. This is an
# observation, not a diagnosis: nothing here controls for a hotter room, a
# different game, or a longer session, the same "coincident, not proven"
# caveat engine/hitch.py already states for a single capture's hitches.
THERMAL_RISE_C = 8.0


def thermal_trend_note(entries: List[HistoryEntry]) -> str:
    temps = [e.median_gpu_temp for e in entries if e.median_gpu_temp is not None]
    if len(temps) < 4:
        return ""
    half = len(temps) // 2
    earlier, later = statistics.median(temps[:half]), statistics.median(temps[half:])
    rise = later - earlier
    if rise < THERMAL_RISE_C:
        return ""
    return (f"Median GPU temperature has risen {round(rise)}°C, from {round(earlier)}°C to "
            f"{round(later)}°C, across these captures. Not proof of anything on its own — a "
            f"hotter room or a different game explains this too — but if nothing else changed, "
            f"dust, a degraded thermal paste application, or a fan curve worth checking are the "
            f"usual causes.")


def os_change_note(entries: List[HistoryEntry]) -> str:
    """Names the specific build transition, not just "it got worse
    somewhere" — the earlier/later split `trend_note` uses is arbitrary,
    this one is anchored to an actual recorded `os_build` change, which is
    the whole point: pointing at the update, not just at a vague decline.
    Only the *first* build change with data on both sides is reported —
    good enough to raise the question, not a claim every later change is
    also implicated."""
    with_build = [e for e in entries if e.os_build and e.low1_fps is not None]
    if len(with_build) < 4:
        return ""
    for i in range(1, len(with_build)):
        if with_build[i].os_build != with_build[i - 1].os_build:
            before = [e.low1_fps for e in with_build[:i]]
            after = [e.low1_fps for e in with_build[i:]]
            if len(before) < 2 or len(after) < 2:
                continue
            before_med, after_med = statistics.median(before), statistics.median(after)
            if before_med <= 0:
                continue
            change = (after_med - before_med) / before_med
            if change <= -0.10:
                return (f"1% low dropped {abs(round(change * 100))}% right around a Windows update "
                        f"(build {with_build[i - 1].os_build} → {with_build[i].os_build}) — "
                        f"{round(before_med)} fps to {round(after_med)} fps. Worth checking if a "
                        f"driver update landed at the same time, or rolling back that update to confirm.")
            return ""
    return ""
