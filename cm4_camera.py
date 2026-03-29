# このファイルはCM4カメラサーバーとの通信処理を共通化し、
# GUI と CLI の両方から利用できる接続設定、画像取得、座標受信、HSV 更新を担当する。
import argparse
import json
import socket
import struct

import requests

API_PORT = 8001
DEFAULT_TIMEOUT = 1.0
DEFAULT_MACHINE_NO = 3


def build_connection_config(machine_no, api_port=API_PORT):
    if not 0 <= machine_no <= 155:
        raise ValueError("machine number must be between 0 and 155")

    ip_last_octet = 100 + machine_no
    return {
        "machine_no": machine_no,
        "api_server": f"http://192.168.20.{ip_last_octet}:{api_port}",
        "mcast_group": f"224.5.10.{ip_last_octet}",
        "mcast_port": 5100 + machine_no,
    }


def fetch_frame(machine_no, image_name, timeout=DEFAULT_TIMEOUT):
    config = build_connection_config(machine_no)
    response = requests.get(f"{config['api_server']}/frame/{image_name}", timeout=timeout)
    response.raise_for_status()
    return response.content


def apply_hsv_params(machine_no, hsv_min, hsv_max, timeout=DEFAULT_TIMEOUT):
    config = build_connection_config(machine_no)
    body = {"hsv_min": hsv_min, "hsv_max": hsv_max}
    response = requests.post(f"{config['api_server']}/params", json=body, timeout=timeout)
    response.raise_for_status()
    return response.text


def create_coord_socket(machine_no, receive_timeout=1.0):
    config = build_connection_config(machine_no)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", config["mcast_port"]))
    mreq = struct.pack("4s4s", socket.inet_aton(config["mcast_group"]), socket.inet_aton("0.0.0.0"))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    sock.settimeout(receive_timeout)
    return sock


def receive_coord(machine_no, receive_timeout=1.0):
    sock = create_coord_socket(machine_no, receive_timeout)
    try:
        data, _address = sock.recvfrom(1024)
        return data.decode()
    finally:
        sock.close()


def build_parser():
    parser = argparse.ArgumentParser(description="CM4 camera client")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    config_parser = subparsers.add_parser("config", help="print connection config")
    config_parser.add_argument("--machine-no", type=int, default=DEFAULT_MACHINE_NO)

    frame_parser = subparsers.add_parser("frame", help="fetch one frame and save it")
    frame_parser.add_argument("--machine-no", type=int, default=DEFAULT_MACHINE_NO)
    frame_parser.add_argument("--image-name", choices=("raw", "mask"), required=True)
    frame_parser.add_argument("--output", required=True)
    frame_parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)

    params_parser = subparsers.add_parser("params", help="post HSV parameters")
    params_parser.add_argument("--machine-no", type=int, default=DEFAULT_MACHINE_NO)
    params_parser.add_argument("--hsv-min", nargs=3, type=int, required=True)
    params_parser.add_argument("--hsv-max", nargs=3, type=int, required=True)
    params_parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)

    coords_parser = subparsers.add_parser("coords", help="receive one multicast coordinate packet")
    coords_parser.add_argument("--machine-no", type=int, default=DEFAULT_MACHINE_NO)
    coords_parser.add_argument("--timeout", type=float, default=1.0)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.subcommand == "config":
        print(json.dumps(build_connection_config(args.machine_no), ensure_ascii=False))
        return

    if args.subcommand == "frame":
        frame_bytes = fetch_frame(args.machine_no, args.image_name, args.timeout)
        with open(args.output, "wb") as file:
            file.write(frame_bytes)
        print(args.output)
        return

    if args.subcommand == "params":
        response_body = apply_hsv_params(args.machine_no, args.hsv_min, args.hsv_max, args.timeout)
        if response_body:
            print(response_body)
        return

    print(receive_coord(args.machine_no, args.timeout))


if __name__ == "__main__":
    main()
