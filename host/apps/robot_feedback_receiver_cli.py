# このファイルはOrionMainと4WS Mainの生フィードバック受信CLIを担当する。
# multicast受信と共通パケットのデコード処理はhost.lib.feedbackに置く。
import argparse
import json
import socket

from host.lib.feedback.receiver import (
    MACHINE_TYPES,
    decode_feedback_packet,
    iter_feedback_packets,
    multicast_endpoint,
    open_multicast_socket,
    resolve_feedback_interface_ip,
    packet_to_dict,
    format_packet_summary,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Receive and decode robot feedback multicast packets")
    parser.add_argument("--machine-no", type=int, default=3, help="target machine number N for 192.168.20.(100 + N)")
    parser.add_argument("--multicast-group", default=None, help="override multicast group")
    parser.add_argument("--port", type=int, default=None, help="override UDP port")
    parser.add_argument("--interface-ip", default=None, help="local interface IP for multicast join (default: auto)")
    parser.add_argument("--max-packets", type=int, default=0, help="stop after receiving this many packets")
    parser.add_argument("--receive-timeout", type=float, default=0.0, help="socket receive timeout in seconds")
    parser.add_argument("--json", action="store_true", help="print decoded packets as JSON lines")
    parser.add_argument("--machine-type", choices=MACHINE_TYPES, default="auto", help="decoder (default: auto)")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    default_group, default_port = multicast_endpoint(args.machine_no)
    group = args.multicast_group or default_group
    port = args.port or default_port

    interface_ip = resolve_feedback_interface_ip(args.machine_no, args.interface_ip)
    sock = open_multicast_socket(group, port, interface_ip)
    if args.receive_timeout > 0:
        sock.settimeout(args.receive_timeout)

    print(f"listen multicast={group}:{port} interface={interface_ip}")
    try:
        for index, payload in enumerate(iter_feedback_packets(sock), start=1):
            try:
                packet = decode_feedback_packet(payload, args.machine_type)
            except ValueError as exc:
                if args.json:
                    print(json.dumps({"decode_error": str(exc), "raw": payload.hex()}, separators=(",", ":")))
                else:
                    print(f"#{index} decode_error={exc} raw={payload.hex()}")
            else:
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
