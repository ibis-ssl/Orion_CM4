# このファイルは稼働状態(running/stopped/offline)とデプロイ済み commit を
# マージしたフリートステータスを提供する。
from concurrent.futures import ThreadPoolExecutor, as_completed

from host.lib.cm4_control_client import fetch_status
from host.lib.fleet import ssh
from host.lib.fleet.deploy import read_deployed_commit


def _fetch_one(host):
    control_state = fetch_status(host.ip, host.control_port)
    commit = None
    try:
        client = ssh.connect(host, allow_unknown_host=False, timeout=5.0)
        try:
            commit = read_deployed_commit(client, host)
        finally:
            client.close()
    except Exception:
        commit = None

    return {
        "machine_no": host.machine_no,
        "ip": host.ip,
        "state": control_state.get("state"),
        "commit": commit,
    }


def fleet_status(hosts, max_workers=8):
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_fetch_one, host) for host in hosts]
        for future in as_completed(futures):
            results.append(future.result())
    return sorted(results, key=lambda r: r["ip"])
