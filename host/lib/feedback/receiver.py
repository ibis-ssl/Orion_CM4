# このファイルはMain共通フィードバックのUDP受信とデコード補助を担当する。
# 同期値からUDP転送元の機体種別を判別し、ペイロードは単一のデコーダで読む。
from __future__ import annotations

from dataclasses import asdict
import socket
import struct
from typing import Iterator

from host.lib.feedback.packet import (
    PACKET_SIZE,
    SYNC0,
    SYNC1,
    UDP_4WS_SYNC1,
    RobotFeedbackPacket,
    decode_robot_feedback_packet,
)

DEFAULT_INTERFACE_IP = "0.0.0.0"
RECEIVE_BUFFER_SIZE = 4096
CM4_IP_OFFSET = 100
MACHINE_TYPES = ("auto", "orion", "4ws")
FeedbackPacket = RobotFeedbackPacket


def multicast_endpoint(machine_no: int) -> tuple[str, int]:
    cm4_ip_last_octet = CM4_IP_OFFSET + machine_no
    return f"224.5.20.{cm4_ip_last_octet}", 50000 + cm4_ip_last_octet


def open_multicast_socket(group: str, port: int, interface_ip: str) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("", port))
    except OSError:
        sock.bind((interface_ip, port))

    membership = struct.pack("=4s4s", socket.inet_aton(group), socket.inet_aton(interface_ip))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
    return sock


def iter_feedback_packets(sock: socket.socket) -> Iterator[bytes]:
    while True:
        payload, _sender = sock.recvfrom(RECEIVE_BUFFER_SIZE)
        if len(payload) == PACKET_SIZE:
            yield payload


def decode_feedback_packet(data: bytes, machine_type: str = "auto") -> FeedbackPacket:
    if machine_type not in MACHINE_TYPES:
        raise ValueError(f"unknown machine type: {machine_type}")
    if len(data) != PACKET_SIZE:
        raise ValueError(f"packet size must be {PACKET_SIZE}, got {len(data)}")

    expected_sync = {
        "orion": bytes((SYNC0, SYNC1)),
        "4ws": bytes((SYNC0, UDP_4WS_SYNC1)),
    }
    actual_sync = data[:2]
    if machine_type == "auto":
        if actual_sync not in expected_sync.values():
            raise ValueError(f"unknown feedback sync bytes: {actual_sync.hex()}")
    elif actual_sync != expected_sync[machine_type]:
        raise ValueError(f"unexpected feedback sync bytes for {machine_type}: {actual_sync.hex()}")

    return decode_robot_feedback_packet(data)


def packet_to_dict(packet: FeedbackPacket) -> dict[str, object]:
    values = asdict(packet)
    values["machine_type"] = packet.machine_type
    values["sync_valid"] = packet.is_sync_valid
    values["kick_state"] = packet.kick_state
    values["motor_current"] = packet.motor_current
    return values


def format_packet_summary(index: int, packet: FeedbackPacket) -> str:
    return (
        f"#{index} type={packet.machine_type} "
        f"counter={packet.check_counter} "
        f"sync={int(packet.is_sync_valid)} "
        f"crc={int(packet.is_crc_valid)} "
        f"yaw={packet.imu_yaw_deg:.3f} "
        f"battery={packet.battery_voltage:.3f} "
        f"kick={packet.kick_state} "
        f"motor_current={','.join(f'{value:.1f}' for value in packet.motor_current)} "
        f"error=({packet.current_error_id},{packet.current_error_info},{packet.current_error_value:.3f})"
    )
