# -*- coding: utf-8 -*-
"""analyze_command_timing の時計別統計と欠落分類を合成ログで検査する。"""

import csv
from pathlib import Path
import tempfile
import unittest

from host.apps.analyze_command_timing import analyze_run


def write_csv(path, header, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        writer.writerows(rows)


class AnalyzeCommandTimingTest(unittest.TestCase):
    def test_clock_domains_duplicates_and_overwrites(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp)
            write_csv(
                run / "send.csv",
                ("sequence", "deadline_ns", "send_before_ns", "send_after_ns", "bytes_sent", "errno"),
                ((1, 0, 100, 101, 715, ""), (2, 200, 300, 301, 715, ""),
                 (3, 400, 500, 501, 715, ""), (4, 600, 700, 701, 715, "")),
            )
            write_csv(
                run / "packets.csv",
                ("kernel_ns", "app_mono_ns", "sequence"),
                ((1000, 10, 1), (1200, 20, 2), (1400, 30, 3), (1600, 40, 4)),
            )
            bridge_header = (
                "event", "sequence", "counter", "valid_self", "adopted", "realtime_ns",
                "kernel_ns", "monotonic_ns", "write_end_ns", "written",
                "socket_drop_total", "trace_record_overflow",
            )
            write_csv(
                run / "bridge_trace.csv",
                bridge_header,
                (
                    ("rx", 1, 1, 1, 1, 1010, 1000, 100, 0, 0, 0, 0),
                    ("tx", 1, 1, 1, 1, 1011, 1000, 110, 115, 72, 0, 0),
                    ("rx", 2, 2, 1, 0, 1210, 1200, 300, 0, 0, 0, 0),
                    ("rx", 3, 3, 1, 1, 1410, 1400, 310, 0, 0, 0, 0),
                    ("tx", 3, 3, 1, 1, 1411, 1400, 320, 325, 72, 0, 0),
                    ("rx", 4, 4, 1, 1, 1610, 1600, 500, 0, 0, 0, 0),
                    ("tx", 4, 4, 1, 1, 1611, 1600, 510, 515, 72, 0, 0),
                ),
            )
            (run / "stm32.log").write_text(
                "TPRB_BEGIN,version=1,clock_hz=1000,duration_cycles=10000\n"
                "TPRB_TYPE,event_sequence,cycle,marker_sequence,counter\n"
                "TPRB_RX,0,100,1,1\n"
                "TPRB_RX,1,150,3,3\n"
                "TPRB_RX,2,300,4,4\n"
                "TPRB_APPLY,0,200,3,3\n"
                "TPRB_END,rx=3,apply=1,filtered=0,rx_overflow=0,apply_overflow=0,apply_race=0\n",
                encoding="utf-8",
            )

            result = analyze_run(run)

        self.assertEqual(result["interval_ms"]["sender"]["p50"], 0.0002)
        self.assertEqual(result["latency_ms"]["kernel_to_bridge_receive"]["p99"], 0.00001)
        self.assertEqual(result["latency_ms"]["bridge_receive_to_write"]["p99"], 0.00001)
        self.assertEqual(result["latency_ms"]["bridge_write_duration"]["p99"], 0.000005)
        self.assertEqual(result["latency_ms"]["stm32_receive_to_apply"]["p99"], 50.0)
        gaps = result["stage_gaps"]
        self.assertEqual(gaps["bridge_fifo_intentional_overwrite"]["sequences"], [2])
        self.assertEqual(gaps["stm32_adoption"]["intentional_overwrite"]["sequences"], [1])
        self.assertEqual(gaps["stm32_adoption"]["after_last_apply_window_uncertain"]["sequences"], [4])
        self.assertEqual(gaps["bridge_written_outside_stm32_window_uncertain"]["count"], 0)
        self.assertEqual(gaps["stm32_rx_dump_row_missing_but_apply_seen"]["count"], 0)
        self.assertTrue(result["stm32_capture"]["integrity"]["rx"]["valid"])

    def test_duplicate_sequence_is_reported_and_excluded_from_pairing(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp)
            write_csv(
                run / "send.csv",
                ("sequence", "send_before_ns"),
                ((1, 1), (1, 2)),
            )
            write_csv(run / "packets.csv", ("sequence", "kernel_ns"), ((1, 1),))
            write_csv(
                run / "bridge_trace.csv",
                ("event", "sequence", "valid_self", "adopted", "realtime_ns", "kernel_ns", "monotonic_ns", "write_end_ns"),
                (("rx", 1, 1, 1, 2, 1, 2, 0), ("tx", 1, 1, 1, 2, 1, 3, 4)),
            )
            (run / "stm32.log").write_text(
                "TPRB_BEGIN,version=1,clock_hz=1000,duration_cycles=10000\n"
                "TPRB_RX,0,1,1,1\nTPRB_APPLY,0,2,1,1\n"
                "TPRB_END,rx=1,apply=1,filtered=0,rx_overflow=0,apply_overflow=0,apply_race=0\n",
                encoding="utf-8",
            )
            result = analyze_run(run)

        self.assertEqual(result["duplicates"]["send"], [1])
        self.assertTrue(result["warnings"])

    def test_corrupt_stm_dump_invalidates_stm_timing(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp)
            write_csv(run / "send.csv", ("sequence", "send_before_ns"), ((1, 1), (2, 2)))
            write_csv(run / "packets.csv", ("sequence", "kernel_ns", "mode"), ((1, 1, 3), (2, 2, 3)))
            write_csv(
                run / "bridge_trace.csv",
                ("event", "sequence", "valid_self", "adopted", "realtime_ns", "kernel_ns", "monotonic_ns", "write_end_ns"),
                (("rx", 1, 1, 1, 2, 1, 2, 0), ("tx", 1, 1, 1, 2, 1, 3, 4)),
            )
            (run / "stm32.log").write_text(
                "TPRB_BEGIN,version=1,clock_hz=1000,duration_cycles=10000\n"
                "TPRB_RX,0,100,1,1\nTPRB_RX,2,90,2,2\n"
                "TPRB_APPLY,0,200,1,1\n"
                "TPRB_END,rx=3,apply=1,filtered=0,rx_overflow=0,apply_overflow=0,apply_race=0\n",
                encoding="utf-8",
            )
            result = analyze_run(run)

        integrity = result["stm32_capture"]["integrity"]["rx"]
        self.assertFalse(integrity["valid"])
        self.assertEqual(integrity["missing_event_sequences"], [1])
        self.assertFalse(integrity["cycle_strictly_increasing"])
        self.assertFalse(result["interval_ms"]["stm32_receive"]["valid"])
        self.assertIsNone(result["latency_ms"]["stm32_receive_to_apply"]["p99"])

    def test_alternate_stm_log_is_only_used_when_explicitly_selected(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp)
            write_csv(run / "send.csv", ("sequence", "send_before_ns"), ((1, 1),))
            write_csv(run / "packets.csv", ("sequence", "kernel_ns", "mode"), ((1, 1, 3),))
            write_csv(
                run / "bridge_trace.csv",
                ("event", "sequence", "valid_self", "adopted", "realtime_ns", "kernel_ns", "monotonic_ns", "write_end_ns"),
                (("rx", 1, 1, 1, 2, 1, 2, 0), ("tx", 1, 1, 1, 2, 1, 3, 4)),
            )
            corrupt = (
                "TPRB_BEGIN,version=1,clock_hz=1000,duration_cycles=10000\n"
                "TPRB_RX,1,100,1,1\nTPRB_APPLY,0,200,1,1\n"
                "TPRB_END,rx=1,apply=1,filtered=0,rx_overflow=0,apply_overflow=0,apply_race=0\n"
            )
            recovered = (
                "TPRB_BEGIN,version=1,clock_hz=1000,duration_cycles=10000\n"
                "TPRB_RX,0,100,1,1\nTPRB_APPLY,0,200,1,1\n"
                "TPRB_END,rx=1,apply=1,filtered=0,rx_overflow=0,apply_overflow=0,apply_race=0\n"
            )
            (run / "stm32.log").write_text(corrupt, encoding="utf-8")
            (run / "stm32_sram.log").write_text(recovered, encoding="utf-8")

            default_result = analyze_run(run)
            sram_result = analyze_run(run, "stm32_sram.log")

        self.assertFalse(default_result["stm32_capture"]["integrity"]["rx"]["valid"])
        self.assertTrue(sram_result["stm32_capture"]["integrity"]["rx"]["valid"])
        self.assertEqual(sram_result["input_sources"]["stm32_log"]["name"], "stm32_sram.log")
        self.assertEqual(
            sram_result["input_sources"]["stm32_log"]["selection"],
            "explicit_stm_log_name",
        )


if __name__ == "__main__":
    unittest.main()
