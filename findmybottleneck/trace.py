"""The trace: what a capture writes, and what a verdict is computed from.

The collector and the engine never speak directly. A capture writes one JSON
file, and everything that reasons about performance reads that file. Two
reasons, and the second is the interesting one:

* The engine becomes testable without the hardware. A capture from a machine
  with a dedicated GPU can be replayed anywhere, so the part that can be wrong
  — the reasoning — is checked against real data on any laptop.
* A trace can be handed to someone else. Today a person with a stutter posts a
  screenshot of a monitoring overlay and waits for a stranger to interpret it.
  A trace is that screenshot, except a program can read it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = 1


@dataclass
class Frame:
    """One presented frame, as PresentMon reports it. Milliseconds."""

    time: float          # seconds since capture start
    frame_time: float    # MsBetweenPresents
    cpu_busy: float      # MsCPUBusy
    cpu_wait: float      # MsCPUWait
    gpu_busy: float      # MsGPUBusy
    gpu_wait: float      # MsGPUWait
    gpu_latency: float   # MsGPULatency
    in_present: float    # MsInPresentAPI
    sync_interval: int   # 0 means the app did not ask to sync to the display


@dataclass
class GpuSample:
    time: float
    utilisation: Optional[float] = None       # per cent
    memory_used: Optional[float] = None       # MiB
    memory_total: Optional[float] = None      # MiB
    temperature: Optional[float] = None       # degrees C
    power_draw: Optional[float] = None        # W
    power_limit: Optional[float] = None       # W
    clock_graphics: Optional[float] = None    # MHz
    clock_max_graphics: Optional[float] = None
    throttle: Dict[str, bool] = field(default_factory=dict)
    pcie_gen: Optional[int] = None
    pcie_gen_max: Optional[int] = None
    pcie_width: Optional[int] = None
    pcie_width_max: Optional[int] = None


@dataclass
class CpuSample:
    time: float
    total: Optional[float] = None             # per cent
    cores: List[float] = field(default_factory=list)
    processor_performance: Optional[float] = None  # % Processor Performance
    frequency: Optional[float] = None         # MHz


@dataclass
class DiskSample:
    time: float
    read_latency: Optional[float] = None      # seconds
    write_latency: Optional[float] = None
    queue: Optional[float] = None


@dataclass
class MemorySample:
    time: float
    available_mb: Optional[float] = None
    pages_per_sec: Optional[float] = None
    gpu_shared_mb: Optional[float] = None     # GPU memory spilled to system RAM


@dataclass
class Hardware:
    """What the machine is, read once. Not a benchmark — a configuration."""

    gpu_name: Optional[str] = None
    driver: Optional[str] = None
    cpu_name: Optional[str] = None
    physical_cores: Optional[int] = None
    logical_cores: Optional[int] = None
    motherboard: Optional[str] = None
    memory_modules: List[Dict[str, Any]] = field(default_factory=list)
    memory_channels_populated: Optional[int] = None
    # What the motherboard reports about itself — how many DIMM slots it
    # has and the largest total it supports (Win32_PhysicalMemoryArray).
    # Not the same question as a module's *speed*: Windows has no field for
    # the fastest speed a motherboard supports, only what is installed and
    # running now — that number is a manufacturer spec (a QVL page or the
    # board's manual), not something this project reads or guesses at.
    memory_slots_total: Optional[int] = None
    memory_max_capacity_gb: Optional[float] = None
    os: Optional[str] = None
    os_build: Optional[str] = None            # Windows build number — a `history` regression that
                                               # lines up with a build change points at the update,
                                               # not at the game or the hardware
    display_refresh_hz: Optional[float] = None


@dataclass
class Trace:
    schema: int = SCHEMA
    findmybottleneck_version: str = "0.1.0"
    captured_at: str = ""
    duration_s: float = 0.0
    target: str = ""                          # the process that was watched
    notes: str = ""                           # what the user tagged this capture with — in-game
                                               # settings, what changed since the last one, anything
                                               # that makes an old trace worth re-reading later
    hardware: Hardware = field(default_factory=Hardware)
    frames: List[Frame] = field(default_factory=list)
    gpu: List[GpuSample] = field(default_factory=list)
    cpu: List[CpuSample] = field(default_factory=list)
    disk: List[DiskSample] = field(default_factory=list)
    memory: List[MemorySample] = field(default_factory=list)
    # Per-process 3D-engine utilization during the capture — {"pid", "name",
    # "median_util_percent"} per process, from \GPU Engine(*) typeperf
    # instances present when the capture started (D-0020). A process
    # launched mid-capture is not enumerated; that is a real limitation,
    # not a bug — the counter is sampled once, at typeperf's own startup.
    gpu_engine: List[Dict[str, Any]] = field(default_factory=list)
    # What the collector could not read, and why. A verdict must be able to say
    # "I did not measure this", so the gaps travel with the data.
    missing: Dict[str, str] = field(default_factory=dict)

    def write(self, path: Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=1), encoding="utf-8")

    @staticmethod
    def read(path: Path) -> "Trace":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if raw.get("schema") != SCHEMA:
            raise ValueError(
                f"this trace is schema {raw.get('schema')}, this findmybottleneck reads schema {SCHEMA}"
            )
        return Trace(
            schema=raw["schema"],
            findmybottleneck_version=raw.get("findmybottleneck_version", ""),
            captured_at=raw.get("captured_at", ""),
            duration_s=raw.get("duration_s", 0.0),
            target=raw.get("target", ""),
            notes=raw.get("notes", ""),
            hardware=Hardware(**raw.get("hardware", {})),
            frames=[Frame(**f) for f in raw.get("frames", [])],
            gpu=[GpuSample(**s) for s in raw.get("gpu", [])],
            cpu=[CpuSample(**s) for s in raw.get("cpu", [])],
            disk=[DiskSample(**s) for s in raw.get("disk", [])],
            memory=[MemorySample(**s) for s in raw.get("memory", [])],
            gpu_engine=raw.get("gpu_engine", []),
            missing=raw.get("missing", {}),
        )
