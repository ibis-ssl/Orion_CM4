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
    TERMINAL_VELOCITY_HIGH, build_config_packet, build_packet, encode_two_byte as enc)
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
        self.config_port = port_base + 3
        self.robot_id = robot_id
        self.master, self.slave = pty.openpty()
        args = [BIN] + (["--debug"] if debug else [])
        args += ["--serial-port", os.ttyname(self.slave),
                "--robot-id", str(robot_id),
                "--ai-cmd-port", str(self.cmd_port),
                "--local-cam-port", str(self.cam_port),
                "--feedback-port", str(self.feedback_port),
                # 既定の 12350 のままだと、テストを並べたときに 2 プロセス目の
                # bind が失敗する (設定ポートには意図的に SO_REUSEADDR を付けていない)。
                "--config-port", str(self.config_port)]
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
        self._wait_until_running()

    def _wait_until_running(self, deadline_s=5.0):
        """起動バナーの最終行が出るまで待つ。

        固定 sleep だと、遅いときは足りず速いときは待ちすぎる。"control kp" は
        全オプションの解析と検証を通り抜けたあとに出るので、この行が出た時点で
        引数エラーで落ちないことと UART/ソケットの初期化開始が確定する。
        """
        end = time.monotonic() + deadline_s
        while time.monotonic() < end:
            if "control kp" in self._read_log():
                return
            if self.proc.poll() is not None:
                raise RuntimeError("ai_cmd_v2.out が起動直後に終了しました:\n" + self._read_log())
            time.sleep(0.01)
        raise RuntimeError(f"ai_cmd_v2.out が {deadline_s}s 以内に起動を終えませんでした:\n" + self._read_log())

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

    def send_config(self, kp, decel, tolerance, robot_id=0xFF):
        self.tx.sendto(build_config_packet(kp, decel, tolerance, robot_id), ("127.0.0.1", self.config_port))

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

    def test_config_packet_updates_gain_while_running(self):
        """crane からの設定パケットで位置制御ゲインが稼働中に変わること。

        実機バイナリ側でも同じ経路 (config_packet.h) を通ることを押さえる。
        シミュレータ側だけ検査していると、現地で効かないことに実機で気付く。
        """
        bridge = self.start(12490)

        def drive_and_measure(seconds=0.4):
            # 目標まで 0.1 m。制動エンベロープ sqrt(2*3*0.1)=0.77 m/s なので
            # kp を 2 -> 4 にしても (0.2 -> 0.4) クランプに当たらない。
            command = build_command(1, POSITION_TARGET_WITH_TERMINAL_VELOCITY_MODE, target=(0.1, 0.0))
            deadline = time.time() + seconds
            while time.time() < deadline:
                bridge.send_command(command)
                bridge.send_feedback(0.0, 0.0)
                time.sleep(0.02)
            frames = [f for f in bridge.frames() if f[FLAGS] & (1 << STOP_EMERGENCY_BIT) == 0]
            self.assertTrue(frames, "feedback が届いていれば安全停止せず速度が出るはず")
            return dec(frames[-1][CONTROL_MODE_ARGS], frames[-1][CONTROL_MODE_ARGS + 1], 32.767)

        self.assertAlmostEqual(drive_and_measure(), 0.2, delta=0.01, msg="既定ゲイン kp=2.0")
        bridge.send_config(kp=4.0, decel=3.0, tolerance=0.01)
        time.sleep(0.05)
        self.assertAlmostEqual(drive_and_measure(), 0.4, delta=0.01, msg="kp=4.0 適用後")
        self.assertIn("位置制御の設定を更新", bridge._read_log())

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

    def _measure_stop_latency_ms(self, port_base, tx_rate_hz):
        """crane の最後の送信から STOP_EMERGENCY が出るまでの実測値 [ms]。"""
        bridge = self.start(port_base, extra_args=[
            "--command-timeout-ms", "100", "--tx-rate-hz", str(tx_rate_hz)])
        command = build_command(1, POSITION_TARGET_WITH_TERMINAL_VELOCITY_MODE, target=(2.0, -1.0))
        deadline = time.time() + 0.4
        while time.time() < deadline:
            bridge.send_command(command)
            bridge.send_feedback(0.0, 0.0)
            time.sleep(0.02)

        # 最後の 1 通の時刻を原点にする。「送信ループを抜けた時刻」を原点にすると
        # 最後の sendto から 1 周期ぶん (ここでは 20 ms) 短く出る。
        bridge.send_command(command)
        bridge.send_feedback(0.0, 0.0)
        last_command_at = time.time()

        # ここから crane だけ黙る。feedback は流し続ける。
        stopped_at = None
        while time.time() - last_command_at < 0.5:
            bridge.send_feedback(0.0, 0.0)
            frames = bridge.frames()
            if frames and frames[-1][FLAGS] & (1 << STOP_EMERGENCY_BIT):
                stopped_at = time.time()
                break
            time.sleep(0.002)
        self.assertIsNotNone(stopped_at, f"--tx-rate-hz {tx_rate_hz} で停止指令が出なかった")
        return (stopped_at - last_command_at) * 1000.0

    def test_stop_latency_does_not_depend_on_tx_rate(self):
        """停止指令は --tx-rate-hz のゲートを待たない。

        停止理由が変わった周期はレートに関わらず即送信する
        (forward_ai_cmd_v2.cpp の safety_changed)。これが壊れると、低い
        --tx-rate-hz ではゲート 1 周期ぶん停止が遅れる。20 Hz なら最大 50 ms で、
        「crane 断から約 104 ms」の予算を大きく超える。

        「動かないこと」を検査にしておかないと、レートを上げ下げしたときに
        停止レイテンシが一緒に動いても誰も気づかない。
        """
        fast = self._measure_stop_latency_ms(12470, tx_rate_hz=200)
        slow = self._measure_stop_latency_ms(12475, tx_rate_hz=20)

        # 予算は --command-timeout-ms(100) + ポーリング 1 ms + ログ検出の粒度。
        for name, value in (("200 Hz", fast), ("20 Hz", slow)):
            self.assertGreater(value, 90.0, f"{name}: {value:.1f} ms は timeout より早すぎる")
            self.assertLess(value, 145.0, f"{name}: {value:.1f} ms は停止予算を超えている")

        # 20 Hz のゲート 1 周期は 50 ms。待っていればここに出る。
        self.assertLess(abs(fast - slow), 30.0,
                        f"--tx-rate-hz で停止レイテンシが変わっている (200Hz {fast:.1f} ms / 20Hz {slow:.1f} ms)")

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

    def _run_feedback_silence_scenario(self, bridge, mode, silent_seconds, resume_feedback=True):
        """feedback と指令を流し、feedback だけ止めて、送信されたフレーム数の推移を返す。

        戻り値: (無音前の総数, 無音の前半を過ぎた時点の総数, 無音の終わりの総数, feedback 再開後の総数)
        """
        counter = 0

        def tick(with_feedback):
            nonlocal counter
            counter = counter + 1 if counter < 200 else 0
            bridge.send_command(build_command(counter, mode))
            if with_feedback:
                bridge.send_feedback(0.0, 0.0)
            time.sleep(0.02)

        end = time.time() + 0.4
        while time.time() < end:
            tick(True)
        time.sleep(0.05)
        before = len(bridge.frames())

        silent_start = time.time()
        mid = None
        while time.time() - silent_start < silent_seconds:
            tick(False)
            if mid is None and time.time() - silent_start > silent_seconds / 2:
                mid = len(bridge.frames())
        time.sleep(0.05)
        after_silence = len(bridge.frames())

        if resume_feedback:
            end = time.time() + 0.3
            while time.time() < end:
                tick(True)
            time.sleep(0.05)
        return before, mid, after_silence, len(bridge.frames())

    def test_suspends_uart_tx_while_g474_feedback_is_silent(self):
        """G474 の TX だけが死ぬ不具合への緩和。feedback が無音のあいだ UART へ送らず、再開したら即送る。

        G474 は CM4 通信が途絶えたまま 6.5 秒続くと自己リセットする。crane が指令を送り続けると
        CM4 も送り続けてしまい、その自己リセットが起きない。
        """
        bridge = self.start(12480, extra_args=["--g474-silence-ms", "300", "--g474-recovery-ms", "5000"])
        before, mid, after_silence, after_resume = self._run_feedback_silence_scenario(
            bridge, POLAR_VELOCITY_TARGET_MODE, silent_seconds=1.2)
        self.assertGreater(before, 5, "feedback が有るあいだは送っているはず")
        # 無音 300ms を過ぎれば止まる。無音の中盤 (0.6s) 以降は 1 本も増えない。
        self.assertEqual(mid, after_silence, "feedback 無音中に UART 送信が続いている")
        self.assertGreater(after_resume, after_silence, "feedback が戻ったのに送信が再開しない")
        self.assertIn("UART 送信を停止", bridge._read_log())
        self.assertIn("UART 送信を再開", bridge._read_log())

    def test_suspends_uart_tx_in_position_control_path_too(self):
        """mode 4 (位置制御パス、CM4 が check_counter を採番して 100Hz で送る) でも同じ。"""
        bridge = self.start(12490, extra_args=["--g474-silence-ms", "300", "--g474-recovery-ms", "5000"])
        before, mid, after_silence, after_resume = self._run_feedback_silence_scenario(
            bridge, POSITION_TARGET_WITH_TERMINAL_VELOCITY_MODE, silent_seconds=1.2)
        self.assertGreater(before, 5)
        self.assertEqual(mid, after_silence, "mode 4 でも feedback 無音中は UART へ送らない")
        self.assertGreater(after_resume, after_silence)

    def test_g474_silence_recovery_can_be_disabled(self):
        bridge = self.start(12500, extra_args=["--g474-silence-ms", "0"])
        before, mid, after_silence, _ = self._run_feedback_silence_scenario(
            bridge, POLAR_VELOCITY_TARGET_MODE, silent_seconds=1.0, resume_feedback=False)
        self.assertGreater(after_silence, mid, "--g474-silence-ms 0 では feedback 無音でも送り続ける (従来動作)")
        self.assertNotIn("UART 送信を停止", bridge._read_log())

    def test_g474_recovery_resumes_after_timeout_and_repeats(self):
        """G474 が復帰しない場合も、待ち時間切れで一度送信を再開して様子を見る。"""
        bridge = self.start(12510, extra_args=["--g474-silence-ms", "200", "--g474-recovery-ms", "500"])
        _, _, _, _ = self._run_feedback_silence_scenario(
            bridge, POLAR_VELOCITY_TARGET_MODE, silent_seconds=1.6, resume_feedback=False)
        log = bridge._read_log()
        self.assertIn("時間切れ", log, "待ち時間 500ms を過ぎたら再開するはず")
        self.assertGreaterEqual(log.count("UART 送信を停止"), 2, "無音が続くなら停止を繰り返す")

    def test_startup_without_feedback_suspends_after_silence_window(self):
        """feedback が一度も来ないまま起動した場合も、無音の起点は起動時刻。"""
        bridge = self.start(12520, extra_args=["--g474-silence-ms", "300", "--g474-recovery-ms", "5000"])
        counter = 0
        deadline = time.time() + 1.0
        while time.time() < deadline:
            counter = counter + 1 if counter < 200 else 0
            bridge.send_command(build_command(counter, POLAR_VELOCITY_TARGET_MODE))
            time.sleep(0.02)
        self.assertIn("UART 送信を停止", bridge._read_log())

    def test_rejects_invalid_g474_recovery_options(self):
        master, slave = pty.openpty()
        try:
            proc = subprocess.run([BIN, "--debug", "--serial-port", os.ttyname(slave), "--robot-id", "0",
                                   "--g474-recovery-ms", "0"], capture_output=True, text=True, timeout=10)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("--g474-recovery-ms", proc.stdout + proc.stderr)
        finally:
            os.close(master)
            os.close(slave)

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
