#!/usr/bin/env python3
# このファイルはCM4のUDP制御受信を単独で監視し、カーネル到着間隔と
# アプリ読み出し遅延を端末表示・CSV保存する。UARTへの転送は行わない。
import argparse
from collections import Counter, deque
import csv
import select
import signal
import socket
import struct
import time

from packet_codec import PACKET_SIZE, SLOT_SIZE, CHECK_COUNTER, CONTROL_MODE

# Linux UAPI。CM4の64bit Raspberry Pi OSを対象とする。
SO_TIMESTAMPNS_NEW = 64
SO_RXQ_OVFL = 40
CSV_FIELDS = ('realtime_ns', 'monotonic_ns', 'kernel_ns', 'source_ip',
              'source_port', 'batch', 'bytes', 'status', 'mode', 'counter',
              'kernel_interval_ms', 'app_interval_ms', 'read_delay_ms',
              'socket_drop_total', 'clock_jump')


def decode(data, robot_id):
    if len(data) != PACKET_SIZE:
        return 'invalid', None, None
    commands = [data[i + 1:i + SLOT_SIZE] for i in range(0, PACKET_SIZE, SLOT_SIZE)
                if data[i] == robot_id and any(data[i + 1:i + SLOT_SIZE])]
    if not commands:
        return 'empty', None, None
    if len(commands) != 1:
        return 'ambiguous', None, None
    cmd = commands[0]
    return 'ok', cmd[CONTROL_MODE], cmd[CHECK_COUNTER]


def ancillary_values(ancillary):
    stamp = drops = None
    for level, kind, value in ancillary:
        if level != socket.SOL_SOCKET:
            continue
        if kind == SO_TIMESTAMPNS_NEW and len(value) >= 16:
            seconds, ns = struct.unpack_from('=qq', value)
            stamp = seconds * 1_000_000_000 + ns
        elif kind == SO_RXQ_OVFL and len(value) >= 4:
            drops = struct.unpack_from('=I', value)[0]
    return stamp, drops


def distribution(values):
    if not values:
        return '-'
    values = sorted(values)
    return ' '.join(f'{name}={values[round((len(values)-1)*p)]:.3f}'
                    for name, p in [('min', 0), ('p50', .5), ('p99', .99), ('max', 1)])


def main():
    parser = argparse.ArgumentParser(description='CM4 UDP受信監視 (UART送信なし)')
    parser.add_argument('--port', type=int, default=12345)
    parser.add_argument('--robot-id', type=int, default=8)
    parser.add_argument('--stream-key', choices=('ip', 'peer'), default='ip',
                        help='周期比較の単位。ipは送信元ポート変更を許容、peerはIP+port')
    parser.add_argument('--duration', type=float, default=0, help='秒。0はCtrl+Cまで')
    parser.add_argument('--csv', help='終了時のCSV保存先 (既存ファイルは上書きしない)')
    parser.add_argument('--max-records', type=int, default=100000,
                        help='CSVおよび分布の最大保持件数。超過時は古い記録を捨てる')
    parser.add_argument('--gap-ms', type=float, default=30)
    parser.add_argument('--delay-ms', type=float, default=5)
    args = parser.parse_args()
    if not (1 <= args.port <= 65535 and 0 <= args.robot_id <= 10
            and args.duration >= 0 and args.max_records > 0
            and args.gap_ms > 0 and args.delay_ms > 0):
        parser.error('引数の範囲が不正です')
    if not hasattr(socket.socket, 'recvmsg'):
        parser.error('Linux CM4上で実行してください')
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    csv_file = None
    try:
        sock.setsockopt(socket.SOL_SOCKET, SO_TIMESTAMPNS_NEW, 1)
        sock.setsockopt(socket.SOL_SOCKET, SO_RXQ_OVFL, 1)
        sock.bind(('0.0.0.0', args.port))  # REUSEPORT/REUSEADDRは使わない
        sock.setblocking(False)
        if args.csv:
            csv_file = open(args.csv, 'x', newline='', encoding='utf-8')
    except OSError as exc:
        sock.close()
        parser.exit(1, f'起動失敗: {exc}\n同じポートの通常ブリッジを停止してください。\n')

    def interrupt(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupt)
    records = deque(maxlen=args.max_records)
    metrics = {key: deque(maxlen=args.max_records) for key in ('kernel', 'app', 'delay')}
    totals, window = Counter(), Counter()
    previous = {}  # 既定はIP単位。送信ごとにソケットを作る送信器にも対応する。
    offset = time.time_ns() - time.monotonic_ns()
    last_drop = 0
    batch = 0
    last = '-'
    started = reported = time.monotonic()
    deadline = started + args.duration if args.duration else float('inf')

    def report(final=False):
        nonlocal reported
        now = time.monotonic()
        elapsed = max(now - reported, .000001)
        print(f'{"FINAL" if final else "RX"} {window["rx"]/elapsed:.1f} pkt/s '
              f'total={totals["rx"]} robot={args.robot_id} last={last}', flush=True)
        for key in metrics:
            print(f'  {key} ms: {distribution(metrics[key])}')
        print(f'  batch_max={window["batch_max"]} cumulative={dict(totals)}', flush=True)
        for values in metrics.values():
            values.clear()
        window.clear()
        reported = now

    print(f'Listening UDP 0.0.0.0:{args.port} robot={args.robot_id} '
          f'stream-key={args.stream_key}; Ctrl+Cで終了', flush=True)
    try:
        while time.monotonic() < deadline:
            timeout = max(0, min(reported + 1, deadline) - time.monotonic())
            if select.select([sock], [], [], timeout)[0]:
                batch += 1
                count = 0
                # 100msまたは4096件で一度集計へ戻る。高入力でも終了・表示可能にする。
                drain_end = time.monotonic() + .1
                while count < 4096 and time.monotonic() < min(drain_end, deadline):
                    try:
                        data, ancillary, flags, peer = sock.recvmsg(65535, 128)
                    except BlockingIOError:
                        break
                    mono = time.monotonic_ns()
                    real = time.time_ns()
                    count += 1
                    totals['rx'] += 1
                    window['rx'] += 1
                    stamp, drops = ancillary_values(ancillary)
                    jump = abs((real - mono) - offset) > 1_000_000
                    offset = real - mono
                    if jump:
                        totals['clock_jump'] += 1
                        previous.clear()
                    if flags & socket.MSG_CTRUNC:
                        stamp = None
                        totals['ancillary_truncated'] += 1
                    if drops is not None:
                        totals['socket_drop'] += (drops - last_drop) & 0xffffffff
                        last_drop = drops
                    status, mode, counter = decode(data, args.robot_id)
                    if flags & socket.MSG_TRUNC:
                        status = 'invalid'
                    totals[status] += 1
                    kernel_dt = app_dt = delay = None
                    if stamp is None:
                        totals['timestamp_missing'] += 1
                    if status == 'ok':
                        last = f'{peer[0]}:{peer[1]} mode={mode} counter={counter}'
                        stream = peer[0] if args.stream_key == 'ip' else peer
                        prev = previous.get(stream)
                        if prev:
                            app_dt = (mono - prev[1]) / 1e6
                            if counter == prev[2]:
                                totals['duplicate_counter'] += 1
                            if not jump and stamp is not None and prev[0] is not None:
                                kernel_dt = (stamp - prev[0]) / 1e6
                        if not jump and stamp is not None:
                            delay = (real - stamp) / 1e6
                            if delay < 0 or (kernel_dt is not None and kernel_dt < 0):
                                totals['clock_invalid'] += 1
                                delay = kernel_dt = None
                        previous[stream] = (stamp, mono, counter)
                        if len(previous) > 256:
                            previous.clear()
                        for key, value in [('kernel', kernel_dt), ('app', app_dt), ('delay', delay)]:
                            if value is not None:
                                metrics[key].append(value)
                        if ((kernel_dt is not None and kernel_dt >= args.gap_ms)
                                or (delay is not None and delay >= args.delay_ms)):
                            totals['anomaly'] += 1
                            # 端末出力で測定を乱さないよう詳細は1秒に3件まで。
                            if window['anomaly_detail'] < 3:
                                print(f'  WARN {last} kernel_ms={kernel_dt} delay_ms={delay}', flush=True)
                                window['anomaly_detail'] += 1
                    if args.csv:
                        if len(records) == args.max_records:
                            totals['csv_evicted'] += 1
                        records.append((real, mono, stamp, peer[0], peer[1], batch,
                                        len(data), status, mode, counter, kernel_dt,
                                        app_dt, delay, totals['socket_drop'], int(jump)))
                window['batch_max'] = max(window['batch_max'], count)
                totals['batch_max'] = max(totals['batch_max'], count)
            if time.monotonic() - reported >= 1:
                report()
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
        report(final=True)
        if csv_file:
            with csv_file:
                writer = csv.writer(csv_file)
                writer.writerow(CSV_FIELDS)
                writer.writerows(records)
            print(f'CSV saved: {args.csv} ({len(records)} records)', flush=True)


if __name__ == '__main__':
    main()
