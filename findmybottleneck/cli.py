"""findmybottleneck — records your PC and names what is limiting your frame rate."""

from __future__ import annotations

import argparse
import json
import platform
import sys
from dataclasses import asdict
from pathlib import Path

__version__ = "0.1.0"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="findmybottleneck",
        description="Record a running game for a few seconds, then say what set the pace.",
    )
    sub = parser.add_subparsers(dest="command")

    capture = sub.add_parser("capture", help="record a trace (Windows only)")
    capture.add_argument("process", nargs="?", help="the game's executable, e.g. cs2.exe")
    capture.add_argument("--seconds", type=int, default=30)
    capture.add_argument("--out", default="bottleneck-trace.json")
    capture.add_argument("--presentmon", help="path to PresentMon.exe, if it is not on PATH")
    capture.add_argument("--keep-csv", action="store_true", help="keep PresentMon's raw CSV beside the trace")

    explain = sub.add_parser("explain", help="read a trace and print the verdict")
    explain.add_argument("trace", nargs="?", default="bottleneck-trace.json")
    explain.add_argument("--json", action="store_true")
    explain.add_argument("--no-sources", action="store_true")

    compare = sub.add_parser(
        "compare", help="check a bottleneck verdict by comparing two captures (the resolution-drop test)")
    compare.add_argument("before", help="the original capture")
    compare.add_argument("after", help="a capture taken after lowering resolution or in-game settings")
    compare.add_argument("--json", action="store_true")

    overlay = sub.add_parser(
        "overlay", help="push a live rolling verdict into RTSS's on-screen display (Windows only, needs RTSS running)")
    overlay.add_argument("process", help="the game's executable, e.g. cs2.exe")
    overlay.add_argument("--presentmon", help="path to PresentMon.exe, if it is not on PATH")
    overlay.add_argument("--window-seconds", type=float, default=3.0,
                          help="how many recent seconds of frames the live verdict is judged from")
    overlay.add_argument("--refresh-ms", type=int, default=1000,
                          help="how often the on-screen text is updated")

    check = sub.add_parser("check", help="what findmybottleneck can and cannot read on this machine")

    sub.add_parser("gui", help="a desktop window for check/capture/explain (needs: pip install findmybottleneck[gui])")

    parser.add_argument("--version", action="version", version=__version__)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "explain":
        from .engine import judge
        from .report import render
        from .trace import Trace

        path = Path(args.trace)
        if not path.exists():
            print(f"no trace at {path}. Record one with: findmybottleneck capture <game.exe>", file=sys.stderr)
            return 2
        report = judge(Trace.read(path))
        if args.json:
            print(json.dumps(asdict(report), indent=2))
        else:
            render(report, show_sources=not args.no_sources)
        return 0

    if args.command == "compare":
        from .engine.compare import compare as do_compare
        from .report import render_compare
        from .trace import Trace

        for label, raw in (("before", args.before), ("after", args.after)):
            if not Path(raw).exists():
                print(f"no trace at {raw} (the {label} capture)", file=sys.stderr)
                return 2
        result = do_compare(Trace.read(Path(args.before)), Trace.read(Path(args.after)))
        if args.json:
            print(json.dumps(asdict(result), indent=2))
        else:
            render_compare(result)
        return 0

    if args.command == "overlay":
        if platform.system() != "Windows":
            print("overlay only runs on Windows — it needs RTSS, which is Windows-only.", file=sys.stderr)
            return 2
        from .collect.windows import run_overlay
        return run_overlay(args)

    if args.command == "capture":
        if platform.system() != "Windows":
            print("capture only runs on Windows — the counters it reads do not exist elsewhere.",
                  file=sys.stderr)
            print("You can still read a trace recorded on a Windows machine: findmybottleneck explain <trace>",
                  file=sys.stderr)
            return 2
        from .collect.windows import capture as do_capture
        return do_capture(args)

    if args.command == "gui":
        try:
            from .gui.app import launch
        except ImportError:
            print("The GUI needs an extra: pip install findmybottleneck[gui]", file=sys.stderr)
            return 2
        launch()
        return 0

    if args.command == "check":
        if platform.system() != "Windows":
            print("This machine is not Windows, so there is nothing to check.")
            return 0
        from .collect.windows import check as do_check
        return do_check()

    build_parser().print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
