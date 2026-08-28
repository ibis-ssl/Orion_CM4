#!/usr/bin/env python3
"""MainのCM4 UART受信へ最大長OFW2フレームを連続送信し、初回応答率と遅延を測定する。"""

from __future__ import annotations

import argparse
import statistics
import time

from sub_can_updater_v2 import Gateway, MAX_PAYLOAD


TEST_MESSAGE_TYPE = 0x40
DIAG_RESET_MESSAGE_TYPE = 0x41


def rolling_hash(current: int, data: bytes) -> int:
    for value in data:
        current = ((current * 33) ^ value) & 0xFFFFFFFF
    return current


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="/dev/serial0")
    parser.add_argument("--count", type=int, default=3000)
    parser.add_argument("--timeout", type=float, default=0.1)
    parser.add_argument("--gap-ms", type=float, default=0.0)
    parser.add_argument("--guard-bytes", type=int, default=0)
    parser.add_argument("--payload-length", type=int, default=MAX_PAYLOAD)
    parser.add_argument("--write-chunk", type=int, default=0)
    parser.add_argument("--chunk-gap-ms", type=float, default=0.0)
    args = parser.parse_args()
    if args.count <= 0 or args.timeout <= 0:
        parser.error("count and timeout must be positive")

    gateway = Gateway(args.port)
    if not 0 <= args.payload_length <= MAX_PAYLOAD:
        parser.error(f"payload-length must be 0..{MAX_PAYLOAD}")
    payload = bytes((index & 0xFF for index in range(args.payload_length)))
    latencies_ms: list[float] = []
    failures = 0
    try:
        gateway.enter_gateway(0x5A)
        gateway.request(DIAG_RESET_MESSAGE_TYPE, timeout=0.2)
        expected_raw_hash = 5381
        expected_raw_count = 0
        started = time.monotonic()
        for index in range(args.count):
            sequence = gateway.sequence
            gateway.sequence = (gateway.sequence + 1) & 0xFFFF
            frame = gateway._frame(TEST_MESSAGE_TYPE, sequence, payload)
            sent = time.monotonic()
            outgoing = bytes(args.guard_bytes) + frame
            expected_raw_hash = rolling_hash(expected_raw_hash, outgoing)
            expected_raw_count += len(outgoing)
            write_chunk = args.write_chunk if args.write_chunk > 0 else len(outgoing)
            written = 0
            for offset in range(0, len(outgoing), write_chunk):
                part = outgoing[offset : offset + write_chunk]
                part_written = gateway.serial.write(part)
                if part_written != len(part):
                    raise IOError(f"short serial write: {part_written}/{len(part)}")
                written += part_written
                if args.chunk_gap_ms > 0:
                    time.sleep(args.chunk_gap_ms / 1000.0)
            gateway.serial.flush()
            try:
                response_type, response_sequence, _ = gateway._read_frame(sent + args.timeout)
                if response_type != (TEST_MESSAGE_TYPE | 0x80) or response_sequence != sequence:
                    failures += 1
                    gateway.serial.reset_input_buffer()
                    gateway.rx.clear()
                else:
                    latencies_ms.append((time.monotonic() - sent) * 1000.0)
            except TimeoutError:
                failures += 1
                gateway.serial.reset_input_buffer()
                gateway.rx.clear()
            if args.gap_ms > 0:
                time.sleep(args.gap_ms / 1000.0)
            if (index + 1) % 500 == 0:
                print(f"progress={index + 1}/{args.count} failures={failures}", flush=True)
        elapsed = time.monotonic() - started
    finally:
        gateway.close()

    if latencies_ms:
        ordered = sorted(latencies_ms)
        p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
        print(
            f"UART_STABILITY_RESULT success={len(latencies_ms)} failures={failures} "
            f"elapsed={elapsed:.3f}s median_ms={statistics.median(latencies_ms):.3f} "
            f"p95_ms={p95:.3f} max_ms={max(latencies_ms):.3f} "
            f"expected_raw_count={expected_raw_count} expected_raw_hash=0x{expected_raw_hash:08X}"
        )
    else:
        print(f"UART_STABILITY_RESULT success=0 failures={failures} elapsed={elapsed:.3f}s")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
