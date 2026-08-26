"""CM4からMain bootloaderのOFW1 UARTを使い、inactive A/B slotを更新・確定する。"""

from __future__ import annotations

import argparse
import secrets
import struct
import time
from pathlib import Path

import serial

UART_BAUD = 1_000_000
LEGACY_SIZE = 72
MAGIC = b"OFW1"
HEADER_SIZE = 12
CHUNK_SIZE = 896
MAX_PAYLOAD = 920
MSG_INFO, MSG_BEGIN, MSG_CHUNK, MSG_FINALIZE, MSG_REBOOT = range(1, 6)


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
    return crc ^ 0xFFFFFFFF


def legacy_request(command: int) -> bytes:
    packet = bytearray(LEGACY_SIZE)
    packet[0] = 0xFE
    packet[1:5] = b"FWUP"
    packet[5] = command
    packet[-1] = sum(packet[:-1]) & 0xFF
    return bytes(packet)


def write_legacy_slow(serial_port: serial.Serial, command: int) -> None:
    """通常アプリの1 byte UART割込みが取りこぼさない速度で要求を送る。"""
    packet = legacy_request(command)
    for _ in range(3):
        for offset in range(0, len(packet), 4):
            serial_port.write(packet[offset : offset + 4])
            serial_port.flush()
            time.sleep(0.001)
        time.sleep(0.05)


def confirm_running_slot(port: str) -> None:
    """実行中スロットを確定し、再起動ガード時間まで待つ。"""
    confirm = serial.Serial(port, UART_BAUD, timeout=0.05)
    try:
        confirm.reset_input_buffer()
        write_legacy_slow(confirm, 5)
    finally:
        confirm.close()
    time.sleep(2.0)


class MainBootloader:
    def __init__(self, port: str) -> None:
        self.serial = serial.Serial(port, UART_BAUD, timeout=0.05)
        self.rx = bytearray()
        self.sequence = secrets.randbelow(0x10000)

    def close(self) -> None:
        self.serial.close()

    @staticmethod
    def frame(message_type: int, sequence: int, payload: bytes) -> bytes:
        header = MAGIC + bytes((1, message_type)) + struct.pack("<HH", sequence, len(payload))
        header += struct.pack("<H", crc16_ccitt(header))
        packet = header + payload
        return packet + struct.pack("<I", crc32c(packet))

    def read_frame(self, deadline: float) -> tuple[int, int, bytes]:
        while time.monotonic() < deadline:
            self.rx.extend(self.serial.read(self.serial.in_waiting or 1))
            index = self.rx.find(MAGIC)
            if index < 0:
                del self.rx[:-3]
                continue
            if index:
                del self.rx[:index]
            if len(self.rx) < HEADER_SIZE:
                continue
            length = struct.unpack_from("<H", self.rx, 8)[0]
            if self.rx[4] != 1 or length > MAX_PAYLOAD or crc16_ccitt(self.rx[:10]) != struct.unpack_from("<H", self.rx, 10)[0]:
                del self.rx[0]
                continue
            total = HEADER_SIZE + length + 4
            if len(self.rx) < total:
                continue
            packet = bytes(self.rx[:total])
            del self.rx[:total]
            if crc32c(packet[:-4]) != struct.unpack_from("<I", packet, total - 4)[0]:
                continue
            return packet[5], struct.unpack_from("<H", packet, 6)[0], packet[HEADER_SIZE:-4]
        raise TimeoutError("Main bootloader UART response timeout")

    def request(self, message_type: int, payload: bytes = b"", timeout: float = 3.0) -> tuple[int, int, int, int, int]:
        sequence = self.sequence
        self.sequence = (self.sequence + 1) & 0xFFFF
        packet = self.frame(message_type, sequence, payload)
        for attempt in range(5):
            self.serial.write(packet)
            self.serial.flush()
            try:
                response_type, response_sequence, response = self.read_frame(time.monotonic() + timeout)
            except TimeoutError:
                print(f"uart_retry type={message_type} sequence={sequence} attempt={attempt + 1}", flush=True)
                continue
            if response_type != message_type | 0x80 or response_sequence != sequence or len(response) != 8:
                continue
            status, slot, state, flags, value = struct.unpack("<BBBBI", response)
            return status, slot, state, flags, value
        raise TimeoutError("Main bootloader retry exhausted")


def validate_image(image: bytes, slot: int) -> None:
    if not 8 <= len(image) <= 0x38000:
        raise ValueError("Main image size is outside the 224KB slot")
    stack, reset = struct.unpack_from("<II", image)
    base = 0x08008000 if slot == 0 else 0x08040000
    if stack & 7 or not (0x20000000 <= stack <= 0x20020000 or 0x10000000 <= stack <= 0x10008000):
        raise ValueError(f"invalid initial SP 0x{stack:08X}")
    if not reset & 1 or not base <= reset & ~1 < base + len(image):
        raise ValueError(f"image is not linked for slot {slot}: reset=0x{reset:08X}")


def update(port: str, slot_a: Path, slot_b: Path) -> None:
    serial_port = serial.Serial(port, UART_BAUD, timeout=0.1)
    serial_port.reset_input_buffer()
    write_legacy_slow(serial_port, 4)
    serial_port.close()
    time.sleep(0.6)

    boot = MainBootloader(port)
    started = time.monotonic()
    try:
        status, target, _, valid_mask, generation = boot.request(MSG_INFO)
        if status != 0 or target not in (0, 1):
            raise RuntimeError(f"INFO failed status={status} target={target}")
        image_path = slot_a if target == 0 else slot_b
        image = image_path.read_bytes()
        validate_image(image, target)
        image_crc = crc32c(image)
        print(f"stage=begin target={'AB'[target]} valid_mask=0x{valid_mask:02X} generation={generation}", flush=True)
        result = boot.request(MSG_BEGIN, bytes((target, 0, 0, 0)) + struct.pack("<II", len(image), image_crc), timeout=15.0)
        if result[0] != 0:
            raise RuntimeError(f"BEGIN failed {result}")
        for offset in range(0, len(image), CHUNK_SIZE):
            chunk = image[offset : offset + CHUNK_SIZE]
            payload = struct.pack("<IIH", offset, crc32c(chunk), len(chunk)) + chunk
            result = boot.request(MSG_CHUNK, payload)
            if result[0] == 3 and result[4] == offset + len(chunk):
                pass
            elif result[0] != 0 or result[4] != offset + len(chunk):
                raise RuntimeError(f"CHUNK failed offset={offset} result={result}")
            if result[4] % 7168 < CHUNK_SIZE or result[4] == len(image):
                print(f"progress={result[4]}/{len(image)}", flush=True)
        result = boot.request(MSG_FINALIZE, timeout=10.0)
        if result[0] != 0 or result[4] != len(image):
            raise RuntimeError(f"FINALIZE failed {result}")
        result = boot.request(MSG_REBOOT)
        if result[0] != 0:
            raise RuntimeError(f"REBOOT failed {result}")
    finally:
        boot.close()

    # ブートローダーの2スロットCRC検証完了後にCONFIRMを要求する。
    time.sleep(2.0)
    confirm_running_slot(port)
    elapsed = time.monotonic() - started
    print(f"MAIN_AB_UPDATE_OK slot={'AB'[target]} size={len(image)} crc32c=0x{image_crc:08X} elapsed={elapsed:.3f}s", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slot-a", type=Path)
    parser.add_argument("--slot-b", type=Path)
    parser.add_argument("--port", default="/dev/ttyS0")
    parser.add_argument("--confirm-only", action="store_true")
    args = parser.parse_args()
    if args.confirm_only:
        confirm_running_slot(args.port)
        print("MAIN_AB_CONFIRM_OK", flush=True)
        return
    if args.slot_a is None or args.slot_b is None:
        parser.error("--slot-a and --slot-b are required unless --confirm-only is used")
    update(args.port, args.slot_a, args.slot_b)


if __name__ == "__main__":
    main()
