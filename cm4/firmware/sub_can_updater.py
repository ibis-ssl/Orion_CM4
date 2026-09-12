#!/usr/bin/env python3
"""CM4のUARTからMainをCAN gatewayとしてF303 sub applicationを更新する。"""

from __future__ import annotations

import argparse
import os
import struct
import time

import serial

UART_PACKET_SIZE = 72
TELEMETRY_SIZE = 128
CAN_COMMAND_ID = 0x614
CAN_DATA_ID = 0x624
BLOCK_SIZE = 112


class SubNack(RuntimeError):
    def __init__(self, command: int, status: int, value: int) -> None:
        super().__init__(f"Sub NACK command=0x{command:02X} status={status} value=0x{value:08X}")
        self.status = status
        self.value = value


def crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return (~crc) & 0xFFFFFFFF


class Gateway:
    def __init__(self, port: str) -> None:
        self.serial = serial.Serial(port, 1_000_000, timeout=0.05)
        self.rx = bytearray()
        self.last_counter: int | None = None
        self.session = int(time.time_ns()) & 0xFF

    def close(self) -> None:
        self.serial.close()

    def _packet(self, operation: int, can_id: int = 0, payload: bytes = b"") -> bytes:
        packet = bytearray(UART_PACKET_SIZE)
        packet[0] = 0xFE
        packet[1:5] = b"FWUP"
        packet[5] = operation
        packet[6:8] = struct.pack("<H", can_id)
        packet[8 : 8 + len(payload)] = payload
        packet[17] = self.session
        packet[-1] = sum(packet[:-1]) & 0xFF
        return bytes(packet)

    def send_local(self, operation: int) -> None:
        self.serial.write(self._packet(operation))
        self.serial.flush()

    def send_can(self, can_id: int, payload: bytes) -> None:
        if len(payload) != 8:
            raise ValueError("CAN payload must be 8 bytes")
        self.serial.write(self._packet(2, can_id, payload))
        self.serial.flush()

    def _read_frame(self, deadline: float) -> bytes:
        while time.monotonic() < deadline:
            available = self.serial.in_waiting
            self.rx.extend(self.serial.read(available if available else 1))
            while len(self.rx) >= 4:
                index = self.rx.find(b"FWRP")
                if index < 0:
                    del self.rx[:-3]
                    break
                if index:
                    del self.rx[:index]
                if len(self.rx) < 16:
                    break
                frame = bytes(self.rx[:16])
                del self.rx[:16]
                if (sum(frame[:13]) & 0xFF) == frame[13] and frame[14:] == b"\r\n":
                    return frame
        raise TimeoutError("Main gateway UART response timeout")

    def enter_update_mode(self, timeout: float = 10.0) -> bytes:
        """Mainの起動中や送信中でも、更新ゲートウェイ応答まで再送して同期する。"""
        deadline = time.monotonic() + timeout
        last_error: TimeoutError | None = None
        while time.monotonic() < deadline:
            self.serial.reset_input_buffer()
            self.rx.clear()
            self.send_local(1)
            try:
                return self.wait_reply(0xF1, min(0.75, deadline - time.monotonic()))
            except TimeoutError as error:
                last_error = error
                time.sleep(0.1)
        raise TimeoutError("Main gateway did not enter update mode") from last_error

    def wait_reply(self, command: int, timeout: float = 3.0) -> bytes:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            frame = self._read_frame(deadline)
            reply = frame[4:12]
            counter = frame[12]
            if self.last_counter == counter:
                continue
            self.last_counter = counter
            if reply[0] == command and reply[7] == self.session:
                if reply[1] != 0:
                    raise SubNack(command, reply[1], struct.unpack_from("<I", reply, 2)[0])
                return reply
        raise TimeoutError(f"Sub response timeout command=0x{command:02X}")

    def command(self, command: int, arguments: bytes = b"", timeout: float = 3.0) -> bytes:
        payload = bytearray(8)
        payload[0] = command
        payload[1 : 1 + len(arguments)] = arguments
        payload[7] = self.session
        for attempt in range(5):
            self.send_can(CAN_COMMAND_ID, bytes(payload))
            try:
                return self.wait_reply(command | 0x80, timeout)
            except TimeoutError:
                if attempt == 4:
                    raise
        raise AssertionError


def update(port: str, image_path: str) -> None:
    image = open(image_path, "rb").read()
    image_crc = crc32c(image)
    gateway = Gateway(port)
    try:
        gateway.serial.reset_input_buffer()
        gateway.enter_update_mode()
        time.sleep(0.2)
        enter_boot = b"OFWUP" + bytes([4, 0, gateway.session])
        for _ in range(10):
            gateway.send_can(0x600, enter_boot)
            time.sleep(0.05)
        gateway.command(1)
        gateway.command(3, struct.pack("<I", image_crc))
        begin_payload = bytes([2]) + struct.pack("<I", len(image)) + b"\x00\x00" + bytes([gateway.session])
        gateway.send_can(CAN_COMMAND_ID, begin_payload)
        time.sleep(5.0)
        gateway.close()
        time.sleep(0.5)
        gateway = Gateway(port)
        gateway.serial.reset_input_buffer()
        gateway.enter_update_mode()
        gateway.command(1)
        begin_reply = gateway.command(2, struct.pack("<I", len(image)))
        offset = struct.unpack_from("<I", begin_reply, 2)[0]
        if offset > len(image) or offset % 2:
            raise RuntimeError(f"invalid resume offset: {offset}")
        while offset < len(image):
            chunk = image[offset : offset + BLOCK_SIZE]
            for block_attempt in range(5):
                block_begin = bytes([4]) + struct.pack("<IH", offset, len(chunk)) + bytes([gateway.session])
                gateway.send_can(CAN_COMMAND_ID, block_begin)
                time.sleep(0.01)
                sequence = 0
                for position in range(0, len(chunk), 7):
                    payload = bytes([sequence]) + chunk[position : position + 7].ljust(7, b"\xFF")
                    gateway.send_can(CAN_DATA_ID, payload)
                    sequence = (sequence + 1) & 0xFF
                    time.sleep(0.002)
                try:
                    block_end = bytes([5]) + struct.pack("<I", crc32c(chunk)) + b"\x00\x00" + bytes([gateway.session])
                    gateway.send_can(CAN_COMMAND_ID, block_end)
                    reply = gateway.wait_reply(0x85, 0.5)
                    break
                except (SubNack, TimeoutError) as error:
                    if isinstance(error, SubNack) and error.status not in (3, 4):
                        raise
                    gateway.close()
                    time.sleep(0.02)
                    gateway = Gateway(port)
                    gateway.serial.reset_input_buffer()
                    status_reply = gateway.command(1)
                    confirmed = struct.unpack_from("<I", status_reply, 2)[0]
                    if confirmed == offset + len(chunk):
                        reply = bytes([0x85, 0]) + struct.pack("<I", confirmed) + bytes([0, gateway.session])
                        break
                    if confirmed != offset or block_attempt == 4:
                        raise RuntimeError(f"unexpected confirmed offset: {confirmed}, expected {offset}") from error
            else:
                raise RuntimeError("block retry exhausted")
            next_offset = struct.unpack_from("<I", reply, 2)[0]
            if next_offset != offset + len(chunk):
                raise RuntimeError(f"offset mismatch: {next_offset} != {offset + len(chunk)}")
            offset = next_offset
            time.sleep(0.01)
            if offset % 4096 < BLOCK_SIZE or offset == len(image):
                print(f"progress={offset}/{len(image)}", flush=True)
        reply = gateway.command(6, timeout=5.0)
        if struct.unpack_from("<I", reply, 2)[0] != len(image):
            raise RuntimeError("END size mismatch")
        gateway.command(7)
        time.sleep(0.3)
        gateway.send_local(3)
        print(f"SUB_CAN_UPDATE_OK size={len(image)} crc32c=0x{image_crc:08X}", flush=True)
    finally:
        gateway.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image")
    parser.add_argument("--port", default="/dev/serial0")
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()
    if not os.path.isfile(args.image):
        parser.error(f"image not found: {args.image}")
    if args.repeat < 1:
        parser.error("--repeat must be 1 or greater")
    for cycle in range(1, args.repeat + 1):
        print(f"CYCLE_START {cycle}/{args.repeat}", flush=True)
        update(args.port, args.image)
        print(f"CYCLE_OK {cycle}/{args.repeat}", flush=True)
        if cycle != args.repeat:
            time.sleep(2.0)


if __name__ == "__main__":
    main()
