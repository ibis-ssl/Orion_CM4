# -*- coding: utf-8 -*-
"""command_timing_probe の安全な指令内容、周期制御、CSV 記録をネットワークなしで検査する。"""

import argparse
import csv
import errno
from pathlib import Path
import struct
import tempfile
import unittest

from cm4.bridge.packet_codec import (
    CHECK_COUNTER,
    CONTROL_MODE,
    FLAGS,
    KICK_POWER,
    DRIBBLE_POWER,
    MODE_POLAR_VELOCITY,
    MODE_POSITION_TARGET,
    STOP_EMERGENCY_BIT,
)
from host.apps import command_timing_probe as probe


class FakeClock:
    def __init__(self, values):
        self.values = iter(values)

    def __call__(self):
        return next(self.values)


class FakeSocket:
    def __init__(self, fail=False):
        self.fail = fail
        self.sent = []
        self.options = []
        self.closed = False

    def setsockopt(self, *option):
        self.options.append(option)

    def sendto(self, packet, destination):
        self.sent.append((packet, destination))
        if self.fail:
            raise OSError(errno.ENETUNREACH, "test error")
        return len(packet)

    def close(self):
        self.closed = True


class FakeSocketFactory:
    def __init__(self, fail=False):
        self.fail = fail
        self.sockets = []

    def __call__(self, family, kind):
        self.asserted_args = (family, kind)
        result = FakeSocket(self.fail)
        self.sockets.append(result)
        return result


def make_args(output, **overrides):
    values = dict(
        host="127.0.0.1",
        port=12345,
        robot_id=8,
        mode=MODE_POLAR_VELOCITY,
        rate_hz=2.0,
        seconds=1.0,
        output=output,
        socket_per_packet=False,
        broadcast=False,
    )
    values.update(overrides)
    return argparse.Namespace(**values)


class CommandTimingProbeTest(unittest.TestCase):
    def test_fixed_socket_sends_only_stop_commands_and_writes_csv(self):
        factory = FakeSocketFactory()
        # start, wait check, before, after, missed check を 2 回分。
        clock = FakeClock((0, 0, 10, 20, 20, 500_000_000, 500_000_010,
                           500_000_020, 500_000_020))
        with tempfile.TemporaryDirectory() as parent:
            output = str(Path(parent) / "result")
            summary = probe.run_probe(
                make_args(output), socket_factory=factory, clock_ns=clock,
                sleeper=lambda _seconds: None,
            )

            self.assertEqual(summary["sent"], 2)
            self.assertEqual(len(factory.sockets), 1)
            self.assertTrue(factory.sockets[0].closed)
            self.assertEqual([target for _, target in factory.sockets[0].sent],
                             [("127.0.0.1", 12345)] * 2)
            for sequence, (packet, _) in enumerate(factory.sockets[0].sent, 1):
                slot = packet[8 * 65 + 1:9 * 65]
                self.assertEqual(slot[CHECK_COUNTER], sequence % 201)
                self.assertEqual(slot[FLAGS], 1 << STOP_EMERGENCY_BIT)
                self.assertEqual(slot[CONTROL_MODE], MODE_POLAR_VELOCITY)
                self.assertEqual(slot[KICK_POWER], 0)
                self.assertEqual(slot[DRIBBLE_POWER], 0)
                self.assertEqual(slot[24:28], b"\x7f\xff\x7f\xff")
                self.assertEqual(slot[38:42], b"TPRB")
                self.assertEqual(struct.unpack_from("<I", slot, 42)[0], sequence)

            with (Path(output) / "send.csv").open(newline="", encoding="utf-8") as stream:
                rows = list(csv.reader(stream))
            self.assertEqual(tuple(rows[0]), probe.CSV_HEADER)
            self.assertEqual(rows[1], ["1", "0", "10", "20", "715", ""])

    def test_mode4_changes_only_control_mode_of_safe_command(self):
        factory = FakeSocketFactory()
        clock = FakeClock((0, 0, 1, 2, 2))
        with tempfile.TemporaryDirectory() as parent:
            summary = probe.run_probe(
                make_args(str(Path(parent) / "result"), seconds=0.5,
                          mode=MODE_POSITION_TARGET),
                socket_factory=factory,
                clock_ns=clock,
                sleeper=lambda _seconds: None,
            )

        self.assertEqual(summary["sent"], 1)
        packet, _ = factory.sockets[0].sent[0]
        slot = packet[8 * 65 + 1:9 * 65]
        expected = probe.command(1)
        expected[CONTROL_MODE] = MODE_POSITION_TARGET
        self.assertEqual(slot, expected)
        self.assertEqual(slot[FLAGS], 1 << STOP_EMERGENCY_BIT)
        self.assertEqual(slot[CONTROL_MODE], MODE_POSITION_TARGET)
        self.assertEqual(slot[KICK_POWER], 0)
        self.assertEqual(slot[DRIBBLE_POWER], 0)
        self.assertEqual(slot[24:28], b"\x7f\xff\x7f\xff")

    def test_socket_per_packet_broadcast_and_error_are_recorded(self):
        factory = FakeSocketFactory(fail=True)
        clock = FakeClock((0, 0, 1, 2, 2))
        with tempfile.TemporaryDirectory() as parent:
            output = str(Path(parent) / "result")
            summary = probe.run_probe(
                make_args(output, seconds=0.5, socket_per_packet=True, broadcast=True),
                socket_factory=factory,
                clock_ns=clock,
                sleeper=lambda _seconds: None,
            )
            self.assertEqual(summary["errors"], 1)
            self.assertEqual(len(factory.sockets), 1)
            self.assertTrue(factory.sockets[0].closed)
            self.assertEqual(
                factory.sockets[0].options,
                [(probe.socket.SOL_SOCKET, probe.socket.SO_BROADCAST, 1)],
            )
            with (Path(output) / "send.csv").open(newline="", encoding="utf-8") as stream:
                rows = list(csv.reader(stream))
            self.assertEqual(rows[1][4:], ["0", str(errno.ENETUNREACH)])

    def test_missed_deadlines_are_skipped_without_catchup(self):
        factory = FakeSocketFactory()
        clock = FakeClock((0, 0, 10, 20, 750_000_000,
                           1_000_000_000, 1_000_000_010, 1_000_000_020,
                           1_000_000_020))
        with tempfile.TemporaryDirectory() as parent:
            summary = probe.run_probe(
                make_args(str(Path(parent) / "result"), seconds=1.5),
                socket_factory=factory,
                clock_ns=clock,
                sleeper=lambda _seconds: None,
            )
        self.assertEqual(summary["attempted"], 2)
        self.assertEqual(summary["skipped_deadlines"], 1)

    def test_keyboard_interrupt_flushes_collected_rows(self):
        factory = FakeSocketFactory()
        clock = FakeClock((0, 0, 1, 2, 2, 2))

        def interrupt(_seconds):
            raise KeyboardInterrupt

        with tempfile.TemporaryDirectory() as parent:
            output = Path(parent) / "result"
            summary = probe.run_probe(
                make_args(str(output)),
                socket_factory=factory,
                clock_ns=clock,
                sleeper=interrupt,
            )
            with (output / "send.csv").open(newline="", encoding="utf-8") as stream:
                rows = list(csv.reader(stream))

        self.assertTrue(summary["interrupted"])
        self.assertEqual(summary["attempted"], 1)
        self.assertEqual(len(rows), 2)
        self.assertTrue(factory.sockets[0].closed)

    def test_host_and_output_are_required(self):
        parser = probe.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args([])


if __name__ == "__main__":
    unittest.main()
