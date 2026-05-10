# このファイルは robot feedback 受信 CLI のエントリポイントを担当する。
# multicast 受信とデコードの共通処理は host.lib.feedback に置く。
import argparse
import json
import socket

from host.lib.feedback.packet import decode_robot_feedback_packet
from host.lib.feedback.receiver import (
    DEFAULT_INTERFACE_IP,
    iter_feedback_packets,
    multicast_endpoint,
    open_multicast_socket,
    packet_to_dict,
    format_packet_summary,
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
