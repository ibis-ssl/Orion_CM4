# -*- coding: utf-8 -*-
"""packet_codec.py のオフセット表が C++ 側の正本とずれていないことを検査する。

C++ 側のレイアウトは robot_packet_layout_test.cpp の static_assert が固定して
いるが、Python テストが持つ表は誰も検査していなかった。同じ実行ファイルに
--dump-offsets を持たせ、その出力と突き合わせることで Python 側も同じ保護下に
入る。robot_packet.h や robot_feedback_packet.h がずれたら、C++ と Python の
両方のテストが同時に落ちる。

cm4/firmware/test_*.py と同じ unittest 方式（pytest は使わない）。
"""

import os
import subprocess
import unittest

import packet_codec

BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin", "robot_packet_layout_test.out")
BIN = os.path.normpath(BIN)


class PacketCodecOffsetTest(unittest.TestCase):
    def test_offsets_match_cpp(self):
        if not os.path.exists(BIN):
            self.skipTest("%s がありません。cm4/build.sh を先に実行してください" % BIN)
        out = subprocess.run([BIN, "--dump-offsets"], capture_output=True, text=True, check=True).stdout

        expected = {}
        for line in out.splitlines():
            name, sep, value = line.partition("=")
            if sep:
                expected[name] = int(value)
        # 古いバイナリは --dump-offsets を知らず通常の検査結果を出すので、
        # ここが空になる。黙って素通りさせない。
        self.assertTrue(expected, "--dump-offsets が何も出力しなかった。cm4/build.sh を実行し直すこと")

        for name, value in sorted(expected.items()):
            self.assertTrue(hasattr(packet_codec, name), "packet_codec.%s が無い" % name)
            self.assertEqual(
                getattr(packet_codec, name), value,
                "packet_codec.%s が C++ 側とずれている (python=%r cpp=%r)" % (name, getattr(packet_codec, name), value))

    def test_derived_sizes(self):
        """CM4入力は65バイト、シミュレータ出力は715バイト。"""
        self.assertEqual(packet_codec.SLOT_SIZE, packet_codec.CMD_SIZE + 1)
        self.assertEqual(packet_codec.INPUT_PACKET_SIZE, 65)
        self.assertEqual(packet_codec.PACKET_SIZE, packet_codec.SLOT_SIZE * packet_codec.SLOTS)
        self.assertEqual(packet_codec.SLOTS, 11)
        self.assertEqual(packet_codec.PACKET_SIZE, 715)
        self.assertEqual(packet_codec.FEEDBACK_SYNC, (0xAB, 0xEA))

    def test_encode_matches_cpp_truncation(self):
        """0.0 が 0x7FFF になること（ゼロ埋め ≠ ゼロ値の根拠）と、
        範囲外がクランプ後に量子化されること。"""
        self.assertEqual(packet_codec.encode_two_byte(0.0, 32.767), bytes([0x7F, 0xFF]))
        # 範囲外は float の時点でクランプする。量子化後の int をクランプすると
        # 1 LSB ずれる (0xFFFF になってしまう)。
        self.assertEqual(packet_codec.encode_two_byte(40.0, 32.767), packet_codec.encode_two_byte(32.767, 32.767))
        self.assertEqual(packet_codec.encode_two_byte(-40.0, 32.767), bytes([0x00, 0x00]))
        # ゼロ埋めされた 2 バイトは -range として復号される
        self.assertAlmostEqual(packet_codec.decode_two_byte(bytes(2), 0, 32.767), -32.767, places=3)


if __name__ == "__main__":
    unittest.main()
