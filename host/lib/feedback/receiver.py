# このファイルはOrionMainと4WS Mainの生フィードバック受信を担当する。
# CLIやGUIから共通利用するソケット、形式選択、表示用変換を提供する。
from __future__ import annotations

from dataclasses import asdict
import socket
import struct
from typing import Iterator

from host.lib.feedback.packet import PACKET_SIZE, TX_VALUE_LABELS, RobotFeedbackPacket, decode_robot_feedback_packet
from host.lib.feedback.packet_4ws import MAGIC as FOUR_WS_MAGIC
from host.lib.feedback.packet_4ws import FourWsFeedbackPacket, decode_4ws_feedback_packet

DEFAULT_INTERFACE_IP = "0.0.0.0"
RECEIVE_BUFFER_SIZE = 4096
CM4_IP_OFFSET = 100
MACHINE_TYPES = ("auto", "orion", "4ws")
FeedbackPacket = RobotFeedbackPacket | FourWsFeedbackPacket


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
    if machine_type == "4ws" or (machine_type == "auto" and data.startswith(FOUR_WS_MAGIC)):
        return decode_4ws_feedback_packet(data)
    return decode_robot_feedback_packet(data)


def packet_to_dict(packet: FeedbackPacket) -> dict[str, object]:
    values = asdict(packet)
    if isinstance(packet, FourWsFeedbackPacket):
        values["machine_type"] = "4ws"
        values["magic"] = packet.magic.decode("ascii", errors="replace")
        values["reserved"] = packet.reserved.hex()
        values["payload"] = packet.payload.hex()
        values["padding"] = packet.padding.hex()
        values["header_valid"] = packet.is_header_valid
        values["padding_zero"] = packet.is_padding_zero
        values["known_status_layout"] = packet.has_known_status_layout
        return values
    values["machine_type"] = "orion"
    values["sync_valid"] = packet.is_sync_valid
    values["camera_pos_x"] = packet.camera_pos_x
    values["camera_radius"] = packet.camera_radius
    values["kick_state"] = packet.kick_state
    values["motor_current"] = packet.motor_current
    values["tx_values"] = dict(zip(TX_VALUE_LABELS, packet.tx_value_array))
    values["reserved"] = packet.reserved.hex()
    return values


def format_packet_summary(index: int, packet: FeedbackPacket) -> str:
    if isinstance(packet, FourWsFeedbackPacket):
        details = (
            f"accepted={packet.accepted_sequence} state={packet.main_state} "
            f"error={packet.error_summary} uptime_ms={packet.uptime_ms} "
            f"wheel_speed={packet.wheel_speed_mps} steering_angle={packet.steering_angle_rad}"
            if packet.has_known_status_layout
            else f"payload={packet.payload.hex()}"
        )
        return (
            f"#{index} type=4ws seq={packet.sequence} version={packet.version} "
            f"message=0x{packet.message_type:02x} length={packet.payload_length} "
            f"header={int(packet.is_header_valid)} crc={int(packet.crc_valid)} "
            f"padding={int(packet.is_padding_zero)} {details}"
        )
    return (
        f"#{index} type=orion "
        f"counter={packet.check_counter} "
        f"sync={int(packet.is_sync_valid)} "
        f"crc={int(packet.is_crc_valid)} "
        f"yaw={packet.imu_yaw_deg:.3f} "
        f"battery={packet.battery_voltage_bldc_right:.3f} "
        f"camera=({packet.camera_pos_x},{packet.camera_pos_y},r={packet.camera_radius},fps={packet.camera_fps}) "
        f"kick={packet.kick_state} "
        f"motor_current={','.join(f'{value:.1f}' for value in packet.motor_current)} "
        f"error=({packet.current_error_id},{packet.current_error_info},{packet.current_error_value:.3f})"
    )
