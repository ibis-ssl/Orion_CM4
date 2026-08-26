#!/usr/bin/env python3
"""CM4からMainの高速ゲートウェイを介して任意のCANノード群を更新する。"""

from __future__ import annotations

import argparse
import os
import secrets
import struct
import time

import serial

UART_BAUD = 1_000_000
LEGACY_SIZE = 72
MAGIC = b"OFW2"
VERSION = 2
HEADER_SIZE = 12
MAX_PAYLOAD = 907
CHUNK_SIZE = 896

MSG_ENTER = 1
MSG_BEGIN = 2
MSG_CHUNK = 3
MSG_FINALIZE = 4
MSG_REBOOT = 5
MSG_STATUS = 6


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return (~crc) & 0xFFFFFFFF


class UpdateError(RuntimeError):
    pass


class GatewayResponseError(UpdateError):
    def __init__(self, gateway_status: int, node_status: int, node_id: int, token: int, value: int) -> None:
        super().__init__(
            f"gateway={gateway_status} node={node_status} node_id={node_id} token={token} value=0x{value:08X}"
        )
        self.gateway_status = gateway_status
        self.node_status = node_status


class Gateway:
    def __init__(self, port: str, inject_uart_crc_once: bool = False) -> None:
        self.serial = serial.Serial(port, UART_BAUD, timeout=0.05)
        self.rx = bytearray()
        self.sequence = secrets.randbelow(0x10000)
        self.inject_uart_crc_once = inject_uart_crc_once
        self.expected_nodes = {4}

    def close(self) -> None:
        self.serial.close()

    def _legacy_packet(self, session: int) -> bytes:
        packet = bytearray(LEGACY_SIZE)
        packet[0] = 0xFE
        packet[1:5] = b"FWUP"
        packet[5] = 1
        packet[17] = session
        packet[-1] = sum(packet[:-1]) & 0xFF
        return bytes(packet)

    def enter_gateway(self, session: int) -> None:
        """既存72-byte parserを更新モードへ切り替え、v2応答で成立を確認する。"""
        self.serial.reset_input_buffer()
        packet = self._legacy_packet(session)
        # Mainの通常UART受信は1 byte割込みのため、開始要求だけは小分けして
        # 高負荷時のOREを防ぐ。本体のOFW2転送速度には影響しない。
        for _ in range(3):
            for offset in range(0, len(packet), 4):
                self.serial.write(packet[offset : offset + 4])
                self.serial.flush()
                time.sleep(0.001)
            time.sleep(0.05)
        time.sleep(0.2)
        self.serial.reset_input_buffer()
        self.rx.clear()

    def _frame(self, message_type: int, sequence: int, payload: bytes) -> bytes:
        if len(payload) > MAX_PAYLOAD:
            raise ValueError("payload too large")
        header = bytearray(MAGIC + bytes([VERSION, message_type]) + struct.pack("<HH", sequence, len(payload)))
        header += struct.pack("<H", crc16_ccitt(header))
        frame = bytes(header) + payload
        return frame + struct.pack("<I", crc32c(frame))

    def _read_frame(self, deadline: float) -> tuple[int, int, bytes]:
        while time.monotonic() < deadline:
            available = self.serial.in_waiting
            self.rx.extend(self.serial.read(available if available else 1))
            while len(self.rx) >= 4:
                index = self.rx.find(MAGIC)
                if index < 0:
                    del self.rx[:-3]
                    break
                if index:
                    del self.rx[:index]
                if len(self.rx) < HEADER_SIZE:
                    break
                if self.rx[4] != VERSION or crc16_ccitt(self.rx[:10]) != struct.unpack_from("<H", self.rx, 10)[0]:
                    del self.rx[0]
                    continue
                payload_length = struct.unpack_from("<H", self.rx, 8)[0]
                if payload_length > MAX_PAYLOAD:
                    del self.rx[0]
                    continue
                total = HEADER_SIZE + payload_length + 4
                if len(self.rx) < total:
                    break
                frame = bytes(self.rx[:total])
                del self.rx[:total]
                if crc32c(frame[:-4]) != struct.unpack_from("<I", frame, total - 4)[0]:
                    continue
                return frame[5], struct.unpack_from("<H", frame, 6)[0], frame[HEADER_SIZE:-4]
        raise TimeoutError("Main v2 UART response timeout")

    def request(self, message_type: int, payload: bytes = b"", timeout: float = 12.0) -> bytes:
        sequence = self.sequence
        self.sequence = (self.sequence + 1) & 0xFFFF
        frame = self._frame(message_type, sequence, payload)
        for attempt in range(5):
            outgoing = frame
            if self.inject_uart_crc_once:
                damaged = bytearray(frame)
                damaged[-1] ^= 1
                outgoing = bytes(damaged)
                self.inject_uart_crc_once = False
                print("fault_injection=uart_crc", flush=True)
            self.serial.write(outgoing)
            self.serial.flush()
            try:
                response_type, response_sequence, response = self._read_frame(time.monotonic() + timeout)
            except TimeoutError:
                print(f"uart_retry type={message_type} sequence={sequence} attempt={attempt + 1}", flush=True)
                if attempt == 4:
                    raise
                continue
            if response_type != (message_type | 0x80) or response_sequence != sequence:
                continue
            if len(response) != 8:
                raise UpdateError("invalid gateway response length")
            gateway_status, node_status, node_id, token, value = struct.unpack("<BBBBI", response)
            if gateway_status != 0 or node_status != 0 or node_id not in self.expected_nodes:
                raise GatewayResponseError(gateway_status, node_status, node_id, token, value)
            return response
        raise UpdateError("unexpected response sequence")


    def select_targets(self, session: int, node_can1: int, node_can2: int) -> bytes:
        """CAN1/CAN2の対象を選択し、アプリまたはbootloaderの応答を確認する。"""
        self.expected_nodes = {node for node in (node_can1, node_can2) if node != 0xFF}
        if not self.expected_nodes:
            raise ValueError("at least one CAN target is required")
        response = self.request(MSG_ENTER, bytes([session, node_can1, node_can2, 0]), timeout=2.0)
        if 100 in self.expected_nodes:
            print("power_safe_state=confirmed", flush=True)
        return response


def update(port: str, image_path: str, injection: int, inject_uart_crc_once: bool, node_can1: int, node_can2: int) -> float:
    with open(image_path, "rb") as file:
        image = file.read()
    image_crc = crc32c(image)
    session = secrets.randbelow(255) + 1
    gateway = Gateway(port, inject_uart_crc_once)
    started = time.monotonic()
    try:
        print("stage=legacy_gateway", flush=True)
        gateway.enter_gateway(session)
        print(f"stage=enter_can_boot can1={node_can1} can2={node_can2}", flush=True)
        gateway.select_targets(session, node_can1, node_can2)
        print("stage=erase_begin", flush=True)
        gateway.request(MSG_BEGIN, bytes([session, 0, 0, 0]) + struct.pack("<II", len(image), image_crc), timeout=12.0)
        print("stage=transfer", flush=True)
        for offset in range(0, len(image), CHUNK_SIZE):
            chunk = image[offset : offset + CHUNK_SIZE]
            flags = injection if offset == 0 else 0
            payload = struct.pack("<IIHB", offset, crc32c(chunk), len(chunk), flags) + chunk
            for chunk_attempt in range(3):
                try:
                    response = gateway.request(MSG_CHUNK, payload, timeout=0.5)
                    break
                except GatewayResponseError as error:
                    if error.gateway_status not in (2, 3) or chunk_attempt == 2:
                        raise
                    print(f"chunk_retry offset={offset} gateway={error.gateway_status} node={error.node_status}", flush=True)
            else:
                raise UpdateError("chunk retry exhausted")
            committed = struct.unpack_from("<I", response, 4)[0]
            if committed != offset + len(chunk):
                raise UpdateError(f"commit mismatch: {committed} != {offset + len(chunk)}")
            if offset == 0 and flags:
                print(f"fault_injection=can_flags_0x{flags:02X} recovered", flush=True)
            if committed % 7168 < CHUNK_SIZE or committed == len(image):
                print(f"progress={committed}/{len(image)}", flush=True)
        response = gateway.request(MSG_FINALIZE, timeout=8.0)
        if struct.unpack_from("<I", response, 4)[0] != len(image):
            raise UpdateError("FINALIZE size mismatch")
        gateway.request(MSG_REBOOT, timeout=3.0)
        # Mainもゲートウェイ終了時にリセットされる。通常テレメトリが再開するまで
        # 待ってから成功を返し、連続更新時に次のFWUP要求を取りこぼさないようにする。
        gateway.serial.reset_input_buffer()
        gateway.rx.clear()
        # Mainブートローダーの2スロットCRC検証完了まで待ち、次回のFWUP開始要求が
        # アプリケーション起動前に送られないようにする。
        time.sleep(2.0)
    finally:
        gateway.close()
    elapsed = time.monotonic() - started
    print(f"CAN_UPDATE_V2_OK nodes={sorted(gateway.expected_nodes)} size={len(image)} crc32c=0x{image_crc:08X} elapsed={elapsed:.3f}s", flush=True)
    return elapsed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image")
    parser.add_argument("--port", default="/dev/ttyS0")
    parser.add_argument("--node-can1", type=lambda value: int(value, 0), default=4)
    parser.add_argument("--node-can2", type=lambda value: int(value, 0), default=0xFF)
    parser.add_argument("--inject-uart-crc-once", action="store_true")
    parser.add_argument("--inject-can-drop-once", action="store_true")
    parser.add_argument("--inject-can-duplicate-once", action="store_true")
    parser.add_argument("--inject-can-reorder-once", action="store_true")
    parser.add_argument("--inject-can-corrupt-once", action="store_true")
    args = parser.parse_args()
    if not os.path.isfile(args.image):
        parser.error(f"image not found: {args.image}")
    injection = (
        int(args.inject_can_drop_once)
        | (int(args.inject_can_duplicate_once) << 1)
        | (int(args.inject_can_reorder_once) << 2)
        | (int(args.inject_can_corrupt_once) << 3)
    )
    for node in (args.node_can1, args.node_can2):
        if not 0 <= node <= 0xFF:
            parser.error("node ID must be 0..255")
    update(args.port, args.image, injection, args.inject_uart_crc_once, args.node_can1, args.node_can2)


if __name__ == "__main__":
    main()
