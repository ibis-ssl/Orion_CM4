#!/usr/bin/env python3
# MainからのUART feedbackを読み、同期・CRC・主要フィールドを検証する。
"""MainからのUART feedbackを読み、同期・CRC・主要フィールドを非走行で確認する。"""

import argparse
import math
import struct
import time

import serial


def crc8_atm(data: bytes) -> int:
    crc = 0
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/serial0")
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()
    if args.count < 1 or args.timeout <= 0:
        parser.error("count and timeout must be positive")

    valid = 0
    bad_crc = 0
    bad_position = 0
    counters = []
    positions = []
    received = bytearray()
    deadline = time.monotonic() + args.timeout
    with serial.Serial(args.port, 1_000_000, timeout=0.05) as uart:
        while valid < args.count and time.monotonic() < deadline:
            received.extend(uart.read(uart.in_waiting or 1))
            while len(received) >= 128:
                offset = received.find(b"\xAB\xEA")
                if offset < 0:
                    del received[:-1]
                    break
                del received[:offset]
                if len(received) < 128:
                    break
                frame = bytes(received[:128])
                if frame[2] != crc8_atm(frame[3:]):
                    bad_crc += 1
                    del received[0]
                    continue
                del received[:128]
                valid += 1
                counters.append(frame[4])
                position = struct.unpack_from("<ff", frame, 112)
                if not all(math.isfinite(value) for value in position):
                    bad_position += 1
                positions.append(position)

    print(f"valid={valid} bad_crc={bad_crc} bad_position={bad_position}")
    if positions:
        print(f"counter_first={counters[0]} counter_last={counters[-1]} "
              f"position_last=({positions[-1][0]:.3f},{positions[-1][1]:.3f})")
    if valid != args.count or bad_crc or bad_position:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
