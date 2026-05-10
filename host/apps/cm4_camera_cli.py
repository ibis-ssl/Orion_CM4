# このファイルは CM4 カメラ CLI のエントリポイントを担当する。
# 通信・画像処理の共通処理は host.lib.cm4_camera_client に置く。
import argparse
import json

from host.lib.cm4_camera_client import (
    DEFAULT_MACHINE_NO,
    DEFAULT_TIMEOUT,
    apply_hsv_params,
    build_debug_connection_config,
    estimate_hsv_params_from_frame_bytes,
    fetch_frame,
    fetch_hsv_params,
    receive_coord,
)


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
        print(json.dumps(build_debug_connection_config(args.machine_no), ensure_ascii=False))
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
