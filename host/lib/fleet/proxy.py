# このファイルは on-demand の HTTP プロキシトンネルを管理する。
# PC 側で HTTP CONNECT プロキシ(http_proxy.py)を起動し、paramiko の
# 固定リバースポートフォワード(request_port_forward + accept ループ、
# demos/rforward.py と同じパターン)で CM4 側の 127.0.0.1:<port> へ中継する。
# SOCKS5 のような追加プロトコルの自前実装や外部 ssh バイナリへの依存は避け、
# 既存の paramiko ベースの接続をそのまま利用する。
import socket
import threading

from host.lib.fleet import http_proxy, ssh

DEFAULT_PORT = 18080


class ProxyTunnel:
    def __init__(self, host, port, client, stop_event, accept_thread):
        self.host = host
        self.port = port
        self._client = client
        self._stop_event = stop_event
        self._accept_thread = accept_thread

    @property
    def ok(self):
        transport = self._client.get_transport()
        return transport is not None and transport.is_active()

    def close(self):
        self._stop_event.set()
        transport = self._client.get_transport()
        if transport is not None:
            try:
                transport.cancel_port_forward("127.0.0.1", self.port)
            except Exception:
                pass
        self._accept_thread.join(timeout=3.0)
        self._client.close()


def _accept_loop(transport, local_http_port, stop_event):
    while not stop_event.is_set():
        channel = transport.accept(1.0)
        if channel is None:
            continue
        try:
            upstream = socket.create_connection(("127.0.0.1", local_http_port), timeout=5.0)
        except OSError:
            channel.close()
            continue
        thread = threading.Thread(target=http_proxy.relay_sockets, args=(channel, upstream), daemon=True)
        thread.start()


def start_local_proxy():
    """PC 側の HTTP プロキシを起動する。(server, port) を返す。
    呼び出し側は使い終わったら server.shutdown() すること。"""
    return http_proxy.start()


def open_tunnel(host, port, local_http_port):
    """host に対し 1 本のリバースフォワードトンネルを張り、
    デバイス側の 127.0.0.1:<port> を local_http_port(PC 側の HTTP プロキシ)へ中継する。"""
    client = ssh.connect(host, allow_unknown_host=False)
    transport = client.get_transport()

    try:
        transport.request_port_forward("127.0.0.1", port)
    except Exception as exc:
        client.close()
        raise RuntimeError(f"デバイス側 127.0.0.1:{port} のリバースフォワード開始に失敗しました: {exc}") from exc

    stop_event = threading.Event()
    accept_thread = threading.Thread(
        target=_accept_loop, args=(transport, local_http_port, stop_event), daemon=True,
    )
    accept_thread.start()
    return ProxyTunnel(host, port, client, stop_event, accept_thread)


def open_tunnels(hosts, port=DEFAULT_PORT):
    """複数ホストへのトンネルをまとめて開く。PC 側の HTTP プロキシは全ホストで共有する。
    1台の失敗が他台に影響しないよう、
    (成功したトンネル一覧, [(host, エラー文字列), ...], 後始末用のクローズ関数) を返す。"""
    local_server, local_http_port = start_local_proxy()

    tunnels = []
    errors = []
    for host in hosts:
        try:
            tunnels.append(open_tunnel(host, port, local_http_port))
        except Exception as exc:
            errors.append((host, str(exc)))

    def close_all():
        close_tunnels(tunnels)
        local_server.shutdown()

    return tunnels, errors, close_all


def close_tunnels(tunnels):
    for tunnel in tunnels:
        tunnel.close()
