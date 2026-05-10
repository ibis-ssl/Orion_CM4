# このファイルはホスト側から CM4 制御 API を呼び出す共通ライブラリを担当する。
# 複数 CM4 の状態確認と、起動/停止コマンド送信を提供する。
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
