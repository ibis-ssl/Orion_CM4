# このファイルはforward_robot_feedback.cppのUDP multicastを受信し、
# Windows/Linux 共通でデコード結果をrerun-sdkへ時系列プロットするCLIツールを担当する。
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import socket
import struct
from typing import Iterable

import rerun as rr
import rerun.blueprint as rrb

from robot_feedback_packet import PACKET_SIZE, TX_VALUE_LABELS, decode_robot_feedback_packet


def build_multicast_config(machine_no: int) -> tuple[str, int]:
    return f"224.5.20.{machine_no}", 50000 + machine_no


def create_multicast_receiver(multicast_group: str, port: int, interface_ip: str) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        sock.bind(("", port))
    except OSError:
        sock.bind((interface_ip, port))

    mreq = struct.pack("4s4s", socket.inet_aton(multicast_group), socket.inet_aton(interface_ip))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    return sock


def build_blueprint() -> rrb.Blueprint:
    return rrb.Blueprint(
        rrb.Horizontal(
            rrb.Spatial2DView(
                origin="/robot_feedback/camera",
                name="Camera 2D",
            ),
            rrb.Vertical(
                rrb.TimeSeriesView(origin="/robot_feedback/status", name="Status"),
                rrb.TimeSeriesView(origin="/robot_feedback/camera_timeseries", name="Camera"),
                rrb.TimeSeriesView(origin="/robot_feedback/tx_values", name="TX Values"),
                rrb.TimeSeriesView(origin="/robot_feedback/power", name="Power"),
            ),
        ),
        collapse_panels=False,
    )


def log_scalar(path: str, value: float | int) -> None:
    rr.log(path, rr.Scalars([float(value)]))


def log_packet(packet_index: int, packet_bytes: bytes) -> None:
    packet = decode_robot_feedback_packet(packet_bytes)

    rr.set_time("packet_index", sequence=packet_index)
    rr.set_time("wall_clock", timestamp=datetime.now(timezone.utc))

    log_scalar("/robot_feedback/status/check_counter", packet.check_counter)
    log_scalar("/robot_feedback/status/checksum", packet.checksum)
    log_scalar("/robot_feedback/status/checksum_valid", int(packet.is_checksum_valid))
    log_scalar("/robot_feedback/status/sync_valid", int(packet.is_sync_valid))

    log_scalar("/robot_feedback/power/battery_voltage_bldc_right", packet.battery_voltage_bldc_right)
    log_scalar("/robot_feedback/power/capacitor_boost_voltage", packet.capacitor_boost_voltage)

    log_scalar("/robot_feedback/status/imu_yaw_deg", packet.imu_yaw_deg)
    log_scalar("/robot_feedback/status/diff_angle_deg", packet.diff_angle_deg)
    log_scalar("/robot_feedback/status/vision_based_position_x", packet.vision_based_position_x)
    log_scalar("/robot_feedback/status/vision_based_position_y", packet.vision_based_position_y)
    log_scalar("/robot_feedback/status/global_odom_speed_x", packet.global_odom_speed_x)
    log_scalar("/robot_feedback/status/global_odom_speed_y", packet.global_odom_speed_y)
    log_scalar("/robot_feedback/status/current_error_id", packet.current_error_id)
    log_scalar("/robot_feedback/status/current_error_info", packet.current_error_info)
    log_scalar("/robot_feedback/status/current_error_value", packet.current_error_value)
    log_scalar("/robot_feedback/status/kick_state", packet.kick_state)

    for index, value in enumerate(packet.motor_current):
        log_scalar(f"/robot_feedback/power/motor_current_{index}", value)

    for index, value in enumerate(packet.temp_motor):
        log_scalar(f"/robot_feedback/status/temp_motor_{index}", value)
    log_scalar("/robot_feedback/status/temp_fet", packet.temp_fet)
    log_scalar("/robot_feedback/status/temp_coil_0", packet.temp_coil[0])
    log_scalar("/robot_feedback/status/temp_coil_1", packet.temp_coil[1])

    for index, value in enumerate(packet.ball_detection):
        log_scalar(f"/robot_feedback/status/ball_detection_{index}", value)
    log_scalar("/robot_feedback/status/ball_detection_extra", packet.ball_detection_extra)

    log_scalar("/robot_feedback/camera_timeseries/camera_pos_x", packet.camera_pos_x)
    log_scalar("/robot_feedback/camera_timeseries/camera_pos_y", packet.camera_pos_y)
    log_scalar("/robot_feedback/camera_timeseries/camera_radius", packet.camera_radius)
    log_scalar("/robot_feedback/camera_timeseries/camera_fps", packet.camera_fps)

    rr.log(
        "/robot_feedback/camera/ball_center",
        rr.Points2D(
            [[packet.camera_pos_x, packet.camera_pos_y]],
            radii=[max(2.0, packet.camera_radius / 2.0)],
            colors=[[255, 128, 0, 255]],
            labels=[f"fps={packet.camera_fps}"],
            show_labels=True,
        ),
    )
    rr.log("/robot_feedback/camera", rr.ViewCoordinates.RIGHT_HAND_Y_DOWN, static=True)

    for label, value in zip(TX_VALUE_LABELS, packet.tx_value_array):
        log_scalar(f"/robot_feedback/tx_values/{label}", value)


def receive_packets(sock: socket.socket) -> Iterable[bytes]:
    while True:
        payload, _address = sock.recvfrom(4096)
        if len(payload) != PACKET_SIZE:
            continue
        yield payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Receive robot feedback multicast and plot with Rerun")
    parser.add_argument("--machine-no", type=int, default=3, help="target machine number")
    parser.add_argument("--multicast-group", default=None, help="override multicast group")
    parser.add_argument("--port", type=int, default=None, help="override UDP port")
    parser.add_argument("--interface-ip", default="0.0.0.0", help="local interface IP for multicast join")
    parser.add_argument("--application-id", default="orion_robot_feedback", help="Rerun application id")
    parser.add_argument("--max-packets", type=int, default=0, help="stop after receiving this many packets")
    parser.add_argument("--no-spawn", action="store_true", help="do not spawn a local Rerun viewer")
    parser.add_argument("--receive-timeout", type=float, default=0.0, help="socket receive timeout in seconds")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    default_group, default_port = build_multicast_config(args.machine_no)
    multicast_group = args.multicast_group or default_group
    port = args.port or default_port

    rr.init(args.application_id, spawn=not args.no_spawn, default_blueprint=build_blueprint())

    sock = create_multicast_receiver(multicast_group, port, args.interface_ip)
    if args.receive_timeout > 0:
        sock.settimeout(args.receive_timeout)
    print(f"listen multicast={multicast_group}:{port} interface={args.interface_ip}")

    try:
        for packet_index, packet_bytes in enumerate(receive_packets(sock), start=1):
            log_packet(packet_index, packet_bytes)
            print(f"received packet_index={packet_index}")
            if args.max_packets > 0 and packet_index >= args.max_packets:
                break
    except socket.timeout:
        print("receive timeout")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
