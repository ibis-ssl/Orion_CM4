# このファイルは CM4 フリート管理 CLI のエントリポイントを担当する。
# 通信処理は host.lib.fleet に置き、ここでは引数処理と標準出力だけを行う。
import argparse
import sys
import time

from host.lib.fleet import bootstrap, config_push, deploy, inventory, proxy, report, ssh
from host.lib.fleet.status import fleet_status


def _resolve_hosts(args):
    hosts = inventory.load_inventory(args.inventory)
    return inventory.resolve_targets(hosts, all_=args.all, machines=args.machines, ips=args.ips)


def _add_target_args(parser):
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="全機体を対象にする")
    group.add_argument("--machines", help='機体番号を指定する(例: "0,1,5-8")')
    group.add_argument("--ips", help="IP アドレスをカンマ区切りで指定する")
    parser.add_argument("--inventory", help="インベントリ JSON ファイルへのパス(省略時はデフォルト規則)")


def build_parser():
    parser = argparse.ArgumentParser(description="CM4 fleet management CLI")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    status_parser = subparsers.add_parser("status", help="fleet status (running state + deployed commit)")
    _add_target_args(status_parser)

    bootstrap_parser = subparsers.add_parser("bootstrap", help="deploy this PC's SSH public key")
    _add_target_args(bootstrap_parser)
    bootstrap_parser.add_argument("--pubkey-file", help="配布する公開鍵ファイル(省略時は既定の鍵を自動検出)")

    deploy_parser = subparsers.add_parser("deploy", help="OTA deploy current repository state")
    _add_target_args(deploy_parser)
    deploy_parser.add_argument("--ref", default="HEAD", help="デプロイする git ref(デフォルト: HEAD)")
    deploy_parser.add_argument("--allow-dirty", action="store_true", help="未コミットの変更を許可する")
    deploy_parser.add_argument("--force", action="store_true", help="稼働中でも上書きする")
    deploy_parser.add_argument(
        "--rebuild-camera", action="store_true",
        help="カメラサーバーを再ビルドする(デバイス側に一時的なネットワーク到達性が必要)",
    )

    push_parser = subparsers.add_parser("push-config", help="push a config file or SSH public key")
    _add_target_args(push_parser)
    push_parser.add_argument("--target", required=True, choices=["env", "hsv", "authorized-keys", "file"])
    push_parser.add_argument("--file", required=True, help="配布するローカルファイル")
    push_parser.add_argument("--remote-path", help="配布先の相対パス(ホームディレクトリ基準)")

    proxy_parser = subparsers.add_parser(
        "proxy", help="open on-demand HTTP proxy tunnels for OTA/debug internet access",
    )
    _add_target_args(proxy_parser)
    proxy_parser.add_argument(
        "--port", type=int, default=proxy.DEFAULT_PORT,
        help=f"デバイス側の待受ポート(デフォルト: {proxy.DEFAULT_PORT})",
    )

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    try:
        _dispatch(args)
    except ValueError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        sys.exit(1)


def _dispatch(args):
    if args.subcommand == "status":
        hosts = _resolve_hosts(args)
        for row in fleet_status(hosts):
            commit = row["commit"][:12] if row["commit"] else "-"
            print(f"machine_no={row['machine_no']} ip={row['ip']}: {row['state']} commit={commit}")
        return

    if args.subcommand == "bootstrap":
        hosts = _resolve_hosts(args)
        pubkey_path = args.pubkey_file or bootstrap.find_default_pubkey()
        if not pubkey_path:
            print("公開鍵が見つかりません。--pubkey-file で指定してください。", file=sys.stderr)
            sys.exit(1)
        password = ssh.get_password(f"{len(hosts)}台に対する SSH パスワード: ")
        results = bootstrap.bootstrap_fleet(hosts, password, pubkey_path)
        print(report.format_results(results, title="SSH鍵ブートストラップ結果:"))
        if any(not r.ok for r in results):
            sys.exit(1)
        return

    if args.subcommand == "deploy":
        hosts = _resolve_hosts(args)
        results = deploy.deploy_fleet(
            hosts,
            ref=args.ref,
            allow_dirty=args.allow_dirty,
            force=args.force,
            rebuild_camera=args.rebuild_camera,
        )
        print(report.format_results(results, title=f"デプロイ結果 (ref={args.ref}):"))
        if any(not r.ok for r in results):
            sys.exit(1)
        return

    if args.subcommand == "push-config":
        hosts = _resolve_hosts(args)
        if args.target == "authorized-keys":
            results = config_push.add_authorized_key_fleet(hosts, args.file)
        else:
            results = config_push.push_config_fleet(hosts, args.target, args.file, args.remote_path)
        print(report.format_results(results, title=f"設定配布結果 (target={args.target}):"))
        if any(not r.ok for r in results):
            sys.exit(1)
        return

    if args.subcommand == "proxy":
        hosts = _resolve_hosts(args)
        tunnels, errors, close_all = proxy.open_tunnels(hosts, port=args.port)
        for host, error in errors:
            print(f"  [FAIL] machine_no={host.machine_no} ip={host.ip}: {error}", file=sys.stderr)
        if not tunnels:
            print("トンネルを1つも確立できませんでした。", file=sys.stderr)
            sys.exit(1)
        for tunnel in tunnels:
            print(
                f"  [ OK ] machine_no={tunnel.host.machine_no} ip={tunnel.host.ip}: "
                f"デバイス上で http_proxy=http://127.0.0.1:{tunnel.port} が利用可能",
            )
        example = tunnels[0].host
        print(f"\n{len(tunnels)}台のトンネルを確立しました。Ctrl+C で終了します。")
        print(
            f"例: ssh {example.ssh_user}@{example.ip} "
            f"\"https_proxy=http://127.0.0.1:{args.port} curl -sS -o /dev/null "
            f"-w 'HTTP=%{{http_code}}\\n' https://pypi.org/simple/\"",
        )
        try:
            while tunnels:
                time.sleep(2.0)
                dead = [t for t in tunnels if not t.ok]
                for t in dead:
                    print(
                        f"  [WARN] machine_no={t.host.machine_no} ip={t.host.ip}: トンネルが切断されました",
                        file=sys.stderr,
                    )
                tunnels = [t for t in tunnels if t.ok]
            if not tunnels:
                print("全てのトンネルが切断されました。終了します。", file=sys.stderr)
        except KeyboardInterrupt:
            print("\n終了します...")
        finally:
            close_all()
        return


if __name__ == "__main__":
    main()
