#!/usr/bin/env python3
"""CM4からMainおよび全CAN基板の開発用build IDとimage CRCを一括取得する。"""

from __future__ import annotations

import argparse
import datetime as dt
import struct
import time
from pathlib import Path

import serial

UART_BAUD = 1_000_000
REQUEST_SIZE = 72
RESPONSE_SIZE = 60
FW_VERSION_MAGIC = 0x52565746
TARGETS = ("main_a", "main_b", "sub", "bldc_can1", "bldc_can2", "power")


def crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return (~crc) & 0xFFFFFFFF


def request_packet() -> bytes:
    packet = bytearray(REQUEST_SIZE)
    packet[0] = 0xFE
    packet[1:5] = b"FWVR"
    packet[-1] = sum(packet[:-1]) & 0xFF
    return bytes(packet)


def read_response(port: str, timeout: float = 3.0) -> tuple[int, int, list[tuple[int, int]]]:
    uart = serial.Serial(port, UART_BAUD, timeout=0.05)
    received = bytearray()
    try:
        uart.reset_input_buffer()
        packet = request_packet()
        for _ in range(3):
            for offset in range(0, len(packet), 4):
                uart.write(packet[offset : offset + 4])
                uart.flush()
                time.sleep(0.001)
            time.sleep(0.05)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            received.extend(uart.read(uart.in_waiting or 1))
            while len(received) >= 4:
                index = received.find(b"FWVR")
                if index < 0:
                    del received[:-3]
                    break
                if index:
                    del received[:index]
                if len(received) < RESPONSE_SIZE:
                    break
                response = bytes(received[:RESPONSE_SIZE])
                del received[:RESPONSE_SIZE]
                expected_crc = struct.unpack_from("<I", response, 56)[0]
                if response[4] != 1 or crc32c(response[:-4]) != expected_crc:
                    continue
                entries = [struct.unpack_from("<II", response, 8 + entry * 8) for entry in range(6)]
                return response[5], response[6] | (response[7] << 8), entries
    finally:
        uart.close()
    raise TimeoutError("FW version response timeout")


def image_identity(path: Path) -> tuple[int, int]:
    image = path.read_bytes()
    if len(image) < 0x408:
        raise ValueError(f"image is too small: {path}")
    magic, build_id = struct.unpack_from("<II", image, 0x400)
    if magic != FW_VERSION_MAGIC or build_id == 0:
        raise ValueError(f"FW version descriptor not found: {path}")
    return build_id, crc32c(image)


def build_time(build_id: int) -> str:
    if build_id == 0:
        return "-"
    return dt.datetime.fromtimestamp(build_id, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="/dev/ttyS0")
    for target in TARGETS:
        parser.add_argument(f"--{target.replace('_', '-')}", type=Path)
    args = parser.parse_args()
    active_slot, masks, entries = read_response(args.port)
    present_mask = masks & 0xFF
    valid_mask = masks >> 8
    print(f"active_slot={'AB'[active_slot] if active_slot in (0, 1) else '?'}")
    print("TARGET       BUILD_ID    BUILD_TIME            CRC32C     STATUS")
    for index, (target, (build_id, image_crc)) in enumerate(zip(TARGETS, entries)):
        expected_path = getattr(args, target)
        if not present_mask & (1 << index):
            status = "UNREACHABLE"
        elif not valid_mask & (1 << index) or build_id == 0:
            status = "INVALID"
        elif expected_path is None:
            status = "INSTALLED"
        else:
            expected_build, expected_crc = image_identity(expected_path)
            if build_id == expected_build and image_crc == expected_crc:
                status = "SAME"
            elif build_id < expected_build:
                status = "OLDER"
            elif build_id > expected_build:
                status = "NEWER"
            else:
                status = "CRC_MISMATCH"
        print(f"{target:12} {build_id:10d}  {build_time(build_id):20}  {image_crc:08X}  {status}")


if __name__ == "__main__":
    main()
