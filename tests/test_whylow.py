"""Standard library only. Two halves, and the split is the whole design.

The parsers are fed text shaped exactly like what the real tools emit, so the
reading half is tested without Windows. The engine is fed traces, so the
reasoning half is tested without hardware. What is left untested is only
whether the three programs actually run and produce that text on a real
machine — which is one capture, not a test suite.

Every rule gets the case that must fire it and the neighbouring case that must
stay silent.
"""

from __future__ import annotations

import json
import struct
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from findmybottleneck import rtss
from findmybottleneck.collect import windows
from findmybottleneck.engine import SOURCES, judge
from findmybottleneck.engine import config as cfg
from findmybottleneck.engine.compare import compare
from findmybottleneck.engine.frames import analyse, classify, looks_capped
from findmybottleneck.engine.hitch import summarise
from findmybottleneck.trace import (CpuSample, DiskSample, Frame, GpuSample, Hardware, MemorySample,
                          Trace)
from findmybottleneck.verdict import Verdict


def frames(count, frame_ms, gpu_ms, cpu_ms, sync=0):
    return [Frame(i * frame_ms / 1000.0, frame_ms, cpu_ms, 0.1, gpu_ms, 0.1, 1.0, 0.2, sync)
            for i in range(count)]


def rtss_buffer(version, arr_size=8, entry_size=4608, owners=None, app_arr_offset=None,
               app_entry_size=600, app_arr_size=256):
    """A synthetic RTSS shared-memory buffer: the 9-DWORD header followed by
    `arr_size` OSD slots, each `entry_size` bytes (real RTSS uses 4608 for a
    v2.7+ build: szOSD[256] + szOSDOwner[256] + szOSDEx[4096]).

    `app_arr_offset` defaults to the value a genuine header always has —
    `arrApp` sits immediately after `arrOSD` — since `parse_header` now
    checks that the two agree; pass an explicit value to build a header that
    fails that check on purpose."""
    owners = owners or {}
    osd_arr_offset = struct.calcsize("<9I")
    if app_arr_offset is None:
        app_arr_offset = osd_arr_offset + arr_size * entry_size
    header = struct.pack(
        "<9I", rtss.SIGNATURE, version,
        app_entry_size, app_arr_offset, app_arr_size,
        entry_size, osd_arr_offset, arr_size, 0)
    body = bytearray(entry_size * arr_size)
    for index, owner in owners.items():
        base = index * entry_size
        owner_bytes = owner.encode("ascii")
        body[base + rtss.OSD_OWNER_OFFSET:base + rtss.OSD_OWNER_OFFSET + len(owner_bytes)] = owner_bytes
    return bytes(header) + bytes(body)


def rtss_raw_header(version, entry_size=4608, arr_size=8, app_arr_offset=None,
                    app_entry_size=600, app_arr_size=256):
    """Just the 36-byte header, for tests that only need to check whether
    `parse_header` accepts or rejects a given geometry — no slot body is
    allocated, so this is safe to call with a huge or zero `entry_size`/
    `arr_size` without trying to build gigabytes of test data."""
    osd_arr_offset = struct.calcsize("<9I")
    if app_arr_offset is None:
        app_arr_offset = osd_arr_offset + arr_size * entry_size
    return struct.pack("<9I", rtss.SIGNATURE, version, app_entry_size, app_arr_offset, app_arr_size,
                       entry_size, osd_arr_offset, arr_size, 0)


class Sources(unittest.TestCase):
    def test_every_method_has_a_url_and_a_quote(self):
        for name, method in SOURCES["methods"].items():
            self.assertTrue(method["url"].startswith("https://"), name)
            self.assertTrue(method["quote"].strip(), name)


class Attribution(unittest.TestCase):
    def test_gpu_filling_the_frame_is_gpu_bound(self):
        self.assertEqual(classify(Frame(0, 10, 4, 0, 9.8, 0, 1, 0, 0)), "gpu")

    def test_cpu_filling_the_frame_is_cpu_bound(self):
        self.assertEqual(classify(Frame(0, 10, 9.9, 0, 4, 0, 1, 0, 0)), "cpu")

    def test_a_frame_neither_was_busy_for_is_a_stall(self):
        self.assertEqual(classify(Frame(0, 100, 5, 0, 6, 0, 1, 0, 0)), "stall")

    def test_a_frame_cap_is_not_reported_as_a_bottleneck(self):
        verdict, _, stats, _ = analyse(frames(200, 16.67, 6.0, 5.0, sync=1))
        self.assertEqual(verdict.limiter, "frame-cap")
        self.assertEqual(stats["median_fps"], 60.0)
        self.assertIn("60 fps", verdict.headline)

    def test_a_cap_is_not_claimed_when_the_gpu_fills_the_frame(self):
        """At 60 fps with the GPU busy the whole time, the GPU is the limit."""
        verdict, _, _, _ = analyse(frames(200, 16.67, 16.4, 5.0, sync=1))
        self.assertEqual(verdict.limiter, "gpu")

    def test_a_cap_is_not_claimed_when_frames_are_uneven(self):
        uneven = frames(100, 16.67, 6.0, 5.0) + frames(100, 33.0, 6.0, 5.0)
        self.assertIsNone(looks_capped(uneven))

    def test_too_few_frames_produces_no_verdict(self):
        verdict, _, _, _ = analyse(frames(5, 10, 9, 4))
        self.assertIsNone(verdict)

    def test_the_lows_are_reported_separately_from_the_median(self):
        mixed = frames(990, 10, 9.8, 4) + frames(10, 60, 9.8, 4)
        _, _, stats, _ = analyse(mixed)
        self.assertEqual(stats["median_fps"], 100.0)
        self.assertLess(stats["low01_fps"], 40.0)


class Configuration(unittest.TestCase):
    def test_memory_below_its_rating_is_high(self):
        trace = Trace(hardware=Hardware(
            memory_modules=[{"configured_mhz": 2133, "rated_mhz": 6000, "bank": "A"},
                            {"configured_mhz": 2133, "rated_mhz": 6000, "bank": "B"}],
            memory_channels_populated=2))
        found = cfg.memory(trace, SOURCES)
        self.assertEqual(found[0].severity, "high")
        self.assertIn("2133", found[0].title)
        self.assertIn("XMP", found[0].fix)

    def test_memory_at_its_rating_is_silent(self):
        trace = Trace(hardware=Hardware(
            memory_modules=[{"configured_mhz": 6000, "rated_mhz": 6000, "bank": "A"},
                            {"configured_mhz": 6000, "rated_mhz": 6000, "bank": "B"}],
            memory_channels_populated=2))
        self.assertEqual(cfg.memory(trace, SOURCES), [])

    def test_a_single_module_is_reported(self):
        trace = Trace(hardware=Hardware(
            memory_modules=[{"configured_mhz": 3200, "rated_mhz": 3200, "bank": "A"}],
            memory_channels_populated=1))
        self.assertTrue(any("single channel" in f.title for f in cfg.memory(trace, SOURCES)))

    def test_a_throttle_present_in_most_samples_is_high(self):
        trace = Trace(gpu=[GpuSample(i, utilisation=99, temperature=88, power_draw=310,
                                     power_limit=320, throttle={"hw_thermal_slowdown": True})
                           for i in range(10)])
        found = cfg.throttling(trace, SOURCES)
        self.assertEqual(found[0].severity, "high")
        self.assertIn("88", " ".join(found[0].evidence))

    def test_a_throttle_in_one_sample_of_fifty_is_silent(self):
        trace = Trace(gpu=[GpuSample(i, throttle={"sw_power_cap": i == 0}) for i in range(50)])
        self.assertEqual(cfg.throttling(trace, SOURCES), [])

    def test_gpu_idle_is_never_reported_as_a_problem(self):
        trace = Trace(gpu=[GpuSample(i, throttle={"gpu_idle": True}) for i in range(10)])
        self.assertEqual(cfg.throttling(trace, SOURCES), [])

    def test_a_fully_loaded_cpu_running_below_its_rated_clock_is_reported(self):
        trace = Trace(cpu=[CpuSample(i, total=95, processor_performance=60) for i in range(10)])
        found = cfg.cpu_throttling(trace, SOURCES)
        self.assertEqual(found[0].severity, "high")
        self.assertIn("60%", found[0].title)

    def test_a_fully_loaded_cpu_at_its_rated_clock_is_silent(self):
        trace = Trace(cpu=[CpuSample(i, total=95, processor_performance=100) for i in range(10)])
        self.assertEqual(cfg.cpu_throttling(trace, SOURCES), [])

    def test_a_boosted_cpu_above_100_percent_is_silent(self):
        trace = Trace(cpu=[CpuSample(i, total=95, processor_performance=140) for i in range(10)])
        self.assertEqual(cfg.cpu_throttling(trace, SOURCES), [])

    def test_an_idle_cpu_running_slow_is_never_reported_as_throttled(self):
        """Frequency scaling down at idle to save power is normal, not a fault."""
        trace = Trace(cpu=[CpuSample(i, total=8, processor_performance=15) for i in range(10)])
        self.assertEqual(cfg.cpu_throttling(trace, SOURCES), [])

    def test_a_pinned_core_is_judged_even_when_the_whole_processor_average_is_low(self):
        """A game bound to one or two threads never pushes the _Total average past 80%."""
        trace = Trace(cpu=[CpuSample(i, total=15, cores=[10.0, 92.0], processor_performance=55)
                          for i in range(10)])
        found = cfg.cpu_throttling(trace, SOURCES)
        self.assertIn("55%", found[0].title)

    def test_an_idle_processor_with_one_busy_core_but_no_throttle_is_silent(self):
        trace = Trace(cpu=[CpuSample(i, total=15, cores=[10.0, 92.0], processor_performance=100)
                          for i in range(10)])
        self.assertEqual(cfg.cpu_throttling(trace, SOURCES), [])

    def test_a_clock_that_settles_at_its_base_speed_is_reported_as_a_declining_trend(self):
        """Losing turbo boost down to (not below) the nominal clock is still throttling —
        the absolute floor can't see it, so the declining-trend check has to."""
        boosted = [CpuSample(i, total=95, processor_performance=140) for i in range(10)]
        settled = [CpuSample(10 + i, total=95, processor_performance=100) for i in range(10)]
        found = cfg.cpu_throttling(Trace(cpu=boosted + settled), SOURCES)
        self.assertEqual(found[0].severity, "medium")
        self.assertIn("fell", found[0].title)

    def test_a_steady_clock_is_not_reported_as_a_declining_trend(self):
        trace = Trace(cpu=[CpuSample(i, total=95, processor_performance=100) for i in range(20)])
        self.assertEqual(cfg.cpu_throttling(trace, SOURCES), [])

    def test_fewer_than_six_busy_samples_is_too_few_for_a_trend(self):
        boosted = [CpuSample(i, total=95, processor_performance=140) for i in range(2)]
        settled = [CpuSample(2 + i, total=95, processor_performance=100) for i in range(3)]
        self.assertEqual(cfg.cpu_throttling(Trace(cpu=boosted + settled), SOURCES), [])

    def test_a_narrow_link_under_load_is_reported(self):
        trace = Trace(gpu=[GpuSample(i, utilisation=97, pcie_width=4, pcie_width_max=16)
                           for i in range(10)])
        found = cfg.pcie_link(trace, SOURCES)
        self.assertEqual(found[0].severity, "high")
        self.assertIn("x4", found[0].title)

    def test_a_narrow_link_at_idle_is_refused_rather_than_reported(self):
        """The trap that discredits every tool in this category."""
        trace = Trace(gpu=[GpuSample(i, utilisation=2, pcie_width=1, pcie_width_max=16)
                           for i in range(10)])
        found = cfg.pcie_link(trace, SOURCES)
        self.assertEqual(found[0].severity, "note")
        self.assertIn("never busy", found[0].title)

    def test_full_vram_with_spill_is_high(self):
        trace = Trace(gpu=[GpuSample(i, memory_used=7900, memory_total=8000) for i in range(10)],
                      memory=[MemorySample(i, gpu_shared_mb=1500) for i in range(10)])
        found = cfg.vram(trace, SOURCES)
        self.assertEqual(found[0].severity, "high")
        self.assertIn("system memory", found[0].title)

    def test_vram_with_room_is_silent(self):
        trace = Trace(gpu=[GpuSample(i, memory_used=4000, memory_total=8000) for i in range(10)])
        self.assertEqual(cfg.vram(trace, SOURCES), [])



class Hitches(unittest.TestCase):
    def test_a_disk_stall_coincident_with_a_spike_is_named(self):
        rows = frames(200, 10, 9.8, 4)
        rows[100] = Frame(1.0, 95.0, 5.0, 0.1, 6.0, 0.1, 1.0, 0.2, 0)
        report = judge(Trace(frames=rows, disk=[DiskSample(1.0, read_latency=0.045)]))
        summary = summarise(report.hitches)
        self.assertEqual(summary["count"], 1)
        self.assertIn("disk stall", summary["by_cause"])

    def test_an_unexplained_hitch_is_admitted(self):
        rows = frames(200, 10, 9.8, 4)
        rows[100] = Frame(1.0, 95.0, 90.0, 0.1, 90.0, 0.1, 1.0, 0.2, 0)
        report = judge(Trace(frames=rows))
        self.assertEqual(summarise(report.hitches)["unexplained"], 1)

    def test_a_smooth_capture_has_no_hitches(self):
        self.assertEqual(judge(Trace(frames=frames(200, 10, 9.8, 4))).hitches, [])


class NotMeasured(unittest.TestCase):
    def test_absent_data_is_declared(self):
        report = judge(Trace(frames=frames(200, 10, 9.8, 4)))
        joined = " ".join(report.not_measured)
        for expected in ("graphics card telemetry", "memory configuration", "disk latency"):
            self.assertIn(expected, joined)

    def test_the_collector_s_own_failures_travel_in_the_trace(self):
        report = judge(Trace(frames=frames(200, 10, 9.8, 4),
                             missing={"frame attribution": "PresentMon was not found"}))
        self.assertTrue(any("PresentMon was not found" in line for line in report.not_measured))


class Parsers(unittest.TestCase):
    """Fed text shaped like what the real tools emit."""

    def test_presentmon_v2_csv(self):
        csv_text = (
            "Application,ProcessID,SwapChainAddress,PresentRuntime,SyncInterval,PresentFlags,"
            "MsBetweenPresents,MsInPresentAPI,MsCPUBusy,MsCPUWait,MsGPULatency,MsGPUTime,MsGPUBusy,MsGPUWait\n"
            "cs2.exe,1234,0x1,DXGI,0,0,7.10,0.30,2.10,0.05,1.20,6.90,6.80,0.10\n"
            "cs2.exe,1234,0x1,DXGI,0,0,7.30,0.31,2.20,0.05,1.20,7.10,7.00,0.10\n")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frames.csv"
            path.write_text(csv_text)
            parsed = windows._parse_presentmon(path)
        self.assertEqual(len(parsed), 2)
        self.assertAlmostEqual(parsed[0].gpu_busy, 6.80)
        self.assertAlmostEqual(parsed[1].time, (7.10 + 7.30) / 1000.0, places=4)
        self.assertEqual(classify(parsed[0]), "gpu")

    def test_presentmon_v1_column_names_still_parse(self):
        csv_text = ("Application,msBetweenPresents,msInPresentAPI,msGPUActive,SyncInterval\n"
                    "old.exe,10.0,0.2,9.8,0\n")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frames.csv"
            path.write_text(csv_text)
            parsed = windows._parse_presentmon(path)
        self.assertAlmostEqual(parsed[0].gpu_busy, 9.8)

    def test_nvidia_smi_csv_with_throttle_flags(self):
        text = ("99, 7900, 8192, 81, 315.5, 320.0, 2550, 2700, 4, 4, 16, 16, "
                "Not Active, Not Active, Active, Not Active, Not Active, Not Active, Not Active, Not Active\n")
        samples = windows._parse_gpu(text, "clocks_event_reasons", 0.5)
        self.assertEqual(len(samples), 1)
        sample = samples[0]
        self.assertEqual(sample.utilisation, 99)
        self.assertEqual(sample.pcie_width, 16)
        self.assertTrue(sample.throttle["sw_power_cap"])
        self.assertFalse(sample.throttle["hw_slowdown"])
        self.assertFalse(sample.throttle["gpu_idle"])

    def test_nvidia_smi_csv_without_throttle_support(self):
        text = "40, 1000, 8192, 45, 60.0, 320.0, 900, 2700, 1, 4, 16, 16\n"
        samples = windows._parse_gpu(text, None, 0.5)
        self.assertEqual(samples[0].throttle, {})
        self.assertEqual(samples[0].pcie_gen, 1)

    def test_typeperf_csv(self):
        text = (
            '"(PDH-CSV 4.0)","\\\\PC\\Processor Information(_Total)\\% Processor Time",'
            '"\\\\PC\\Processor Information(_Total)\\% Processor Performance",'
            '"\\\\PC\\Processor Information(0,0)\\% Processor Time",'
            '"\\\\PC\\Processor Information(0,1)\\% Processor Time",'
            '"\\\\PC\\Memory\\Available MBytes","\\\\PC\\Memory\\Pages/sec",'
            '"\\\\PC\\PhysicalDisk(_Total)\\Avg. Disk sec/Read",'
            '"\\\\PC\\PhysicalDisk(_Total)\\Avg. Disk sec/Write",'
            '"\\\\PC\\PhysicalDisk(_Total)\\Current Disk Queue Length"\n'
            '"09/21/2026 01:00:00.000","23.5","78.2","99.1","4.2","2048","12.0","0.004","0.002","0"\n'
            '"09/21/2026 01:00:01.000","24.1","77.9","98.7","5.0","2040","900.0","0.031","0.002","2"\n')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cpu.csv"
            path.write_text(text)
            cpu, disk, memory = windows._parse_typeperf(path)
        self.assertEqual(len(cpu), 2)
        self.assertEqual(cpu[0].total, 23.5)
        self.assertEqual(cpu[0].processor_performance, 78.2)
        self.assertEqual(cpu[0].cores, [99.1, 4.2])
        self.assertEqual(disk[1].read_latency, 0.031)
        self.assertEqual(memory[1].pages_per_sec, 900.0)

    def test_a_truncated_csv_does_not_raise(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "x.csv"
            path.write_text("Application,MsBetweenPresents\n")
            self.assertEqual(windows._parse_presentmon(path), [])
            self.assertEqual(windows._parse_typeperf(path), ([], [], []))


class TraceFile(unittest.TestCase):
    def test_a_trace_round_trips(self):
        original = Trace(captured_at="now", duration_s=30.0, target="cs2.exe",
                         hardware=Hardware(gpu_name="RTX", memory_modules=[{"configured_mhz": 6000}]),
                         frames=frames(3, 10, 9, 4), gpu=[GpuSample(0, utilisation=50)])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "t.json"
            original.write(path)
            again = Trace.read(path)
        self.assertEqual(again.target, "cs2.exe")
        self.assertEqual(len(again.frames), 3)
        self.assertEqual(again.hardware.gpu_name, "RTX")

    def test_a_trace_from_another_schema_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "t.json"
            path.write_text(json.dumps({"schema": 99}))
            with self.assertRaises(ValueError):
                Trace.read(path)


class Compare(unittest.TestCase):
    """The resolution-drop test: a second capture confirms or contradicts the first."""

    def test_cpu_bound_confirmed_when_fps_does_not_move(self):
        before = Trace(frames=frames(200, 10.0, 4.0, 9.9))   # cpu-bound, 100 fps
        after = Trace(frames=frames(200, 10.2, 4.0, 10.1))   # still cpu-bound, ~98 fps
        result = compare(before, after)
        self.assertEqual(result.agreement, "confirms")
        self.assertIn("barely moved", result.headline)
        self.assertIn("identical captures", result.note)

    def test_the_exact_tolerance_boundary_is_computed_from_unrounded_medians(self):
        """A rounded-fps comparison can flip right at the 8% boundary; the raw one must not."""
        # 100.04 fps -> 108.00 fps is a 7.96% rise (just inside tolerance), but both
        # round to 100.0 and 108.0 fps, which computes as exactly 8% if rounded first —
        # enough to flip "confirms" (cpu-bound, unchanged) into "contradicts".
        before = Trace(frames=frames(200, 1000.0 / 100.04, 3.0, 9.9))
        after = Trace(frames=frames(200, 1000.0 / 108.00, 3.0, 9.15))
        result = compare(before, after)
        self.assertEqual(result.agreement, "confirms")
        self.assertLess(result.fps_change_pct, 0.08)

    def test_cpu_bound_contradicted_when_fps_jumps(self):
        before = Trace(frames=frames(200, 10.0, 4.0, 9.9))   # cpu-bound, 100 fps
        after = Trace(frames=frames(200, 5.0, 4.9, 2.0))     # fps roughly doubled
        result = compare(before, after)
        self.assertEqual(result.agreement, "contradicts")
        self.assertIn("may not have been comparable", result.note)

    def test_gpu_bound_confirmed_when_fps_rises(self):
        before = Trace(frames=frames(200, 10.0, 9.9, 4.0))   # gpu-bound, 100 fps
        after = Trace(frames=frames(200, 5.0, 4.9, 2.0))     # lighter load, gpu-bound, ~200 fps
        result = compare(before, after)
        self.assertEqual(result.agreement, "confirms")
        self.assertIn("rose", result.headline)
        self.assertIn("lighter section", result.note)

    def test_gpu_bound_contradicted_when_fps_stays_flat(self):
        before = Trace(frames=frames(200, 10.0, 9.9, 4.0))   # gpu-bound, 100 fps
        after = Trace(frames=frames(200, 10.1, 10.0, 4.0))   # unchanged
        result = compare(before, after)
        self.assertEqual(result.agreement, "contradicts")
        self.assertIn("may not have been comparable", result.note)

    def test_a_frame_cap_is_inconclusive_rather_than_scored(self):
        before = Trace(frames=frames(200, 16.67, 6.0, 5.0, sync=1))  # 60 fps cap
        after = Trace(frames=frames(200, 16.67, 6.0, 5.0, sync=1))
        result = compare(before, after)
        self.assertEqual(result.agreement, "inconclusive")
        self.assertIn("frame cap", result.headline)

    def test_an_after_capture_that_lands_on_a_cap_is_also_inconclusive(self):
        """A clean cpu-bound 'before' does not excuse an unusable 'after' — both sides count."""
        before = Trace(frames=frames(200, 10.0, 4.0, 9.9))            # cpu-bound, 100 fps
        after = Trace(frames=frames(200, 16.67, 6.0, 5.0, sync=1))    # coincidentally a 60 fps cap
        result = compare(before, after)
        self.assertEqual(result.agreement, "inconclusive")
        self.assertIn("after capture was a frame cap", result.headline)

    def test_an_after_capture_that_stalls_is_also_inconclusive(self):
        before = Trace(frames=frames(200, 10.0, 4.0, 9.9))    # cpu-bound
        after = Trace(frames=frames(200, 100.0, 6.0, 5.0))    # neither busy: a stall, every frame
        result = compare(before, after)
        self.assertEqual(result.agreement, "inconclusive")
        self.assertIn("after capture was 'stall'", result.headline)

    def test_too_few_frames_is_inconclusive(self):
        result = compare(Trace(frames=frames(3, 10, 9, 4)), Trace(frames=frames(200, 10, 9, 4)))
        self.assertEqual(result.agreement, "inconclusive")


class RTSSSharedMemory(unittest.TestCase):
    """The byte-level half of the RTSS overlay writer: tested against a
    synthetic buffer shaped like the real shared memory, the same way
    PresentMon/nvidia-smi CSVs are tested against synthetic text."""

    def test_open_never_raises_even_where_mmap_has_no_tagname_support(self):
        """mmap.mmap's `tagname` kwarg doesn't exist at all on a non-Windows
        mmap — RTSS is Windows-only, so this platform mismatch should read as
        just another "not available here" (`False`), never an uncaught
        `TypeError`. Only asserts the "no crash" half: on a real Windows box
        with RTSS actually running this legitimately returns `True`, so the
        return value itself isn't checked here, just that it's a bool."""
        writer = rtss.RTSSWriter()
        self.assertIn(writer.open(), (True, False))
        writer.close()

    def test_parse_header_reads_signature_and_offsets(self):
        header = rtss.parse_header(rtss_buffer(version=0x00020007))
        self.assertEqual(header.version, 0x00020007)
        self.assertEqual(header.osd_arr_size, 8)
        self.assertEqual(header.osd_entry_size, 4608)

    def test_parse_header_rejects_a_bad_signature(self):
        self.assertIsNone(rtss.parse_header(b"not rtss, just garbage bytes padded out to size"))

    def test_parse_header_rejects_a_truncated_buffer(self):
        self.assertIsNone(rtss.parse_header(rtss_buffer(version=0x00020007)[:10]))

    def test_parse_header_rejects_a_zero_entry_size(self):
        """The exact geometry a corrupt header needs to turn find_slot's scan
        into a near-infinite loop over the same address (base never advances
        when entry_size is 0) — rejected before find_slot ever sees it."""
        self.assertIsNone(rtss.parse_header(rtss_raw_header(0x00020007, entry_size=0)))

    def test_parse_header_rejects_an_absurd_slot_count(self):
        # app_arr_offset given explicitly: the default would overflow a u32
        # when multiplied by this arr_size, which is beside the point here.
        header = rtss_raw_header(0x00020007, arr_size=0xFFFFFFFF, app_arr_offset=0)
        self.assertIsNone(rtss.parse_header(header))

    def test_parse_header_rejects_a_v1_header(self):
        self.assertIsNone(rtss.parse_header(rtss_raw_header(0x00010003)))

    def test_parse_header_rejects_an_entry_size_too_small_for_its_own_version(self):
        """A header that claims v2.7 (szOSDEx present) but an entry size that
        couldn't hold it is exactly what would make build_writes compute an
        offset past the end of the slot."""
        self.assertIsNone(rtss.parse_header(rtss_raw_header(0x00020007, entry_size=600)))

    def test_parse_header_accepts_a_legacy_entry_size_below_the_ex_minimum(self):
        header = rtss.parse_header(rtss_raw_header(0x00020006, entry_size=512))
        self.assertIsNotNone(header)

    def test_parse_header_rejects_an_app_array_offset_that_does_not_match_the_osd_array(self):
        """The concrete failure a fixed-slack bound on osd_arr_offset alone
        could not catch: a corrupt offset landing inside a neighbouring
        slot's boundary. app_arr_offset must equal osd_arr_offset +
        osd_arr_size * osd_entry_size in any genuine header — a corruption
        that shifts one is not going to shift the other to match by luck."""
        osd_arr_offset = struct.calcsize("<9I")
        wrong_app_offset = osd_arr_offset + 8 * 512 - 256  # shifted by one owner field's width
        header = rtss_raw_header(0x00020006, entry_size=512, arr_size=8, app_arr_offset=wrong_app_offset)
        self.assertIsNone(rtss.parse_header(header))

    def test_parse_header_rejects_a_zero_app_entry_size(self):
        self.assertIsNone(rtss.parse_header(rtss_raw_header(0x00020007, app_entry_size=0)))

    def test_find_slot_skips_slot_zero_and_prefers_the_first_free_one(self):
        buf = rtss_buffer(version=0x00020007, owners={0: "someone-else", 2: "another-app"})
        header = rtss.parse_header(buf)
        # slot 0 is owned but must never be returned; slot 1 is free and comes before slot 2.
        self.assertEqual(rtss.find_slot(buf, header, "findmybottleneck"), 1)

    def test_find_slot_skips_slot_zero_even_when_slot_zero_is_free(self):
        """Slot 0 must never be returned, not merely be less preferred —
        this is the case an occupied-slot-0 fixture can't tell apart from a
        find_slot that starts scanning at 0."""
        buf = rtss_buffer(version=0x00020007)  # every slot, including 0, is free
        header = rtss.parse_header(buf)
        self.assertEqual(rtss.find_slot(buf, header, "findmybottleneck"), 1)

    def test_find_slot_reuses_a_slot_we_already_own(self):
        buf = rtss_buffer(version=0x00020007, owners={1: "someone-else", 3: "findmybottleneck"})
        header = rtss.parse_header(buf)
        self.assertEqual(rtss.find_slot(buf, header, "findmybottleneck"), 3)

    def test_should_clear_is_true_only_for_our_own_slot(self):
        buf = rtss_buffer(version=0x00020007, owners={1: "findmybottleneck", 2: "someone-else"})
        header = rtss.parse_header(buf)
        self.assertTrue(rtss.should_clear(buf, header, 1, "findmybottleneck"))
        self.assertFalse(rtss.should_clear(buf, header, 2, "findmybottleneck"))

    def test_find_slot_is_none_when_every_slot_is_taken(self):
        buf = rtss_buffer(version=0x00020007, arr_size=2,
                          owners={0: "reserved", 1: "someone-else"})
        header = rtss.parse_header(buf)
        self.assertIsNone(rtss.find_slot(buf, header, "findmybottleneck"))

    def test_build_writes_uses_szosdex_from_v2_7_onward(self):
        header = rtss.parse_header(rtss_buffer(version=0x00020007))
        writes = rtss.build_writes(header, slot_index=1, owner="fmb", text="hello")
        offsets = [offset for offset, _ in writes]
        self.assertIn(header.osd_arr_offset + 1 * header.osd_entry_size + rtss.OSD_EX_TEXT_OFFSET, offsets)

    def test_build_writes_falls_back_to_szosd_below_v2_7(self):
        header = rtss.parse_header(rtss_buffer(version=0x00020006, entry_size=600))
        writes = rtss.build_writes(header, slot_index=1, owner="fmb", text="hello")
        offsets = [offset for offset, _ in writes]
        self.assertIn(header.osd_arr_offset + 1 * header.osd_entry_size + rtss.OSD_TEXT_OFFSET, offsets)
        self.assertNotIn(header.osd_arr_offset + 1 * header.osd_entry_size + rtss.OSD_EX_TEXT_OFFSET, offsets)

    def test_build_writes_truncates_long_text_without_overflowing_the_slot(self):
        header = rtss.parse_header(rtss_buffer(version=0x00020007))
        writes = rtss.build_writes(header, slot_index=1, owner="fmb", text="x" * 10_000)
        base = header.osd_arr_offset + 1 * header.osd_entry_size
        for offset, data in writes:
            # every write must land, start to end, inside this one slot.
            self.assertGreaterEqual(offset, base)
            self.assertLessEqual(offset + len(data), base + header.osd_entry_size)
        text_offset, text_bytes = writes[-1]
        self.assertEqual(len(text_bytes), rtss.OSD_EX_TEXT_MAX + 1)  # + the NUL

    def test_build_writes_truncates_a_long_owner_too(self):
        header = rtss.parse_header(rtss_buffer(version=0x00020007))
        writes = rtss.build_writes(header, slot_index=1, owner="o" * 10_000, text="hi")
        owner_offset, owner_bytes = writes[0]
        self.assertEqual(len(owner_bytes), rtss.OSD_OWNER_MAX + 1)  # + the NUL

    def test_format_status_before_enough_frames(self):
        self.assertIn("warming up", rtss.format_status(None, {}, frame_count=3))

    def test_format_status_for_gpu_bound(self):
        verdict = Verdict(limiter="gpu", headline="", share=0.92)
        text = rtss.format_status(verdict, {"median_fps": 144, "low1_fps": 121}, frame_count=90)
        self.assertIn("GPU-bound", text)
        self.assertIn("144fps", text)

    def test_format_status_for_a_frame_cap(self):
        verdict = Verdict(limiter="frame-cap", headline="", share=1.0)
        text = rtss.format_status(verdict, {"median_fps": 60}, frame_count=90)
        self.assertIn("capped", text)
        self.assertNotIn("GPU-bound", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
