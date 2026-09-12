"""cm4_sim の結合スモークテストを担当する。

cm4_sim.out 単体を相手にする（simulator-cli は起動しない）ので、CI で
framework リポジトリに依存しない。実チェーンの検証は test_cm4_sim_chain.py。

検査するもの:
  1. mode 4 の 715B を投げると mode 3 の 715B が出る（CHECK_COUNTER が毎回変化）
  2. 担当しないスロットが robot_id=0xFF + コマンド 64B ゼロ
  3. feedback の位置が出力の VISION_GLOBAL_X/Y へ反映される
  4. crane 無通信で速度指令がゼロになり STOP_EMERGENCY が立つ
  5. --seed 固定で劣化注入（遅延・ジッタ・ロス）が再現する

既定から離れたポートを使うので、動作中の試合やシミュレータには影響しない。

実行: python3 -m unittest discover -s cm4/bridge -p 'test_cm4_sim.py'
"""

import itertools
import math
import os
import socket
import struct
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BINARY = REPO / "cm4" / "bin" / "cm4_sim.out"

CMD_SIZE = 64
SLOTS = 11
SLOT_SIZE = CMD_SIZE + 1
PACKET_SIZE = SLOT_SIZE * SLOTS
FEEDBACK_SIZE = 128

MODE_POLAR_VELOCITY = 3
MODE_POSITION_TARGET = 4
KICK_POWER = 10
DRIBBLE_POWER = 11

# 既定 (12345 / 12346 / 50100) から離す
# ポートはテストごとにずらし、さらにプロセスごとにもずらす。
# 全テストで同じポートを使うと、前のテストの cm4_sim がまだ終了しきっていない
# ときに bind が EADDRINUSE で失敗し（crane 入力ソケットには意図的に
# SO_REUSEADDR を付けていない）、「起動したつもりで何も返ってこない」形で
# 散発的に落ちる。落ちたテストが残したプロセスが次の実行まで生き残る例もあった。
PORT_BASE = 20000 + (os.getpid() % 300) * 64
FEEDBACK_BASE = 40000 + (os.getpid() % 90) * 256
_port_slot = itertools.count()

# robot_packet.h の enum Address
CHECK_COUNTER = 1
VISION_GLOBAL_X_HIGH = 2
VISION_GLOBAL_Y_HIGH = 4
ACCELERATION_LIMIT_HIGH = 12
LINEAR_VELOCITY_LIMIT_HIGH = 14
ANGULAR_VELOCITY_LIMIT_HIGH = 16
FLAGS = 22
CONTROL_MODE = 23
CONTROL_MODE_ARGS = 24
TARGET_GLOBAL_POS_X_HIGH = 32
TARGET_GLOBAL_POS_Y_HIGH = 34
TERMINAL_VELOCITY_HIGH = 36

STOP_EMERGENCY_BIT = 3


def encode_two_byte(value, value_range):
    """crane の convertFloatToTwoByte と同じ式（丸めずに切り捨て）。"""
    raw = int(32767.0 * (value / value_range) + 32767.0)
    raw = max(0, min(65535, raw))
    return bytes([(raw >> 8) & 0xFF, raw & 0xFF])


def decode_two_byte(data, offset, value_range):
    raw = (data[offset] << 8) | data[offset + 1]
    return (raw - 32767.0) / 32767.0 * value_range


def build_command(counter, target=(1.0, 0.0), vision=(0.0, 0.0), mode=MODE_POSITION_TARGET,
                  terminal_velocity_xy=(0.0, 0.0), terminal_velocity=0.0,
                  linear_velocity_limit=3.0, stop_emergency=False, vision_available=True):
    d = bytearray(CMD_SIZE)
    d[CHECK_COUNTER] = counter & 0xFF
    d[VISION_GLOBAL_X_HIGH:VISION_GLOBAL_X_HIGH + 2] = encode_two_byte(vision[0], 32.767)
    d[VISION_GLOBAL_Y_HIGH:VISION_GLOBAL_Y_HIGH + 2] = encode_two_byte(vision[1], 32.767)
    d[6:8] = encode_two_byte(0.0, math.pi)      # VISION_GLOBAL_THETA
    d[8:10] = encode_two_byte(0.0, math.pi)     # TARGET_GLOBAL_THETA
    d[ACCELERATION_LIMIT_HIGH:ACCELERATION_LIMIT_HIGH + 2] = encode_two_byte(4.0, 32.767)
    d[LINEAR_VELOCITY_LIMIT_HIGH:LINEAR_VELOCITY_LIMIT_HIGH + 2] = encode_two_byte(linear_velocity_limit, 32.767)
    d[ANGULAR_VELOCITY_LIMIT_HIGH:ANGULAR_VELOCITY_LIMIT_HIGH + 2] = encode_two_byte(5.0, 32.767)
    d[FLAGS] = (0x01 if vision_available else 0x00) | ((1 << STOP_EMERGENCY_BIT) if stop_emergency else 0)
    d[CONTROL_MODE] = mode
    d[CONTROL_MODE_ARGS:CONTROL_MODE_ARGS + 2] = encode_two_byte(terminal_velocity_xy[0], 32.767)
    d[CONTROL_MODE_ARGS + 2:CONTROL_MODE_ARGS + 4] = encode_two_byte(terminal_velocity_xy[1], 32.767)
    d[TARGET_GLOBAL_POS_X_HIGH:TARGET_GLOBAL_POS_X_HIGH + 2] = encode_two_byte(target[0], 32.767)
    d[TARGET_GLOBAL_POS_Y_HIGH:TARGET_GLOBAL_POS_Y_HIGH + 2] = encode_two_byte(target[1], 32.767)
    d[TERMINAL_VELOCITY_HIGH:TERMINAL_VELOCITY_HIGH + 2] = encode_two_byte(terminal_velocity, 32.767)
    return bytes(d)


def build_packet(robot_id, command):
    """715 バイト。crane と同じく未使用スロットは添字 + 64B ゼロ埋め。"""
    pkt = bytearray()
    for slot in range(SLOTS):
        pkt += bytes([slot]) + (command if slot == robot_id else bytes(CMD_SIZE))
    return bytes(pkt)


def build_feedback(robot_id, counter, x, y):
    """simulator-cli の ibisBuildFeedbackPacket と同じバイト配置。"""
    d = bytearray(FEEDBACK_SIZE)
    d[0], d[1] = 0xAB, 0xEA
    d[2] = robot_id
    d[3] = counter & 0xFF
    struct.pack_into("<f", d, 4, 0.0)    # yaw (制御では使わない)
    struct.pack_into("<f", d, 44, x)     # vision_based_position_x
    struct.pack_into("<f", d, 48, y)     # vision_based_position_y
    struct.pack_into("<f", d, 52, 0.0)   # 速度
    struct.pack_into("<f", d, 56, 0.0)
    d[60] = 0x01
    return bytes(d)


def slot_of(packet, robot_id):
    off = robot_id * SLOT_SIZE
    return packet[off], packet[off + 1:off + 1 + CMD_SIZE]


# 起動した cm4_sim を必ず後始末するための登録簿。テストが finally へ到達せずに
# 落ちたとき、残ったプロセスが次のテスト・次の実行のポートを塞いでしまう。
_live_sims = []


class Cm4Sim:
    """cm4_sim.out を起動し、crane 側と simulator-cli 側の両方を演じるヘルパ。"""

    def __init__(self, robot_ids="0", extra_args=(), out_port=None, in_port=None,
                 feedback_base=None, relay=False, relay_port_base=None):
        slot = next(_port_slot) % 16
        in_port = PORT_BASE + slot * 4 if in_port is None else in_port
        out_port = PORT_BASE + slot * 4 + 1 if out_port is None else out_port
        feedback_base = FEEDBACK_BASE + slot * 16 if feedback_base is None else feedback_base
        # 再配信先は入力ポートと独立 (crane の crane_robot_receiver は 50100+id 固定)。
        # テストでは既定の 50100 を塞がないよう、入力と同じ値を明示指定する。
        relay_port_base = feedback_base if relay_port_base is None else relay_port_base
        self.feedback_relay_port_base = relay_port_base
        _live_sims.append(self)
        self.in_port = in_port
        self.feedback_base = feedback_base
        # simulator-cli 役: cm4_sim の出力を受ける
        self.out_rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.out_rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.out_rx.bind(("127.0.0.1", out_port))
        # crane 役 / feedback 送出役
        self.tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # テストでは送出レートを落とす。既定の 1kHz は 715KB/s になり、
        # 受信側のドレインが生成に追いつかず終わらなくなる。
        args = [str(BINARY),
                "--rate-hz", "100",
                "--robot-ids", robot_ids,
                "--in-port", str(in_port),
                "--out-addr", "127.0.0.1", "--out-port", str(out_port),
                "--feedback-port-base", str(feedback_base),
                "--feedback-relay-port-base", str(relay_port_base),
                *(() if relay else ("--no-feedback-relay",)),  # 既定ではテストで multicast を出さない
                *extra_args]
        self.log_path = Path(tempfile.mkdtemp(prefix="cm4-sim-")) / "cm4_sim.log"
        self.log = open(self.log_path, "w+")
        self.proc = subprocess.Popen(args, stdout=self.log, stderr=subprocess.STDOUT)
        time.sleep(0.4)
        # 起動失敗を「出力が来ない」ではなく起動時点で検出する。
        if self.proc.poll() is not None:
            self.log.flush()
            self.log.seek(0)
            raise RuntimeError("cm4_sim.out が起動直後に終了しました:\n" + self.log.read())

    def send_command(self, packet):
        self.tx.sendto(packet, ("127.0.0.1", self.in_port))

    def send_feedback(self, robot_id, counter, x, y):
        self.tx.sendto(build_feedback(robot_id, counter, x, y),
                       ("127.0.0.1", self.feedback_base + robot_id))

    def recv_output(self, timeout=2.0):
        self.out_rx.settimeout(timeout)
        try:
            data = self.out_rx.recv(2048)
        except socket.timeout:
            return None
        return data if len(data) == PACKET_SIZE else None

    def recv_latest_output(self, timeout=2.0, max_drain=500):
        """バッファに溜まっている中で最も新しい出力を返す。

        cm4_sim は制御周期ごとに送るので、recv() が返すのは「最も古い」パケット。
        タイムアウト経過後の状態を見たいときはこちらを使う。
        ドレインは必ず回数で打ち切る（時間だけで切ると生成に追いつけず終わらない）。
        """
        latest = self.recv_output(timeout)
        if latest is None:
            return None
        self.out_rx.settimeout(0.005)
        for _ in range(max_drain):
            try:
                data = self.out_rx.recv(2048)
            except socket.timeout:
                break
            if len(data) == PACKET_SIZE:
                latest = data
        return latest

    def drain_output(self, max_drain=2000):
        self.out_rx.settimeout(0.005)
        for _ in range(max_drain):
            try:
                self.out_rx.recv(2048)
            except socket.timeout:
                return

    def close(self):
        if self in _live_sims:
            _live_sims.remove(self)
        if self.proc.poll() is None:
            self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self.out_rx.close()
        self.tx.close()
        if not self.log.closed:
            self.log.flush()
            self.log.seek(0)
            self.output = self.log.read()
            self.log.close()


@unittest.skipUnless(BINARY.exists(), f"{BINARY} が無い。先に ./cm4/build.sh を実行すること")
class Cm4SimSmokeTest(unittest.TestCase):
    def tearDown(self):
        # finally へ到達せずに落ちたテストの cm4_sim を確実に始末する。
        while _live_sims:
            sim = _live_sims[-1]
            try:
                sim.close()
            except Exception:
                pass
            if sim in _live_sims:
                _live_sims.remove(sim)


    def test_mode4_in_mode3_out(self):
        """mode 4 を受けて mode 3 を出し、CHECK_COUNTER が毎回変化すること。"""
        sim = Cm4Sim(robot_ids="0")
        try:
            # cm4_sim は起動直後から制御周期ごとに送り続けるので、最初の数個は
            # crane コマンド未受信の空スロット。まず 1 つ流し込んで排水する。
            sim.send_command(build_packet(0, build_command(1, target=(1.0, 0.0))))
            sim.send_feedback(0, 1, 0.0, 0.0)
            time.sleep(0.1)
            sim.drain_output()

            counters = []
            for i in range(2, 32):
                sim.send_command(build_packet(0, build_command(i, target=(1.0, 0.0))))
                sim.send_feedback(0, i, 0.0, 0.0)
                time.sleep(0.03)
                out = sim.recv_latest_output()
                self.assertIsNotNone(out, "715 バイトの出力が来ない")
                robot_id, cmd = slot_of(out, 0)
                self.assertEqual(robot_id, 0)
                self.assertEqual(cmd[CONTROL_MODE], MODE_POLAR_VELOCITY,
                                 "出力の control_mode は 3 でなければならない")
                counters.append(cmd[CHECK_COUNTER])

            # simulator-cli は前回と同じ check_counter のスロットを丸ごと無視する
            for a, b in zip(counters, counters[1:]):
                self.assertNotEqual(a, b, "CHECK_COUNTER が連続で同じ値になっている")
        finally:
            sim.close()

    def test_unhandled_slots_are_empty(self):
        """担当しないスロットは robot_id=0xFF + コマンド 64B ゼロ。"""
        sim = Cm4Sim(robot_ids="0,3")
        try:
            for i in range(1, 6):
                sim.send_command(build_packet(0, build_command(i)))
                sim.send_command(build_packet(3, build_command(i)))
                sim.send_feedback(0, i, 0.0, 0.0)
                sim.send_feedback(3, i, 0.0, 0.0)
                time.sleep(0.02)
            sim.drain_output()
            sim.send_command(build_packet(0, build_command(50)))
            time.sleep(0.05)
            out = sim.recv_latest_output()
            self.assertIsNotNone(out)

            for slot in range(SLOTS):
                robot_id, cmd = slot_of(out, slot)
                if slot in (0, 3):
                    self.assertEqual(robot_id, slot, f"担当スロット {slot} の robot_id")
                    self.assertNotEqual(cmd, bytes(CMD_SIZE), f"担当スロット {slot} が空")
                else:
                    self.assertEqual(robot_id, 0xFF, f"担当外スロット {slot} の robot_id は 0xFF")
                    self.assertEqual(cmd, bytes(CMD_SIZE), f"担当外スロット {slot} はゼロ埋め")
        finally:
            sim.close()

    def test_vision_echoes_feedback_position(self):
        """出力の VISION_GLOBAL_X/Y が feedback 由来の実位置になること。

        simulator-cli はこの値を実位置と 0.5m 以内で照合してチーム判定するので、
        crane 由来の古い値を流すと劣化注入時にコマンドが全部捨てられる。
        """
        sim = Cm4Sim(robot_ids="0")
        try:
            fb_x, fb_y = 1.25, -0.75
            crane_vision = (-2.0, 2.0)  # わざと feedback と離す
            for i in range(1, 20):
                sim.send_command(build_packet(0, build_command(i, target=(2.0, 0.0), vision=crane_vision)))
                sim.send_feedback(0, i, fb_x, fb_y)
                time.sleep(0.01)
            sim.drain_output()
            out = sim.recv_latest_output()
            self.assertIsNotNone(out)
            _, cmd = slot_of(out, 0)
            self.assertAlmostEqual(decode_two_byte(cmd, VISION_GLOBAL_X_HIGH, 32.767), fb_x, delta=2e-3)
            self.assertAlmostEqual(decode_two_byte(cmd, VISION_GLOBAL_Y_HIGH, 32.767), fb_y, delta=2e-3)
        finally:
            sim.close()

    def test_crane_silence_stops_the_robot(self):
        """crane 無通信で速度指令がゼロになり STOP_EMERGENCY が立つこと（検収条件 4）。"""
        sim = Cm4Sim(robot_ids="0", extra_args=["--command-timeout-ms", "100"])
        try:
            # まず動いている状態を作る。
            # recv_output は「最も古い」パケットを返すので、起動直後の空スロットを
            # 見てしまう。最新を見る recv_latest_output を使う。
            moving = False
            for i in range(1, 40):
                sim.send_command(build_packet(0, build_command(i, target=(2.0, 0.0))))
                sim.send_feedback(0, i, 0.0, 0.0)
                time.sleep(0.02)
                out = sim.recv_latest_output(timeout=0.5)
                if out:
                    _, cmd = slot_of(out, 0)
                    if decode_two_byte(cmd, CONTROL_MODE_ARGS, 32.767) > 0.1:
                        moving = True
            self.assertTrue(moving, "crane 通信中に速度指令が出ていない（制御が動いていない）")

            # crane を止める。feedback は流し続ける（crane 断だけを切り分けるため）
            deadline = time.time() + 1.0
            counter = 100
            while time.time() < deadline:
                sim.send_feedback(0, counter, 0.0, 0.0)
                counter += 1
                time.sleep(0.01)
            sim.drain_output()
            sim.send_feedback(0, counter, 0.0, 0.0)
            time.sleep(0.05)

            out = sim.recv_latest_output()
            self.assertIsNotNone(out)
            _, cmd = slot_of(out, 0)
            r = decode_two_byte(cmd, CONTROL_MODE_ARGS, 32.767)
            self.assertAlmostEqual(r, 0.0, delta=2e-3, msg="crane 無通信後も速度指令が出ている")
            self.assertTrue(cmd[FLAGS] & (1 << STOP_EMERGENCY_BIT), "STOP_EMERGENCY が立っていない")
        finally:
            sim.close()

    def test_feedback_silence_stops_the_robot(self):
        """feedback 途絶でも速度指令がゼロになること（位置信号が無ければ閉じられない）。"""
        sim = Cm4Sim(robot_ids="0", extra_args=["--feedback-timeout-ms", "100"])
        try:
            deadline = time.time() + 0.8
            counter = 1
            while time.time() < deadline:
                sim.send_command(build_packet(0, build_command(counter, target=(2.0, 0.0))))
                counter += 1
                time.sleep(0.01)
            sim.drain_output()
            out = sim.recv_latest_output()
            self.assertIsNotNone(out)
            _, cmd = slot_of(out, 0)
            self.assertAlmostEqual(decode_two_byte(cmd, CONTROL_MODE_ARGS, 32.767), 0.0, delta=2e-3)
        finally:
            sim.close()

    def test_feedback_is_relayed_to_multicast(self):
        """feedback が multicast へ再配信されること。

        実機の robot_feedback.out と同じ経路で、crane と host ツールがここを見る。
        開発 PC には 192.168.20.x が無いので送出 IF は loopback にする。
        """
        # --no-feedback-relay を外し、送出 IF を loopback にする
        sim = Cm4Sim(robot_ids="0", extra_args=["--multicast-if", "127.0.0.1"],
                     relay=True)
        # 再配信先はテストごとにずらした feedback_base から決まるので、
        # 起動後の実ポートを見て join する（定数を直接使うとズレる）。
        group = f"224.5.20.{100 + 0}"
        port = sim.feedback_relay_port_base + 0

        mc = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        mc.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        mc.bind((group, port))
        mc.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                      socket.inet_aton(group) + socket.inet_aton("127.0.0.1"))
        try:
            got = None
            for i in range(1, 40):
                sim.send_feedback(0, i, 0.5, -0.25)
                mc.settimeout(0.1)
                try:
                    data = mc.recv(256)
                except socket.timeout:
                    continue
                if len(data) == FEEDBACK_SIZE and data[0] == 0xAB and data[1] == 0xEA:
                    got = data
                    break
            self.assertIsNotNone(got, "multicast 再配信を受信できない")
            self.assertAlmostEqual(struct.unpack_from("<f", got, 44)[0], 0.5, delta=1e-6)
            self.assertAlmostEqual(struct.unpack_from("<f", got, 48)[0], -0.25, delta=1e-6)
        finally:
            sim.close()
            mc.close()

    def test_unset_terminal_velocity_does_not_send_the_robot_off_field(self):
        """mode 4 の終端速度が未設定でも、目標と無関係な方向へ走り出さないこと。

        2 バイト固定小数 (range 32.767) の未設定フィールドは 0.0 ではなく -32.767 として
        復号される。encode が 0.0 を 0x7FFF へ写すためで、memset でゼロ埋めしたつもりの
        フィールドが最大級の負値になる。実チェーンで crane 役が ARGS 24..27 を書き忘れた
        結果、feedforward が (-32.767, -32.767) になってロボットが場外まで走った。
        """
        sim = Cm4Sim(robot_ids="0")
        try:
            # ARGS 24..27 と TERMINAL_VELOCITY 36..37 をゼロバイトのままにする。
            command = bytearray(build_command(1, target=(2.8, -1.8), linear_velocity_limit=2.0))
            command[CONTROL_MODE_ARGS:CONTROL_MODE_ARGS + 4] = bytes(4)
            command[TERMINAL_VELOCITY_HIGH:TERMINAL_VELOCITY_HIGH + 2] = bytes(2)
            command = bytes(command)
            self.assertAlmostEqual(decode_two_byte(command, CONTROL_MODE_ARGS, 32.767), -32.767, delta=1e-3,
                                   msg="前提: ゼロバイトは -32.767 として復号される")

            got = None
            for _ in range(40):
                sim.send_command(build_packet(0, command))
                sim.send_feedback(0, 1, 4.3, -2.8)
                time.sleep(0.02)
                out = sim.recv_latest_output(timeout=0.5)
                if out is None:
                    continue
                _, cmd = slot_of(out, 0)
                if cmd != bytes(CMD_SIZE) and cmd[FLAGS] & (1 << STOP_EMERGENCY_BIT) == 0:
                    got = cmd
                    break
            self.assertIsNotNone(got, "終端速度が未設定でも制御は続けるはず（止めるのは行き過ぎ）")

            theta = decode_two_byte(got, CONTROL_MODE_ARGS + 2, 32.767)
            expected = math.atan2(-1.8 - (-2.8), 2.8 - 4.3)  # 目標方向 2.5536 rad
            self.assertAlmostEqual(theta, expected, delta=0.02,
                                   msg="終端速度を無視せず、目標と無関係な方向を向いている")
            r = decode_two_byte(got, CONTROL_MODE_ARGS, 32.767)
            self.assertLessEqual(r, 2.0 + 2e-3, "linear_velocity_limit を超えている")
        finally:
            sim.close()

    def test_unset_target_position_stops_the_robot(self):
        """mode 4 の目標位置が未設定なら止まること。位置には安全な代替値が無い。"""
        sim = Cm4Sim(robot_ids="0")
        try:
            command = bytearray(build_command(1, target=(0.0, 0.0), linear_velocity_limit=2.0))
            command[TARGET_GLOBAL_POS_X_HIGH:TARGET_GLOBAL_POS_X_HIGH + 4] = bytes(4)
            command = bytes(command)
            got = None
            for _ in range(40):
                sim.send_command(build_packet(0, command))
                sim.send_feedback(0, 1, 0.0, 0.0)
                time.sleep(0.02)
                out = sim.recv_latest_output(timeout=0.5)
                if out is None:
                    continue
                _, cmd = slot_of(out, 0)
                if cmd != bytes(CMD_SIZE):
                    got = cmd
                    break
            self.assertIsNotNone(got)
            self.assertAlmostEqual(decode_two_byte(got, CONTROL_MODE_ARGS, 32.767), 0.0, delta=2e-3)
            self.assertTrue(got[FLAGS] & (1 << STOP_EMERGENCY_BIT))
        finally:
            sim.close()

    def test_vision_unavailable_stops_the_robot(self):
        """crane が見失っている間は止めること。

        simulator-cli はこのビットを復号するだけで何もしない
        (src/simulator/ibis_protocol.h:145) 一方、実機 G474 は同じ条件で
        ホイールを止める (state_func.c:314)。CM4 で止めることで両者が揃う。
        """
        sim = Cm4Sim(robot_ids="0")
        try:
            command = build_command(1, target=(3.0, 0.0), linear_velocity_limit=2.0, vision_available=False)
            got = None
            for _ in range(40):
                sim.send_command(build_packet(0, command))
                sim.send_feedback(0, 1, 0.0, 0.0)
                time.sleep(0.02)
                out = sim.recv_latest_output(timeout=0.5)
                if out is None:
                    continue
                _, cmd = slot_of(out, 0)
                if cmd != bytes(CMD_SIZE):
                    got = cmd
                    break
            self.assertIsNotNone(got)
            self.assertEqual(got[CONTROL_MODE], MODE_POLAR_VELOCITY)
            self.assertAlmostEqual(decode_two_byte(got, CONTROL_MODE_ARGS, 32.767), 0.0, delta=2e-3)
            self.assertTrue(got[FLAGS] & (1 << STOP_EMERGENCY_BIT))
        finally:
            sim.close()

    def test_mode3_is_forwarded_without_position_control(self):
        """mode 3 は位置制御せずそのまま転送すること（旧構成。A/B 比較の基準側）。

        crane 由来の check_counter も触らない。simulator-cli は同じ check_counter を
        無視するので、結果として「crane のレートでコマンドが適用される」という
        旧構成そのものの挙動になる。
        """
        sim = Cm4Sim(robot_ids="0")
        try:
            command = build_command(7, mode=MODE_POLAR_VELOCITY,
                                    terminal_velocity_xy=(1.25, -0.5), target=(9.0, 9.0))
            got = None
            for _ in range(40):
                sim.send_command(build_packet(0, command))
                sim.send_feedback(0, 1, 0.0, 0.0)
                time.sleep(0.02)
                out = sim.recv_latest_output(timeout=0.5)
                if out is None:
                    continue
                _, cmd = slot_of(out, 0)
                if cmd != bytes(CMD_SIZE):
                    got = cmd
                    break
            self.assertIsNotNone(got, "mode 3 の出力が出ていない")
            self.assertEqual(got[CONTROL_MODE], MODE_POLAR_VELOCITY)
            self.assertEqual(got[CHECK_COUNTER], 7, "素通しでは crane の check_counter を変えない")
            # mode 3 args は位置制御の出力で上書きされず、受信値のまま。
            self.assertAlmostEqual(decode_two_byte(got, CONTROL_MODE_ARGS, 32.767), 1.25, delta=2e-3)
            self.assertAlmostEqual(decode_two_byte(got, CONTROL_MODE_ARGS + 2, 32.767), -0.5, delta=2e-3)
            # 目標位置 (9,9) に対して位置制御が走っていたら r は 0 のままではない。
            self.assertEqual(bytes(got[28:32]), command[28:32], "予約領域まで素通しする")
        finally:
            sim.close()

    def test_mode3_passthrough_stops_when_crane_goes_silent(self):
        """素通し経路でも crane 断で止まること。

        旧構成では G474 の connected_ai タイムアウトが拾って止めるが、
        simulator-cli はそれを模擬しない。代行しないと A/B 比較の基準側だけが
        crane 断で走り続けてしまう。
        """
        sim = Cm4Sim(robot_ids="0", extra_args=["--command-timeout-ms", "100"])
        try:
            command = build_command(7, mode=MODE_POLAR_VELOCITY, terminal_velocity_xy=(1.25, 0.0))
            for _ in range(20):
                sim.send_command(build_packet(0, command))
                sim.send_feedback(0, 1, 0.0, 0.0)
                time.sleep(0.02)
            time.sleep(0.4)  # crane を止める
            sim.drain_output()
            time.sleep(0.05)
            out = sim.recv_latest_output()
            self.assertIsNotNone(out)
            _, cmd = slot_of(out, 0)
            self.assertEqual(cmd[CONTROL_MODE], MODE_POLAR_VELOCITY)
            self.assertAlmostEqual(decode_two_byte(cmd, CONTROL_MODE_ARGS, 32.767), 0.0, delta=2e-3)
            self.assertTrue(cmd[FLAGS] & (1 << STOP_EMERGENCY_BIT))
        finally:
            sim.close()

    def _run_degraded(self, seed, packets=60):
        """劣化注入を有効にして crane パケットを N 個投げ、終了時の要約と
        出力に現れた目標位置の集合を返す。

        要約行 (rx-degrader: ...) は seed と受信パケット数だけで決まるので、
        実時間のサンプリングに依存せず決定論的に比較できる。ロスのコイン投げと
        遅延・ジッタの抽選の両方がハッシュに入る。
        """
        sim = Cm4Sim(robot_ids="0",
                     extra_args=["--rx-delay-ms", "20", "--rx-jitter-ms", "10",
                                 "--rx-loss-rate", "0.3", "--seed", str(seed)])
        targets = set()
        try:
            for i in range(1, packets + 1):
                target_x = 0.05 * i  # コマンドごとに一意な目印
                sim.send_command(build_packet(0, build_command(i, target=(target_x, 0.0))))
                sim.send_feedback(0, i, 0.0, 0.0)
                time.sleep(0.02)
                sim.out_rx.settimeout(0.005)
                for _ in range(200):  # 必ず回数で打ち切る
                    try:
                        out = sim.out_rx.recv(2048)
                    except socket.timeout:
                        break
                    if len(out) != PACKET_SIZE:
                        continue
                    _, cmd = slot_of(out, 0)
                    if cmd == bytes(CMD_SIZE):
                        continue
                    targets.add(round(decode_two_byte(cmd, TARGET_GLOBAL_POS_X_HIGH, 32.767), 3))
        finally:
            sim.close()

        summary = None
        for line in sim.output.splitlines():
            if line.startswith("rx-degrader:"):
                summary = line
        self.assertIsNotNone(summary, f"cm4_sim が劣化注入の要約を出していない:\n{sim.output}")
        # 決定列は「受信した順番」で駆動されるので、受信数が送信数と食い違えば
        # seed が同じでもハッシュはずれる。再現性の検証はここが前提になる。
        pushed = int(summary.split("pushed=")[1].split()[0])
        self.assertEqual(pushed, packets,
                         f"受信パケット数が送信数と一致しない（他プロセスの混入か取りこぼし）: {summary}")
        return summary, targets

    def test_degradation_is_reproducible_with_seed(self):
        """--seed 固定で遅延・ジッタ・ロスが再現すること（検収条件 5）。"""
        summary_a, targets_a = self._run_degraded(42)
        summary_b, _ = self._run_degraded(42)
        self.assertEqual(summary_a, summary_b,
                         "同一 seed で劣化注入の決定列が一致しない（再現性が無い）")

        summary_c, _ = self._run_degraded(1234)
        self.assertNotEqual(summary_a, summary_c,
                            "異なる seed で決定列が同じ（seed が効いていない）")

        # 要約が「何もしていない」値でないこと（ロス率 0.3 なら数個は落ちる）
        dropped = int(summary_a.split("dropped=")[1].split()[0])
        pushed = int(summary_a.split("pushed=")[1].split()[0])
        self.assertGreater(pushed, 40, f"crane パケットがほとんど届いていない: {summary_a}")
        self.assertGreater(dropped, 0, f"ロス率 0.3 なのに 1 つも落ちていない: {summary_a}")
        self.assertLess(dropped, pushed, f"全部落ちている: {summary_a}")

        # 出力ストリーム側の健全性: 落ちたぶん、出力に現れる目標の種類は送った数より少ない
        self.assertGreater(len(targets_a), 5, "出力に目標が現れていない（制御経路が動いていない）")
        self.assertLess(len(targets_a), 60, "ロスしているのに全コマンドが出力に現れている")


if __name__ == "__main__":
    unittest.main()
