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
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from findmybottleneck.collect import windows
from findmybottleneck.engine import SOURCES, judge
from findmybottleneck.engine import config as cfg
from findmybottleneck.engine.frames import analyse, classify, looks_capped
from findmybottleneck.engine.hitch import summarise
from findmybottleneck.trace import (DiskSample, Frame, GpuSample, Hardware, MemorySample,
                          Trace)


def frames(count, frame_ms, gpu_ms, cpu_ms, sync=0):
    return [Frame(i * frame_ms / 1000.0, frame_ms, cpu_ms, 0.1, gpu_ms, 0.1, 1.0, 0.2, sync)
            for i in range(count)]


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
