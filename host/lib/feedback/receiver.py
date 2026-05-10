# このファイルは robot feedback の UDP multicast 受信処理を担当する。
# CLI や GUI から共通利用できるソケット生成、パケット反復、表示用変換を提供する。
from __future__ import annotations

from dataclasses import asdict
import socket
import struct
from typing import Iterator

from host.lib.feedback.packet import PACKET_SIZE, TX_VALUE_LABELS, RobotFeedbackPacket

DEFAULT_INTERFACE_IP = "0.0.0.0"
RECEIVE_BUFFER_SIZE = 4096
CM4_IP_OFFSET = 100


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


def packet_to_dict(packet: RobotFeedbackPacket) -> dict[str, object]:
    values = asdict(packet)
    values["sync_valid"] = packet.is_sync_valid
    values["checksum_valid"] = packet.is_checksum_valid
    values["camera_pos_x"] = packet.camera_pos_x
    values["camera_radius"] = packet.camera_radius
    values["kick_state"] = packet.kick_state
    values["motor_current"] = packet.motor_current
    values["tx_values"] = dict(zip(TX_VALUE_LABELS, packet.tx_value_array))
    values["reserved"] = packet.reserved.hex()
    return values


def format_packet_summary(index: int, packet: RobotFeedbackPacket) -> str:
    return (
        f"#{index} "
        f"counter={packet.check_counter} "
        f"sync={int(packet.is_sync_valid)} "
        f"checksum={int(packet.is_checksum_valid)} "
        f"yaw={packet.imu_yaw_deg:.3f} "
        f"battery={packet.battery_voltage_bldc_right:.3f} "
        f"camera=({packet.camera_pos_x},{packet.camera_pos_y},r={packet.camera_radius},fps={packet.camera_fps}) "
        f"kick={packet.kick_state} "
        f"motor_current={','.join(f'{value:.1f}' for value in packet.motor_current)} "
        f"error=({packet.current_error_id},{packet.current_error_info},{packet.current_error_value:.3f})"
    )
