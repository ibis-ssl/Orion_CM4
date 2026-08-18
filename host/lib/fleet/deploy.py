# このファイルは OTA デプロイ本体を担当する。
# git archive でリポジトリの tarball を作成し、対象デバイスへ SFTP 転送、
# 既存ツリーへ展開、cm4/update.sh によるブリッジ再ビルド・サービス再起動、
# デプロイ済みバージョン記録までを一台ずつ・並列に行う。
import datetime
import json
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from host.lib.cm4_control_client import fetch_status
from host.lib.fleet import proxy, ssh

REMOTE_REPO_RELATIVE = "Orion_CM4"
REMOTE_INCOMING_RELATIVE = ".orion_deploy/incoming"
REMOTE_VERSION_RELATIVE = ".orion_deploy/deployed_version.json"
UPDATE_SCRIPT_RELATIVE = "cm4/update.sh"
CAMERA_SOURCE_PREFIX = "cm4/camera/"


@dataclass
class DeployResult:
    host: object
    ok: bool
    stage: str
    commit: str = ""
    skipped: bool = False
    error: str = ""
    warning: str = ""


def _run_git(args, cwd=None):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def resolve_commit(ref, repo_dir=None):
    return _run_git(["rev-parse", ref], cwd=repo_dir).strip()


def has_dirty_worktree(repo_dir=None):
    return bool(_run_git(["status", "--porcelain"], cwd=repo_dir).strip())


def build_archive(ref="HEAD", allow_dirty=False, repo_dir=None, scratch_dir=None):
    """(tarball パス, commit sha, dirty フラグ) を返す。"""
    dirty = has_dirty_worktree(repo_dir)
    if dirty and not allow_dirty:
        raise RuntimeError("未コミットの変更があります。--allow-dirty を指定するか、先にコミットしてください。")

    archive_ref = ref
    if dirty and allow_dirty:
        stash_sha = _run_git(["stash", "create"], cwd=repo_dir).strip()
        if stash_sha:
            archive_ref = stash_sha

    commit_sha = resolve_commit(archive_ref, repo_dir)

    scratch_dir = Path(scratch_dir) if scratch_dir else Path(tempfile.gettempdir())
    scratch_dir.mkdir(parents=True, exist_ok=True)
    archive_path = scratch_dir / f"orion-{commit_sha[:12]}.tar.gz"
    _run_git(["archive", "--format=tar.gz", "-o", str(archive_path), archive_ref], cwd=repo_dir)
    return archive_path, commit_sha, dirty


def _commit_resolvable(commit, repo_dir=None):
    """commit がローカルリポジトリで解決可能なコミットかどうかを返す。
    --allow-dirty でデプロイした際の deployed_version.json には
    `git stash create` が作る、どの ref からも到達不能なコミットが記録されうる。
    それらは git gc で回収され得るため、以後の diff 系処理では
    「解決できない古い commit」を「old_commit が無い」と同様に安全側で扱う。"""
    if not commit:
        return False
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=repo_dir, capture_output=True, check=False,
    )
    return result.returncode == 0


def deleted_paths_between(old_commit, new_commit, repo_dir=None):
    """old_commit -> new_commit の間で削除されたファイルの一覧(リポジトリ相対パス)を返す。
    old_commit が None、または解決不能(初回デプロイ・GC 済み等)の場合は
    安全側に倒して空リストを返す。"""
    if not _commit_resolvable(old_commit, repo_dir):
        return []
    output = _run_git(["diff", "--name-status", "--diff-filter=D", old_commit, new_commit], cwd=repo_dir)
    paths = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        _, path = line.split("\t", 1)
        paths.append(path)
    return paths


def camera_source_changed(old_commit, new_commit, repo_dir=None):
    """cm4/camera/ 配下のソースが old_commit -> new_commit の間で変更されたかを返す。
    old_commit が None、または解決不能(初回デプロイ・GC 済み等)の場合は判定できないため False。"""
    if not _commit_resolvable(old_commit, repo_dir):
        return False
    output = _run_git(["diff", "--name-only", old_commit, new_commit], cwd=repo_dir)
    return any(line.strip().startswith(CAMERA_SOURCE_PREFIX) for line in output.splitlines())


def read_deployed_commit(client, host):
    remote_path = f"/home/{host.ssh_user}/{REMOTE_VERSION_RELATIVE}"
    text = ssh.sftp_get_text(client, remote_path)
    if not text:
        return None
    try:
        return json.loads(text).get("commit")
    except ValueError:
        return None


def _build_version_payload(commit_sha, ref, dirty):
    payload = {
        "commit": commit_sha,
        "ref": ref,
        "dirty": dirty,
        "deployed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _verify_reachable(host, attempts=5, delay=1.5):
    """再起動直後は uvicorn の起動が完了するまで数秒かかりうるため、
    短いリトライを行ってから Offline を確定させる。"""
    last = None
    for _ in range(attempts):
        last = fetch_status(host.ip, host.control_port, timeout=2.0)
        if last.get("state") != "Offline":
            return last
        time.sleep(delay)
    return last


def deploy_one(host, archive_path, commit_sha, ref, dirty, *, force=False,
                rebuild_camera=False, repo_dir=None, local_http_port=None,
                proxy_remote_port=proxy.DEFAULT_PORT):
    if not force:
        current = fetch_status(host.ip, host.control_port)
        if current.get("state") == "Running":
            return DeployResult(
                host=host, ok=True, stage="skip", commit=commit_sha,
                skipped=True, error="稼働中のためスキップしました(--force で上書き可能)",
            )

    try:
        client = ssh.connect(host, allow_unknown_host=False)
    except Exception as exc:
        return DeployResult(host=host, ok=False, stage="connect", commit=commit_sha, error=str(exc))

    tunnel = None
    try:
        remote_home = f"/home/{host.ssh_user}"
        remote_repo = f"{remote_home}/{REMOTE_REPO_RELATIVE}"
        remote_tarball = f"{remote_home}/{REMOTE_INCOMING_RELATIVE}/{archive_path.name}"

        try:
            ssh.sftp_put_atomic(client, archive_path, remote_tarball)
        except Exception as exc:
            return DeployResult(host=host, ok=False, stage="transfer", commit=commit_sha, error=str(exc))

        extract = ssh.run(client, f"tar xzf '{remote_tarball}' -C '{remote_repo}'")
        if not extract.ok:
            return DeployResult(host=host, ok=False, stage="extract", commit=commit_sha,
                                 error=extract.stderr.strip() or extract.stdout.strip())
        ssh.run(client, f"rm -f '{remote_tarball}'")

        try:
            old_commit = read_deployed_commit(client, host)
            deleted = deleted_paths_between(old_commit, commit_sha, repo_dir)
            camera_changed = camera_source_changed(old_commit, commit_sha, repo_dir)
        except Exception as exc:
            return DeployResult(host=host, ok=False, stage="diff", commit=commit_sha, error=str(exc))

        if deleted:
            quoted = " ".join(f"'{remote_repo}/{path}'" for path in deleted)
            ssh.run(client, f"rm -f {quoted}")

        update_cmd = f"bash '{remote_repo}/{UPDATE_SCRIPT_RELATIVE}'"
        if rebuild_camera:
            try:
                tunnel = proxy.open_tunnel(host, proxy_remote_port, local_http_port)
            except Exception as exc:
                return DeployResult(host=host, ok=False, stage="proxy", commit=commit_sha, error=str(exc))
            proxy_url = f"http://127.0.0.1:{proxy_remote_port}"
            update_cmd = f"http_proxy={proxy_url} https_proxy={proxy_url} {update_cmd} --rebuild-camera"
        update_result = ssh.run(client, update_cmd, timeout=300.0)
        if not update_result.ok:
            return DeployResult(host=host, ok=False, stage="build", commit=commit_sha,
                                 error=update_result.stderr.strip() or update_result.stdout.strip())

        version_payload = _build_version_payload(commit_sha, ref, dirty)
        ssh.sftp_put_text_atomic(client, version_payload, f"{remote_home}/{REMOTE_VERSION_RELATIVE}")

        verify = _verify_reachable(host)
        if verify.get("state") == "Offline":
            return DeployResult(host=host, ok=False, stage="verify", commit=commit_sha,
                                 error="デプロイ後に status API へ到達できません")

        warning = ""
        if camera_changed and not rebuild_camera:
            warning = (
                "cm4/camera/ に変更がありますが --rebuild-camera が未指定のため、"
                "カメラサーバーのバイナリは更新されていません"
            )

        return DeployResult(host=host, ok=True, stage="done", commit=commit_sha, warning=warning)
    finally:
        if tunnel is not None:
            tunnel.close()
        client.close()


def deploy_fleet(hosts, *, ref="HEAD", allow_dirty=False, force=False,
                  rebuild_camera=False, repo_dir=None, max_workers=8):
    archive_path, commit_sha, dirty = build_archive(ref, allow_dirty, repo_dir)

    # --rebuild-camera 時、PC 側の HTTP プロキシは全ホストで 1 つ共有する
    # (デバイスごとに別プロセスを立てる必要はないため)。
    local_proxy_server = None
    local_http_port = None
    if rebuild_camera:
        local_proxy_server, local_http_port = proxy.start_local_proxy()

    try:
        results = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    deploy_one, host, archive_path, commit_sha, ref, dirty,
                    force=force, rebuild_camera=rebuild_camera, repo_dir=repo_dir,
                    local_http_port=local_http_port,
                ): host
                for host in hosts
            }
            for future in as_completed(futures):
                host = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    results.append(DeployResult(host=host, ok=False, stage="unexpected",
                                                 commit=commit_sha, error=str(exc)))
        return sorted(results, key=lambda r: r.host.ip)
    finally:
        if local_proxy_server is not None:
            local_proxy_server.shutdown()
