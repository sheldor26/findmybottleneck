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
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

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

# Per-process 3D-engine load — who else is asking the GPU for frames while
# the game runs (a browser, Discord, OBS). Not in CPU_COUNTERS: unlike
# every fixed counter above, whether this counter object exists at all has
# to be asked first (see `_gpu_engine_available`), since a counter path
# typeperf does not recognise can fail the *whole* invocation, not just
# this one column.
GPU_ENGINE_COUNTER = r"\GPU Engine(*)\Utilization Percentage"
_GPU_ENGINE_INSTANCE = re.compile(r"pid_(\d+).*?engtype_(\w+)", re.IGNORECASE)


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


def _gpu_engine_available() -> bool:
    """Whether `\\GPU Engine(*)\\Utilization Percentage` is a counter this
    machine actually has, asked with `typeperf -q` before it is ever added
    to the real capture command — a counter path typeperf does not
    recognise can make the whole invocation fail, not just this column."""
    code, out, _ = _run(["typeperf", "-q", GPU_ENGINE_COUNTER], timeout=10)
    return code == 0 and out.strip() != ""


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


def config_audit() -> List[CheckItem]:
    """Windows settings that cost frames without anyone touching a game
    setting — read once, not tied to a capture. Unlike `check_status()`
    (can findmybottleneck read this machine at all), a "miss" here is not a
    missing tool, it is a configuration choice with a real, documented FPS
    cost; `ok=False` just means "flagged", not "broken"."""
    items: List[CheckItem] = []

    code, out, _ = _run(["powercfg", "/getactivescheme"])
    if code == 0 and out.strip():
        name_match = re.search(r"\((.+?)\)", out)
        name = name_match.group(1) if name_match else out.strip()
        is_high_perf = "high performance" in name.lower() or "ultimate performance" in name.lower()
        items.append(CheckItem(is_high_perf, "power plan", name))
    else:
        items.append(CheckItem(False, "power plan", "powercfg did not answer"))

    code, out, _ = _powershell(
        "(Get-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\GraphicsDrivers' "
        "-Name HwSchMode -ErrorAction SilentlyContinue).HwSchMode")
    if code == 0 and out.strip().isdigit():
        # 2 = on, 1 (or the key absent) = off — Microsoft's own documented values.
        on = out.strip() == "2"
        items.append(CheckItem(on, "hardware-accelerated GPU scheduling", "on" if on else "off"))
    else:
        items.append(CheckItem(False, "hardware-accelerated GPU scheduling",
                                "registry value not found — off, or an older Windows build"))

    code, out, _ = _powershell(
        "(Get-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\GameBar' "
        "-Name AutoGameModeEnabled -ErrorAction SilentlyContinue).AutoGameModeEnabled")
    if code == 0 and out.strip().isdigit():
        on = out.strip() != "0"
        items.append(CheckItem(on, "Game Mode", "on" if on else "off"))
    else:
        items.append(CheckItem(True, "Game Mode", "registry value not found — on by default on current Windows"))

    code, out, _ = _powershell(
        "(Get-CimInstance -Namespace root\\Microsoft\\Windows\\DeviceGuard -ClassName Win32_DeviceGuard "
        "-ErrorAction SilentlyContinue).SecurityServicesRunning")
    if code == 0 and out.strip():
        # 2 = Hypervisor-enforced Code Integrity (HVCI) — Memory Integrity in
        # Windows Security. Documented, measurable CPU overhead, most visible
        # in CPU-bound games; this is a report, not a claim it is *the*
        # bottleneck here — that is what the capture's own verdict is for.
        running = {v.strip() for v in out.strip().splitlines()}
        hvci_on = "2" in running
        items.append(CheckItem(not hvci_on, "Memory Integrity (HVCI)",
                                "on — has a measurable CPU cost in CPU-bound games" if hvci_on else "off"))
    else:
        items.append(CheckItem(True, "Memory Integrity (HVCI)", "could not be read — assume unknown, not off"))

    return items


def disk_health() -> List[CheckItem]:
    """SMART-derived disk health — a near-full or failing drive causes the
    exact texture-streaming stutters `engine/hitch.py` already attributes
    to disk stalls, so this names the machine-level cause behind that
    per-hitch evidence rather than duplicating it."""
    items: List[CheckItem] = []
    code, out, _ = _powershell(
        "Get-PhysicalDisk | ForEach-Object { '{0}|{1}|{2}' -f $_.FriendlyName, $_.HealthStatus, $_.MediaType }")
    if code != 0 or not out.strip():
        items.append(CheckItem(False, "disk health", "Get-PhysicalDisk did not answer"))
        return items

    for line in out.strip().splitlines():
        fields = [f.strip() for f in line.split("|")]
        if len(fields) < 2:
            continue
        name, health = fields[0], fields[1]
        media = fields[2] if len(fields) > 2 else ""
        detail = f"{health}" + (f" ({media})" if media else "")
        items.append(CheckItem(health.lower() == "healthy", f"disk: {name or '(unnamed)'}", detail))
    return items


def check() -> int:
    items = check_status()

    print()
    for item in items:
        text = f"{item.label}: {item.detail}" if item.detail else item.label
        print(f"  ok    {text}" if item.ok else f"  miss  {text}")
    print()

    # Config/disk items are flags, not missing capabilities — printed
    # separately so they never touch the pass/fail exit code below.
    print("Windows settings and disk health:")
    for item in config_audit() + disk_health():
        text = f"{item.label}: {item.detail}" if item.detail else item.label
        print(f"  ok    {text}" if item.ok else f"  flag  {text}")
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

    code, out, _ = _powershell(
        "$o = Get-CimInstance Win32_OperatingSystem; '{0}|{1}' -f $o.Version, $o.BuildNumber")
    if code == 0 and "|" in out:
        version, build = (out.strip().split("|") + [""])[:2]
        hw.os_build = build.strip() or version.strip() or None
    else:
        missing["OS build"] = "Win32_OperatingSystem did not answer"

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
        "'{0}|{1}|{2}|{3}|{4}|{5}|{6}' -f $_.ConfiguredClockSpeed, $_.Speed, $_.Manufacturer, "
        "$_.PartNumber, $_.BankLabel, $_.DeviceLocator, $_.Capacity }")
    if code == 0 and out.strip():
        banks = set()
        for line in out.strip().splitlines():
            fields = [f.strip() for f in line.split("|")]
            if len(fields) < 6:
                continue
            configured, rated, manufacturer, part, bank, locator = fields[:6]
            capacity = fields[6] if len(fields) > 6 else ""
            hw.memory_modules.append({
                "configured_mhz": int(configured) if configured.isdigit() else None,
                "rated_mhz": int(rated) if rated.isdigit() else None,
                "manufacturer": manufacturer or None,
                "part": part or None,
                "bank": bank or None,
                "slot": locator or None,
                "capacity_gb": round(int(capacity) / (1024 ** 3), 1) if capacity.isdigit() else None,
            })
            banks.add(bank or locator)
        hw.memory_channels_populated = len(banks) or None
    else:
        missing["memory configuration"] = "Win32_PhysicalMemory did not answer"

    return hw, missing


def motherboard_status() -> Tuple[Optional[str], Optional[int], Optional[float], Dict[str, str]]:
    """Motherboard identity and RAM slot layout (D-0018) — for the System
    page's upgrade guidance only, not for `hardware()`. `hardware()` runs on
    every `capture` (it feeds `Trace.hardware`); motherboard model and free
    DIMM slots have nothing to do with diagnosing what set a game's pace, so
    keeping these two extra PowerShell calls out of it means an ordinary
    capture doesn't pay their latency, and a probe failure here doesn't show
    up as noise in a capture's own `missing`/"Not measured" list next to
    genuinely capture-relevant gaps like frame attribution.

    Returns (motherboard, memory_slots_total, memory_max_capacity_gb, missing)
    — the three `Hardware` fields this fills in, plus what could not be read,
    for the caller to merge into a `Hardware` object it already has from a
    separate `hardware()` call.
    """
    missing: Dict[str, str] = {}

    motherboard = None
    code, out, _ = _powershell(
        "$b = Get-CimInstance Win32_BaseBoard | Select-Object -First 1; "
        "'{0}|{1}' -f $b.Manufacturer, $b.Product")
    if code == 0 and "|" in out:
        manufacturer, product = (out.strip().split("|") + [""])[:2]
        board = " ".join(p.strip() for p in (manufacturer, product) if p.strip())
        motherboard = board or None
    else:
        missing["motherboard identity"] = "Win32_BaseBoard did not answer"

    # How many DIMM slots exist and the largest total the board supports —
    # not the fastest speed it supports, which no WMI class reports (see
    # Hardware.memory_slots_total's own docstring note).
    memory_slots_total = None
    memory_max_capacity_gb = None
    code, out, _ = _powershell(
        "$a = Get-CimInstance Win32_PhysicalMemoryArray | Select-Object -First 1; "
        "'{0}|{1}' -f $a.MemoryDevices, $a.MaxCapacity")
    if code == 0 and "|" in out:
        slots, max_kb = (out.strip().split("|") + ["", ""])[:2]
        memory_slots_total = int(slots) if slots.strip().isdigit() else None
        memory_max_capacity_gb = round(int(max_kb) / (1024 ** 2), 1) if max_kb.strip().isdigit() else None
    else:
        missing["memory slot layout"] = "Win32_PhysicalMemoryArray did not answer"

    return motherboard, memory_slots_total, memory_max_capacity_gb, missing


def _gpu_query(prefix: Optional[str]) -> str:
    fields = list(GPU_FIELDS)
    if prefix:
        fields += [f"{prefix}.{flag}" for flag in THROTTLE_FLAGS]
    return ",".join(fields)


def _parse_gpu_row(cells: List[str], prefix: Optional[str], time: float) -> GpuSample:
    """One nvidia-smi CSV row, in the field order `_gpu_query` asked for.
    Shared by `_parse_gpu` (a whole capture's worth of rows) and `gpu_status`
    (a single live reading for the System page) so the two never drift on
    what column means what."""

    def number(position: int) -> Optional[float]:
        try:
            return float(cells[position])
        except (ValueError, IndexError):
            return None

    sample = GpuSample(
        time=time,
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
    return sample


def _parse_gpu(text: str, prefix: Optional[str], interval: float) -> List[GpuSample]:
    samples = []
    for index, line in enumerate(text.strip().splitlines()):
        cells = [c.strip() for c in line.split(",")]
        if len(cells) < len(GPU_FIELDS):
            continue
        samples.append(_parse_gpu_row(cells, prefix, index * interval))
    return samples


def gpu_status() -> Tuple[Optional[GpuSample], Dict[str, str]]:
    """One live nvidia-smi reading — temperature, utilisation, clocks, VRAM,
    power and throttle reasons — for the System page. Not tied to a capture:
    same fields `_gpu_query` already asks for, just a single row instead of
    a polled series written to a file."""
    missing: Dict[str, str] = {}
    prefix = supported_throttle_prefix()
    code, out, _ = _run(["nvidia-smi", f"--query-gpu={_gpu_query(prefix)}",
                         "--format=csv,noheader,nounits"])
    if code != 0 or not out.strip():
        missing["gpu sensors"] = "nvidia-smi did not answer"
        return None, missing
    cells = [c.strip() for c in out.strip().splitlines()[0].split(",")]
    if len(cells) < len(GPU_FIELDS):
        missing["gpu sensors"] = "nvidia-smi returned fewer fields than expected"
        return None, missing
    return _parse_gpu_row(cells, prefix, 0.0), missing


def cpu_temperature() -> Tuple[Optional[float], Dict[str, str]]:
    """CPU package temperature, via LibreHardwareMonitor's Python binding
    (D-0017) — the optional `sensors` extra, `pip install
    findmybottleneck[sensors]`. This is the one reading in this module that
    is not "shell out to something already on the machine" (D-0005): there
    is no command-line tool on Windows that exposes CPU temperature, so this
    is a new, opt-in dependency, and it needs Administrator (the library's
    own bundled driver, not one this project ships or signs). Never part of
    `hardware()` or `check()` — nothing here should make ordinary capture or
    check need Administrator just because this exists. A missing package, a
    missing sensor, or missing Administrator all land in `missing`, exactly
    like every other probe in this module, not an exception."""
    missing: Dict[str, str] = {}
    try:
        from HardwareMonitor.Hardware import Computer, HardwareType, IVisitor, SensorType
    except ImportError:
        missing["cpu temperature"] = ("HardwareMonitor is not installed — "
                                      "pip install findmybottleneck[sensors]")
        return None, missing
    except Exception as exc:
        # pythonnet's own failure modes (no .NET runtime found, a bad CLR
        # version, ...) surface as RuntimeError or worse from deep inside
        # `import clr`, not ImportError — an interop boundary this module
        # does not control, so it is caught broadly here on purpose, unlike
        # everywhere else in this file, and recorded the same as any other
        # missing probe rather than crashing the caller.
        missing["cpu temperature"] = f"HardwareMonitor could not start: {exc}"
        return None, missing

    class _UpdateVisitor(IVisitor):
        __namespace__ = "FindMyBottleneck"

        def VisitComputer(self, computer):
            computer.Traverse(self)

        def VisitHardware(self, hardware):
            hardware.Update()
            for sub in hardware.SubHardware:
                sub.Update()

        def VisitParameter(self, parameter):
            pass

        def VisitSensor(self, sensor):
            pass

    try:
        computer = Computer()
        computer.IsCpuEnabled = True
    except Exception as exc:
        # Same interop boundary as the import above — building the Computer
        # object is still .NET-side work and can fail the same unpredictable
        # ways, before there is even a `computer` to guard with try/finally.
        missing["cpu temperature"] = f"HardwareMonitor could not start: {exc}"
        return None, missing

    readings: List[Tuple[str, float]] = []
    try:
        try:
            computer.Open()
            computer.Accept(_UpdateVisitor())
            for hw in computer.Hardware:
                if hw.HardwareType != HardwareType.Cpu:
                    continue
                for sensor in hw.Sensors:
                    if sensor.SensorType == SensorType.Temperature and sensor.Value is not None:
                        readings.append((sensor.Name, float(sensor.Value)))
        except Exception as exc:
            # Same interop boundary as the import above — a driver that
            # refuses to load without Administrator can raise here instead
            # of just returning no sensors, depending on the .NET side.
            missing["cpu temperature"] = f"reading sensors failed: {exc}"
            return None, missing
    finally:
        try:
            computer.Close()
        except Exception:
            pass

    if not readings:
        missing["cpu temperature"] = ("no temperature sensor was exposed — this usually means "
                                      "findmybottleneck was not run as Administrator")
        return None, missing

    package = next((value for name, value in readings
                    if "package" in name.lower() or "tctl" in name.lower() or "tdie" in name.lower()), None)
    return (package if package is not None else max(value for _, value in readings)), missing


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


def _parse_gpu_engine(path: Path) -> List[Dict[str, Any]]:
    """Per-process 3D-engine utilization, from the same typeperf CSV the
    CPU counters already come from — `\\GPU Engine(*)` instances are named
    like `pid_1234_luid_...engtype_3D`, one per process per engine per GPU
    node, so a process with more than one node (hybrid graphics) gets its
    instances summed under one PID. Utilization is the median over the
    whole capture, the same statistic every other counter in this project
    reports rather than a peak or an average that a single spike distorts."""
    rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8", errors="replace"))))
    if len(rows) < 2:
        return []
    header = [h.strip('"') for h in rows[0]]

    by_pid: Dict[int, List[int]] = {}
    for index, name in enumerate(header):
        match = _GPU_ENGINE_INSTANCE.search(name)
        if not match or match.group(2).lower() != "3d":
            continue
        by_pid.setdefault(int(match.group(1)), []).append(index)
    if not by_pid:
        return []

    samples: Dict[int, List[float]] = {pid: [] for pid in by_pid}
    for row in rows[1:]:
        if not row:
            continue
        for pid, indices in by_pid.items():
            total = 0.0
            for i in indices:
                if i < len(row):
                    try:
                        total += float(row[i].strip('"'))
                    except ValueError:
                        pass
            samples[pid].append(total)

    return [{"pid": pid, "median_util_percent": round(statistics.median(values), 1)}
            for pid, values in samples.items() if values]


def _pid_names() -> Dict[int, str]:
    """PID → process name, the same `tasklist` shelling every other process
    lookup in this module already uses (D-0005)."""
    _, out, _ = _run(["tasklist", "/fo", "csv", "/nh"], timeout=10)
    names: Dict[int, str] = {}
    for line in out.strip().splitlines():
        cells = [c.strip('"') for c in line.split('","')]
        if len(cells) >= 2 and cells[1].isdigit():
            names[int(cells[1])] = cells[0]
    return names


def _process_running(name: str) -> bool:
    """Ask Windows directly whether `name` is running — the same
    "shell out to what's already there" pattern as find_presentmon (D-0005),
    not a new dependency (e.g. psutil)."""
    _, out, _ = _run(["tasklist", "/fi", f"imagename eq {name}", "/fo", "csv", "/nh"], timeout=10)
    return name.lower() in out.lower()


def _wait_for_target(name: str, timeout: float,
                     on_wait: Optional[Callable[[float], None]] = None) -> bool:
    """Poll for `name` to appear in the process list, up to `timeout` seconds.
    Returns whether it appeared. The caller records with whatever it has
    either way, the same as every other probe in this module — a game that
    never starts is not a crash, it's a `missing` entry."""
    started = time.time()
    while not _process_running(name):
        elapsed = time.time() - started
        if elapsed >= timeout:
            return False
        if on_wait:
            on_wait(elapsed)
        time.sleep(1.0)
    return True


def run_capture(process: str, seconds: int, out_path: Path,
                presentmon_path: Optional[str] = None, keep_csv: bool = False,
                on_progress: Optional[Callable[[float, Optional[float]], None]] = None,
                launch: Optional[str] = None, wait_timeout: float = 120.0,
                on_wait: Optional[Callable[[float], None]] = None,
                notes: str = "") -> Tuple[Trace, Optional[Path]]:
    """Record a trace and write it to `out_path`. Returns the trace, and the
    workdir the raw CSVs were kept in if `keep_csv` was set (else `None`).

    This is the whole body of what used to be `capture(args)` — the CLI
    below is now a thin wrapper that prints what it always printed, and the
    GUI is another caller that drives the same recording with its own
    progress bar instead of a `\\r`-overwritten countdown. No new subprocess
    logic, no new parsing: this and `capture()` are one implementation.

    If `launch` is given, that executable is started before recording. Either
    way, once a target process name is known, recording waits for it to
    actually appear (D-0014) instead of counting down through a loading
    screen or an empty capture window.

    `seconds <= 0` means no fixed window: recording runs for as long as
    `target` stays alive instead of a fixed number of seconds, so a whole
    play session becomes one trace instead of a 30s sample of it — more
    frames behind the median, 1% low and throttle checks (D-0015). It needs
    a target process to know when to stop; without one (no `process` and no
    `launch`) it falls back to a 30s capture and says so under `missing`.
    `on_progress` is called with `total=None` while unbounded, since there
    is no total to report a fraction of.
    """
    unbounded = int(seconds) <= 0
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
    target = process or (Path(launch).name if launch else "")
    if not presentmon:
        missing["frame attribution"] = "PresentMon was not found, so nothing could attribute a frame to the CPU or GPU"
    elif not target:
        missing["frame attribution"] = "no process was given: findmybottleneck capture <game.exe>"

    if unbounded and not target:
        missing["duration"] = "no --seconds and no target process to stop on — recording 30s instead"
        unbounded = False
        seconds = 30
    if not unbounded:
        seconds = max(5, int(seconds))

    if launch:
        try:
            subprocess.Popen([launch], cwd=str(Path(launch).resolve().parent))
        except OSError as exc:
            missing["launch"] = f"could not start {launch}: {exc}"

    if target and presentmon and wait_timeout > 0 and not _process_running(target):
        if not _wait_for_target(target, wait_timeout, on_wait):
            missing["frame attribution"] = (
                f"{target} never appeared within {int(wait_timeout)}s — nothing was recorded for it")

    gpu_csv, cpu_csv, frames_csv = workdir / "gpu.csv", workdir / "cpu.csv", workdir / "frames.csv"
    workers = [subprocess.Popen(
        ["nvidia-smi", f"--query-gpu={_gpu_query(prefix)}",
         "--format=csv,noheader,nounits", "-lms", "500", "-f", str(gpu_csv)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]

    gpu_engine_available = _gpu_engine_available()
    typeperf_cmd = ["typeperf", *CPU_COUNTERS]
    if gpu_engine_available:
        typeperf_cmd.append(GPU_ENGINE_COUNTER)
    typeperf_cmd += ["-si", "1"]
    if not unbounded:
        typeperf_cmd += ["-sc", str(seconds)]
    typeperf_cmd += ["-f", "CSV", "-o", str(cpu_csv), "-y"]
    workers.append(subprocess.Popen(typeperf_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))

    presentmon_worker = None
    if presentmon and target:
        presentmon_cmd = [presentmon, "--process_name", target]
        if not unbounded:
            presentmon_cmd += ["--timed", str(seconds), "--terminate_after_timed"]
        presentmon_cmd += ["--output_file", str(frames_csv), "--no_console_stats"]
        presentmon_worker = subprocess.Popen(
            presentmon_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

    started = time.time()
    if unbounded:
        # No fixed window: stop when the game does, not on a clock (D-0015).
        while _process_running(target):
            time.sleep(0.5)
            if on_progress:
                on_progress(time.time() - started, None)
    else:
        while time.time() - started < seconds:
            time.sleep(0.5)
            if on_progress:
                on_progress(time.time() - started, seconds)
    actual_seconds = time.time() - started

    for worker in workers:
        worker.terminate()
    if presentmon_worker:
        if unbounded:
            # Bounded mode's PresentMon stops itself via --terminate_after_timed;
            # unbounded mode has no timer, so it needs the same explicit stop the
            # other two workers already get.
            try:
                presentmon_worker.terminate()
            except OSError:
                pass
        try:
            _, stderr = presentmon_worker.communicate(timeout=15)
            if presentmon_worker.returncode not in (0, None) and stderr:
                missing["frame attribution"] = f"PresentMon exited with an error: {stderr.strip()[:200]}"
        except subprocess.TimeoutExpired:
            presentmon_worker.kill()
            missing["frame attribution"] = "PresentMon did not stop when asked"

    trace = Trace(
        captured_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        duration_s=round(actual_seconds, 1) if unbounded else float(seconds),
        target=target, notes=notes, hardware=hw, missing=missing,
    )

    if gpu_csv.exists():
        trace.gpu = _parse_gpu(gpu_csv.read_text(encoding="utf-8", errors="replace"), prefix, 0.5)
    else:
        missing["gpu telemetry"] = "nvidia-smi wrote nothing"

    if cpu_csv.exists():
        trace.cpu, trace.disk, trace.memory = _parse_typeperf(cpu_csv)
        if gpu_engine_available:
            raw_engine = _parse_gpu_engine(cpu_csv)
            if raw_engine:
                names = _pid_names()
                trace.gpu_engine = [{**entry, "name": names.get(entry["pid"])} for entry in raw_engine]
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
    seconds = int(args.seconds)
    unbounded = seconds <= 0
    launch = getattr(args, "launch", None)
    wait_timeout = getattr(args, "wait_timeout", 120.0)
    target = args.process or (Path(launch).name if launch else "")

    if launch:
        print(f"launching {launch}")
    if unbounded and target:
        print(f"waiting for {target} to start, then recording until it closes — play normally")
    elif target:
        print(f"waiting for {target} to start, then recording {seconds}s — play normally")
    else:
        print(f"recording {seconds}s — play normally")

    def on_progress(elapsed: float, total: Optional[float]) -> None:
        if total is None:
            print(f"\r  {int(elapsed):>5}s recorded, waiting for {target} to close ", end="", flush=True)
        else:
            print(f"\r  {int(total - elapsed):>3}s left ", end="", flush=True)

    def on_wait(elapsed: float) -> None:
        print(f"\r  waiting… {int(elapsed)}s ", end="", flush=True)

    trace, workdir = run_capture(
        args.process, seconds, Path(args.out),
        presentmon_path=getattr(args, "presentmon", None),
        keep_csv=getattr(args, "keep_csv", False),
        on_progress=on_progress,
        launch=launch,
        wait_timeout=wait_timeout,
        on_wait=on_wait,
        notes=getattr(args, "notes", "") or "",
    )
    print("\r" + " " * 60 + "\r", end="")

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
            [presentmon, "--process_name", target, "--output_file", str(frames_csv), "--no_console_stats"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

        window_s = float(getattr(args, "window_seconds", 3.0))
        refresh_s = max(0.1, int(getattr(args, "refresh_ms", 1000)) / 1000.0)
        min_frames = 10

        # Optional: the same rolling verdict this loop already computes,
        # appended to a file with a timestamp (D-0020) — so a stutter
        # noticed mid-match can be looked up after the fact ("what did the
        # verdict say at that moment?") without a separate capture running
        # in advance. Nothing new is measured; this only persists what
        # `overlay` already decides every refresh and would otherwise only
        # ever show on screen for an instant.
        log_path = getattr(args, "log", None)

        print(f"pushing a live verdict for {target} into RTSS — Ctrl+C to stop")
        if log_path:
            print(f"also logging every refresh to {log_path}")
        while True:
            time.sleep(refresh_s)
            all_frames = _parse_presentmon(frames_csv) if frames_csv.exists() else []
            recent = []
            if all_frames:
                latest = all_frames[-1].time
                recent = [f for f in all_frames if f.time >= latest - window_s]
            verdict, _, stats, _ = analyse(recent) if len(recent) >= min_frames else (None, [], {}, [])
            status = format_status(verdict, stats, len(recent), min_frames)
            writer.push(status)
            if log_path:
                timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
                try:
                    with open(log_path, "a", encoding="utf-8") as log_file:
                        log_file.write(f"{timestamp}  {status}\n")
                except OSError:
                    pass
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
