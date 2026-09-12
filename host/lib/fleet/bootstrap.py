# このファイルは初回 SSH 鍵配布(ブートストラップ)を担当する。
# パスワード認証で接続し、公開鍵を authorized_keys に追記した上で、
# 鍵認証での再接続を検証する。
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from host.lib.fleet import ssh

AUTHORIZED_KEYS_RELATIVE = ".ssh/authorized_keys"
DEFAULT_PUBKEY_CANDIDATES = (
    "~/.ssh/id_ed25519.pub",
    "~/.ssh/id_rsa.pub",
)


@dataclass
class BootstrapResult:
    host: object
    ok: bool
    stage: str
    error: str = ""


def find_default_pubkey():
    for candidate in DEFAULT_PUBKEY_CANDIDATES:
        path = Path(candidate).expanduser()
        if path.exists():
            return str(path)
    return None


def _read_pubkey(pubkey_path):
    text = Path(pubkey_path).expanduser().read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"公開鍵ファイルが空です: {pubkey_path}")
    return text


def _append_authorized_key(client, host, pubkey_text):
    """既存の接続 client を使って authorized_keys に鍵を追記する。
    既に同じ鍵が存在する場合は何もしない。戻り値は追記したかどうか。"""
    remote_path = f"/home/{host.ssh_user}/{AUTHORIZED_KEYS_RELATIVE}"
    existing = ssh.run(
        client,
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat ~/.ssh/authorized_keys 2>/dev/null || true",
    )
    if pubkey_text in existing.stdout:
        return False

    new_content = existing.stdout
    if new_content and not new_content.endswith("\n"):
        new_content += "\n"
    new_content += pubkey_text + "\n"
    ssh.sftp_put_text_atomic(client, new_content, remote_path, mode=0o600)
    return True


def bootstrap_one(host, password, pubkey_path):
    try:
        pubkey_text = _read_pubkey(pubkey_path)
    except (OSError, ValueError) as exc:
        return BootstrapResult(host=host, ok=False, stage="pubkey", error=str(exc))

    try:
        client = ssh.connect(host, password=password, allow_unknown_host=True)
    except Exception as exc:
        return BootstrapResult(host=host, ok=False, stage="connect_password", error=str(exc))

    try:
        added = _append_authorized_key(client, host, pubkey_text)
    except Exception as exc:
        return BootstrapResult(host=host, ok=False, stage="append_key", error=str(exc))
    finally:
        client.close()

    try:
        verify_client = ssh.connect(host, allow_unknown_host=True)
        verify_client.close()
    except Exception as exc:
        return BootstrapResult(host=host, ok=False, stage="verify_key_auth", error=str(exc))

    return BootstrapResult(host=host, ok=True, stage="key_added" if added else "already_present")


def add_key_only(host, pubkey_path):
    """既に鍵認証が通っているホストへ追加の公開鍵を配布する(push-config 用)。"""
    try:
        pubkey_text = _read_pubkey(pubkey_path)
    except (OSError, ValueError) as exc:
        return BootstrapResult(host=host, ok=False, stage="pubkey", error=str(exc))

    try:
        client = ssh.connect(host, allow_unknown_host=False)
    except Exception as exc:
        return BootstrapResult(host=host, ok=False, stage="connect", error=str(exc))

    try:
        added = _append_authorized_key(client, host, pubkey_text)
        return BootstrapResult(host=host, ok=True, stage="key_added" if added else "already_present")
    except Exception as exc:
        return BootstrapResult(host=host, ok=False, stage="append_key", error=str(exc))
    finally:
        client.close()


def bootstrap_fleet(hosts, password, pubkey_path, max_workers=8):
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(bootstrap_one, h, password, pubkey_path): h for h in hosts}
        for future in as_completed(futures):
            host = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                results.append(BootstrapResult(host=host, ok=False, stage="unexpected", error=str(exc)))
    return sorted(results, key=lambda r: r.host.ip)
