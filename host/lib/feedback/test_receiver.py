# このファイルは共通フィードバックのCRCとUDP同期値による機体判別を検証する。
import unittest

from host.lib.feedback.packet import calc_feedback_crc8
from host.lib.feedback.receiver import decode_feedback_packet, packet_to_dict


def make_frame(sync1: int) -> bytes:
    frame = bytearray(128)
    frame[:2] = bytes((0xAB, sync1))
    frame[3] = 42
    frame[2] = calc_feedback_crc8(frame)
    return bytes(frame)


class CommonFeedbackTest(unittest.TestCase):
    def test_udp_type_marker_does_not_change_crc(self) -> None:
        orion = decode_feedback_packet(make_frame(0xEA))
        four_ws = decode_feedback_packet(make_frame(0xEB))
        self.assertEqual(orion.crc8, four_ws.crc8)
        self.assertTrue(orion.is_crc_valid)
        self.assertTrue(four_ws.is_crc_valid)
        self.assertEqual(packet_to_dict(orion)["machine_type"], "orion")
        self.assertEqual(packet_to_dict(four_ws)["machine_type"], "4ws")

    def test_manual_type_must_match_udp_marker(self) -> None:
        with self.assertRaisesRegex(ValueError, "unexpected feedback sync bytes"):
            decode_feedback_packet(make_frame(0xEA), "4ws")
        with self.assertRaisesRegex(ValueError, "unknown feedback sync bytes"):
            decode_feedback_packet(bytes(128))


if __name__ == "__main__":
    unittest.main()
