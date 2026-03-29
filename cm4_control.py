# このファイルはCM4制御サーバーへの起動停止と状態確認を共通化し、
# CLI と GUI の両方から利用できる制御クライアント機能を担当する。
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

DEFAULT_PORT = 8000
DEFAULT_TIMEOUT = 0.5
DEFAULT_IP_LIST = [f"192.168.20.{i}" for i in range(100, 113)]


def build_base_url(ip, port=DEFAULT_PORT):
    return f"http://{ip}:{port}"


def send_command(ip, command, port=DEFAULT_PORT, timeout=DEFAULT_TIMEOUT):
    response = requests.post(f"{build_base_url(ip, port)}/{command}", timeout=timeout)
    response.raise_for_status()
    return {
        "ip": ip,
        "command": command,
        "status_code": response.status_code,
        "body": response.text,
    }


def fetch_status(ip, port=DEFAULT_PORT, timeout=DEFAULT_TIMEOUT):
    try:
        response = requests.get(f"{build_base_url(ip, port)}/status", timeout=timeout)
        response.raise_for_status()
        running = response.json().get("running", False)
        return {"ip": ip, "state": "Running" if running else "Stopped"}
    except requests.RequestException as exc:
        return {"ip": ip, "state": "Offline", "error": str(exc)}


def fetch_statuses(ip_list, port=DEFAULT_PORT, timeout=DEFAULT_TIMEOUT, max_workers=10):
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(fetch_status, ip, port, timeout) for ip in ip_list]
        for future in as_completed(futures):
            results.append(future.result())
    return sorted(results, key=lambda item: item["ip"])


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
