# このファイルは CM4 制御 CLI のエントリポイントを担当する。
# 通信処理は host.lib.cm4_control_client に置き、ここでは引数処理と標準出力だけを行う。
import argparse

from host.lib.cm4_control_client import (
    DEFAULT_IP_LIST,
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    fetch_status,
    fetch_statuses,
    send_command,
)


def build_parser():
    parser = argparse.ArgumentParser(description="CM4 control server client")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    status_parser = subparsers.add_parser("status", help="fetch one CM4 status")
    status_parser.add_argument("--ip", required=True)
    status_parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    status_parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)

    scan_parser = subparsers.add_parser("scan", help="scan multiple CM4 statuses")
    scan_parser.add_argument("--ips", nargs="*", default=DEFAULT_IP_LIST)
    scan_parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    scan_parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    scan_parser.add_argument("--workers", type=int, default=10)

    for command_name in ("start", "stop"):
        command_parser = subparsers.add_parser(command_name, help=f"send {command_name} command")
        command_parser.add_argument("--ip", required=True)
        command_parser.add_argument("--port", type=int, default=DEFAULT_PORT)
        command_parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.subcommand == "status":
        result = fetch_status(args.ip, args.port, args.timeout)
        print(f"{result['ip']}: {result['state']}")
        if "error" in result:
            print(result["error"])
        return

    if args.subcommand == "scan":
        for result in fetch_statuses(args.ips, args.port, args.timeout, args.workers):
            print(f"{result['ip']}: {result['state']}")
        return

    result = send_command(args.ip, args.subcommand, args.port, args.timeout)
    print(f"{result['ip']}: {result['command']} -> {result['status_code']}")
    if result["body"]:
        print(result["body"])


if __name__ == "__main__":
    main()
