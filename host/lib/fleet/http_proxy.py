# このファイルは PC 側で動く最小限の HTTP フォワードプロキシを実装する。
# CM4 側からの apt/pip/curl 等のプロキシ経由アクセスを、実際のインターネットへ中継する。
# HTTPS(CONNECT メソッドによるトンネリング)と、素の HTTP(絶対URI形式のリクエスト)の
# 両方に対応する。SOCKS 等の追加プロトコルは扱わない(apt/pip/curl が標準で
# http_proxy 環境変数だけで使えるようにするため)。
import http.server
import select
import socket
import socketserver
import threading
from urllib.parse import urlsplit

_IDLE_TIMEOUT = 60.0
_UPSTREAM_CONNECT_TIMEOUT = 8.0


def connect_upstream(host, port, timeout=_UPSTREAM_CONNECT_TIMEOUT):
    """host:port へ接続する。IPv4 アドレスを優先して試す。

    一部のネットワークでは DNS が IPv6 アドレスを
    返すにもかかわらず実際には経路が無く、素の socket.create_connection() だと
    その疎通しない IPv6 候補それぞれに timeout 秒フルにかけてから
    ようやく生きている IPv4 候補を試す動きになり、呼び出し元(pip 等)の
    タイムアウトの方が先に切れて失敗する。そのため候補を IPv4 優先に並べ替え、
    1 候補あたりのタイムアウトを候補数で分割する。
    """
    try:
        infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    except OSError as exc:
        raise OSError(f"名前解決に失敗しました: {exc}") from exc

    infos.sort(key=lambda info: 0 if info[0] == socket.AF_INET else 1)
    per_attempt_timeout = max(2.0, timeout / len(infos))

    last_exc = None
    for family, socktype, proto, _canonname, sockaddr in infos:
        sock = socket.socket(family, socktype, proto)
        sock.settimeout(per_attempt_timeout)
        try:
            sock.connect(sockaddr)
            sock.settimeout(None)
            return sock
        except OSError as exc:
            sock.close()
            last_exc = exc
    raise last_exc


def relay_sockets(a, b):
    """a <-> b を双方向に中継する(どちらかが閉じる/60秒アイドルになるまで)。"""
    try:
        sockets = [a, b]
        while True:
            readable, _, exceptional = select.select(sockets, [], sockets, _IDLE_TIMEOUT)
            if exceptional or not readable:
                break
            done = False
            for sock in readable:
                other = b if sock is a else a
                try:
                    data = sock.recv(65536)
                except OSError:
                    done = True
                    break
                if not data:
                    done = True
                    break
                try:
                    other.sendall(data)
                except OSError:
                    done = True
                    break
            if done:
                break
    finally:
        a.close()
        b.close()


class _ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass  # 標準の access log は不要

    def do_CONNECT(self):
        try:
            host, port_str = self.path.rsplit(":", 1)
            port = int(port_str)
        except ValueError:
            self.send_error(400, "Bad CONNECT target")
            return

        try:
            upstream = connect_upstream(host, port)
        except OSError as exc:
            self._send_error_safe(502, f"Upstream connect failed: {exc}")
            return

        self.send_response(200, "Connection Established")
        self.end_headers()
        self.wfile.flush()
        relay_sockets(self.connection, upstream)

    def _relay_absolute_uri(self):
        # http:// の素のリクエスト(絶対URI形式)を中継する。
        parts = urlsplit(self.path)
        host = parts.hostname
        port = parts.port or 80
        if not host:
            self.send_error(400, "Bad request target")
            return

        try:
            upstream = connect_upstream(host, port)
        except OSError as exc:
            self._send_error_safe(502, f"Upstream connect failed: {exc}")
            return

        request_target = parts.path or "/"
        if parts.query:
            request_target += f"?{parts.query}"
        try:
            upstream.sendall(f"{self.command} {request_target} {self.request_version}\r\n".encode())
            for name, value in self.headers.items():
                if name.lower() == "proxy-connection":
                    continue
                upstream.sendall(f"{name}: {value}\r\n".encode())
            upstream.sendall(b"\r\n")

            length = self.headers.get("Content-Length")
            if length:
                upstream.sendall(self.rfile.read(int(length)))
        except OSError as exc:
            self._send_error_safe(502, f"Upstream write failed: {exc}")
            upstream.close()
            return

        relay_sockets(self.connection, upstream)

    def _send_error_safe(self, code, message):
        # クライアント側が既に切断している場合、send_error() の書き込みが
        # BrokenPipeError で失敗しうる(呼び出し元の pip/curl が先にタイムアウトした場合等)。
        # ログを汚すだけの無害な二次的エラーなので握りつぶす。
        try:
            self.send_error(code, message)
        except OSError:
            pass

    do_GET = _relay_absolute_uri
    do_POST = _relay_absolute_uri
    do_HEAD = _relay_absolute_uri
    do_PUT = _relay_absolute_uri


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start(bind_host="127.0.0.1", bind_port=0):
    """ローカル HTTP フォワードプロキシを起動する。(server, port) を返す。
    呼び出し側は不要になったら server.shutdown() すること。"""
    server = _Server((bind_host, bind_port), _ProxyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]
