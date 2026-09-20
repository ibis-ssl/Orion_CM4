#!/usr/bin/env python3
# このファイルはCM4内部から停止状態のmode 3指令を周期送信し、入力時刻と
# 任意の疑似UART受信時刻をCSVに保存して、ネットワークを除いた周期診断を行う。
import argparse
import csv
import os
from pathlib import Path
import select
import signal
import socket
import struct
import subprocess
import threading
import time

from packet_codec import (CMD_SIZE, CHECK_COUNTER, FLAGS, CONTROL_MODE,
                          MODE_POLAR_VELOCITY, STOP_EMERGENCY_BIT, build_packet)


def command(sequence):
    data = bytearray(CMD_SIZE)
    data[0] = 254
    data[CHECK_COUNTER] = sequence % 201
    # 符号付き固定小数のゼロは0x7fff。速度・角度・位置をゼロにする。
    for offset in (2, 4, 6, 8, 12, 14, 16, 24, 26, 32, 34, 36):
        data[offset:offset + 2] = b'\x7f\xff'
    data[FLAGS] = 1 << STOP_EMERGENCY_BIT
    data[CONTROL_MODE] = MODE_POLAR_VELOCITY
    # mainのブリッジが素通しする予約領域。counterの巡回に依存せず照合する。
    data[38:42] = b'TPRB'
    struct.pack_into('<I', data, 42, sequence)
    return data


def intervals(label, stamps):
    values = sorted((b - a) / 1e6 for a, b in zip(stamps, stamps[1:]))
    if not values:
        print(f'{label}: samples={len(stamps)}')
        return
    def percentile(p):
        return values[round((len(values) - 1) * p)]
    print(f'{label}: samples={len(stamps)} interval_ms '
          f'min={values[0]:.3f} p50={percentile(.5):.3f} '
          f'p95={percentile(.95):.3f} p99={percentile(.99):.3f} max={values[-1]:.3f}')


def main():
    def stop_signal(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_signal)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--robot-id', type=int, default=8)
    parser.add_argument('--rate-hz', type=float, default=50)
    parser.add_argument('--seconds', type=float, default=30)
    parser.add_argument('--port', type=int, default=12345,
                        help='既存ブリッジへのlocalhost UDP入力ポート')
    parser.add_argument('--pty', action='store_true', help='独立ブリッジと疑似UARTで測定')
    parser.add_argument('--binary', default=str(Path(__file__).resolve().parents[1] / 'bin/ai_cmd_v2.out'))
    parser.add_argument('--output', required=True, help='結果を保存する新規ディレクトリ')
    args = parser.parse_args()
    if not (0 <= args.robot_id <= 10 and 0 < args.rate_hz <= 500
            and 0 < args.seconds <= 3600 and 1 <= args.port <= 65535):
        parser.error('id=0..10、rate=0..500 Hz、seconds=0..3600、port=1..65535が必要')
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    sent, received, errors = [], [], []
    stop = threading.Event()
    proc = worker = log = None
    master = slave = None
    reservations = []
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def read_pty():
        pending = bytearray()
        try:
            while not stop.is_set():
                if not select.select([master], [], [], .05)[0]:
                    continue
                chunk = os.read(master, 4096)
                stamp = time.monotonic_ns()
                pending.extend(chunk)
                while len(pending) >= 72:
                    frame = pending[:72]
                    if frame[0] != 254 or sum(frame[:71]) & 255 != frame[71]:
                        raise RuntimeError('疑似UARTフレームの同期/チェックサム異常')
                    if frame[38:42] != b'TPRB':
                        raise RuntimeError('診断外フレームを受信')
                    received.append((struct.unpack_from('<I', frame, 42)[0],
                                     stamp, frame[CHECK_COUNTER]))
                    del pending[:72]
            if pending:
                errors.append(f'末尾に未完成フレーム {len(pending)} byte')
        except Exception as exc:
            errors.append(str(exc))

    try:
        if args.pty:
            import pty
            master, slave = pty.openpty()
            ports = []
            for _ in range(4):
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.bind(('127.0.0.1', 0))
                ports.append(sock.getsockname()[1])
                reservations.append(sock)
            for sock in reservations:
                sock.close()
            args.port = ports[0]
            log = open(output / 'bridge.log', 'wb')
            proc = subprocess.Popen([
                args.binary, '--serial-port', os.ttyname(slave),
                '--robot-id', str(args.robot_id), '--ai-cmd-port', str(ports[0]),
                '--local-cam-port', str(ports[1]), '--feedback-port', str(ports[2]),
                '--config-port', str(ports[3])], stdout=log, stderr=subprocess.STDOUT)
            worker = threading.Thread(target=read_pty)
            worker.start()
            time.sleep(.5)
            if proc.poll() is not None:
                raise RuntimeError('ブリッジの起動失敗: bridge.logを参照')
        period = round(1e9 / args.rate_hz)
        start = deadline = time.monotonic_ns()
        end = start + int(args.seconds * 1e9)
        sequence = 1
        skipped = 0
        while deadline < end:
            packet = build_packet(args.robot_id, command(sequence))
            while True:
                remaining = deadline - time.monotonic_ns()
                if remaining <= 0:
                    break
                time.sleep(remaining / 1e9)
            before = time.monotonic_ns()
            tx.sendto(packet, ('127.0.0.1', args.port))
            after = time.monotonic_ns()
            sent.append((sequence, deadline, before, after))
            sequence += 1
            deadline += period
            # 遅れた周期を連打で取り戻さない。過ぎたdeadlineはスキップする。
            now = time.monotonic_ns()
            if deadline <= now:
                missed = (now - deadline) // period + 1
                skipped += missed
                deadline += missed * period
        time.sleep(.2)
        if proc is not None and proc.poll() is not None:
            errors.append('測定中にブリッジが終了')
        print(f'skipped_deadlines={skipped}; target=127.0.0.1:{args.port}')
    except KeyboardInterrupt:
        print('停止要求を受信: 測定CSVを保存します')
    finally:
        stop.set()
        if worker:
            worker.join()
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        for fd in (master, slave):
            if fd is not None:
                os.close(fd)
        if log:
            log.close()
        tx.close()
        for name, header, rows in (
                ('send.csv', ('sequence', 'deadline_ns', 'send_before_ns', 'send_after_ns'), sent),
                ('pty.csv', ('sequence', 'read_ns', 'counter'), received)):
            with open(output / name, 'w', newline='', encoding='utf-8') as stream:
                writer = csv.writer(stream)
                writer.writerow(header)
                writer.writerows(rows)
    intervals('UDP send', [row[2] for row in sent])
    if args.pty:
        intervals('PTY read (線上時刻ではない)', [row[1] for row in received])
        print(f'received={len(received)}/{len(sent)}; errors={errors}')
        if errors or not received:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
