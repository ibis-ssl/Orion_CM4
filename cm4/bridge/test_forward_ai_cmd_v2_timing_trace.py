# -*- coding: utf-8 -*-
"""forward_ai_cmd_v2 のUDP/UARTタイミングCSVをPTY上で結合検証する。"""

import csv
import os
import pty
import signal
import socket
import struct
import subprocess
import tempfile
import threading
import time
import unittest

from packet_codec import CHECK_COUNTER, CMD_SIZE, CONTROL_MODE, MODE_POLAR_VELOCITY, build_packet


BIN = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "bin", "ai_cmd_v2.out"))


def command(sequence):
    data = bytearray(CMD_SIZE)
    data[0] = 0xFE
    data[CHECK_COUNTER] = sequence % 201
    data[CONTROL_MODE] = MODE_POLAR_VELOCITY
    data[38:42] = b"TPRB"
    struct.pack_into("<I", data, 42, sequence)
    return bytes(data)


def reserve_ports(count=4):
    sockets = []
    ports = []
    for _ in range(count):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        sockets.append(sock)
        ports.append(sock.getsockname()[1])
    return sockets, ports


class TimingTraceTest(unittest.TestCase):
    def test_trace_flush_adoption_and_exclusive_create(self):
        reservations, ports = reserve_ports()
        master, slave = pty.openpty()
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        drain_stop = threading.Event()

        def drain_pty():
            while not drain_stop.is_set():
                try:
                    os.read(master, 4096)
                except OSError:
                    return

        with tempfile.TemporaryDirectory() as directory:
            trace_path = os.path.join(directory, "bridge.csv")
            log_path = os.path.join(directory, "bridge.log")
            args = [
                BIN, "--serial-port", os.ttyname(slave), "--robot-id", "0",
                "--ai-cmd-port", str(ports[0]), "--local-cam-port", str(ports[1]),
                "--feedback-port", str(ports[2]), "--config-port", str(ports[3]),
                "--timing-trace", trace_path, "--timing-seconds", "60",
            ]
            for sock in reservations:
                sock.close()
            with open(log_path, "w+", encoding="utf-8") as log:
                proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)
                reader = threading.Thread(target=drain_pty, daemon=True)
                reader.start()
                try:
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        log.flush()
                        log.seek(0)
                        if "control kp" in log.read():
                            break
                        if proc.poll() is not None:
                            self.fail("bridge terminated during startup")
                        time.sleep(0.01)
                    else:
                        self.fail("bridge startup timeout")

                    # Suspend the consumer so all three datagrams are drained in one loop.
                    os.kill(proc.pid, signal.SIGSTOP)
                    for sequence in (101, 102, 103):
                        sender.sendto(build_packet(0, command(sequence)), ("127.0.0.1", ports[0]))
                    os.kill(proc.pid, signal.SIGCONT)
                    time.sleep(0.2)
                    proc.terminate()
                    proc.wait(timeout=5)
                finally:
                    if proc.poll() is None:
                        os.kill(proc.pid, signal.SIGCONT)
                        proc.kill()
                        proc.wait()
                    drain_stop.set()
                    os.close(slave)
                    os.close(master)
                    reader.join(timeout=1)

            with open(trace_path, newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            rx = [row for row in rows if row["event"] == "rx"]
            tx = [row for row in rows if row["event"] == "tx"]
            self.assertEqual([int(row["sequence"]) for row in rx], [101, 102, 103])
            self.assertEqual([int(row["adopted"]) for row in rx], [0, 0, 1])
            self.assertTrue(all(int(row["kernel_ns"]) > 0 for row in rx))
            self.assertTrue(all(int(row["monotonic_ns"]) > 0 for row in rx))
            self.assertTrue(tx)
            self.assertEqual(int(tx[-1]["sequence"]), 103)
            self.assertEqual(int(tx[-1]["written"]), 72)
            self.assertGreater(int(tx[-1]["write_end_ns"]), int(tx[-1]["monotonic_ns"]))

            exclusive = subprocess.run(
                [BIN, "--robot-id", "0", "--timing-trace", trace_path],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=5)
            self.assertNotEqual(exclusive.returncode, 0)
            self.assertIn("open(--timing-trace)", exclusive.stdout)

        sender.close()


if __name__ == "__main__":
    unittest.main()
