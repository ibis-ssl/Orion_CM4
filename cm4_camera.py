# このファイルはCM4カメラサーバーとの通信処理を共通化し、
# GUI と CLI の両方から利用できる接続設定、画像取得、座標受信、HSV 更新、ROI からのHSV推定を担当する。
import argparse
import json
import socket
import struct

import cv2
import numpy as np
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


def infer_local_interface_ip(remote_ip, remote_port=API_PORT):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((remote_ip, remote_port))
        return sock.getsockname()[0]
    finally:
        sock.close()


def fetch_frame(machine_no, image_name, timeout=DEFAULT_TIMEOUT):
    config = build_connection_config(machine_no)
    response = requests.get(f"{config['api_server']}/frame/{image_name}", timeout=timeout)
    response.raise_for_status()
    return response.content


def fetch_hsv_params(machine_no, timeout=DEFAULT_TIMEOUT):
    config = build_connection_config(machine_no)
    response = requests.get(f"{config['api_server']}/params", timeout=timeout)
    response.raise_for_status()
    return response.json()


def apply_hsv_params(machine_no, hsv_min, hsv_max, timeout=DEFAULT_TIMEOUT):
    config = build_connection_config(machine_no)
    body = {"hsv_min": hsv_min, "hsv_max": hsv_max}
    response = requests.post(f"{config['api_server']}/params", json=body, timeout=timeout)
    response.raise_for_status()
    return response.text


def estimate_hsv_params_from_frame_bytes(frame_bytes, roi, hue_margin=10, sat_margin=40, val_margin=40):
    np_buffer = np.frombuffer(frame_bytes, dtype=np.uint8)
    image = cv2.imdecode(np_buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("failed to decode frame bytes")

    x, y, width, height = roi
    if width <= 0 or height <= 0:
        raise ValueError("ROI size must be positive")

    image_height, image_width = image.shape[:2]
    x = max(0, min(x, image_width - 1))
    y = max(0, min(y, image_height - 1))
    width = min(width, image_width - x)
    height = min(height, image_height - y)
    if width <= 0 or height <= 0:
        raise ValueError("ROI is outside of frame")

    roi_bgr = image[y:y + height, x:x + width]
    hsv = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV)
    flat = hsv.reshape(-1, 3)

    sat_values = flat[:, 1]
    val_values = flat[:, 2]
    valid_mask = (sat_values >= 40) & (val_values >= 40)
    if np.any(valid_mask):
        flat = flat[valid_mask]

    hue_values = flat[:, 0].astype(np.int16)
    sat_values = flat[:, 1].astype(np.int16)
    val_values = flat[:, 2].astype(np.int16)

    hue_center = int(np.median(hue_values))
    sat_low = int(np.percentile(sat_values, 10))
    sat_high = int(np.percentile(sat_values, 90))
    val_low = int(np.percentile(val_values, 10))
    val_high = int(np.percentile(val_values, 90))

    hsv_min = [
        max(0, hue_center - hue_margin),
        max(0, sat_low - sat_margin),
        max(0, val_low - val_margin),
    ]
    hsv_max = [
        min(180, hue_center + hue_margin),
        min(255, sat_high + sat_margin),
        min(255, val_high + val_margin),
    ]
    return {
        "roi": [x, y, width, height],
        "hsv_min": hsv_min,
        "hsv_max": hsv_max,
        "hue_center": hue_center,
    }


def create_coord_socket(machine_no, receive_timeout=1.0, interface_ip=None):
    config = build_connection_config(machine_no)
    api_ip = config["api_server"].split("//", 1)[1].split(":", 1)[0]
    if interface_ip is None:
        interface_ip = infer_local_interface_ip(api_ip)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", config["mcast_port"]))
    mreq = struct.pack("4s4s", socket.inet_aton(config["mcast_group"]), socket.inet_aton(interface_ip))
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

    get_params_parser = subparsers.add_parser("get-params", help="fetch current HSV parameters")
    get_params_parser.add_argument("--machine-no", type=int, default=DEFAULT_MACHINE_NO)
    get_params_parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)

    coords_parser = subparsers.add_parser("coords", help="receive one multicast coordinate packet")
    coords_parser.add_argument("--machine-no", type=int, default=DEFAULT_MACHINE_NO)
    coords_parser.add_argument("--timeout", type=float, default=1.0)

    roi_parser = subparsers.add_parser("roi-calibrate", help="estimate HSV parameters from one ROI")
    roi_parser.add_argument("--machine-no", type=int, default=DEFAULT_MACHINE_NO)
    roi_parser.add_argument("--image-name", choices=("raw", "mask"), default="raw")
    roi_parser.add_argument("--left", type=int, required=True)
    roi_parser.add_argument("--top", type=int, required=True)
    roi_parser.add_argument("--width", type=int, required=True)
    roi_parser.add_argument("--height", type=int, required=True)
    roi_parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    roi_parser.add_argument("--apply", action="store_true")

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

    if args.subcommand == "get-params":
        print(json.dumps(fetch_hsv_params(args.machine_no, args.timeout), ensure_ascii=False))
        return

    if args.subcommand == "roi-calibrate":
        frame_bytes = fetch_frame(args.machine_no, args.image_name, args.timeout)
        estimated = estimate_hsv_params_from_frame_bytes(
            frame_bytes,
            (args.left, args.top, args.width, args.height),
        )
        print(json.dumps(estimated, ensure_ascii=False))
        if args.apply:
            response_body = apply_hsv_params(
                args.machine_no,
                estimated["hsv_min"],
                estimated["hsv_max"],
                args.timeout,
            )
            if response_body:
                print(response_body)
        return

    print(receive_coord(args.machine_no, args.timeout))


if __name__ == "__main__":
    main()
