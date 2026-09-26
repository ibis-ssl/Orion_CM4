# このファイルはUDP受信診断のパケット判定・時刻抽出とLinux実ソケット動作を検査する。
import csv
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest

from packet_codec import build_packet
from mode3_timing_probe import command
from udp_rx_monitor import decode, ancillary_values, SO_TIMESTAMPNS_NEW, SO_RXQ_OVFL


class MonitorTest(unittest.TestCase):
    def test_decode_and_ancillary(self):
        self.assertEqual(decode(build_packet(8, command(1)), 8), ('ok', 3, 1))
        self.assertEqual(decode(build_packet(8, command(1)), 7)[0], 'empty')
        self.assertEqual(decode(b'bad', 8)[0], 'invalid')
        self.assertEqual(ancillary_values([
            (socket.SOL_SOCKET, SO_TIMESTAMPNS_NEW, struct.pack('=qq', 10, 123)),
            (socket.SOL_SOCKET, SO_RXQ_OVFL, struct.pack('=I', 7))]),
            (10_000_000_123, 7))

    @unittest.skipUnless(sys.platform == 'linux', 'Linux timestamp APIが必要')
    def test_real_socket_csv_and_conflict(self):
        script = str(Path(__file__).with_name('udp_rx_monitor.py'))
        with tempfile.TemporaryDirectory() as directory, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as tx:
            tx.bind(('127.0.0.1', 0))
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reserved:
                reserved.bind(('127.0.0.1', 0))
                port = reserved.getsockname()[1]
            csv_path = str(Path(directory) / 'rx.csv')
            proc = subprocess.Popen([sys.executable, '-u', script, '--port', str(port),
                                     '--duration', '2', '--csv', csv_path],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                ready = proc.stdout.readline()
                self.assertIn('Listening', ready)
                collision = subprocess.run([sys.executable, script, '--port', str(port),
                                            '--duration', '.1'], capture_output=True, timeout=3)
                self.assertNotEqual(collision.returncode, 0)
                for sequence in range(1, 11):
                    # 送信ごとにportが変わっても同じIPの周期比較を継続できること。
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as changing_tx:
                        changing_tx.sendto(build_packet(8, command(sequence)), ('127.0.0.1', port))
                    time.sleep(.02)
                tx.sendto(b'bad', ('127.0.0.1', port))
                tx.sendto(build_packet(7, command(1)), ('127.0.0.1', port))
                output, _ = proc.communicate(timeout=5)
                self.assertEqual(proc.returncode, 0, output)
                with open(csv_path, encoding='utf-8') as stream:
                    rows = list(csv.DictReader(stream))
                good = [row for row in rows if row['status'] == 'ok']
                self.assertEqual(len(good), 10)
                self.assertTrue(all(int(row['kernel_ns']) > 0 for row in good))
                self.assertTrue(all(float(row['read_delay_ms']) >= 0 for row in good))
                self.assertGreater(len({row['source_port'] for row in good}), 1)
                self.assertEqual(good[0]['kernel_interval_ms'], '')
                self.assertTrue(all(float(row['kernel_interval_ms']) > 0 for row in good[1:]))
                self.assertTrue(all(float(row['app_interval_ms']) > 0 for row in good[1:]))
                self.assertEqual([row['status'] for row in rows[-2:]], ['invalid', 'empty'])
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()


if __name__ == '__main__':
    unittest.main()
