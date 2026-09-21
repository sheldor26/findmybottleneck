"""The reasoning. Reads a trace, writes a report. Never touches hardware."""

from __future__ import annotations

import json
from pathlib import Path

from ..trace import Trace
from ..verdict import Report
from . import config, frames, hitch

SOURCES = json.loads((Path(__file__).parent.parent / "data" / "sources.json").read_text(encoding="utf-8"))


def judge(trace: Trace) -> Report:
    report = Report()
    verdict, secondary, stats, hitch_indices = frames.analyse(trace.frames)
    usable = [f for f in trace.frames if f.frame_time > 0]

    report.verdict = verdict
    report.secondary = secondary
    report.frames = stats
    report.hitches = hitch.explain(trace, hitch_indices, usable) if hitch_indices else []

    report.findings = (
        config.memory(trace, SOURCES)
        + config.throttling(trace, SOURCES)
        + config.vram(trace, SOURCES)
        + config.pcie_link(trace, SOURCES)
    )

    if not trace.frames:
        report.not_measured.append("what paced the frames — the capture has no frame data")
    if not trace.gpu:
        report.not_measured.append("graphics card telemetry: throttling, memory, PCIe link")
    if not trace.hardware.memory_modules:
        report.not_measured.append("memory configuration")
    if not trace.disk:
        report.not_measured.append("disk latency")
    for what, why in (trace.missing or {}).items():
        report.not_measured.append(f"{what} ({why})")
    return report
