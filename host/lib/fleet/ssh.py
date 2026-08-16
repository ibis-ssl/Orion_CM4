# このファイルは paramiko ベースの SSH/SFTP ラッパーを担当する。
# 接続確立、コマンド実行、ファイルの原子的転送、パスワード取得を提供する。
import getpass
import os
import posixpath
import uuid
from dataclasses import dataclass

import paramiko

PASSWORD_ENV_VAR = "ORION_FLEET_PASSWORD"


def get_password(prompt="SSH password: "):
    password = os.environ.get(PASSWORD_ENV_VAR)
    if password:
        return password
    return getpass.getpass(prompt)


@dataclass
class CommandResult:
    exit_status: int
    stdout: str
    stderr: str

    @property
    def ok(self):
        return self.exit_status == 0


def connect(host, *, password=None, key_filename=None, timeout=10.0, allow_unknown_host=False):
    """paramiko.SSHClient へ接続する。

    password 指定時はパスワード認証、それ以外は key_filename または
    ssh-agent/デフォルト鍵(~/.ssh/id_*)による鍵認証を使う。

    allow_unknown_host=True の場合のみ未知のホスト鍵を自動受理し、
    ~/.ssh/known_hosts へ永続化する(bootstrap 用途)。それ以外は
    ~/.ssh/known_hosts に登録済みのホスト鍵のみ許可する。
    """
    client = paramiko.SSHClient()
    known_hosts_path = os.path.expanduser("~/.ssh/known_hosts")
    client.load_system_host_keys()
    if os.path.exists(known_hosts_path):
        client.load_host_keys(known_hosts_path)

    if allow_unknown_host:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    else:
        client.set_missing_host_key_policy(paramiko.RejectPolicy())

    connect_kwargs = dict(
        hostname=host.ip,
        port=host.ssh_port,
        username=host.ssh_user,
        timeout=timeout,
        banner_timeout=timeout,
        auth_timeout=timeout,
    )
    if password is not None:
        connect_kwargs.update(password=password, look_for_keys=False, allow_agent=False)
    elif key_filename is not None:
        connect_kwargs.update(key_filename=key_filename, look_for_keys=False, allow_agent=False)

    client.connect(**connect_kwargs)

    if allow_unknown_host:
        try:
            os.makedirs(os.path.dirname(known_hosts_path), exist_ok=True)
            client.save_host_keys(known_hosts_path)
        except OSError:
            pass

    return client


def run(client, command, *, timeout=60.0):
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    stdin.close()
    exit_status = stdout.channel.recv_exit_status()
    return CommandResult(
        exit_status=exit_status,
        stdout=stdout.read().decode(errors="replace"),
        stderr=stderr.read().decode(errors="replace"),
    )


def _mkdir_p(sftp, remote_dir):
    if remote_dir in ("", "/", "."):
        return
    try:
        sftp.stat(remote_dir)
        return
    except FileNotFoundError:
        pass
    _mkdir_p(sftp, posixpath.dirname(remote_dir))
    try:
        sftp.mkdir(remote_dir)
    except OSError:
        pass  # 並行実行時に他のホストが同名ディレクトリを作った場合を許容


def sftp_put_atomic(client, local_path, remote_path, mode=0o644):
    """一時ファイル名で put してから rename することで、転送中断時に
    破損した転送先ファイルを残さない。"""
    sftp = client.open_sftp()
    try:
        tmp_remote = f"{remote_path}.new-{uuid.uuid4().hex[:8]}"
        remote_dir = posixpath.dirname(remote_path)
        _mkdir_p(sftp, remote_dir)
        sftp.put(str(local_path), tmp_remote)
        sftp.chmod(tmp_remote, mode)
        sftp.posix_rename(tmp_remote, remote_path)
    finally:
        sftp.close()


def sftp_put_text_atomic(client, text, remote_path, mode=0o644):
    sftp = client.open_sftp()
    try:
        tmp_remote = f"{remote_path}.new-{uuid.uuid4().hex[:8]}"
        remote_dir = posixpath.dirname(remote_path)
        _mkdir_p(sftp, remote_dir)
        with sftp.open(tmp_remote, "w") as f:
            f.write(text)
        sftp.chmod(tmp_remote, mode)
        sftp.posix_rename(tmp_remote, remote_path)
    finally:
        sftp.close()


def sftp_get_text(client, remote_path):
    sftp = client.open_sftp()
    try:
        with sftp.open(remote_path, "r") as f:
            data = f.read()
        return data.decode() if isinstance(data, bytes) else data
    except FileNotFoundError:
        return None
    finally:
        sftp.close()
