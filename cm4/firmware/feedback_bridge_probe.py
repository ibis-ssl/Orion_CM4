#!/usr/bin/env python3
# UARTからloopback UDPまでのfeedbackブリッジを検証する。
"""実機のUART→CM4ブリッジ→loopback UDPを制御指令なしで検査する。"""

import argparse
import math
from pathlib import Path
import socket
import struct
import subprocess

from feedback_probe import crc8_atm


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge", required=True)
    parser.add_argument("--machine-number", type=int, required=True)
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--sample-output", type=Path)
    args = parser.parse_args()
    if args.count < 1:
        parser.error("count must be positive")
    valid = 0
    last_frame = b""
    positions = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(("127.0.0.1", 50000 + args.machine_number))
        receiver.settimeout(5.0)
        bridge = subprocess.Popen(
            [args.bridge, "-n", str(args.machine_number)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(args.count):
                frame, _ = receiver.recvfrom(256)
                if len(frame) != 128 or frame[:2] != b"\xAB\xEA" or frame[2] != crc8_atm(frame[3:]):
                    raise RuntimeError("invalid bridged feedback frame")
                position = struct.unpack_from("<ff", frame, 112)
                if not all(math.isfinite(value) for value in position):
                    raise RuntimeError("invalid feedback position")
                positions.append(position)
                valid += 1
                last_frame = frame
        finally:
            bridge.terminate()
            bridge.wait(timeout=3.0)

    print(f"bridged_valid={valid} position_last=({positions[-1][0]:.3f},{positions[-1][1]:.3f})")
    if args.sample_output:
        args.sample_output.write_bytes(last_frame)


if __name__ == "__main__":
    main()
