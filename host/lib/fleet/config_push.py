# このファイルは複数台への設定ファイル・SSH 公開鍵配布を担当する。
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from host.lib.fleet import bootstrap, ssh

# 機体固有ファイル(全台一括配布を禁止する対象)
MACHINE_SPECIFIC_TARGETS = {"hsv"}

# target ごとの配布先(ホームディレクトリからの相対パス)。--remote-path で上書き可能。
TARGET_REMOTE_PATHS = {
    "env": ".orion_deploy/env",
    "hsv": "Orion_CM4/cm4/runtime/cam_server_v3_hsv.json",
}


@dataclass
class PushResult:
    host: object
    ok: bool
    error: str = ""


def push_file_one(host, local_path, remote_path):
    try:
        client = ssh.connect(host, allow_unknown_host=False)
    except Exception as exc:
        return PushResult(host=host, ok=False, error=str(exc))

    try:
        ssh.sftp_put_atomic(client, local_path, f"/home/{host.ssh_user}/{remote_path}")
        return PushResult(host=host, ok=True)
    except Exception as exc:
        return PushResult(host=host, ok=False, error=str(exc))
    finally:
        client.close()


def push_config_fleet(hosts, target, local_path, remote_path=None, *, max_workers=8):
    if target in MACHINE_SPECIFIC_TARGETS and len(hosts) != 1:
        raise ValueError(
            f"target={target!r} は機体固有ファイルのため、対象を1台に限定してください"
            "(--machines で単一機体を指定)"
        )

    resolved_remote_path = remote_path or TARGET_REMOTE_PATHS.get(target)
    if not resolved_remote_path:
        raise ValueError(f"target={target!r} には --remote-path の指定が必要です")

    local_path = Path(local_path)
    if not local_path.exists():
        raise ValueError(f"配布元ファイルが見つかりません: {local_path}")

    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(push_file_one, host, local_path, resolved_remote_path): host
            for host in hosts
        }
        for future in as_completed(futures):
            host = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                results.append(PushResult(host=host, ok=False, error=str(exc)))
    return sorted(results, key=lambda r: r.host.ip)


def add_authorized_key_fleet(hosts, pubkey_path, *, max_workers=8):
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(bootstrap.add_key_only, host, pubkey_path): host for host in hosts}
        for future in as_completed(futures):
            host = futures[future]
            try:
                bootstrap_result = future.result()
                results.append(PushResult(host=host, ok=bootstrap_result.ok, error=bootstrap_result.error))
            except Exception as exc:
                results.append(PushResult(host=host, ok=False, error=str(exc)))
    return sorted(results, key=lambda r: r.host.ip)
