#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""コマンド時刻診断の複数 run を時計ドメインごとに集計し、JSON と短表を出力する。"""

import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import re
import sys


STM_BEGIN_RE = re.compile(r"TPRB_BEGIN,.*?clock_hz=(\d+),duration_cycles=(\d+)")
STM_EVENT_RE = re.compile(r"TPRB_(RX|APPLY),(\d+),(\d+),(\d+),(\d+)")
STM_END_RE = re.compile(
    r"TPRB_END,rx=(\d+),apply=(\d+),filtered=(\d+),rx_overflow=(\d+),"
    r"apply_overflow=(\d+),apply_race=(\d+)"
)


def _read_csv(path):
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def _int(row, key, default=0):
    value = row.get(key, "")
    return int(value) if value not in (None, "") else default


def _stats(values, scale=1.0):
    ordered = sorted(value / scale for value in values)
    if not ordered:
        return {"valid": True, "count": 0, "min": None, "p50": None, "p99": None, "max": None}

    def percentile(fraction):
        return ordered[round((len(ordered) - 1) * fraction)]

    return {
        "valid": True,
        "count": len(ordered),
        "min": ordered[0],
        "p50": percentile(0.50),
        "p99": percentile(0.99),
        "max": ordered[-1],
    }


def _invalid_stats(stats, reasons):
    return {
        "valid": False,
        "count": stats["count"],
        "min": None,
        "p50": None,
        "p99": None,
        "max": None,
        "invalid_reasons": reasons,
    }


def _intervals(rows, timestamp, scale):
    stamps = sorted(timestamp(row) for row in rows)
    return _stats([right - left for left, right in zip(stamps, stamps[1:])], scale)


def _duplicates(rows):
    counts = Counter(_int(row, "sequence") for row in rows)
    return sorted(sequence for sequence, count in counts.items() if count > 1)


def _unique_by_sequence(rows):
    grouped = {}
    for row in rows:
        grouped.setdefault(_int(row, "sequence"), []).append(row)
    return {sequence: values[0] for sequence, values in grouped.items() if len(values) == 1}


def _sequence_report(sequences):
    values = sorted(sequences)
    return {"count": len(values), "sequences": values}


def _parse_stm32(path):
    captures = []
    current = None
    with path.open(encoding="utf-8", errors="replace") as stream:
        for raw_line in stream:
            line = raw_line.strip()
            begin = STM_BEGIN_RE.search(line)
            if begin:
                current = {
                    "clock_hz": int(begin.group(1)),
                    "duration_cycles": int(begin.group(2)),
                    "rx": [],
                    "apply": [],
                    "footer": None,
                }
                continue
            if current is None:
                continue
            event = STM_EVENT_RE.search(line)
            if event:
                current[event.group(1).lower()].append(
                    {
                        "event_sequence": int(event.group(2)),
                        "cycle": int(event.group(3)),
                        "sequence": int(event.group(4)),
                        "counter": int(event.group(5)),
                    }
                )
                continue
            end = STM_END_RE.search(line)
            if end:
                current["footer"] = {
                    key: int(value)
                    for key, value in zip(
                        ("rx", "apply", "filtered", "rx_overflow", "apply_overflow", "apply_race"),
                        end.groups(),
                    )
                }
                captures.append(current)
                current = None
    if not captures:
        raise ValueError(f"完了した TPRB capture がありません: {path}")
    return captures[-1]


def _capture_integrity(rows, expected_count):
    event_sequences = [row["event_sequence"] for row in rows]
    event_counts = Counter(event_sequences)
    observed = set(event_sequences)
    expected = set(range(expected_count))
    cycles = [row["cycle"] for row in rows]
    duplicate_events = sorted(value for value, count in event_counts.items() if count > 1)
    missing_events = sorted(expected - observed)
    unexpected_events = sorted(observed - expected)
    event_order_strict = all(right > left for left, right in zip(event_sequences, event_sequences[1:]))
    cycle_monotonic_strict = all(right > left for left, right in zip(cycles, cycles[1:]))
    count_matches_footer = len(rows) == expected_count
    reasons = []
    if not count_matches_footer:
        reasons.append("footer_count_mismatch")
    if duplicate_events:
        reasons.append("duplicate_event_sequence")
    if missing_events:
        reasons.append("missing_event_sequence")
    if unexpected_events:
        reasons.append("unexpected_event_sequence")
    if not event_order_strict:
        reasons.append("event_sequence_not_strictly_increasing")
    if not cycle_monotonic_strict:
        reasons.append("cycle_not_strictly_increasing")
    return {
        "valid": not reasons,
        "parsed_count": len(rows),
        "footer_count": expected_count,
        "count_matches_footer": count_matches_footer,
        "missing_event_sequences": missing_events,
        "duplicate_event_sequences": duplicate_events,
        "unexpected_event_sequences": unexpected_events,
        "event_sequence_strictly_increasing": event_order_strict,
        "cycle_strictly_increasing": cycle_monotonic_strict,
        "invalid_reasons": reasons,
    }


def _stm_adoption(rx_rows, apply_rows):
    applied_sequences = {_int(row, "sequence") for row in apply_rows}
    ordered_apply = sorted(apply_rows, key=lambda row: _int(row, "cycle"))
    intentional = set()
    unexplained = set()
    outside_tail = set()

    for rx in rx_rows:
        sequence = _int(rx, "sequence")
        if sequence in applied_sequences:
            continue
        later_apply = next(
            (row for row in ordered_apply if _int(row, "cycle") >= _int(rx, "cycle")),
            None,
        )
        if later_apply is None:
            outside_tail.add(sequence)
            continue
        adopted_rx = next(
            (
                row
                for row in rx_rows
                if _int(row, "sequence") == _int(later_apply, "sequence")
            ),
            None,
        )
        if (
            adopted_rx is not None
            and _int(rx, "cycle") < _int(adopted_rx, "cycle") <= _int(later_apply, "cycle")
        ):
            intentional.add(sequence)
        else:
            unexplained.add(sequence)

    return {
        "intentional_overwrite": _sequence_report(intentional),
        "unexplained_adoption_miss": _sequence_report(unexplained),
        "after_last_apply_window_uncertain": _sequence_report(outside_tail),
    }


def analyze_run(run_dir, stm_log_name="stm32.log"):
    run_dir = Path(run_dir).resolve()
    if Path(stm_log_name).name != stm_log_name:
        raise ValueError("--stm-log-name はrunディレクトリ直下のファイル名で指定してください")
    stm_log_path = run_dir / stm_log_name
    send_path = run_dir / "send.csv"
    if not send_path.exists():
        send_path = run_dir / "sender" / "send.csv"
    paths = {
        "send": send_path,
        "packets": run_dir / "packets.csv",
        "bridge": run_dir / "bridge_trace.csv",
        "stm32": stm_log_path,
    }
    missing_files = [str(path) for path in paths.values() if not path.is_file()]
    if missing_files:
        raise FileNotFoundError("必要な入力がありません: " + ", ".join(missing_files))

    send = _read_csv(paths["send"])
    packets = _read_csv(paths["packets"])
    modes = {_int(row, "mode") for row in packets if row.get("mode") not in (None, "")}
    if modes and modes != {3}:
        raise ValueError(
            f"この版は mode 3 専用です（検出した mode: {sorted(modes)}）: {paths['packets']}"
        )
    bridge = _read_csv(paths["bridge"])
    bridge_rx = [row for row in bridge if row.get("event") == "rx"]
    bridge_tx = [row for row in bridge if row.get("event") == "tx"]
    bridge_adopted = [
        row for row in bridge_rx if _int(row, "valid_self") and _int(row, "adopted")
    ]
    bridge_overwritten = [
        row for row in bridge_rx if _int(row, "valid_self") and not _int(row, "adopted")
    ]
    stm = _parse_stm32(paths["stm32"])
    stm_rx = stm["rx"]
    stm_apply = stm["apply"]
    clock_hz = stm["clock_hz"]

    stage_rows = {
        "send": send,
        "packet_capture": packets,
        "bridge_receive": bridge_rx,
        "bridge_write": bridge_tx,
        "stm32_receive": stm_rx,
        "stm32_apply": stm_apply,
    }
    duplicates = {name: _duplicates(rows) for name, rows in stage_rows.items()}
    unique = {name: _unique_by_sequence(rows) for name, rows in stage_rows.items()}

    kernel_to_recv = []
    for row in unique["bridge_receive"].values():
        # bridge trace 内の realtime/kernel は同じホストの CLOCK_REALTIME。
        if _int(row, "kernel_ns"):
            kernel_to_recv.append(_int(row, "realtime_ns") - _int(row, "kernel_ns"))

    recv_to_write = []
    write_duration = []
    for sequence in unique["bridge_receive"].keys() & unique["bridge_write"].keys():
        rx = unique["bridge_receive"][sequence]
        tx = unique["bridge_write"][sequence]
        recv_to_write.append(_int(tx, "monotonic_ns") - _int(rx, "monotonic_ns"))
        write_duration.append(_int(tx, "write_end_ns") - _int(tx, "monotonic_ns"))

    stm_rx_to_apply = []
    for sequence in unique["stm32_receive"].keys() & unique["stm32_apply"].keys():
        stm_rx_to_apply.append(
            _int(unique["stm32_apply"][sequence], "cycle")
            - _int(unique["stm32_receive"][sequence], "cycle")
        )

    sequence_sets = {
        name: set(rows.keys()) for name, rows in unique.items()
    }
    stm_observed = sequence_sets["stm32_receive"] | sequence_sets["stm32_apply"]
    stm_window = None
    if stm_observed:
        stm_window = (
            min(stm_observed),
            max(stm_observed),
        )
    written_not_stm = sequence_sets["bridge_write"] - sequence_sets["stm32_receive"]
    rx_dump_missing_but_apply_seen = written_not_stm & sequence_sets["stm32_apply"]
    written_unobserved = written_not_stm - rx_dump_missing_but_apply_seen
    if stm_window:
        outside_stm_window = {
            sequence
            for sequence in written_unobserved
            if sequence < stm_window[0] or sequence > stm_window[1]
        }
    else:
        outside_stm_window = written_unobserved
    inside_stm_missing = written_unobserved - outside_stm_window

    footer = stm["footer"] or {}
    rx_integrity = _capture_integrity(stm_rx, footer.get("rx", len(stm_rx)))
    apply_integrity = _capture_integrity(stm_apply, footer.get("apply", len(stm_apply)))
    stm_rx_intervals = _intervals(stm_rx, lambda row: _int(row, "cycle"), clock_hz / 1000)
    stm_apply_intervals = _intervals(stm_apply, lambda row: _int(row, "cycle"), clock_hz / 1000)
    stm_latency = _stats(stm_rx_to_apply, clock_hz / 1000)
    if not rx_integrity["valid"]:
        stm_rx_intervals = _invalid_stats(stm_rx_intervals, rx_integrity["invalid_reasons"])
    if not apply_integrity["valid"]:
        stm_apply_intervals = _invalid_stats(stm_apply_intervals, apply_integrity["invalid_reasons"])
    if not rx_integrity["valid"] or not apply_integrity["valid"]:
        reasons = sorted(set(rx_integrity["invalid_reasons"] + apply_integrity["invalid_reasons"]))
        stm_latency = _invalid_stats(stm_latency, reasons)

    warnings = []
    if any(duplicates.values()):
        warnings.append("重複連番は対応時間差の計算から除外しました")
    if footer.get("rx_overflow") or footer.get("apply_overflow"):
        warnings.append("STM32 capture buffer overflow があるため欠落分類は不完全です")
    if footer.get("apply_race"):
        warnings.append("STM32 apply_race が記録されています")
    if not rx_integrity["valid"] or not apply_integrity["valid"]:
        warnings.append(
            "STM32 dump整合性エラーのため、該当するSTM32時間統計を無効化しました "
            f"(RX: {','.join(rx_integrity['invalid_reasons']) or 'OK'}; "
            f"APPLY: {','.join(apply_integrity['invalid_reasons']) or 'OK'})"
        )

    return {
        "run_dir": str(run_dir),
        "input_sources": {
            "stm32_log": {
                "name": stm_log_name,
                "path": str(stm_log_path.resolve()),
                "provenance_name": stm_log_name,
                "selection": "default" if stm_log_name == "stm32.log" else "explicit_stm_log_name",
            }
        },
        "stm32_capture": {
            "clock_hz": clock_hz,
            "duration_cycles": stm["duration_cycles"],
            "footer": footer,
            "observed_sequence_window": list(stm_window) if stm_window else None,
            "integrity": {"rx": rx_integrity, "apply": apply_integrity},
        },
        "counts": {name: len(rows) for name, rows in stage_rows.items()},
        "duplicates": duplicates,
        "interval_ms": {
            "sender": _intervals(send, lambda row: _int(row, "send_before_ns"), 1_000_000),
            "packet_kernel": _intervals(packets, lambda row: _int(row, "kernel_ns"), 1_000_000),
            "bridge_receive": _intervals(bridge_rx, lambda row: _int(row, "monotonic_ns"), 1_000_000),
            "bridge_write_end": _intervals(bridge_tx, lambda row: _int(row, "write_end_ns"), 1_000_000),
            "stm32_receive": stm_rx_intervals,
            "stm32_apply": stm_apply_intervals,
        },
        "latency_ms": {
            "kernel_to_bridge_receive": _stats(kernel_to_recv, 1_000_000),
            "bridge_receive_to_write": _stats(recv_to_write, 1_000_000),
            "bridge_write_duration": _stats(write_duration, 1_000_000),
            "stm32_receive_to_apply": stm_latency,
        },
        "stage_gaps": {
            "send_not_seen_by_packet_capture": _sequence_report(
                sequence_sets["send"] - sequence_sets["packet_capture"]
            ),
            "packet_capture_not_seen_by_bridge": _sequence_report(
                sequence_sets["packet_capture"] - sequence_sets["bridge_receive"]
            ),
            "bridge_fifo_intentional_overwrite": _sequence_report(
                _int(row, "sequence") for row in bridge_overwritten
            ),
            "bridge_adopted_not_written": _sequence_report(
                {_int(row, "sequence") for row in bridge_adopted}
                - sequence_sets["bridge_write"]
            ),
            "stm32_rx_dump_row_missing_but_apply_seen": _sequence_report(
                rx_dump_missing_but_apply_seen
            ),
            "bridge_written_not_stm32_inside_window": _sequence_report(inside_stm_missing),
            "bridge_written_outside_stm32_window_uncertain": _sequence_report(outside_stm_window),
            "stm32_adoption": _stm_adoption(stm_rx, stm_apply),
        },
        "warnings": warnings,
    }


def _metric_p99(result, name):
    metric = result["latency_ms"][name]
    if not metric["valid"]:
        return "INVALID"
    value = metric["p99"]
    return "-" if value is None else f"{value:.3f}"


def _print_table(results, stream):
    header = (
        "run", "send", "pkt", "brx", "btx", "srx", "apply", "fifo_ovr",
        "stm_ovr", "rxlog?", "stm_miss", "tail?", "k-rx p99", "rx-w p99", "write p99", "stm p99",
    )
    print(" ".join(f"{item:>10}" for item in header), file=stream)
    for result in results:
        gaps = result["stage_gaps"]
        adoption = gaps["stm32_adoption"]
        values = (
            Path(result["run_dir"]).name,
            result["counts"]["send"],
            result["counts"]["packet_capture"],
            result["counts"]["bridge_receive"],
            result["counts"]["bridge_write"],
            result["counts"]["stm32_receive"],
            result["counts"]["stm32_apply"],
            gaps["bridge_fifo_intentional_overwrite"]["count"],
            adoption["intentional_overwrite"]["count"],
            gaps["stm32_rx_dump_row_missing_but_apply_seen"]["count"],
            adoption["unexplained_adoption_miss"]["count"],
            gaps["bridge_written_outside_stm32_window_uncertain"]["count"],
            _metric_p99(result, "kernel_to_bridge_receive"),
            _metric_p99(result, "bridge_receive_to_write"),
            _metric_p99(result, "bridge_write_duration"),
            _metric_p99(result, "stm32_receive_to_apply"),
        )
        print(" ".join(f"{str(item):>10}" for item in values), file=stream)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dirs", nargs="+", help="send/packet/bridge/STM32ログを含むrunディレクトリ")
    parser.add_argument(
        "--stm-log-name",
        default="stm32.log",
        metavar="NAME",
        help="各run内で読むSTM32ログ名。既定は stm32.log。SRAM復元ログは明示指定する",
    )
    parser.add_argument(
        "--json",
        default="-",
        metavar="PATH",
        help="JSON出力先。既定の '-' は標準出力（短表は標準エラー）",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        results = [analyze_run(run_dir, args.stm_log_name) for run_dir in args.run_dirs]
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    document = {"schema_version": 1, "runs": results}
    if args.json == "-":
        _print_table(results, sys.stderr)
        json.dump(document, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        output = Path(args.json)
        with output.open("w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        _print_table(results, sys.stdout)
        print(f"JSON: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
