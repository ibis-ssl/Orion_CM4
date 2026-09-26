# このファイルは周期診断が停止状態のmode 3指令を生成し、連番を維持することを検査する。
import struct
import unittest

from mode3_timing_probe import command
from packet_codec import build_packet, decode_two_byte


class ProbePacketTest(unittest.TestCase):
    def test_stop_packet_and_sequence_wrap(self):
        for sequence in (1, 200, 201, 202, 65536):
            cmd = command(sequence)
            self.assertEqual(len(cmd), 64)
            self.assertEqual(cmd[1], sequence % 201)
            self.assertEqual(cmd[22], 8)  # STOP_EMERGENCYのみ
            self.assertEqual(cmd[23], 3)
            self.assertEqual(cmd[10:12], b'\0\0')
            for offset in (24, 26):
                self.assertEqual(decode_two_byte(cmd, offset, 32.767), 0)
            self.assertEqual(struct.unpack_from('<I', cmd, 42)[0], sequence)
            packet = build_packet(8, cmd)
            self.assertEqual(len(packet), 65)
            self.assertEqual(packet[0], 8)
            self.assertEqual(packet[1:], cmd)


if __name__ == '__main__':
    unittest.main()
