"""The window: check this machine, capture a game, see the verdict.

Three tabs, one per step. Nothing here computes a verdict or decides what a
capture should read — it drives the same functions the CLI drives
(`collect.windows.check_status`, `collect.windows.run_capture`,
`engine.judge`) and hands the result to `report_view`. The GUI is a third
sink, exactly like `report.py` (terminal) and `rtss.py` (RTSS's OSD).

Threading: `run_capture` blocks for the whole recording, so it runs in a
background `threading.Thread`. The one rule that matters for Tkinter/CTk is
that only the main thread touches widgets — the worker thread only ever
pushes plain tuples into a `queue.Queue`, and the main loop drains it via
`self.after(...)`, the standard safe pattern for this.
"""

from __future__ import annotations

import platform
import queue
import subprocess
import threading
import tkinter.filedialog as filedialog
from pathlib import Path
from typing import List, Optional

import customtkinter as ctk

from . import report_view
from ..collect.windows import CheckItem, check_status, run_capture
from ..engine import judge
from ..trace import Trace

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

_ACCENT_HOVER = "#1D4ED8"
_OK_COLOR = "#22C55E"
_MISS_COLOR = "#EF4444"
_MUTED = ("gray30", "gray65")


def _running_process_names() -> List[str]:
    """Processes currently running, for the Capture tab's dropdown — the same
    "shell out to what's already there" pattern the rest of the collector
    uses (D-0005), not a new dependency. Windows only; the field is still
    free text everywhere else."""
    if platform.system() != "Windows":
        return []
    try:
        out = subprocess.run(["tasklist", "/fo", "csv", "/nh"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    names = []
    for line in out.strip().splitlines():
        cells = [c.strip('"') for c in line.split('","')]
        if cells and cells[0].lower().endswith(".exe"):
            names.append(cells[0])
    return sorted(set(names))


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("findmybottleneck")
        self.geometry("780x660")
        self.minsize(640, 520)

        self._progress_queue: "queue.Queue" = queue.Queue()
        self._last_trace_path: Optional[Path] = None
        self._progress_bar_indeterminate = False

        self._tabs = ctk.CTkTabview(self, corner_radius=12)
        self._tabs.pack(fill="both", expand=True, padx=16, pady=16)
        self.tab_check = self._tabs.add("1. Check")
        self.tab_capture = self._tabs.add("2. Capture")
        self.tab_report = self._tabs.add("3. Report")

        self._build_check_tab()
        self._build_capture_tab()
        self._build_report_tab()

    # ------------------------------------------------------------ Check

    def _build_check_tab(self) -> None:
        tab = self.tab_check
        ctk.CTkLabel(tab, text="What this machine can be read for",
                    font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=8, pady=(12, 8))
        self._check_button = ctk.CTkButton(tab, text="Check this machine", hover_color=_ACCENT_HOVER,
                                           command=self._on_check)
        self._check_button.pack(anchor="w", padx=8, pady=(0, 12))
        self._check_list = ctk.CTkScrollableFrame(tab, corner_radius=12)
        self._check_list.pack(fill="both", expand=True, padx=8, pady=(0, 12))

    def _on_check(self) -> None:
        self._check_button.configure(state="disabled", text="Checking…")
        for child in self._check_list.winfo_children():
            child.destroy()

        def work() -> None:
            items = check_status()
            self.after(0, lambda: self._show_check_results(items))

        threading.Thread(target=work, daemon=True).start()

    def _show_check_results(self, items: List[CheckItem]) -> None:
        self._check_button.configure(state="normal", text="Check this machine")
        for item in items:
            row = ctk.CTkFrame(self._check_list, fg_color="transparent")
            row.pack(fill="x", pady=3)
            ctk.CTkLabel(row, text="●", text_color=_OK_COLOR if item.ok else _MISS_COLOR, width=20).pack(
                side="left")
            text = f"{item.label}: {item.detail}" if item.detail else item.label
            ctk.CTkLabel(row, text=text, anchor="w", justify="left", wraplength=620).pack(
                side="left", fill="x", expand=True)

    # ---------------------------------------------------------- Capture

    def _build_capture_tab(self) -> None:
        tab = self.tab_capture
        is_windows = platform.system() == "Windows"

        ctk.CTkLabel(tab, text="Record a game", font=ctk.CTkFont(size=16, weight="bold")).pack(
            anchor="w", padx=8, pady=(12, 8))

        process_row = ctk.CTkFrame(tab, fg_color="transparent")
        process_row.pack(fill="x", padx=8, pady=4)
        ctk.CTkLabel(process_row, text="Process:", width=90, anchor="w").pack(side="left")
        self._process_var = ctk.StringVar()
        ctk.CTkComboBox(process_row, variable=self._process_var,
                        values=_running_process_names() or ["cs2.exe"]).pack(
            side="left", fill="x", expand=True)

        launch_row = ctk.CTkFrame(tab, fg_color="transparent")
        launch_row.pack(fill="x", padx=8, pady=4)
        ctk.CTkLabel(launch_row, text="Launch:", width=90, anchor="w").pack(side="left")
        self._launch_var = ctk.StringVar()
        ctk.CTkEntry(launch_row, textvariable=self._launch_var,
                    placeholder_text="optional — leave blank to just wait for it").pack(
            side="left", fill="x", expand=True)
        ctk.CTkButton(launch_row, text="Browse…", width=80, hover_color=_ACCENT_HOVER,
                     command=self._on_browse_launch).pack(side="left", padx=(6, 0))

        seconds_row = ctk.CTkFrame(tab, fg_color="transparent")
        seconds_row.pack(fill="x", padx=8, pady=4)
        ctk.CTkLabel(seconds_row, text="Seconds:", width=90, anchor="w").pack(side="left")
        self._seconds_var = ctk.StringVar(value="30")
        ctk.CTkEntry(seconds_row, textvariable=self._seconds_var, width=80).pack(side="left")
        ctk.CTkLabel(seconds_row, text="0 = record until the game closes instead of a fixed window",
                    text_color=_MUTED).pack(side="left", padx=(10, 0))

        self._capture_button = ctk.CTkButton(
            tab, text="Start capture" if is_windows else "Capture needs Windows",
            state="normal" if is_windows else "disabled",
            hover_color=_ACCENT_HOVER, command=self._on_start_capture)
        self._capture_button.pack(anchor="w", padx=8, pady=(12, 4))

        if not is_windows:
            ctk.CTkLabel(tab, text="capture only runs on Windows — the counters it reads do not exist "
                                   "elsewhere. You can still open a trace recorded on a Windows machine "
                                   "from the Report tab.", text_color=_MUTED, wraplength=620,
                        justify="left").pack(anchor="w", padx=8, pady=(0, 8))

        self._progress_bar = ctk.CTkProgressBar(tab)
        self._progress_bar.set(0)
        self._progress_bar.pack(fill="x", padx=8, pady=(16, 4))
        self._progress_label = ctk.CTkLabel(tab, text="", text_color=_MUTED)
        self._progress_label.pack(anchor="w", padx=8)

    def _on_browse_launch(self) -> None:
        path = filedialog.askopenfilename(title="Pick the game's .exe",
                                          filetypes=[("Executable", "*.exe"), ("All files", "*.*")])
        if not path:
            return
        self._launch_var.set(path)
        if not self._process_var.get().strip():
            self._process_var.set(Path(path).name)

    def _on_start_capture(self) -> None:
        process = self._process_var.get().strip()
        launch = self._launch_var.get().strip() or None
        if not process and not launch:
            self._progress_label.configure(text="type or pick a game's .exe, or browse for one to launch")
            return
        try:
            seconds = int(self._seconds_var.get())
        except ValueError:
            seconds = 30
        if seconds > 0:
            seconds = max(5, seconds)

        self._capture_button.configure(state="disabled", text="Waiting…")
        if self._progress_bar_indeterminate:
            self._progress_bar.stop()
            self._progress_bar.configure(mode="determinate")
            self._progress_bar_indeterminate = False
        self._progress_bar.set(0)
        target = process or Path(launch).name
        wait_label = f"launching {target}…" if launch else f"waiting for {target} to start…"
        self._progress_label.configure(text=wait_label)

        out_path = Path.cwd() / "bottleneck-trace.json"

        def on_progress(elapsed: float, total: Optional[float]) -> None:
            self._progress_queue.put(("progress", elapsed, total))

        def on_wait(elapsed: float) -> None:
            self._progress_queue.put(("waiting", elapsed))

        def work() -> None:
            trace, _ = run_capture(process, seconds, out_path, launch=launch,
                                   on_progress=on_progress, on_wait=on_wait)
            self._progress_queue.put(("done", trace, out_path))

        threading.Thread(target=work, daemon=True).start()
        self.after(100, self._poll_progress)

    def _poll_progress(self) -> None:
        done = False
        try:
            while True:
                message = self._progress_queue.get_nowait()
                if message[0] == "waiting":
                    _, elapsed = message
                    self._progress_label.configure(text=f"waiting for the game to start… {int(elapsed)}s")
                elif message[0] == "progress":
                    _, elapsed, total = message
                    self._capture_button.configure(text="Recording…")
                    if total is None:
                        if not self._progress_bar_indeterminate:
                            self._progress_bar.configure(mode="indeterminate")
                            self._progress_bar.start()
                            self._progress_bar_indeterminate = True
                        self._progress_label.configure(
                            text=f"recording — {int(elapsed)}s so far, stops when the game closes")
                    else:
                        if self._progress_bar_indeterminate:
                            self._progress_bar.stop()
                            self._progress_bar.configure(mode="determinate")
                            self._progress_bar_indeterminate = False
                        self._progress_bar.set(min(1.0, elapsed / total) if total else 0)
                        self._progress_label.configure(text=f"recording — {max(0, int(total - elapsed))}s left")
                elif message[0] == "done":
                    _, trace, out_path = message
                    self._on_capture_done(trace, out_path)
                    done = True
        except queue.Empty:
            pass
        if not done:
            self.after(100, self._poll_progress)

    def _on_capture_done(self, trace: Trace, out_path: Path) -> None:
        self._capture_button.configure(state="normal", text="Start capture")
        if self._progress_bar_indeterminate:
            self._progress_bar.stop()
            self._progress_bar.configure(mode="determinate")
            self._progress_bar_indeterminate = False
        self._progress_bar.set(1.0)
        self._progress_label.configure(text=f"{len(trace.frames)} frames captured — see the Report tab")
        self._last_trace_path = out_path
        self._show_report(trace)
        self._tabs.set("3. Report")

    # ----------------------------------------------------------- Report

    def _build_report_tab(self) -> None:
        tab = self.tab_report
        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.pack(fill="x", padx=8, pady=(12, 8))
        ctk.CTkButton(top, text="Open trace…", hover_color=_ACCENT_HOVER,
                     command=self._on_open_trace).pack(side="left")
        self._report_scroll = ctk.CTkScrollableFrame(tab, corner_radius=12)
        self._report_scroll.pack(fill="both", expand=True, padx=8, pady=(0, 12))
        report_view.render_message(self._report_scroll,
                                   "Run a capture, or open an existing trace, to see a verdict here.")

    def _on_open_trace(self) -> None:
        path = filedialog.askopenfilename(title="Open a findmybottleneck trace",
                                          filetypes=[("Trace JSON", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            trace = Trace.read(Path(path))
        except (OSError, ValueError, KeyError) as exc:
            report_view.render_message(self._report_scroll, f"Could not read this trace: {exc}")
            return
        self._last_trace_path = Path(path)
        self._show_report(trace)
        self._tabs.set("3. Report")

    def _show_report(self, trace: Trace) -> None:
        report_view.render(self._report_scroll, judge(trace))


def launch() -> None:
    App().mainloop()
