"""Everything that reaches the terminal.

The category this tool lands in is full of sites that print a single
percentage with no defined denominator. So the output leads with a sentence in
plain language, shows the numbers that produced it, and ends with what was not
measured. There is no score.
"""

from __future__ import annotations

import os
import sys
import textwrap
from typing import List

from .engine.compare import CompareResult
from .engine.history import HistoryEntry
from .verdict import Finding, Report

_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _paint(code: str):
    return lambda text: f"\033[{code}m{text}\033[0m" if _COLOR else text


bold, dim, red, yellow, blue, green, cyan = (
    _paint("1"), _paint("2"), _paint("31"), _paint("33"), _paint("34"), _paint("32"), _paint("36"))

LABEL = {"high": red("high  "), "medium": yellow("medium"), "low": blue("low   "), "note": dim("note  ")}
ORDER = {"high": 0, "medium": 1, "low": 2, "note": 3}


def _wrap(text: str, width: int = 74, indent: str = "  ") -> List[str]:
    return [indent + line for line in textwrap.wrap(text, width=width)] or [indent]


def render(report: Report, show_sources: bool = True) -> None:
    print()
    if report.notes:
        print(dim(f"  “{report.notes}”"))
        print()
    verdict = report.verdict
    if verdict is None:
        print(f"{yellow('No verdict.')} The capture has too few frames to attribute anything.\n")
    else:
        print(bold(verdict.headline))
        print()
        for line in verdict.evidence:
            print(f"  {dim('·')} {line}")
        if verdict.heuristic:
            print(f"  {dim('· this one is a judgement of ours, not a published rule')}")
        print()
        for line in _wrap(verdict.fix):
            print(line)
        print()
        if verdict.limiter in ("gpu", "cpu"):
            print(dim("  To check this: capture again after lowering resolution or in-game settings,"))
            print(dim("  then run: findmybottleneck compare <first trace> <second trace>"))
            print()
        if report.secondary:
            print(dim("  it was not the same on every frame: " +
                      ", ".join(f"{v.limiter} on {round(v.share * 100)}%" for v in report.secondary)))
            print()

    if report.frames:
        f = report.frames
        print(bold("Frames"))
        print(f"  median {f['median_ms']} ms ({f['median_fps']} fps) · "
              f"1% low {f['low1_fps']} fps · 0.1% low {f['low01_fps']} fps")
        print(dim("  the lows are what you feel. A high average hides them."))
        print()

    if report.hitches:
        from .engine.hitch import summarise
        s = summarise(report.hitches)
        print(bold(f"Hitches: {s['count']}, of which {s['explained']} have something to blame"))
        for cause, count in s["by_cause"].items():
            print(f"  {count:>3}  {cause}")
        if s["unexplained"]:
            print(f"  {s['unexplained']:>3}  {dim('unexplained — nothing in this capture accounts for them')}")
        print()
        print(dim("  Coincident, not proven: the counters are sampled about once a second and a"))
        print(dim("  hitch lasts milliseconds. What you get is what was happening at the time."))
        print()
        for hitch in report.hitches[:5]:
            causes = ", ".join(c["cause"] for c in hitch["causes"]) or "unexplained"
            print(f"  t={hitch['at_s']:>6}s  {hitch['frame_ms']:>6} ms  {causes}")
        if len(report.hitches) > 5:
            print(dim(f"  … and {len(report.hitches) - 5} more"))
        print()

    if report.findings:
        print(bold("What is wrong with the machine"))
        print()
        for finding in sorted(report.findings, key=lambda f: ORDER[f.severity]):
            tag = dim(" (heuristic)") if finding.heuristic else ""
            print(f"{LABEL[finding.severity]} {bold(finding.title)}{tag}")
            for line in finding.evidence:
                print(f"         {dim(line)}")
            for line in _wrap(finding.fix, width=66, indent="         "):
                print(line)
            if show_sources and finding.source:
                if finding.quote:
                    for line in _wrap(f'"{finding.quote}"', width=66, indent="         "):
                        print(dim(line))
                print(f"         {dim(finding.source)}")
            print()

    print(bold("Not measured"))
    if report.not_measured:
        for item in report.not_measured:
            print(f"  {dim('·')} {item}")
    else:
        print(dim("  everything this version knows how to read was read"))
    print()
    print(dim("  findmybottleneck does not know whether the component is worth replacing, what anything"))
    print(dim("  costs, or how another part would perform. It measures your machine running"))
    print(dim("  your game, and says what set the pace."))
    print()


AGREEMENT_LABEL = {
    "confirms": green("confirms"),
    "contradicts": red("contradicts"),
    "inconclusive": yellow("inconclusive"),
}


def render_compare(result: CompareResult) -> None:
    print()
    print(bold(result.headline))
    print(f"  {dim('·')} {AGREEMENT_LABEL[result.agreement]}")
    print()
    for line in result.evidence:
        print(f"  {dim('·')} {line}")
    if result.note:
        print()
        for line in _wrap(result.note):
            print(line)
    print()


def render_history(entries: List[HistoryEntry], note: str, thermal_note: str = "",
                   build_note: str = "") -> None:
    print()
    if not entries:
        print(dim("  no readable trace files found in this folder"))
        print()
        return
    print(bold(f"{len(entries)} captures"))
    print()
    for entry in entries:
        target = entry.target or "?"
        limiter = entry.limiter or "no verdict"
        median = f"{entry.median_fps} fps" if entry.median_fps is not None else "—"
        low1 = f"{entry.low1_fps} fps 1% low" if entry.low1_fps is not None else "—"
        line = f"  {entry.captured_at}  {target:<16}  {limiter:<10}  {median:>10}  {low1:>16}"
        print(line)
        if entry.notes:
            print(dim(f"      “{entry.notes}”"))
    print()
    if note:
        for line in _wrap(note):
            print(line)
        print()
    if thermal_note:
        for line in _wrap(thermal_note):
            print(line)
        print()
    if build_note:
        for line in _wrap(build_note):
            print(line)
        print()
