"""What is wrong with the machine, independent of what paced the frames.

These are the findings that turn a verdict into an action. A processor that
looks slow is very often a processor waiting for memory that never had its
profile enabled — the single most common real cause in the wild, and invisible
to every score-ratio site because none of them ever touch the machine.
"""

from __future__ import annotations

import statistics
from typing import Dict, List, Optional

from ..trace import Trace
from ..verdict import Finding

# Below this share of its rated speed, memory is running on a fallback default.
MEMORY_SLACK = 0.9
# A GPU spending this much of the capture throttled is worth reporting.
THROTTLE_SHARE = 0.10
# Disk latency, in seconds, at which a read is long enough to be felt.
DISK_STALL_S = 0.020
# GPU memory this close to full, with spill present, is an overflow.
VRAM_FULL = 0.95
# % Processor Time at or above this counts as the processor being under load —
# below it, a low % Processor Performance is normal frequency scaling to save
# power, not throttling, and means nothing.
CPU_BUSY_PERCENT = 80
# Below this share of its rated (nominal) clock, a busy processor is being
# held back rather than just not boosting.
CPU_PERFORMANCE_FLOOR = 0.85
# This share of busy samples held back is worth reporting.
CPU_THROTTLE_SHARE = 0.20


def _cite(trace_sources: dict, key: str) -> Dict[str, str]:
    method = trace_sources["methods"].get(key, {})
    return {"source": method.get("url"), "quote": method.get("quote", "")}


def memory(trace: Trace, sources: dict) -> List[Finding]:
    found: List[Finding] = []
    modules = [m for m in trace.hardware.memory_modules if m]
    if not modules:
        return found

    cite = _cite(sources, "memory_speed")
    slow = [
        m for m in modules
        if m.get("configured_mhz") and m.get("rated_mhz")
        and m["configured_mhz"] < m["rated_mhz"] * MEMORY_SLACK
    ]
    if slow:
        first = slow[0]
        found.append(Finding(
            severity="high",
            title=f"Your memory is running at {first['configured_mhz']} MHz, and the modules are rated for {first['rated_mhz']}",
            evidence=[
                f"{len(slow)} of {len(modules)} modules below their rating",
                *[f"{m.get('manufacturer', '?')} {m.get('part', '?')}: configured {m.get('configured_mhz')} MHz, rated {m.get('rated_mhz')} MHz"
                  for m in slow[:4]],
            ],
            fix="Enable the memory profile in your BIOS — XMP on Intel boards, EXPO on AMD. It is one "
                "setting, it costs nothing, and this is the most common reason a fast machine feels slow.",
            **cite,
        ))

    channels = trace.hardware.memory_channels_populated
    if len(modules) == 1:
        found.append(Finding(
            severity="high",
            title="Only one memory module is installed, so memory is running in single channel",
            evidence=["one module detected"],
            fix="Add a second matched module. Single channel halves memory bandwidth, and on a "
                "machine with integrated graphics it is worse than that.",
            **cite,
        ))
    elif channels == 1 and len(modules) > 1:
        found.append(Finding(
            severity="high",
            title=f"{len(modules)} modules are installed but they are all on one channel",
            evidence=["every module reports the same channel or bank"],
            fix="Move one module to the other channel — usually the slots are colour-coded in pairs, "
                "and the correct pair is almost never the two next to each other.",
            heuristic=True,
            **cite,
        ))
    return found


def throttling(trace: Trace, sources: dict) -> List[Finding]:
    if not trace.gpu:
        return []
    cite = _cite(sources, "throttle_reasons")
    labels = {
        "sw_power_cap": ("power limit", "The card asked for more power than its limit allows. Raising the "
                                        "power limit in a tuning tool, or improving airflow, gives it back."),
        "hw_slowdown": ("hardware slowdown", "A hardware protection engaged — temperature, power brake, or "
                                             "a fast trigger. Check temperatures and the power connector."),
        "hw_thermal_slowdown": ("hardware thermal slowdown", "The card hit its hardware temperature limit. "
                                                             "This is airflow, dust, or dried thermal paste."),
        "sw_thermal_slowdown": ("thermal", "The driver reduced clocks to stay within temperature."),
        "hw_power_brake_slowdown": ("external power brake", "The power supply asserted a brake. This is a "
                                                            "power delivery problem, not a GPU problem."),
    }
    found = []
    for flag, (label, fix) in labels.items():
        active = [s for s in trace.gpu if s.throttle.get(flag)]
        if not active or len(active) / len(trace.gpu) < THROTTLE_SHARE:
            continue
        share = len(active) / len(trace.gpu)
        temps = [s.temperature for s in active if s.temperature is not None]
        powers = [(s.power_draw, s.power_limit) for s in active if s.power_draw and s.power_limit]
        evidence = [f"{label} active in {round(share * 100)}% of samples"]
        if temps:
            evidence.append(f"GPU temperature while throttled: median {round(statistics.median(temps))} °C, peak {round(max(temps))} °C")
        if powers:
            draw, limit = powers[0]
            evidence.append(f"power draw {round(statistics.median([p for p, _ in powers]))} W against a limit of {round(limit)} W")
        found.append(Finding(
            severity="high" if share > 0.5 else "medium",
            title=f"Your graphics card spent {round(share * 100)}% of the capture throttled by {label}",
            evidence=evidence, fix=fix, **cite,
        ))
    return found


def cpu_throttling(trace: Trace, sources: dict) -> List[Finding]:
    """Every score-ratio site only ever asks the GPU whether it is throttled.

    Only samples where the processor was actually busy mean anything here —
    the same rule as the PCIe link (D-0004): a metric that is only meaningful
    under load is judged only from samples taken under load. An idle processor
    runs below its rated clock on purpose, to save power, and that is not a
    finding.
    """
    if not trace.cpu:
        return []
    busy = [s for s in trace.cpu
            if (s.total or 0) >= CPU_BUSY_PERCENT and s.processor_performance is not None]
    if not busy:
        return []
    held_back = [s for s in busy if s.processor_performance < CPU_PERFORMANCE_FLOOR * 100]
    share = len(held_back) / len(busy)
    if share < CPU_THROTTLE_SHARE:
        return []
    perf = [s.processor_performance for s in held_back]
    median_perf = round(statistics.median(perf))
    return [Finding(
        severity="high" if share > 0.5 else "medium",
        title=f"Your processor is running at {median_perf}% of its rated clock while fully loaded, "
              f"in {round(share * 100)}% of the busy samples",
        evidence=[
            f"{len(held_back)} of {len(busy)} samples with % Processor Time at or above "
            f"{CPU_BUSY_PERCENT}% were below {round(CPU_PERFORMANCE_FLOOR * 100)}% of nominal performance",
            f"median {median_perf}% of nominal, lowest {round(min(perf))}%",
        ],
        fix="This is a processor being held back, not one with nothing to do. Check Power Options → "
            "Processor power management → Maximum processor state (a common laptop default caps it "
            "below 100%), and check temperatures under load — this is thermal throttling on most "
            "laptops and small-form-factor machines.",
        **_cite(sources, "cpu_performance"),
    )]


def pcie_link(trace: Trace, sources: dict) -> List[Finding]:
    """Only a sample taken while the GPU was working means anything here."""
    if not trace.gpu:
        return []
    busy = [s for s in trace.gpu if (s.utilisation or 0) >= 50]
    if not busy:
        return [Finding(
            severity="note",
            title="The PCIe link was not judged, because the GPU was never busy during the capture",
            evidence=["no sample with GPU utilisation at or above 50%"],
            fix="Capture again while the game is actually rendering. A link read at idle is always "
                "narrow: the card downtrains to save power, and reporting that as a fault is how "
                "these tools earn their reputation.",
            **_cite(sources, "pcie_link"),
        )]
    narrow = [s for s in busy if s.pcie_width and s.pcie_width_max and s.pcie_width < s.pcie_width_max]
    slow = [s for s in busy if s.pcie_gen and s.pcie_gen_max and s.pcie_gen < s.pcie_gen_max]
    found = []
    if narrow:
        s = narrow[0]
        found.append(Finding(
            severity="high",
            title=f"Under load the graphics card is linked at x{s.pcie_width}, and it supports x{s.pcie_width_max}",
            evidence=[f"measured while GPU utilisation was {round(s.utilisation or 0)}%",
                      f"{len(narrow)} of {len(busy)} busy samples agree"],
            fix="Reseat the card in the top slot, check the slot latch for damage, and look for a BIOS "
                "setting forcing the link width. A second M.2 drive sharing lanes with the slot also does this.",
            **_cite(sources, "pcie_link"),
        ))
    if slow:
        s = slow[0]
        found.append(Finding(
            severity="medium",
            title=f"Under load the link negotiated PCIe Gen {s.pcie_gen}, and the card supports Gen {s.pcie_gen_max}",
            evidence=[f"measured while GPU utilisation was {round(s.utilisation or 0)}%"],
            fix="Check the PCIe generation setting in BIOS, which is often left on a fixed value rather than Auto.",
            **_cite(sources, "pcie_link"),
        ))
    return found


def vram(trace: Trace, sources: dict) -> List[Finding]:
    if not trace.gpu:
        return []
    full = [s for s in trace.gpu if s.memory_used and s.memory_total and s.memory_used / s.memory_total >= VRAM_FULL]
    spill = [s for s in trace.memory if s.gpu_shared_mb and s.gpu_shared_mb > 0]
    if not full:
        return []
    share = len(full) / len(trace.gpu)
    used = max(s.memory_used for s in full)
    total = full[0].memory_total
    evidence = [f"graphics memory at {round(used)} MiB of {round(total)} MiB for {round(share * 100)}% of the capture"]
    if spill:
        evidence.append(f"up to {round(max(s.gpu_shared_mb for s in spill))} MiB of it spilled into system memory")
    return [Finding(
        severity="high" if spill else "medium",
        title="Graphics memory is full" + (", and overflowing into system memory" if spill else ""),
        evidence=evidence,
        fix="Lower texture quality by one step and measure again. The symptom of this is stutter and "
            "textures that appear late, not a lower average frame rate, so it is easy to misread.",
        heuristic=not spill,
        source=sources["methods"]["gpu_busy"]["url"] if not spill else None,
    )]
