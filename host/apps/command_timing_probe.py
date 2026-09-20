#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PC から安全な停止状態の mode 3 UDP 指令を周期送信し、送信時刻を CSV に記録する。"""

import argparse
import csv
import errno as errno_module
from pathlib import Path
import signal
import socket
import time

from cm4.bridge.mode3_timing_probe import command
from cm4.bridge.packet_codec import CONTROL_MODE, build_packet


CSV_HEADER = (
    "sequence",
    "deadline_ns",
    "send_before_ns",
    "send_after_ns",
    "bytes_sent",
    "errno",
)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="送信先 IPv4 アドレスまたはホスト名")
    parser.add_argument("--port", type=int, default=12345, help="送信先 UDP ポート")
    parser.add_argument("--robot-id", type=int, default=8, help="指令対象 ID (0..10)")
    parser.add_argument("--mode", type=int, choices=(3, 4), default=3,
                        help="CONTROL_MODE (既定 3)")
    parser.add_argument("--rate-hz", type=float, default=66.6666667, help="送信周期 [Hz]")
    parser.add_argument("--seconds", type=float, default=30.0, help="測定時間 [秒]")
    parser.add_argument("--output", required=True, help="CSV を作成する新規ディレクトリ")
    parser.add_argument(
        "--socket-per-packet",
        action="store_true",
        help="Qt 側の比較用に、パケットごとに UDP socket を作り直す",
    )
    parser.add_argument(
        "--broadcast",
        action="store_true",
        help="SO_BROADCAST を明示的に有効にする",
    )
    return parser


def validate_args(parser, args):
    if not 1 <= args.port <= 65535:
        parser.error("--port は 1..65535 で指定してください")
    if not 0 <= args.robot_id <= 10:
        parser.error("--robot-id は 0..10 で指定してください")
    if not 0 < args.rate_hz <= 1000:
        parser.error("--rate-hz は 0 より大きく 1000 以下で指定してください")
    if not 0 < args.seconds <= 3600:
        parser.error("--seconds は 0 より大きく 3600 以下で指定してください")


def _new_socket(socket_factory, broadcast):
    tx = socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
    if broadcast:
        tx.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    return tx


def _wait_until(deadline_ns, clock_ns, sleeper):
    while True:
        remaining_ns = deadline_ns - clock_ns()
        if remaining_ns <= 0:
            return
        sleeper(remaining_ns / 1_000_000_000)


def _write_csv(output, rows):
    with (output / "send.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(CSV_HEADER)
        writer.writerows(rows)


def run_probe(
    args,
    *,
    socket_factory=socket.socket,
    clock_ns=time.perf_counter_ns,
    sleeper=time.sleep,
):
    """測定を実行し、Ctrl+C や送信エラー時も収集済み行を CSV に保存する。"""
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    destination = (args.host, args.port)
    period_ns = round(1_000_000_000 / args.rate_hz)
    duration_ns = int(args.seconds * 1_000_000_000)
    rows = []
    skipped = 0
    interrupted = False
    fixed_socket = None

    try:
        if not args.socket_per_packet:
            fixed_socket = _new_socket(socket_factory, args.broadcast)

        start_ns = clock_ns()
        deadline_ns = start_ns
        end_ns = start_ns + duration_ns
        sequence = 1

        while deadline_ns < end_ns:
            safe_command = command(sequence)
            safe_command[CONTROL_MODE] = args.mode
            packet = build_packet(args.robot_id, safe_command)
            _wait_until(deadline_ns, clock_ns, sleeper)
            tx = fixed_socket
            if args.socket_per_packet:
                tx = _new_socket(socket_factory, args.broadcast)

            before_ns = clock_ns()
            bytes_sent = 0
            error_number = ""
            try:
                bytes_sent = tx.sendto(packet, destination)
            except OSError as exc:
                error_number = exc.errno if exc.errno is not None else errno_module.EIO
            finally:
                after_ns = clock_ns()
                if args.socket_per_packet:
                    tx.close()

            rows.append(
                (sequence, deadline_ns, before_ns, after_ns, bytes_sent, error_number)
            )
            sequence += 1
            deadline_ns += period_ns

            now_ns = clock_ns()
            if deadline_ns <= now_ns:
                missed = (now_ns - deadline_ns) // period_ns + 1
                skipped += missed
                deadline_ns += missed * period_ns
    except KeyboardInterrupt:
        interrupted = True
    finally:
        if fixed_socket is not None:
            fixed_socket.close()
        _write_csv(output, rows)

    return {
        "attempted": len(rows),
        "sent": sum(row[4] > 0 and row[5] == "" for row in rows),
        "errors": sum(row[5] != "" for row in rows),
        "skipped_deadlines": skipped,
        "interrupted": interrupted,
        "output": output,
    }


def _print_summary(summary, host, port):
    state = "interrupted" if summary["interrupted"] else "completed"
    print(
        f"{state}: target={host}:{port} attempted={summary['attempted']} "
        f"sent={summary['sent']} errors={summary['errors']} "
        f"skipped_deadlines={summary['skipped_deadlines']} "
        f"csv={summary['output'] / 'send.csv'}"
    )


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(parser, args)

    def stop_signal(_signum, _frame):
        raise KeyboardInterrupt

    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop_signal)

    try:
        summary = run_probe(args)
    except FileExistsError:
        parser.error("--output には存在しない新規ディレクトリを指定してください")
    _print_summary(summary, args.host, args.port)
    return 130 if summary["interrupted"] else (1 if summary["errors"] else 0)


if __name__ == "__main__":
    raise SystemExit(main())
