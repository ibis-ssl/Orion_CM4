#!/usr/bin/env python3
# 停止フラグ付きmode 3指令とMain feedbackのカウンタ反射を検証する。
"""停止フラグ付きmode 3指令のCM4→Main伝送とfeedback反射を非走行で確認する。"""

import argparse
import socket
import subprocess
import time

import serial

from feedback_probe import crc8_atm


def stopped_mode3(counter: int) -> bytes:
    command = bytearray(64)
    command[0] = 0xFE
    command[1] = counter
    for offset in (2, 4, 6, 8, 12, 14, 16, 24, 26, 32, 34, 36):
        command[offset:offset + 2] = b"\x7F\xFF"  # 速度・位置・角度などは0
    command[22] = 1 << 3  # STOP_EMERGENCY
    command[23] = 3
    return bytes((counter,)) + bytes(command)


def next_feedback(uart: serial.Serial, received: bytearray, deadline: float) -> bytes:
    while time.monotonic() < deadline:
        received.extend(uart.read(uart.in_waiting or 1))
        while len(received) >= 128:
            offset = received.find(b"\xAB\xEA")
            if offset < 0:
                del received[:-1]
                break
            del received[:offset]
            if len(received) < 128:
                break
            feedback = bytes(received[:128])
            if feedback[2] != crc8_atm(feedback[3:]):
                del received[0]
                continue
            del received[:128]
            return feedback
    raise TimeoutError("Main feedback was not received")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge", required=True)
    parser.add_argument("--robot-id", type=int, required=True)
    parser.add_argument("--port", default="/dev/serial0")
    args = parser.parse_args()

    received = bytearray()
    with serial.Serial(args.port, 1_000_000, timeout=0.05) as uart:
        bridge = subprocess.Popen(
            [args.bridge, "--robot-id", str(args.robot_id), "--passthrough"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(0.5)
            if bridge.poll() is not None:
                raise RuntimeError("ai_cmd_v2 exited before sending the stopped command")
            baseline = next_feedback(uart, received, time.monotonic() + 2.0)
            counter = (baseline[3] % 255) + 1
            frame = stopped_mode3(counter)
            received.clear()
            uart.reset_input_buffer()
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                sender.sendto(frame, ("127.0.0.1", 12345))
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                feedback = next_feedback(uart, received, deadline)
                if feedback[3] == counter:
                    print(f"stop_command_echo={counter} crc_valid=1")
                    return
            raise RuntimeError("stopped command was not reflected in Main feedback")
        finally:
            bridge.terminate()
            bridge.wait(timeout=3.0)


if __name__ == "__main__":
    main()
