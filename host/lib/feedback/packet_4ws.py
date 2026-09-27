# このファイルは4WS Mainの128バイトSPI状態応答をデコードし、
# 生フィードバック用デバッグツールへヘッダ・CRC・車輪状態を提供する。
from __future__ import annotations

from dataclasses import dataclass
import math
import struct

PACKET_SIZE = 128
MAGIC = b"O4WS"
VERSION = 1
STATUS_MESSAGE_TYPE = 0x82
HEADER_SIZE = 12
CRC_OFFSET = 124
MAX_PAYLOAD_SIZE = CRC_OFFSET - HEADER_SIZE
STATUS_PAYLOAD_SIZE = 24
WHEEL_SPEED_RANGE_MPS = 32.767
STEERING_ANGLE_RANGE_RAD = 10.0 * math.pi


def crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return crc ^ 0xFFFFFFFF


def decode_two_byte_value(raw: int, value_range: float) -> float | None:
    if raw == 0xFFFF:
        return None
    return (raw - 32767) * value_range / 32767


@dataclass(slots=True)
class FourWsFeedbackPacket:
    magic: bytes
    version: int
    message_type: int
    sequence: int
    payload_length: int
    reserved: bytes
    payload: bytes
    padding: bytes
    crc32c_value: int
    crc_valid: bool
    accepted_sequence: int | None
    main_state: int | None
    error_summary: int | None
    wheel_speed_mps: tuple[float | None, ...] | None
    steering_angle_rad: tuple[float | None, ...] | None
    uptime_ms: int | None

    @property
    def is_header_valid(self) -> bool:
        return (
            self.magic == MAGIC
            and self.version == VERSION
            and self.message_type == STATUS_MESSAGE_TYPE
            and self.payload_length <= MAX_PAYLOAD_SIZE
            and self.reserved == b"\x00\x00"
        )

    @property
    def is_padding_zero(self) -> bool:
        return self.payload_length <= MAX_PAYLOAD_SIZE and not any(self.padding)

    @property
    def has_known_status_layout(self) -> bool:
        return (
            self.version == VERSION
            and self.message_type == STATUS_MESSAGE_TYPE
            and self.payload_length == STATUS_PAYLOAD_SIZE
        )


def decode_4ws_feedback_packet(data: bytes) -> FourWsFeedbackPacket:
    if len(data) != PACKET_SIZE:
        raise ValueError(f"packet size must be {PACKET_SIZE}, got {len(data)}")

    payload_length = struct.unpack_from("<H", data, 8)[0]
    bounded_length = min(payload_length, MAX_PAYLOAD_SIZE)
    payload = data[HEADER_SIZE : HEADER_SIZE + bounded_length]
    padding = data[HEADER_SIZE + bounded_length : CRC_OFFSET]
    known_status = data[4] == VERSION and data[5] == STATUS_MESSAGE_TYPE and payload_length == STATUS_PAYLOAD_SIZE

    accepted_sequence = None
    main_state = None
    error_summary = None
    wheel_speed_mps = None
    steering_angle_rad = None
    uptime_ms = None
    if known_status:
        accepted_sequence = struct.unpack_from("<H", payload, 0)[0]
        main_state = payload[2]
        error_summary = payload[3]
        wheel_speed_mps = tuple(
            decode_two_byte_value(struct.unpack_from(">H", payload, 4 + index * 4)[0], WHEEL_SPEED_RANGE_MPS)
            for index in range(4)
        )
        steering_angle_rad = tuple(
            decode_two_byte_value(struct.unpack_from(">H", payload, 6 + index * 4)[0], STEERING_ANGLE_RANGE_RAD)
            for index in range(4)
        )
        uptime_ms = struct.unpack_from("<I", payload, 20)[0]

    crc_value = struct.unpack_from("<I", data, CRC_OFFSET)[0]
    return FourWsFeedbackPacket(
        magic=data[:4],
        version=data[4],
        message_type=data[5],
        sequence=struct.unpack_from("<H", data, 6)[0],
        payload_length=payload_length,
        reserved=data[10:12],
        payload=payload,
        padding=padding,
        crc32c_value=crc_value,
        crc_valid=crc_value == crc32c(data[:CRC_OFFSET]),
        accepted_sequence=accepted_sequence,
        main_state=main_state,
        error_summary=error_summary,
        wheel_speed_mps=wheel_speed_mps,
        steering_angle_rad=steering_angle_rad,
        uptime_ms=uptime_ms,
    )
