# -*- coding: utf-8 -*-
"""forward_ai_cmd_v2.cpp (ai_cmd_v2.out) のホスト上での結合スモークテスト。

--debug モードは UART へ書かず、送信するはずの 72 バイトを 16 進表示する。
その出力がワイヤ上のバイト列そのものなので、実機 UART も STM32 も無しに
「何を G474 へ送るか」を機械検査できる。/dev/serial0 の open だけは成立させる
必要があるので pty を渡す。

検査する不変条件:
  1. passthrough (mode 3 / --passthrough) は受信 64 バイトを byte0 だけ 0xFE に
     置き換えて素通しする。旧構成との A/B 比較の土台がここ。
  2. mode 4 は CM4 で位置制御ループを閉じ、mode 3 へ変換して送る。
  3. crane 無通信・feedback 無通信のいずれでも速度指令が 0 になり
     STOP_EMERGENCY が立つ (検収条件 4)。
  4. ロボット ID を決定できないときは黙って 0 号機として動かず落ちる。
  5. --debug なしの実運用モードで、位置制御の状態表示が実際に出ること。
     ここだけは pty を実 UART 代わりに使って本物の送信経路を通す。

cm4/firmware/test_*.py と同じ unittest 方式 (pytest は使わない)。
"""

import math
import os
import pty
import re
import socket
import struct
import subprocess
import sys
import threading
import time
import unittest

BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin", "ai_cmd_v2.out")
BIN = os.path.normpath(BIN)

# パケットのオフセットと符号化は packet_codec.py が正本。
from packet_codec import (  # noqa: E402
    CHECK_COUNTER, CMD_SIZE, CONTROL_MODE, CONTROL_MODE_ARGS, DRIBBLE_POWER, FEEDBACK_POS_X_OFFSET,
    FEEDBACK_POS_Y_OFFSET, FEEDBACK_SIZE, FEEDBACK_SYNC, FLAGS, KICK_POWER,
    LINEAR_VELOCITY_LIMIT_HIGH, PACKET_SIZE, STOP_EMERGENCY_BIT, TARGET_GLOBAL_POS_X_HIGH,
    TERMINAL_VELOCITY_HIGH, build_packet, encode_two_byte as enc)
from packet_codec import MODE_POLAR_VELOCITY as POLAR_VELOCITY_TARGET_MODE  # noqa: E402
from packet_codec import MODE_POSITION_TARGET as POSITION_TARGET_WITH_TERMINAL_VELOCITY_MODE  # noqa: E402

UART_PACKET_SIZE = 72

HEX_TOKEN = re.compile(r"0x([0-9a-f]+)")


def dec(high, low, value_range):
    return ((high << 8 | low) - 32767.0) / 32767.0 * value_range


def build_command(check_counter, mode, target=(2.0, -1.0), velocity_limit=3.0, terminal_velocity=0.0, terminal_xy=(0.0, 0.0), flags=0x01):
    d = bytearray(CMD_SIZE)
    d[0] = 0xFE
    d[CHECK_COUNTER] = check_counter & 0xFF
    d[2:4] = enc(0.5, 32.767)    # vision_global_pos x
    d[4:6] = enc(0.25, 32.767)   # vision_global_pos y
    d[6:8] = enc(0.3, math.pi)   # vision_global_theta
    d[8:10] = enc(-0.7, math.pi)  # target_global_theta
    d[KICK_POWER] = 10
    d[DRIBBLE_POWER] = 5
    d[12:14] = enc(4.0, 32.767)  # acceleration_limit
    d[LINEAR_VELOCITY_LIMIT_HIGH:LINEAR_VELOCITY_LIMIT_HIGH + 2] = enc(velocity_limit, 32.767)
    d[16:18] = enc(5.0, 32.767)  # angular_velocity_limit
    d[18:20] = struct.pack(">H", 100)  # latency_time_ms (生の uint16)
    d[20:22] = struct.pack(">H", 33)   # elapsed_time_ms_since_last_vision
    d[FLAGS] = flags
    d[CONTROL_MODE] = mode
    if mode == POSITION_TARGET_WITH_TERMINAL_VELOCITY_MODE:
        d[CONTROL_MODE_ARGS:CONTROL_MODE_ARGS + 2] = enc(terminal_xy[0], 32.767)
        d[CONTROL_MODE_ARGS + 2:CONTROL_MODE_ARGS + 4] = enc(terminal_xy[1], 32.767)
    else:
        d[CONTROL_MODE_ARGS:CONTROL_MODE_ARGS + 2] = enc(1.25, 32.767)
        d[CONTROL_MODE_ARGS + 2:CONTROL_MODE_ARGS + 4] = enc(-0.5, 32.767)
    # crane は byte 28..31 / 38..63 をゼロ初期化しない。素通し経路がその領域まで
    # そのまま運ぶことを確かめるため、固定パターンを入れておく。
    d[28:32] = bytes([0xDE, 0xAD, 0xBE, 0xEF])
    d[TARGET_GLOBAL_POS_X_HIGH:TARGET_GLOBAL_POS_X_HIGH + 2] = enc(target[0], 32.767)
    d[TARGET_GLOBAL_POS_X_HIGH + 2:TARGET_GLOBAL_POS_X_HIGH + 4] = enc(target[1], 32.767)
    d[TERMINAL_VELOCITY_HIGH:TERMINAL_VELOCITY_HIGH + 2] = enc(terminal_velocity, 32.767)
    for i in range(38, CMD_SIZE):
        d[i] = (i * 7) & 0xFF
    return bytes(d)


def build_feedback(x, y):
    fb = bytearray(FEEDBACK_SIZE)
    fb[0], fb[1] = FEEDBACK_SYNC
    fb[FEEDBACK_POS_X_OFFSET:FEEDBACK_POS_X_OFFSET + 4] = struct.pack("<f", x)
    fb[FEEDBACK_POS_Y_OFFSET:FEEDBACK_POS_Y_OFFSET + 4] = struct.pack("<f", y)
    return bytes(fb)


def checksum(buf):
    return sum(buf[:UART_PACKET_SIZE - 1]) & 0xFF


class Bridge(object):
    """ai_cmd_v2.out を --debug で起動し、送信されるはずの 72 バイトを読み出す。"""

    def __init__(self, port_base, robot_id=0, extra_args=None, debug=True):
        self.cmd_port = port_base
        self.cam_port = port_base + 1
        self.feedback_port = port_base + 2
        self.robot_id = robot_id
        self.master, self.slave = pty.openpty()
        args = [BIN] + (["--debug"] if debug else [])
        args += ["--serial-port", os.ttyname(self.slave),
                "--robot-id", str(robot_id),
                "--ai-cmd-port", str(self.cmd_port),
                "--local-cam-port", str(self.cam_port),
                "--feedback-port", str(self.feedback_port)]
        if extra_args:
            args += extra_args
        self.log_path = "/tmp/test_ai_cmd_v2_%d.log" % port_base
        self.log = open(self.log_path, "w+")
        self.proc = subprocess.Popen(["stdbuf", "-oL"] + args, stdout=self.log, stderr=subprocess.STDOUT, text=True)
        self.tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # --debug なしでは pty へ本当に書く。読み手が居ないと pty のバッファが
        # 埋まって write_some がブロックし、制御ループごと止まる。
        self.drain_stop = False
        self.drain = None
        if not debug:
            self.drain = threading.Thread(target=self._drain_pty, daemon=True)
            self.drain.start()
        time.sleep(0.5)
        if self.proc.poll() is not None:
            raise RuntimeError("ai_cmd_v2.out が起動直後に終了しました:\n" + self._read_log())

    def _drain_pty(self):
        while not self.drain_stop:
            try:
                if not os.read(self.master, 4096):
                    return
            except OSError:
                return

    def _read_log(self):
        self.log.flush()
        pos = self.log.tell()
        self.log.seek(0)
        text = self.log.read()
        self.log.seek(pos)
        return text

    def send_command(self, command):
        self.tx.sendto(build_packet(self.robot_id, command), ("127.0.0.1", self.cmd_port))

    def send_raw(self, payload):
        self.tx.sendto(payload, ("127.0.0.1", self.cmd_port))

    def send_feedback(self, x, y):
        self.tx.sendto(build_feedback(x, y), ("127.0.0.1", self.feedback_port))

    def frames(self):
        """--debug 出力を 72 バイトのリスト列として返す（送信順）。"""
        out = []
        for line in self._read_log().splitlines():
            tokens = HEX_TOKEN.findall(line.strip())
            if len(tokens) != UART_PACKET_SIZE:
                continue
            if HEX_TOKEN.sub("", line.strip()).strip() != "":
                continue
            # pritBinData は uint8_t へキャスト済みだが、比較前に念のため下位 1 バイトへ正規化する。
            out.append([int(t, 16) & 0xFF for t in tokens])
        return out

    def close(self):
        self.drain_stop = True
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)
        self.tx.close()
        self.log.close()
        os.close(self.master)
        os.close(self.slave)


class ForwardAiCmdV2Test(unittest.TestCase):
    def setUp(self):
        if not os.path.exists(BIN):
            self.skipTest("%s がありません。cm4/build.sh を先に実行してください" % BIN)
        self.bridges = []

    def tearDown(self):
        for bridge in self.bridges:
            bridge.close()

    def start(self, port_base, **kwargs):
        bridge = Bridge(port_base, **kwargs)
        self.bridges.append(bridge)
        return bridge

    def assert_passthrough(self, frame, command):
        """素通し経路は byte0 を 0xFE にする以外、受信 64 バイトを変えない。"""
        self.assertEqual(frame[0], 0xFE)
        self.assertEqual(bytes(frame[1:CMD_SIZE]), command[1:CMD_SIZE])
        # カメラ未接続なので byte 64..70 はゼロ、byte 71 はチェックサム。
        self.assertEqual(frame[CMD_SIZE:UART_PACKET_SIZE - 1], [0] * 7)
        self.assertEqual(frame[UART_PACKET_SIZE - 1], checksum(frame))

    def test_mode3_is_forwarded_unchanged(self):
        bridge = self.start(12420)
        commands = [build_command(c, POLAR_VELOCITY_TARGET_MODE) for c in range(1, 6)]
        for command in commands:
            bridge.send_command(command)
            time.sleep(0.05)
        time.sleep(0.2)
        frames = bridge.frames()
        self.assertEqual(len(frames), len(commands), "mode 3 は crane の check_counter 変化ごとに 1 回だけ送る")
        for frame, command in zip(frames, commands):
            self.assert_passthrough(frame, command)

    def test_passthrough_flag_forwards_mode4_unchanged(self):
        bridge = self.start(12430, extra_args=["--passthrough"])
        commands = [build_command(c, POSITION_TARGET_WITH_TERMINAL_VELOCITY_MODE) for c in range(1, 6)]
        for command in commands:
            bridge.send_command(command)
            time.sleep(0.05)
        time.sleep(0.2)
        frames = bridge.frames()
        self.assertEqual(len(frames), len(commands))
        for frame, command in zip(frames, commands):
            self.assert_passthrough(frame, command)
            self.assertEqual(frame[CONTROL_MODE], POSITION_TARGET_WITH_TERMINAL_VELOCITY_MODE,
                             "--passthrough は mode 4 を変換せずそのまま流す")

    def test_mode4_closes_position_loop_and_emits_mode3(self):
        bridge = self.start(12440)
        command = build_command(1, POSITION_TARGET_WITH_TERMINAL_VELOCITY_MODE, target=(2.0, -1.0), velocity_limit=3.0)
        deadline = time.time() + 1.0
        while time.time() < deadline:
            bridge.send_command(command)
            bridge.send_feedback(0.0, 0.0)
            time.sleep(0.02)
        frames = [f for f in bridge.frames() if f[FLAGS] & (1 << STOP_EMERGENCY_BIT) == 0]
        self.assertTrue(frames, "feedback が届いていれば安全停止せず速度が出るはず")
        frame = frames[-1]
        self.assertEqual(frame[CONTROL_MODE], POLAR_VELOCITY_TARGET_MODE, "mode 4 を G474 へ流してはならない")
        r = dec(frame[CONTROL_MODE_ARGS], frame[CONTROL_MODE_ARGS + 1], 32.767)
        theta = dec(frame[CONTROL_MODE_ARGS + 2], frame[CONTROL_MODE_ARGS + 3], 32.767)
        # 現在位置 (0,0) から目標 (2,-1) へ。|e|=2.236 なので kp=2 で 4.47 m/s、
        # velocity_limit=3.0 と制動エンベロープ sqrt(2*3*2.236)=3.66 の小さい方で 3.0 に丸まる。
        self.assertAlmostEqual(r, 3.0, delta=0.02)
        self.assertAlmostEqual(theta, math.atan2(-1.0, 2.0), delta=0.02)
        # check_counter は CM4 が採番するので、送信ごとに変わらなければならない。
        counters = [f[CHECK_COUNTER] for f in bridge.frames()]
        self.assertTrue(len(set(counters)) > 5, "check_counter が変化しないと G474 の connected_ai が落ちる")
        for a, b in zip(counters, counters[1:]):
            self.assertNotEqual(a, b)

    def test_stops_when_crane_goes_silent(self):
        """検収条件 4: crane 無通信で速度指令がゼロになること。"""
        bridge = self.start(12450, extra_args=["--command-timeout-ms", "100"])
        command = build_command(1, POSITION_TARGET_WITH_TERMINAL_VELOCITY_MODE, target=(2.0, -1.0))
        deadline = time.time() + 0.5
        while time.time() < deadline:
            bridge.send_command(command)
            bridge.send_feedback(0.0, 0.0)
            time.sleep(0.02)
        moving = [f for f in bridge.frames() if f[FLAGS] & (1 << STOP_EMERGENCY_BIT) == 0]
        self.assertTrue(moving, "止める前に一度は動いている必要がある")

        # crane だけ止める。feedback は流し続ける。
        silent_start = time.time()
        while time.time() - silent_start < 0.4:
            bridge.send_feedback(0.0, 0.0)
            time.sleep(0.02)

        frame = bridge.frames()[-1]
        r = dec(frame[CONTROL_MODE_ARGS], frame[CONTROL_MODE_ARGS + 1], 32.767)
        self.assertEqual(frame[CONTROL_MODE], POLAR_VELOCITY_TARGET_MODE)
        self.assertAlmostEqual(r, 0.0, delta=1e-3, msg="crane 断で速度指令がゼロにならない")
        self.assertTrue(frame[FLAGS] & (1 << STOP_EMERGENCY_BIT), "crane 断で STOP_EMERGENCY を立てる")
        self.assertEqual(frame[KICK_POWER], 0, "crane 断で古いキック指令を撃ち続けない")
        self.assertEqual(frame[DRIBBLE_POWER], 0)

    def test_stops_when_feedback_goes_silent(self):
        """feedback は位置制御ループ内で唯一の位置信号。途絶したら止めるしかない。"""
        bridge = self.start(12460, extra_args=["--feedback-timeout-ms", "100"])
        command = build_command(1, POSITION_TARGET_WITH_TERMINAL_VELOCITY_MODE, target=(2.0, -1.0))
        deadline = time.time() + 0.5
        while time.time() < deadline:
            bridge.send_command(command)
            bridge.send_feedback(0.0, 0.0)
            time.sleep(0.02)
        self.assertTrue([f for f in bridge.frames() if f[FLAGS] & (1 << STOP_EMERGENCY_BIT) == 0])

        # feedback だけ止める。crane は流し続ける。
        counter = 100
        silent_start = time.time()
        while time.time() - silent_start < 0.4:
            counter = counter + 1 if counter < 200 else 0
            bridge.send_command(build_command(counter, POSITION_TARGET_WITH_TERMINAL_VELOCITY_MODE, target=(2.0, -1.0)))
            time.sleep(0.02)

        frame = bridge.frames()[-1]
        r = dec(frame[CONTROL_MODE_ARGS], frame[CONTROL_MODE_ARGS + 1], 32.767)
        self.assertAlmostEqual(r, 0.0, delta=1e-3, msg="feedback 断で速度指令がゼロにならない")
        self.assertTrue(frame[FLAGS] & (1 << STOP_EMERGENCY_BIT))

    def test_startup_without_feedback_does_not_move(self):
        """起動直後は feedback 未受信。位置が分からないまま動かしてはならない。"""
        bridge = self.start(12470)
        for c in range(1, 6):
            bridge.send_command(build_command(c, POSITION_TARGET_WITH_TERMINAL_VELOCITY_MODE, target=(2.0, -1.0)))
            time.sleep(0.05)
        time.sleep(0.2)
        frames = bridge.frames()
        self.assertTrue(frames)
        for frame in frames:
            self.assertEqual(frame[CONTROL_MODE], POLAR_VELOCITY_TARGET_MODE)
            r = dec(frame[CONTROL_MODE_ARGS], frame[CONTROL_MODE_ARGS + 1], 32.767)
            self.assertAlmostEqual(r, 0.0, delta=1e-3)
            self.assertTrue(frame[FLAGS] & (1 << STOP_EMERGENCY_BIT))

    def test_stops_when_vision_is_unavailable(self):
        """crane が見失っている間の target_global_pos は推測値。そこへ走ってはならない。

        G474 は state_func.c:314 の同じ条件でホイールを止めるので実機の挙動は
        変わらないが、CM4 で止めることで simulator-cli 側（このビットを見ない）と
        挙動が揃う。
        """
        bridge = self.start(12475)
        for c in range(1, 8):
            bridge.send_command(build_command(c, POSITION_TARGET_WITH_TERMINAL_VELOCITY_MODE, target=(2.0, -1.0), flags=0x00))
            bridge.send_feedback(0.0, 0.0)
            time.sleep(0.03)
        time.sleep(0.1)
        frames = bridge.frames()
        self.assertTrue(frames)
        for frame in frames:
            self.assertEqual(frame[CONTROL_MODE], POLAR_VELOCITY_TARGET_MODE)
            r = dec(frame[CONTROL_MODE_ARGS], frame[CONTROL_MODE_ARGS + 1], 32.767)
            self.assertAlmostEqual(r, 0.0, delta=1e-3, msg="vision 不可でも動いている")
            self.assertTrue(frame[FLAGS] & (1 << STOP_EMERGENCY_BIT))

    def test_position_state_is_logged_in_normal_mode(self):
        """--debug なしの実運用モードで位置制御の状態表示が出ること。

        他のテストは --debug 経路しか通らないので、実運用の表示だけが無言に
        なっていても気付けない。表示は「crane から新しい指令が来た周期」と
        「停止理由が変わった周期」に限る設計なので、crane レート (20Hz) 相当で
        出て UART 送信レート (100Hz) 相当では出ないことまで検査する。
        """
        bridge = self.start(12495, debug=False)
        counter = 0
        deadline = time.time() + 1.0
        while time.time() < deadline:
            counter = counter + 1 if counter < 200 else 0
            bridge.send_command(build_command(counter, POSITION_TARGET_WITH_TERMINAL_VELOCITY_MODE, target=(2.0, -1.0)))
            bridge.send_feedback(0.0, 0.0)
            time.sleep(0.05)  # 20Hz
        time.sleep(0.2)

        lines = [ln for ln in bridge._read_log().splitlines() if "POS[" in ln]
        self.assertTrue(lines, "位置制御の状態が一度も表示されていない:\n" + bridge._read_log()[-2000:])
        self.assertIn("POS[Ok]", "\n".join(lines))
        self.assertGreaterEqual(len(lines), 10, "crane の指令ごとに出ていない (%d 行)" % len(lines))
        self.assertLessEqual(len(lines), 45, "UART 送信のたびに出ている (%d 行)" % len(lines))

    def test_malformed_datagrams_are_discarded(self):
        """715 バイト以外は捨てる。旧実装は recv の戻り値を見ていなかった。"""
        bridge = self.start(12480)
        command = build_command(7, POLAR_VELOCITY_TARGET_MODE)
        bridge.send_command(command)
        time.sleep(0.1)
        before = len(bridge.frames())
        for payload in (b"", b"\x00" * 64, b"\x00" * (PACKET_SIZE - 1), b"\x00" * (PACKET_SIZE + 1)):
            bridge.send_raw(payload)
            time.sleep(0.05)
        time.sleep(0.2)
        frames = bridge.frames()
        self.assertEqual(len(frames), before, "不正長のデータグラムで送信してはならない")
        self.assert_passthrough(frames[-1], command)

    def test_exits_when_robot_id_is_out_of_range(self):
        """ID が決まらないまま 0 号機として動き出さないこと。"""
        master, slave = pty.openpty()
        try:
            proc = subprocess.run([BIN, "--debug", "--serial-port", os.ttyname(slave), "--robot-id", "99"],
                                  capture_output=True, text=True, timeout=10)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("ロボット ID を決定できません", proc.stdout + proc.stderr)
        finally:
            os.close(master)
            os.close(slave)


if __name__ == "__main__":
    unittest.main()
