"""crane -> cm4_sim -> simulator-cli -> cm4_sim の全経路テストを担当する。

実際に simulator-cli を起動するので、framework リポジトリが手元にあるときだけ走る。
CI ではスキップされる（test_cm4_sim.py が cm4_sim 単体を検査している）。

実行:
  SIMULATOR_CLI=/path/to/framework/build/bin/simulator-cli \\
    python3 -m unittest discover -s cm4/bridge -p 'test_cm4_sim_chain.py' -v

検査するもの:
  1. mode 4 を投げるとロボットが目標へ近づく（位置ループが実際に閉じている）
  2. simulator-cli のログに POSITION_TARGET 警告が出ない
     （出たら mode 変換に失敗して mode 4 がそのまま流れている）
  3. feedback が multicast へ再配信される（crane と host ツールが見る経路）

既定から離れたポートを使うので、動作中の試合には影響しない。
"""

import os
import socket
import struct
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from test_cm4_sim import (CMD_SIZE, PACKET_SIZE, build_command, build_packet)

REPO = Path(__file__).resolve().parents[2]
CM4_SIM = REPO / "cm4" / "bin" / "cm4_sim.out"
SIMULATOR_CLI = os.environ.get("SIMULATOR_CLI", "")

# simulator-cli 側に必要な機能。framework のブランチやビルドによっては
# まだ入っていないことがあるので、起動前に --help で確認してスキップする。
# (mode 3 以外を停止して警告する IbisCommandAdaptor は
#  framework/docs/robot-side-position-control.md の構成が入ったビルドにのみある)
# --vision-port / --tracker-port は廃止された lockstep ブランチ専用のオプションで、
# ibis ブランチには来ない（TrackerAdaptor 自体が lockstep コミット由来のため、
# ibis の simulator-cli は SSL tracker を出力しない）。能力判定には使わない。
# vision の出力先を変えるオプションも無いので、既定の 224.5.23.2:10020 へ出る前提。
REQUIRED_SIM_OPTIONS = ("--ibis-port", "--ibis-feedback-port-base")


def missing_simulator_options():
    """simulator-cli が必要なオプションを持っているか調べる。

    --help に出ていても実際には受理しないビルドがあったので、
    --help の記載だけでなく実際にパースさせて確認する。
    """
    if not SIMULATOR_CLI or not Path(SIMULATOR_CLI).exists():
        return ["(SIMULATOR_CLI 未設定)"]
    missing = []
    for opt in REQUIRED_SIM_OPTIONS:
        probe = subprocess.run(
            [SIMULATOR_CLI, "-g", "2020B", "--realism", "None", "--localhost", opt, "1",
             "--exit-immediately-if-this-is-not-a-real-option"],
            capture_output=True, text=True, timeout=30)
        text = (probe.stdout or "") + (probe.stderr or "")
        if f"Unknown option '{opt.lstrip('-')}'" in text or f"{opt.lstrip('-')}," in text and "Unknown options" in text:
            missing.append(opt)
    return missing


MISSING_OPTIONS = missing_simulator_options()

# test_cm4_sim.py と同時に走っても衝突しないポート
CRANE_TO_CM4_PORT = 12398   # crane -> cm4_sim (mode 4)
CM4_TO_SIM_PORT = 12397     # cm4_sim -> simulator-cli (mode 3)
FEEDBACK_BASE = 50800       # simulator-cli -> cm4_sim

ROBOT_ID = 0
MULTICAST_GROUP = f"224.5.20.{100 + ROBOT_ID}"
MULTICAST_PORT = FEEDBACK_BASE + ROBOT_ID


def parse_feedback(data):
    if len(data) != 128 or data[0] != 0xAB or data[1] != 0xEA:
        return None
    return {
        "counter": data[3],
        "x": struct.unpack_from("<f", data, 44)[0],
        "y": struct.unpack_from("<f", data, 48)[0],
    }


@unittest.skipIf(MISSING_OPTIONS,
                 f"simulator-cli が必要なオプションを持っていない: {MISSING_OPTIONS}. "
                 "framework を robot-side-position-control 対応のブランチでビルドし直すこと")
@unittest.skipUnless(CM4_SIM.exists(), f"{CM4_SIM} が無い。先に ./cm4/build.sh を実行すること")
class Cm4SimChainTest(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cm4-sim-chain-"))
        self.sim_log_path = self.tmp / "simulator-cli.log"
        self.sim_log = open(self.sim_log_path, "w")

        # simulator-cli は G474 とロボット物理を担当する。位置制御は行わない。
        # --realism None で物理の揺らぎを排除する（既定は Ibis）。
        # log() は flush しないので stdbuf が要る。
        self.sim = subprocess.Popen(
            ["stdbuf", "-oL", "-eL", SIMULATOR_CLI,
             "-g", "2020B", "--realism", "None", "--localhost",
             "--ibis-port", str(CM4_TO_SIM_PORT),
             "--ibis-feedback-port-base", str(FEEDBACK_BASE)],
            stdout=self.sim_log, stderr=subprocess.STDOUT)

        # feedback 再配信を loopback の multicast で受けるので --multicast-if 127.0.0.1。
        # 実機は 192.168.20.x だが開発 PC には無い。
        self.cm4 = subprocess.Popen(
            [str(CM4_SIM),
             "--robot-ids", str(ROBOT_ID),
             "--in-port", str(CRANE_TO_CM4_PORT),
             "--out-addr", "127.0.0.1", "--out-port", str(CM4_TO_SIM_PORT),
             "--feedback-port-base", str(FEEDBACK_BASE),
             "--feedback-relay-port-base", str(FEEDBACK_BASE),
             "--multicast-if", "127.0.0.1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)

        self.crane = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # crane / host ツールが見る multicast 経路
        self.mc = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.mc.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.mc.bind((MULTICAST_GROUP, MULTICAST_PORT))
        self.mc.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                           socket.inet_aton(MULTICAST_GROUP) + socket.inet_aton("127.0.0.1"))
        time.sleep(1.5)

    def tearDown(self):
        for p in (self.cm4, self.sim):
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
        self.crane.close()
        self.mc.close()
        self.sim_log.close()
        print(f"\nsimulator-cli ログ: {self.sim_log_path}")

    def _latest_position(self, timeout=2.0):
        """multicast 再配信から最新の位置を取る。"""
        self.mc.settimeout(timeout)
        latest = None
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                fb = parse_feedback(self.mc.recv(256))
            except socket.timeout:
                break
            if fb:
                latest = fb
                self.mc.settimeout(0.05)
        return latest

    def test_position_loop_closes_through_the_real_simulator(self):
        target = (1.0, 0.0)
        counter = 0
        start = None

        # crane 相当を 62.5Hz で回す
        deadline = time.time() + 8.0
        while time.time() < deadline:
            counter = counter % 200 + 1
            # crane はロボットの実位置を vision_global_pos に入れる。
            # ここでは multicast から読んだ最新位置を使う（実運用の vision 相当）。
            pos = self._latest_position(timeout=0.02)
            if pos:
                if start is None:
                    start = (pos["x"], pos["y"])
                vision = (pos["x"], pos["y"])
            else:
                vision = (0.0, 0.0)
            self.crane.sendto(build_packet(ROBOT_ID, build_command(counter, target=target, vision=vision)),
                              ("127.0.0.1", CRANE_TO_CM4_PORT))
            time.sleep(1 / 62.5)

        final = self._latest_position(timeout=2.0)
        self.assertIsNotNone(final, "multicast 再配信から feedback が来ない")
        self.assertIsNotNone(start, "開始位置を取得できなかった")

        start_dist = ((target[0] - start[0]) ** 2 + (target[1] - start[1]) ** 2) ** 0.5
        final_dist = ((target[0] - final["x"]) ** 2 + (target[1] - final["y"]) ** 2) ** 0.5
        print(f"\n目標まで {start_dist:.3f} m -> {final_dist:.3f} m")
        self.assertLess(final_dist, start_dist * 0.5,
                        "位置ループが閉じていない（目標へ近づいていない）")

        # mode 変換に失敗していれば simulator-cli がこの警告を出す
        log = self.sim_log_path.read_text(errors="replace")
        self.assertNotIn("POSITION_TARGET", log,
                         "simulator-cli が mode 4 を受け取っている（cm4_sim の mode 変換が効いていない）")


if __name__ == "__main__":
    unittest.main()
