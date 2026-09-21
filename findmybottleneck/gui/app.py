"""The window: check this machine, capture a game, see the verdict.

Three pages, one per step, behind a sidebar nav instead of tabs. Nothing
here computes a verdict or decides what a capture should read — it drives
the same functions the CLI drives (`collect.windows.check_status`,
`collect.windows.run_capture`, `engine.judge`) and hands the result to
`report_view`. The GUI is a third sink, exactly like `report.py` (terminal)
and `rtss.py` (RTSS's OSD).

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
from typing import Dict, List, Optional

import customtkinter as ctk

from . import report_view
from ..collect.windows import (CheckItem, check_status, config_audit, cpu_temperature, disk_health,
                               gpu_status, hardware, motherboard_status, run_capture)
from ..engine import judge
from ..engine.compare import compare
from ..engine.config import THROTTLE_LABELS
from ..trace import GpuSample, Hardware, Trace

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

# Near-black, one accent color for action/identity, semantic colors for
# status — the same palette language report_view.py already uses for
# verdicts and findings, extended to the window chrome around it.
_BG = "#0B0B0D"
_SIDEBAR_BG = "#131316"
_CARD_BG = "#18181C"
_BORDER = "#26262B"
_ACCENT = "#F97316"
_ACCENT_HOVER = "#EA670A"
_ACCENT_SOFT = "#2A1B0E"
_OK_COLOR = "#22C55E"
_MISS_COLOR = "#EF4444"
_TEXT = "#F4F4F5"
_MUTED = "#8A8A93"

_PAGES = [("check", "Check"), ("system", "System"), ("capture", "Capture"), ("report", "Report")]


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


def _primary_button(parent, text, command, **kwargs) -> ctk.CTkButton:
    return ctk.CTkButton(parent, text=text, command=command, fg_color=_ACCENT,
                         hover_color=_ACCENT_HOVER, text_color="#0B0B0D",
                         font=ctk.CTkFont(size=13, weight="bold"), corner_radius=8, **kwargs)


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("findmybottleneck")
        self.geometry("880x680")
        self.minsize(700, 540)
        self.configure(fg_color=_BG)

        self._progress_queue: "queue.Queue" = queue.Queue()
        self._last_trace_path: Optional[Path] = None
        self._last_trace: Optional[Trace] = None
        self._baseline_trace: Optional[Trace] = None
        self._progress_bar_indeterminate = False
        self._nav_buttons: Dict[str, ctk.CTkButton] = {}
        self._pages: Dict[str, ctk.CTkFrame] = {}

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self._build_sidebar()

        content = ctk.CTkFrame(self, corner_radius=0, fg_color=_BG)
        content.grid(row=0, column=1, sticky="nsew")
        content.grid_rowconfigure(0, weight=1)
        content.grid_columnconfigure(0, weight=1)

        for key, _ in _PAGES:
            frame = ctk.CTkFrame(content, corner_radius=0, fg_color=_BG)
            frame.grid(row=0, column=0, sticky="nsew", padx=28, pady=24)
            self._pages[key] = frame

        self.tab_check = self._pages["check"]
        self.tab_system = self._pages["system"]
        self.tab_capture = self._pages["capture"]
        self.tab_report = self._pages["report"]

        self._build_check_tab()
        self._build_system_tab()
        self._build_capture_tab()
        self._build_report_tab()

        self._show_page("check")

    # ------------------------------------------------------------- Shell

    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(self, width=210, corner_radius=0, fg_color=_SIDEBAR_BG)
        sidebar.grid(row=0, column=0, sticky="nsw")
        sidebar.grid_propagate(False)

        brand = ctk.CTkFrame(sidebar, fg_color="transparent")
        brand.pack(fill="x", padx=20, pady=(24, 28))
        ctk.CTkLabel(brand, text="●", text_color=_ACCENT, font=ctk.CTkFont(size=18)).pack(side="left")
        ctk.CTkLabel(brand, text="findmybottleneck", text_color=_TEXT,
                    font=ctk.CTkFont(size=15, weight="bold")).pack(side="left", padx=(6, 0))

        for key, label in _PAGES:
            btn = ctk.CTkButton(
                sidebar, text=label, anchor="w", corner_radius=8,
                fg_color="transparent", hover_color=_BORDER, text_color=_MUTED,
                font=ctk.CTkFont(size=13, weight="bold"), height=38,
                command=lambda k=key: self._show_page(k))
            btn.pack(fill="x", padx=14, pady=3)
            self._nav_buttons[key] = btn

    def _show_page(self, name: str) -> None:
        for key, frame in self._pages.items():
            if key == name:
                frame.tkraise()
        for key, btn in self._nav_buttons.items():
            active = key == name
            btn.configure(fg_color=_ACCENT_SOFT if active else "transparent",
                         text_color=_TEXT if active else _MUTED)

    # ------------------------------------------------------------ Check

    def _build_check_tab(self) -> None:
        tab = self.tab_check
        ctk.CTkLabel(tab, text="What this machine can be read for", text_color=_TEXT,
                    font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="w", pady=(0, 4))
        ctk.CTkLabel(tab, text="Everything findmybottleneck shells out to, and whether it's there.",
                    text_color=_MUTED, font=ctk.CTkFont(size=13)).pack(anchor="w", pady=(0, 14))
        self._check_button = _primary_button(tab, "Check this machine", self._on_check)
        self._check_button.pack(anchor="w", pady=(0, 14))
        self._check_list = ctk.CTkScrollableFrame(tab, corner_radius=12, fg_color=_CARD_BG,
                                                   border_width=1, border_color=_BORDER)
        self._check_list.pack(fill="both", expand=True)

    def _on_check(self) -> None:
        self._check_button.configure(state="disabled", text="Checking…")
        for child in self._check_list.winfo_children():
            child.destroy()

        def work() -> None:
            items = check_status()
            config_items = config_audit() + disk_health()
            self.after(0, lambda: self._show_check_results(items, config_items))

        threading.Thread(target=work, daemon=True).start()

    def _show_check_results(self, items: List[CheckItem], config_items: List[CheckItem] = ()) -> None:
        self._check_button.configure(state="normal", text="Check this machine")
        for item in items:
            row = ctk.CTkFrame(self._check_list, fg_color="transparent")
            row.pack(fill="x", pady=6, padx=4)
            ctk.CTkLabel(row, text="●", text_color=_OK_COLOR if item.ok else _MISS_COLOR, width=20).pack(
                side="left")
            text = f"{item.label}: {item.detail}" if item.detail else item.label
            ctk.CTkLabel(row, text=text, anchor="w", justify="left", wraplength=620,
                        text_color=_TEXT).pack(side="left", fill="x", expand=True)

        if config_items:
            ctk.CTkLabel(self._check_list, text="Windows settings and disk health", text_color=_TEXT,
                        font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w", padx=4, pady=(14, 4))
            for item in config_items:
                row = ctk.CTkFrame(self._check_list, fg_color="transparent")
                row.pack(fill="x", pady=6, padx=4)
                ctk.CTkLabel(row, text="●", text_color=_OK_COLOR if item.ok else _ACCENT, width=20).pack(
                    side="left")
                text = f"{item.label}: {item.detail}" if item.detail else item.label
                ctk.CTkLabel(row, text=text, anchor="w", justify="left", wraplength=620,
                            text_color=_TEXT).pack(side="left", fill="x", expand=True)

    # ----------------------------------------------------------- System

    def _build_system_tab(self) -> None:
        tab = self.tab_system
        is_windows = platform.system() == "Windows"

        ctk.CTkLabel(tab, text="What's inside this machine", text_color=_TEXT,
                    font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="w", pady=(0, 4))
        ctk.CTkLabel(tab, text="Identity and a live GPU reading — the same things a capture records.",
                    text_color=_MUTED, font=ctk.CTkFont(size=13)).pack(anchor="w", pady=(0, 14))

        self._system_button = _primary_button(
            tab, "Read this machine's components" if is_windows else "Needs Windows",
            self._on_read_system, state="normal" if is_windows else "disabled")
        self._system_button.pack(anchor="w", pady=(0, 14))

        if not is_windows:
            ctk.CTkLabel(tab, text="component identity is read the same way a capture reads it — "
                                   "nvidia-smi and Windows' own CIM classes — so it only works on "
                                   "Windows.", text_color=_MUTED, wraplength=680, justify="left",
                        font=ctk.CTkFont(size=12)).pack(anchor="w", pady=(0, 8))

        self._system_scroll = ctk.CTkScrollableFrame(tab, corner_radius=12, fg_color=_CARD_BG,
                                                      border_width=1, border_color=_BORDER)
        self._system_scroll.pack(fill="both", expand=True)
        ctk.CTkLabel(self._system_scroll, text="Click the button above to read this machine.",
                    text_color=_MUTED, font=ctk.CTkFont(size=13)).pack(anchor="w", padx=4, pady=20)

    def _on_read_system(self) -> None:
        self._system_button.configure(state="disabled", text="Reading…")
        for child in self._system_scroll.winfo_children():
            child.destroy()

        def work() -> None:
            hw, missing = hardware()
            gpu, gpu_missing = gpu_status()
            missing.update(gpu_missing)
            cpu_temp, cpu_temp_missing = cpu_temperature()
            missing.update(cpu_temp_missing)
            hw.motherboard, hw.memory_slots_total, hw.memory_max_capacity_gb, board_missing = motherboard_status()
            missing.update(board_missing)
            self.after(0, lambda: self._show_system_results(hw, gpu, cpu_temp, missing))

        threading.Thread(target=work, daemon=True).start()

    def _system_section(self, title: str, lines: List[str]) -> None:
        ctk.CTkLabel(self._system_scroll, text=title, text_color=_TEXT,
                    font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=4, pady=(14, 4))
        if not lines:
            ctk.CTkLabel(self._system_scroll, text="not read", text_color=_MUTED,
                        font=ctk.CTkFont(size=13)).pack(anchor="w", padx=4, pady=(0, 4))
            return
        for line in lines:
            ctk.CTkLabel(self._system_scroll, text=line, text_color=_TEXT, anchor="w", justify="left",
                        wraplength=600, font=ctk.CTkFont(size=13)).pack(anchor="w", padx=4, pady=1, fill="x")

    def _show_system_results(self, hw: Hardware, gpu: Optional[GpuSample], cpu_temp: Optional[float],
                             missing: Dict[str, str]) -> None:
        self._system_button.configure(state="normal", text="Read this machine's components")
        for child in self._system_scroll.winfo_children():
            child.destroy()

        gpu_lines = []
        if hw.gpu_name:
            gpu_lines.append(hw.gpu_name + (f"  ·  driver {hw.driver}" if hw.driver else ""))
        if gpu is not None:
            bits = []
            if gpu.temperature is not None:
                bits.append(f"{round(gpu.temperature)}°C")
            if gpu.utilisation is not None:
                bits.append(f"{round(gpu.utilisation)}% util")
            if gpu.clock_graphics is not None:
                clock = f"{round(gpu.clock_graphics)} MHz"
                if gpu.clock_max_graphics:
                    clock += f" / {round(gpu.clock_max_graphics)} MHz max"
                bits.append(clock)
            if gpu.memory_used is not None and gpu.memory_total is not None:
                bits.append(f"{round(gpu.memory_used)} / {round(gpu.memory_total)} MB VRAM")
            if gpu.power_draw is not None:
                power = f"{round(gpu.power_draw)} W"
                if gpu.power_limit:
                    power += f" / {round(gpu.power_limit)} W limit"
                bits.append(power)
            if bits:
                gpu_lines.append("  ·  ".join(bits))
            active = [THROTTLE_LABELS[flag] for flag, on in gpu.throttle.items() if on and flag in THROTTLE_LABELS]
            if active:
                gpu_lines.append("throttling: " + ", ".join(active))
        self._system_section("GPU", gpu_lines)

        cpu_lines = []
        if hw.cpu_name:
            counts = []
            if hw.physical_cores:
                counts.append(f"{hw.physical_cores} cores")
            if hw.logical_cores:
                counts.append(f"{hw.logical_cores} threads")
            cpu_lines.append(hw.cpu_name + ("  ·  " + ", ".join(counts) if counts else ""))
        if cpu_temp is not None:
            cpu_lines.append(f"{round(cpu_temp)}°C")
        self._system_section("CPU", cpu_lines)

        self._system_section("Motherboard", [hw.motherboard] if hw.motherboard else [])

        mem_lines = []
        for module in hw.memory_modules:
            bits = [b for b in (module.get("manufacturer"), module.get("part")) if b]
            configured, rated = module.get("configured_mhz"), module.get("rated_mhz")
            speed = f"{configured} MHz" if configured else "speed unknown"
            if rated and rated != configured:
                speed += f" (rated {rated} MHz)"
            capacity = module.get("capacity_gb")
            size = f"{capacity:g} GB  ·  " if capacity else ""
            slot = module.get("slot") or module.get("bank") or "?"
            mem_lines.append(f"[{slot}]  " + size + (" ".join(bits) + "  ·  " if bits else "") + speed)

        populated = len(hw.memory_modules)
        if hw.memory_slots_total:
            free = hw.memory_slots_total - populated
            slot_line = f"{populated} of {hw.memory_slots_total} slots used"
            if free > 0:
                slot_line += f"  ·  {free} free — room to add a module"
            elif populated:
                slot_line += "  ·  no free slots — an upgrade means replacing, not adding"
            mem_lines.append(slot_line)
        elif hw.memory_channels_populated:
            mem_lines.append(f"{hw.memory_channels_populated} channel(s) populated")
        if hw.memory_max_capacity_gb:
            mem_lines.append(f"motherboard supports up to {hw.memory_max_capacity_gb:g} GB total")
        self._system_section("Memory", mem_lines)
        if mem_lines:
            ctk.CTkLabel(self._system_scroll, text=(
                    "To add a matching module: buy the exact manufacturer and part number above, "
                    "at the same speed. findmybottleneck can't read the motherboard's maximum "
                    "supported RAM speed — that's a manufacturer spec, not something Windows "
                    "exposes — so check the board's QVL or manual before buying RAM faster than "
                    "what's already here."),
                        text_color=_MUTED, anchor="w", justify="left", wraplength=600,
                        font=ctk.CTkFont(size=12, slant="italic")).pack(
                anchor="w", padx=4, pady=(0, 4), fill="x")

        self._system_section("OS", [hw.os] if hw.os else [])

        if missing:
            ctk.CTkLabel(self._system_scroll, text="Not read", text_color=_TEXT,
                        font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=4, pady=(18, 4))
            for label, reason in missing.items():
                row = ctk.CTkFrame(self._system_scroll, fg_color="transparent")
                row.pack(fill="x", padx=4, pady=2)
                ctk.CTkLabel(row, text="●", text_color=_MISS_COLOR, width=20).pack(side="left")
                ctk.CTkLabel(row, text=f"{label}: {reason}", text_color=_MUTED, anchor="w",
                            justify="left", wraplength=600).pack(side="left", fill="x", expand=True)

    # ---------------------------------------------------------- Capture

    def _build_capture_tab(self) -> None:
        tab = self.tab_capture
        is_windows = platform.system() == "Windows"

        ctk.CTkLabel(tab, text="Record a game", text_color=_TEXT,
                    font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="w", pady=(0, 4))
        ctk.CTkLabel(tab, text="Play normally while it records — nothing here changes settings.",
                    text_color=_MUTED, font=ctk.CTkFont(size=13)).pack(anchor="w", pady=(0, 16))

        form = ctk.CTkFrame(tab, corner_radius=12, fg_color=_CARD_BG, border_width=1, border_color=_BORDER)
        form.pack(fill="x", pady=(0, 16))

        process_row = ctk.CTkFrame(form, fg_color="transparent")
        process_row.pack(fill="x", padx=18, pady=(18, 6))
        ctk.CTkLabel(process_row, text="Process", width=90, anchor="w", text_color=_MUTED).pack(side="left")
        self._process_var = ctk.StringVar()
        ctk.CTkComboBox(process_row, variable=self._process_var, fg_color=_BG, border_color=_BORDER,
                        button_color=_ACCENT, button_hover_color=_ACCENT_HOVER,
                        values=_running_process_names() or ["cs2.exe"]).pack(
            side="left", fill="x", expand=True)

        launch_row = ctk.CTkFrame(form, fg_color="transparent")
        launch_row.pack(fill="x", padx=18, pady=6)
        ctk.CTkLabel(launch_row, text="Launch", width=90, anchor="w", text_color=_MUTED).pack(side="left")
        self._launch_var = ctk.StringVar()
        ctk.CTkEntry(launch_row, textvariable=self._launch_var, fg_color=_BG, border_color=_BORDER,
                    placeholder_text="optional — leave blank to just wait for it").pack(
            side="left", fill="x", expand=True)
        ctk.CTkButton(launch_row, text="Browse…", width=80, corner_radius=8,
                     fg_color=_BORDER, hover_color=_ACCENT, text_color=_TEXT,
                     command=self._on_browse_launch).pack(side="left", padx=(6, 0))

        seconds_row = ctk.CTkFrame(form, fg_color="transparent")
        seconds_row.pack(fill="x", padx=18, pady=(6, 18))
        ctk.CTkLabel(seconds_row, text="Seconds", width=90, anchor="w", text_color=_MUTED).pack(side="left")
        self._seconds_var = ctk.StringVar(value="30")
        ctk.CTkEntry(seconds_row, textvariable=self._seconds_var, width=80, fg_color=_BG,
                    border_color=_BORDER).pack(side="left")
        ctk.CTkLabel(seconds_row, text="0 = record until the game closes instead of a fixed window",
                    text_color=_MUTED, font=ctk.CTkFont(size=12)).pack(side="left", padx=(10, 0))

        notes_row = ctk.CTkFrame(form, fg_color="transparent")
        notes_row.pack(fill="x", padx=18, pady=(0, 18))
        ctk.CTkLabel(notes_row, text="Notes", width=90, anchor="w", text_color=_MUTED).pack(side="left")
        self._notes_var = ctk.StringVar()
        ctk.CTkEntry(notes_row, textvariable=self._notes_var, fg_color=_BG, border_color=_BORDER,
                    placeholder_text="optional — in-game settings, what changed since last time").pack(
            side="left", fill="x", expand=True)

        self._capture_button = _primary_button(
            tab, "Start capture" if is_windows else "Capture needs Windows",
            self._on_start_capture, state="normal" if is_windows else "disabled")
        self._capture_button.pack(anchor="w", pady=(0, 4))

        if not is_windows:
            ctk.CTkLabel(tab, text="capture only runs on Windows — the counters it reads do not exist "
                                   "elsewhere. You can still open a trace recorded on a Windows machine "
                                   "from the Report page.", text_color=_MUTED, wraplength=680,
                        justify="left", font=ctk.CTkFont(size=12)).pack(anchor="w", pady=(4, 8))

        self._progress_bar = ctk.CTkProgressBar(tab, progress_color=_ACCENT, fg_color=_BORDER)
        self._progress_bar.set(0)
        self._progress_bar.pack(fill="x", pady=(20, 6))
        self._progress_label = ctk.CTkLabel(tab, text="", text_color=_MUTED, font=ctk.CTkFont(size=12))
        self._progress_label.pack(anchor="w")

        compare_card = ctk.CTkFrame(tab, corner_radius=12, fg_color=_CARD_BG,
                                    border_width=1, border_color=_BORDER)
        compare_card.pack(fill="x", pady=(20, 0))
        ctk.CTkLabel(compare_card, text="Did a tuning change actually help?", text_color=_TEXT,
                    font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=18, pady=(14, 2))
        ctk.CTkLabel(compare_card, text="Capture a baseline, apply your undervolt/overclock in "
                                        "MSI Afterburner (or similar), capture again, then compare — "
                                        "the resolution-drop test findmybottleneck already automates.",
                    text_color=_MUTED, wraplength=680, justify="left", font=ctk.CTkFont(size=12)).pack(
            anchor="w", padx=18, pady=(0, 10))
        compare_buttons = ctk.CTkFrame(compare_card, fg_color="transparent")
        compare_buttons.pack(anchor="w", padx=18, pady=(0, 6))
        self._baseline_button = ctk.CTkButton(
            compare_buttons, text="Save last capture as baseline", corner_radius=8,
            fg_color=_BORDER, hover_color=_ACCENT, text_color=_TEXT, state="disabled",
            command=self._on_save_baseline)
        self._baseline_button.pack(side="left")
        self._compare_button = ctk.CTkButton(
            compare_buttons, text="Compare last capture to baseline", corner_radius=8,
            fg_color=_BORDER, hover_color=_ACCENT, text_color=_TEXT, state="disabled",
            command=self._on_compare_to_baseline)
        self._compare_button.pack(side="left", padx=(8, 0))
        self._compare_label = ctk.CTkLabel(compare_card, text="", text_color=_MUTED, anchor="w",
                                           justify="left", wraplength=680, font=ctk.CTkFont(size=12))
        self._compare_label.pack(anchor="w", padx=18, pady=(0, 14), fill="x")

    def _on_save_baseline(self) -> None:
        if self._last_trace is None:
            return
        self._baseline_trace = self._last_trace
        self._compare_button.configure(state="normal")
        self._compare_label.configure(
            text=f"Baseline saved from the last capture ({len(self._baseline_trace.frames)} frames). "
                 "Apply your tuning change, capture again, then compare.",
            text_color=_MUTED)

    def _on_compare_to_baseline(self) -> None:
        if self._baseline_trace is None or self._last_trace is None:
            return
        result = compare(self._baseline_trace, self._last_trace)
        color = {"confirms": _OK_COLOR, "contradicts": _MISS_COLOR, "inconclusive": _ACCENT}[result.agreement]
        lines = [result.headline, f"({result.agreement})"] + result.evidence
        if result.note:
            lines.append(result.note)
        self._compare_label.configure(text="\n".join(lines), text_color=color)

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

        notes = self._notes_var.get().strip()

        def work() -> None:
            trace, _ = run_capture(process, seconds, out_path, launch=launch,
                                   on_progress=on_progress, on_wait=on_wait, notes=notes)
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
        self._progress_label.configure(text=f"{len(trace.frames)} frames captured — see the Report page")
        self._last_trace_path = out_path
        self._last_trace = trace
        self._baseline_button.configure(state="normal")
        self._compare_button.configure(state="normal" if self._baseline_trace is not None else "disabled")
        self._show_report(trace)
        self._show_page("report")

    # ----------------------------------------------------------- Report

    def _build_report_tab(self) -> None:
        tab = self.tab_report
        top = ctk.CTkFrame(tab, fg_color="transparent")
        top.pack(fill="x", pady=(0, 16))
        ctk.CTkLabel(top, text="Report", text_color=_TEXT, font=ctk.CTkFont(size=18, weight="bold")).pack(
            side="left")
        ctk.CTkButton(top, text="Open trace…", corner_radius=8, fg_color=_BORDER, hover_color=_ACCENT,
                     text_color=_TEXT, command=self._on_open_trace).pack(side="right")
        self._report_scroll = ctk.CTkScrollableFrame(tab, corner_radius=0, fg_color="transparent")
        self._report_scroll.pack(fill="both", expand=True)
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
        self._show_page("report")

    def _show_report(self, trace: Trace) -> None:
        report_view.render(self._report_scroll, judge(trace))


def launch() -> None:
    App().mainloop()
