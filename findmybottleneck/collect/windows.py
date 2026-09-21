"""Reading a Windows machine while a game runs.

Everything here shells out to something already on the machine. Nothing is
bundled, nothing is compiled, and every probe that fails is written into the
trace under ``missing`` rather than swallowed — a verdict has to be able to say
"I did not measure this".

Privilege: only PresentMon needs any, and it is the only thing that can
attribute a frame to the CPU or the GPU. ``findmybottleneck check`` says so and prints
the one command that fixes it.
"""

from __future__ import annotations

import csv
import io
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from ..trace import (CpuSample, DiskSample, Frame, GpuSample, Hardware,
                     MemorySample, Trace)

GPU_FIELDS = [
    "utilization.gpu", "memory.used", "memory.total", "temperature.gpu",
    "power.draw", "power.limit", "clocks.current.graphics", "clocks.max.graphics",
    "pcie.link.gen.current", "pcie.link.gen.max",
    "pcie.link.width.current", "pcie.link.width.max",
]

THROTTLE_FLAGS = [
    "gpu_idle", "applications_clocks_setting", "sw_power_cap", "hw_slowdown",
    "hw_thermal_slowdown", "hw_power_brake_slowdown", "sync_boost", "sw_thermal_slowdown",
]

CPU_COUNTERS = [
    r"\Processor Information(_Total)\% Processor Time",
    r"\Processor Information(_Total)\% Processor Performance",
    r"\Processor Information(*)\% Processor Time",
    r"\Memory\Available MBytes",
    r"\Memory\Pages/sec",
    r"\PhysicalDisk(_Total)\Avg. Disk sec/Read",
    r"\PhysicalDisk(_Total)\Avg. Disk sec/Write",
    r"\PhysicalDisk(_Total)\Current Disk Queue Length",
]


def _run(args: List[str], timeout: int = 20) -> Tuple[int, str, str]:
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return done.returncode, done.stdout, done.stderr
    except FileNotFoundError:
        return 127, "", f"{args[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"


def _powershell(script: str, timeout: int = 25) -> Tuple[int, str, str]:
    return _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout)


def find_presentmon(explicit: Optional[str] = None) -> Optional[str]:
    if explicit and Path(explicit).exists():
        return explicit
    for name in ("PresentMon.exe", "presentmon.exe", "PresentMon-2.3.0-x64.exe"):
        found = shutil.which(name)
        if found:
            return found
    for base in (Path.cwd(), Path.home() / "Downloads", Path(os.environ.get("LOCALAPPDATA", "")) / "findmybottleneck"):
        if not base or not base.exists():
            continue
        for candidate in sorted(base.glob("PresentMon*.exe")):
            return str(candidate)
    return None


# ---------------------------------------------------------------- probing

def supported_throttle_prefix() -> Optional[str]:
    """Newer drivers renamed the field. Ask, do not assume."""
    for prefix in ("clocks_event_reasons", "clocks_throttle_reasons"):
        code, out, _ = _run(["nvidia-smi", f"--query-gpu={prefix}.active", "--format=csv,noheader"])
        if code == 0 and out.strip():
            return prefix
    return None


@dataclass
class CheckItem:
    """One line of `findmybottleneck check`'s output, as data rather than a print
    statement — so the GUI can show the same information without re-running
    or re-parsing anything."""
    ok: bool
    label: str
    detail: str = ""


def check_status() -> List[CheckItem]:
    """What this machine can and cannot be read for. `check()` below is the
    only thing that prints; everything else (the CLI's own summary, the GUI's
    Check tab) reads this list instead of re-deriving it."""
    items: List[CheckItem] = []

    code, out, _ = _run(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"])
    if code == 0 and out.strip():
        items.append(CheckItem(True, "nvidia-smi", out.strip().splitlines()[0]))
        prefix = supported_throttle_prefix()
        if prefix:
            items.append(CheckItem(True, "throttle reasons", f"{prefix}.*"))
        else:
            items.append(CheckItem(False, "throttle reasons", "this driver exposes neither field name"))
    else:
        items.append(CheckItem(False, "nvidia-smi",
                                "not found — findmybottleneck reads NVIDIA telemetry only, for now"))

    if shutil.which("typeperf"):
        items.append(CheckItem(True, "typeperf", "present"))
    else:
        items.append(CheckItem(False, "typeperf", "not found, which is unusual on Windows"))

    presentmon = find_presentmon()
    if presentmon:
        items.append(CheckItem(True, "PresentMon", presentmon))
        code, out, err = _run([presentmon, "--version"], timeout=10)
        blob = (out + err).lower()
        if "performance log users" in blob or "access" in blob or "denied" in blob:
            items.append(CheckItem(False, "PresentMon permission", "it ran but reported a permission problem"))
    else:
        items.append(CheckItem(False, "PresentMon",
                                "not found — download it from github.com/GameTechDev/PresentMon "
                                "and put PresentMon.exe next to this command, or pass --presentmon"))

    # Only needed for `overlay` — capture and explain work without it, so this
    # never affects the pass/fail exit code below.
    from ..rtss import RTSSWriter
    rtss_writer = RTSSWriter()
    if rtss_writer.open():
        items.append(CheckItem(True, "RTSS", "found (needed only for `overlay`)"))
    else:
        items.append(CheckItem(False, "RTSS",
                                "not running (only needed for `overlay` — RivaTuner Statistics Server, "
                                "free, ships with MSI Afterburner)"))
    rtss_writer.close()

    return items


def check() -> int:
    items = check_status()

    print()
    for item in items:
        text = f"{item.label}: {item.detail}" if item.detail else item.label
        print(f"  ok    {text}" if item.ok else f"  miss  {text}")
    print()
    # RTSS is only needed for `overlay`, not capture/explain, so it never
    # affects the pass/fail exit code — same as before this was refactored.
    missing_labels = {item.label for item in items if not item.ok and item.label != "RTSS"}
    if missing_labels & {"PresentMon", "PresentMon permission"}:
        print("PresentMon is the only thing that can attribute a frame to the CPU or the GPU, so")
        print("without it findmybottleneck can still find a misconfigured machine but cannot tell you what")
        print("set the pace. It needs your user to be in the Performance Log Users group. Once,")
        print("in an Administrator prompt:")
        print()
        print(f'  net localgroup "Performance Log Users" "%USERNAME%" /add')
        print()
        print("Then sign out and back in. Nothing needs Administrator after that.")
        print()
    return 1 if missing_labels else 0


# ------------------------------------------------------------- collecting

def hardware() -> Tuple[Hardware, Dict[str, str]]:
    hw, missing = Hardware(os=f"{sys.platform} {os.environ.get('OS', '')}".strip()), {}

    code, out, _ = _run(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"])
    if code == 0 and out.strip():
        parts = [p.strip() for p in out.strip().splitlines()[0].split(",")]
        hw.gpu_name = parts[0]
        hw.driver = parts[1] if len(parts) > 1 else None
    else:
        missing["gpu identity"] = "nvidia-smi did not answer"

    code, out, _ = _powershell(
        "$c = Get-CimInstance Win32_Processor | Select-Object -First 1; "
        "'{0}|{1}|{2}' -f $c.Name, $c.NumberOfCores, $c.NumberOfLogicalProcessors")
    if code == 0 and "|" in out:
        name, cores, logical = (out.strip().split("|") + ["", ""])[:3]
        hw.cpu_name = name.strip() or None
        hw.physical_cores = int(cores) if cores.strip().isdigit() else None
        hw.logical_cores = int(logical) if logical.strip().isdigit() else None
    else:
        missing["cpu identity"] = "Win32_Processor did not answer"

    # ConfiguredClockSpeed is what the memory is actually running at; Speed is
    # what the module is rated for. The documented unit for Speed is wrong —
    # it says nanoseconds and reports MHz — so both are read as MHz.
    code, out, _ = _powershell(
        "Get-CimInstance Win32_PhysicalMemory | ForEach-Object { "
        "'{0}|{1}|{2}|{3}|{4}|{5}' -f $_.ConfiguredClockSpeed, $_.Speed, $_.Manufacturer, "
        "$_.PartNumber, $_.BankLabel, $_.DeviceLocator }")
    if code == 0 and out.strip():
        banks = set()
        for line in out.strip().splitlines():
            fields = [f.strip() for f in line.split("|")]
            if len(fields) < 6:
                continue
            configured, rated, manufacturer, part, bank, locator = fields[:6]
            hw.memory_modules.append({
                "configured_mhz": int(configured) if configured.isdigit() else None,
                "rated_mhz": int(rated) if rated.isdigit() else None,
                "manufacturer": manufacturer or None,
                "part": part or None,
                "bank": bank or None,
                "slot": locator or None,
            })
            banks.add(bank or locator)
        hw.memory_channels_populated = len(banks) or None
    else:
        missing["memory configuration"] = "Win32_PhysicalMemory did not answer"

    return hw, missing


def _gpu_query(prefix: Optional[str]) -> str:
    fields = list(GPU_FIELDS)
    if prefix:
        fields += [f"{prefix}.{flag}" for flag in THROTTLE_FLAGS]
    return ",".join(fields)


def _parse_gpu(text: str, prefix: Optional[str], interval: float) -> List[GpuSample]:
    samples = []
    for index, line in enumerate(text.strip().splitlines()):
        cells = [c.strip() for c in line.split(",")]
        if len(cells) < len(GPU_FIELDS):
            continue

        def number(position: int) -> Optional[float]:
            try:
                return float(cells[position])
            except (ValueError, IndexError):
                return None

        sample = GpuSample(
            time=index * interval,
            utilisation=number(0), memory_used=number(1), memory_total=number(2),
            temperature=number(3), power_draw=number(4), power_limit=number(5),
            clock_graphics=number(6), clock_max_graphics=number(7),
            pcie_gen=int(number(8)) if number(8) is not None else None,
            pcie_gen_max=int(number(9)) if number(9) is not None else None,
            pcie_width=int(number(10)) if number(10) is not None else None,
            pcie_width_max=int(number(11)) if number(11) is not None else None,
        )
        if prefix:
            for offset, flag in enumerate(THROTTLE_FLAGS):
                cell = cells[len(GPU_FIELDS) + offset] if len(cells) > len(GPU_FIELDS) + offset else ""
                sample.throttle[flag] = cell.strip().lower() in ("active", "1", "true", "yes")
        samples.append(sample)
    return samples


def _parse_presentmon(path: Path) -> List[Frame]:
    """PresentMon v2 CSV. Column names are read from the header, never assumed."""
    rows = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8", errors="replace"))))
    if not rows:
        return []

    def pick(row: dict, *names: str) -> float:
        for name in names:
            if name in row and row[name] not in (None, "", "NA"):
                try:
                    return float(row[name])
                except ValueError:
                    continue
        return 0.0

    frames, clock = [], 0.0
    for row in rows:
        frame_time = pick(row, "MsBetweenPresents", "msBetweenPresents")
        clock += frame_time / 1000.0
        frames.append(Frame(
            time=round(clock, 4),
            frame_time=frame_time,
            cpu_busy=pick(row, "MsCPUBusy", "msCPUBusy"),
            cpu_wait=pick(row, "MsCPUWait", "msCPUWait"),
            gpu_busy=pick(row, "MsGPUBusy", "msGPUBusy", "msGPUActive"),
            gpu_wait=pick(row, "MsGPUWait", "msGPUWait"),
            gpu_latency=pick(row, "MsGPULatency", "msGPULatency"),
            in_present=pick(row, "MsInPresentAPI", "msInPresentAPI"),
            sync_interval=int(pick(row, "SyncInterval", "syncInterval")),
        ))
    return frames


def _parse_typeperf(path: Path) -> Tuple[List[CpuSample], List[DiskSample], List[MemorySample]]:
    rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8", errors="replace"))))
    if len(rows) < 2:
        return [], [], []
    header = [h.strip('"') for h in rows[0]]

    def column(fragment: str) -> Optional[int]:
        for index, name in enumerate(header):
            if fragment.lower() in name.lower():
                return index
        return None

    def cores() -> List[int]:
        return [i for i, name in enumerate(header)
                if "% processor time" in name.lower() and "_total" not in name.lower()]

    total_i, perf_i = column("_Total)\\% Processor Time"), column("% Processor Performance")
    avail_i, pages_i = column("Available MBytes"), column("Pages/sec")
    read_i, write_i, queue_i = column("Avg. Disk sec/Read"), column("Avg. Disk sec/Write"), column("Disk Queue Length")
    core_indices = cores()

    cpu, disk, memory = [], [], []
    for position, row in enumerate(rows[1:]):
        if not row or len(row) < 2:
            continue

        def value(index: Optional[int]) -> Optional[float]:
            if index is None or index >= len(row):
                return None
            try:
                return float(row[index].strip('"'))
            except ValueError:
                return None

        when = float(position)
        cpu.append(CpuSample(time=when, total=value(total_i),
                             cores=[v for v in (value(i) for i in core_indices) if v is not None],
                             processor_performance=value(perf_i)))
        disk.append(DiskSample(time=when, read_latency=value(read_i),
                               write_latency=value(write_i), queue=value(queue_i)))
        memory.append(MemorySample(time=when, available_mb=value(avail_i), pages_per_sec=value(pages_i)))
    return cpu, disk, memory


def run_capture(process: str, seconds: int, out_path: Path,
                presentmon_path: Optional[str] = None, keep_csv: bool = False,
                on_progress: Optional[Callable[[float, float], None]] = None) -> Tuple[Trace, Optional[Path]]:
    """Record a trace and write it to `out_path`. Returns the trace, and the
    workdir the raw CSVs were kept in if `keep_csv` was set (else `None`).

    This is the whole body of what used to be `capture(args)` — the CLI
    below is now a thin wrapper that prints what it always printed, and the
    GUI is another caller that drives the same recording with its own
    progress bar instead of a `\\r`-overwritten countdown. No new subprocess
    logic, no new parsing: this and `capture()` are one implementation.
    """
    seconds = max(5, int(seconds))
    out_path = Path(out_path)
    workdir = out_path.parent / f".fmb-{int(time.time())}"
    workdir.mkdir(parents=True, exist_ok=True)

    missing: Dict[str, str] = {}
    hw, hw_missing = hardware()
    missing.update(hw_missing)

    prefix = supported_throttle_prefix()
    if not prefix:
        missing["throttle reasons"] = "this driver exposes neither clocks_event_reasons nor clocks_throttle_reasons"

    presentmon = find_presentmon(presentmon_path)
    target = process or ""
    if not presentmon:
        missing["frame attribution"] = "PresentMon was not found, so nothing could attribute a frame to the CPU or GPU"
    elif not target:
        missing["frame attribution"] = "no process was given: findmybottleneck capture <game.exe>"

    gpu_csv, cpu_csv, frames_csv = workdir / "gpu.csv", workdir / "cpu.csv", workdir / "frames.csv"
    workers = [subprocess.Popen(
        ["nvidia-smi", f"--query-gpu={_gpu_query(prefix)}",
         "--format=csv,noheader,nounits", "-lms", "500", "-f", str(gpu_csv)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]

    workers.append(subprocess.Popen(
        ["typeperf", *CPU_COUNTERS, "-si", "1", "-sc", str(seconds), "-f", "CSV", "-o", str(cpu_csv), "-y"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))

    presentmon_worker = None
    if presentmon and target:
        presentmon_worker = subprocess.Popen(
            [presentmon, "--process_name", target, "--timed", str(seconds),
             "--terminate_after_timed", "--output_file", str(frames_csv), "--no_top"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

    started = time.time()
    while time.time() - started < seconds:
        time.sleep(0.5)
        if on_progress:
            on_progress(time.time() - started, seconds)

    for worker in workers:
        worker.terminate()
    if presentmon_worker:
        try:
            _, stderr = presentmon_worker.communicate(timeout=15)
            if presentmon_worker.returncode not in (0, None) and stderr:
                missing["frame attribution"] = f"PresentMon exited with an error: {stderr.strip()[:200]}"
        except subprocess.TimeoutExpired:
            presentmon_worker.kill()
            missing["frame attribution"] = "PresentMon did not stop when asked"

    trace = Trace(
        captured_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        duration_s=float(seconds), target=target, hardware=hw, missing=missing,
    )

    if gpu_csv.exists():
        trace.gpu = _parse_gpu(gpu_csv.read_text(encoding="utf-8", errors="replace"), prefix, 0.5)
    else:
        missing["gpu telemetry"] = "nvidia-smi wrote nothing"

    if cpu_csv.exists():
        trace.cpu, trace.disk, trace.memory = _parse_typeperf(cpu_csv)
    else:
        missing["cpu, disk and memory counters"] = "typeperf wrote nothing"

    if frames_csv.exists():
        trace.frames = _parse_presentmon(frames_csv)
        if not trace.frames:
            missing["frame attribution"] = "PresentMon wrote a file with no frames in it — was the game running?"
    elif presentmon and target:
        missing.setdefault("frame attribution", "PresentMon wrote no file")

    trace.missing = missing
    trace.write(out_path)

    if keep_csv:
        return trace, workdir
    shutil.rmtree(workdir, ignore_errors=True)
    return trace, None


def capture(args) -> int:
    seconds = max(5, int(args.seconds))
    target = args.process or ""
    print(f"recording {seconds}s" + (f" of {target}" if target else "") + " — play normally")

    def on_progress(elapsed: float, total: float) -> None:
        left = int(total - elapsed)
        print(f"\r  {left:>3}s left ", end="", flush=True)

    trace, workdir = run_capture(
        target, seconds, Path(args.out),
        presentmon_path=getattr(args, "presentmon", None),
        keep_csv=getattr(args, "keep_csv", False),
        on_progress=on_progress,
    )
    print("\r" + " " * 20 + "\r", end="")

    if workdir is not None:
        print(f"raw csv kept in {workdir}")

    print(f"{len(trace.frames)} frames, {len(trace.gpu)} gpu samples, {len(trace.cpu)} counter samples")
    print(f"written to {args.out}")
    print()
    print(f"  findmybottleneck explain {args.out}")
    print()
    return 0


def run_overlay(args) -> int:
    """Push a live rolling verdict into RTSS's on-screen display.

    Unlike ``capture``, this never stops on its own and writes no trace file
    — it is a live view, not a capture. It reuses PresentMon and the existing
    CSV parser exactly as ``capture`` does; the only new thing is that
    PresentMon runs open-ended (no ``--timed``) and the CSV is re-read on a
    short interval instead of once at the end.
    """
    from ..engine.frames import analyse
    from ..rtss import RTSSWriter, format_status

    presentmon = find_presentmon(getattr(args, "presentmon", None))
    target = args.process
    if not presentmon:
        print("PresentMon was not found — findmybottleneck check says where to get it.", file=sys.stderr)
        return 2

    writer = RTSSWriter()
    if not writer.open():
        print("Could not reach RTSS's shared memory — is RTSS (RivaTuner Statistics Server) running?",
              file=sys.stderr)
        return 2

    # Everything from here on must run inside try/finally: writer.open()
    # already succeeded, so a mapped RTSS slot exists and has to be released
    # on the way out even if PresentMon fails to start or Ctrl+C lands before
    # the loop begins. workdir/presentmon_worker are initialised as the very
    # first statements inside the try, not before it, so there is no gap
    # between "a slot is claimed" and "a KeyboardInterrupt is caught".
    try:
        workdir = None
        presentmon_worker = None
        workdir = Path.cwd() / f".fmb-overlay-{int(time.time())}"
        workdir.mkdir(parents=True, exist_ok=True)
        frames_csv = workdir / "frames.csv"

        presentmon_worker = subprocess.Popen(
            [presentmon, "--process_name", target, "--output_file", str(frames_csv), "--no_top"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

        window_s = float(getattr(args, "window_seconds", 3.0))
        refresh_s = max(0.1, int(getattr(args, "refresh_ms", 1000)) / 1000.0)
        min_frames = 10

        print(f"pushing a live verdict for {target} into RTSS — Ctrl+C to stop")
        while True:
            time.sleep(refresh_s)
            all_frames = _parse_presentmon(frames_csv) if frames_csv.exists() else []
            recent = []
            if all_frames:
                latest = all_frames[-1].time
                recent = [f for f in all_frames if f.time >= latest - window_s]
            verdict, _, stats, _ = analyse(recent) if len(recent) >= min_frames else (None, [], {}, [])
            writer.push(format_status(verdict, stats, len(recent), min_frames))
    except KeyboardInterrupt:
        pass
    finally:
        if presentmon_worker is not None:
            presentmon_worker.terminate()
            try:
                presentmon_worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                presentmon_worker.kill()
        writer.close()
        if workdir is not None:
            shutil.rmtree(workdir, ignore_errors=True)
        print("\nstopped")
    return 0
