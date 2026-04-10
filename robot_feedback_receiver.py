# このファイルは robot feedback の UDP multicast を受信し、
# 128 バイト状態パケットのデコード結果を標準出力へ出す CLI ツールを担当する。
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import socket
import struct
from typing import Iterator

from robot_feedback_packet import PACKET_SIZE, TX_VALUE_LABELS, RobotFeedbackPacket, decode_robot_feedback_packet


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Receive and decode robot feedback multicast packets")
    parser.add_argument("--machine-no", type=int, default=3, help="target machine number N for 192.168.20.(100 + N)")
    parser.add_argument("--multicast-group", default=None, help="override multicast group")
    parser.add_argument("--port", type=int, default=None, help="override UDP port")
    parser.add_argument("--interface-ip", default=DEFAULT_INTERFACE_IP, help="local interface IP for multicast join")
    parser.add_argument("--max-packets", type=int, default=0, help="stop after receiving this many packets")
    parser.add_argument("--receive-timeout", type=float, default=0.0, help="socket receive timeout in seconds")
    parser.add_argument("--json", action="store_true", help="print decoded packets as JSON lines")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    default_group, default_port = multicast_endpoint(args.machine_no)
    group = args.multicast_group or default_group
    port = args.port or default_port

    sock = open_multicast_socket(group, port, args.interface_ip)
    if args.receive_timeout > 0:
        sock.settimeout(args.receive_timeout)

    print(f"listen multicast={group}:{port} interface={args.interface_ip}")
    try:
        for index, payload in enumerate(iter_feedback_packets(sock), start=1):
            packet = decode_robot_feedback_packet(payload)
            if args.json:
                print(json.dumps(packet_to_dict(packet), ensure_ascii=False, separators=(",", ":")))
            else:
                print(format_packet_summary(index, packet))

            if args.max_packets > 0 and index >= args.max_packets:
                break
    except socket.timeout:
        print("receive timeout")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
