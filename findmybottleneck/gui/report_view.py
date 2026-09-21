"""Renders a Report into CustomTkinter widgets.

The GUI's counterpart to report.py: "everything that reaches the terminal"
becomes "everything that reaches the window". Nothing here decides what to
show or in what order — it mirrors report.py's structure (the same verdict
headline, the same evidence lines, findings in the same severity order, the
same "not measured" honesty) so the two never drift apart in content, only
in medium. If a rule changes what it reports, both update themselves from
the same Report object; neither hardcodes a second opinion about what
matters.
"""

from __future__ import annotations

import customtkinter as ctk

LIMITER_COLOR = {
    "gpu": "#3B82F6",        # blue
    "cpu": "#F97316",        # orange
    "stall": "#EF4444",      # red
    "frame-cap": "#22C55E",  # green — good news, nothing is limiting you
    "mixed": "#A855F7",      # purple
    "unknown": "#9CA3AF",    # grey
}

SEVERITY_COLOR = {
    "high": "#EF4444",
    "medium": "#F59E0B",
    "low": "#3B82F6",
    "note": "#6B7280",
}
# Same ordering report.py's ORDER dict uses.
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "note": 3}

_OK_COLOR = "#22C55E"
_TEXT = "#F4F4F5"
_MUTED = "#8A8A93"
_CARD_BG = "#18181C"
_BORDER = "#26262B"
_TILE_BG = "#18181C"
_WRAP = 620


def _label(parent, text, *, size=13, weight="normal", slant="roman", color=None, wrap=_WRAP):
    return ctk.CTkLabel(
        parent, text=text, justify="left", anchor="w", wraplength=wrap,
        font=ctk.CTkFont(size=size, weight=weight, slant=slant),
        text_color=color if color is not None else _TEXT,
    )


def render_message(container: ctk.CTkScrollableFrame, text: str) -> None:
    """A placeholder or error state — nothing to render as a Report yet."""
    for child in container.winfo_children():
        child.destroy()
    _label(container, text, size=14, color=_MUTED).pack(anchor="w", pady=20, fill="x")


def render(container: ctk.CTkScrollableFrame, report) -> None:
    """Clear `container` and rebuild it from `report`, top to bottom, the
    same order report.render() prints in."""
    for child in container.winfo_children():
        child.destroy()

    _stat_row(container, report)

    if report.notes:
        _label(container, f"“{report.notes}”", size=13, slant="italic", color=_MUTED).pack(
            anchor="w", fill="x", pady=(0, 12))

    if report.verdict is None:
        _label(container, "No verdict. The capture has too few frames to attribute anything.",
               size=14, color=_MUTED).pack(anchor="w", fill="x", pady=(4, 16))
    else:
        _verdict_card(container, report)

    if report.frames:
        _frames_card(container, report.frames)

    if report.hitches:
        _hitches_card(container, report)

    if report.findings:
        _label(container, "What is wrong with the machine", size=16, weight="bold").pack(
            anchor="w", pady=(20, 8))
        for finding in sorted(report.findings, key=lambda f: SEVERITY_ORDER.get(f.severity, 9)):
            _finding_card(container, finding)

    _not_measured_card(container, report.not_measured)


def _card(parent) -> ctk.CTkFrame:
    frame = ctk.CTkFrame(parent, corner_radius=12, fg_color=_CARD_BG,
                         border_width=1, border_color=_BORDER)
    frame.pack(fill="x", pady=(0, 14))
    return frame


def _stat_tile(parent, label: str, value: str, color: str) -> ctk.CTkFrame:
    tile = ctk.CTkFrame(parent, corner_radius=12, fg_color=_TILE_BG,
                        border_width=1, border_color=_BORDER)
    ctk.CTkLabel(tile, text=value, font=ctk.CTkFont(size=24, weight="bold"),
                text_color=color).pack(anchor="w", padx=16, pady=(14, 0))
    ctk.CTkLabel(tile, text=label, font=ctk.CTkFont(size=12), text_color=_MUTED).pack(
        anchor="w", padx=16, pady=(2, 14))
    return tile


def _stat_row(container, report) -> None:
    """The at-a-glance row: verdict, median FPS, 1% low, findings — the
    numbers a screenshot of this window should carry on its own, before
    anyone reads a single evidence line below."""
    row = ctk.CTkFrame(container, fg_color="transparent")
    row.pack(fill="x", pady=(0, 16))
    for i in range(4):
        row.grid_columnconfigure(i, weight=1, uniform="stat")

    stats = report.frames or {}
    v = report.verdict
    verdict_color = LIMITER_COLOR.get(v.limiter, LIMITER_COLOR["unknown"]) if v else _MUTED
    verdict_text = v.limiter.replace("-", " ").upper() if v else "N/A"

    if report.findings:
        worst = min(report.findings, key=lambda f: SEVERITY_ORDER.get(f.severity, 9))
        findings_color = SEVERITY_COLOR.get(worst.severity, SEVERITY_COLOR["note"])
    else:
        findings_color = _OK_COLOR

    tiles = [
        ("Verdict", verdict_text, verdict_color),
        ("Median FPS", str(stats.get("median_fps", "—")), "#3B82F6"),
        ("1% Low FPS", str(stats.get("low1_fps", "—")), "#F59E0B"),
        ("Findings", str(len(report.findings)), findings_color),
    ]
    for i, (label, value, color) in enumerate(tiles):
        tile = _stat_tile(row, label, value, color)
        tile.grid(row=0, column=i, padx=(0 if i == 0 else 8, 0), sticky="nsew")


def _verdict_card(container, report) -> None:
    v = report.verdict
    color = LIMITER_COLOR.get(v.limiter, LIMITER_COLOR["unknown"])
    card = _card(container)
    _label(card, v.headline, size=20, weight="bold", color=color).pack(
        anchor="w", padx=18, pady=(18, 10), fill="x")

    for line in v.evidence:
        _label(card, f"·  {line}", size=13, color=_MUTED).pack(anchor="w", padx=18, pady=1, fill="x")
    if v.heuristic:
        _label(card, "·  this one is a judgement of ours, not a published rule",
               size=12, slant="italic", color=_MUTED).pack(anchor="w", padx=18, pady=(1, 8), fill="x")
    else:
        ctk.CTkFrame(card, height=1, fg_color="transparent").pack(fill="x", pady=4)

    if v.fix:
        _label(card, v.fix, size=14).pack(anchor="w", padx=18, pady=(6, 18), fill="x")

    if report.secondary:
        summary = ", ".join(f"{s.limiter} on {round(s.share * 100)}%" for s in report.secondary)
        _label(card, f"it was not the same on every frame: {summary}", size=12, color=_MUTED).pack(
            anchor="w", padx=18, pady=(0, 16), fill="x")


def _frames_card(container, stats: dict) -> None:
    card = _card(container)
    _label(card, "Frames", size=15, weight="bold").pack(anchor="w", padx=18, pady=(14, 4))
    line = (f"median {stats.get('median_ms')} ms ({stats.get('median_fps')} fps)  ·  "
            f"1% low {stats.get('low1_fps')} fps  ·  0.1% low {stats.get('low01_fps')} fps")
    _label(card, line, size=13).pack(anchor="w", padx=18, pady=2, fill="x")
    _label(card, "the lows are what you feel. A high average hides them.", size=12, color=_MUTED).pack(
        anchor="w", padx=18, pady=(2, 14), fill="x")


def _hitches_card(container, report) -> None:
    from ..engine.hitch import summarise
    s = summarise(report.hitches)
    card = _card(container)
    _label(card, f"Hitches: {s['count']}, of which {s['explained']} have something to blame",
           size=15, weight="bold").pack(anchor="w", padx=18, pady=(14, 6))
    for cause, count in s["by_cause"].items():
        _label(card, f"{count:>3}   {cause}", size=13).pack(anchor="w", padx=18, pady=1, fill="x")
    if s.get("unexplained"):
        _label(card, f"{s['unexplained']:>3}   unexplained — nothing in this capture accounts for them",
               size=13, color=_MUTED).pack(anchor="w", padx=18, pady=1, fill="x")
    _label(card, "Coincident, not proven: the counters are sampled about once a second and a hitch "
                 "lasts milliseconds. What you get is what was happening at the time.",
           size=12, color=_MUTED).pack(anchor="w", padx=18, pady=(8, 6), fill="x")
    for hitch in report.hitches[:5]:
        causes = ", ".join(c["cause"] for c in hitch["causes"]) or "unexplained"
        _label(card, f"t={hitch['at_s']:>6}s   {hitch['frame_ms']:>6} ms   {causes}", size=12).pack(
            anchor="w", padx=18, pady=1, fill="x")
    if len(report.hitches) > 5:
        _label(card, f"… and {len(report.hitches) - 5} more", size=12, color=_MUTED).pack(
            anchor="w", padx=18, pady=(1, 14), fill="x")
    else:
        ctk.CTkFrame(card, height=1, fg_color="transparent").pack(fill="x", pady=6)


def _finding_card(container, finding) -> None:
    color = SEVERITY_COLOR.get(finding.severity, SEVERITY_COLOR["note"])
    card = _card(container)
    header = ctk.CTkFrame(card, fg_color="transparent")
    header.pack(anchor="w", fill="x", padx=18, pady=(14, 4))

    badge = ctk.CTkLabel(header, text=finding.severity.upper(), font=ctk.CTkFont(size=11, weight="bold"),
                         text_color="white", fg_color=color, corner_radius=6, width=64, height=22)
    badge.pack(side="left", padx=(0, 10))
    title = finding.title + ("  (heuristic)" if finding.heuristic else "")
    _label(header, title, size=14, weight="bold").pack(side="left", fill="x", expand=True)

    for line in finding.evidence:
        _label(card, line, size=12, color=_MUTED).pack(anchor="w", padx=18, pady=1, fill="x")
    if finding.fix:
        _label(card, finding.fix, size=13).pack(anchor="w", padx=18, pady=(6, 8), fill="x")
    if finding.source:
        if finding.quote:
            _label(card, f'"{finding.quote}"', size=11, slant="italic", color=_MUTED).pack(
                anchor="w", padx=18, pady=(0, 2), fill="x")
        _label(card, finding.source, size=11, color=_MUTED).pack(anchor="w", padx=18, pady=(0, 14), fill="x")
    else:
        ctk.CTkFrame(card, height=1, fg_color="transparent").pack(fill="x", pady=6)


def _not_measured_card(container, not_measured) -> None:
    card = _card(container)
    _label(card, "Not measured", size=15, weight="bold").pack(anchor="w", padx=18, pady=(14, 6))
    if not_measured:
        for item in not_measured:
            _label(card, f"·  {item}", size=13, color=_MUTED).pack(anchor="w", padx=18, pady=2, fill="x")
    else:
        _label(card, "everything this version knows how to read was read", size=13, color=_MUTED).pack(
            anchor="w", padx=18, pady=(2, 8), fill="x")
    _label(card, "findmybottleneck does not know whether the component is worth replacing, what "
                 "anything costs, or how another part would perform. It measures your machine "
                 "running your game, and says what set the pace.",
           size=11, color=_MUTED).pack(anchor="w", padx=18, pady=(8, 16), fill="x")
