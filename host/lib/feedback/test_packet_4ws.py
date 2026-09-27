# このファイルは4WS Main生フィードバックのCRC・フィールド配置と、
# 両対応デバッグツールの形式選択を検証する。
import struct
import unittest

from host.lib.feedback.packet import RobotFeedbackPacket
from host.lib.feedback.packet_4ws import FourWsFeedbackPacket, crc32c, decode_4ws_feedback_packet
from host.lib.feedback.receiver import decode_feedback_packet, packet_to_dict


def make_status_frame() -> bytes:
    frame = bytearray(128)
    frame[:4] = b"O4WS"
    frame[4] = 1
    frame[5] = 0x82
    struct.pack_into("<H", frame, 6, 0x1234)
    struct.pack_into("<H", frame, 8, 24)
    struct.pack_into("<H", frame, 12, 0x1122)
    frame[14] = 2
    frame[15] = 0xA5
    for index in range(4):
        struct.pack_into(">H", frame, 16 + index * 4, 0x7FFF)
        struct.pack_into(">H", frame, 18 + index * 4, 0x7FFF)
    struct.pack_into(">H", frame, 16, 0)
    struct.pack_into(">H", frame, 18, 0xFFFF)
    struct.pack_into("<I", frame, 32, 123456)
    struct.pack_into("<I", frame, 124, crc32c(frame[:124]))
    return bytes(frame)


class FourWsFeedbackTest(unittest.TestCase):
    def test_crc32c_standard_vector(self) -> None:
        self.assertEqual(crc32c(b"123456789"), 0xE3069283)

    def test_status_layout_and_auto_detection(self) -> None:
        packet = decode_feedback_packet(make_status_frame())
        self.assertIsInstance(packet, FourWsFeedbackPacket)
        self.assertTrue(packet.is_header_valid)
        self.assertTrue(packet.crc_valid)
        self.assertTrue(packet.is_padding_zero)
        self.assertEqual(packet.sequence, 0x1234)
        self.assertEqual(packet.accepted_sequence, 0x1122)
        self.assertEqual(packet.main_state, 2)
        self.assertEqual(packet.error_summary, 0xA5)
        self.assertEqual(packet.uptime_ms, 123456)
        self.assertAlmostEqual(packet.wheel_speed_mps[0], -32.767)
        self.assertIsNone(packet.steering_angle_rad[0])
        self.assertEqual(packet.wheel_speed_mps[1:], (0.0, 0.0, 0.0))
        self.assertEqual(packet_to_dict(packet)["machine_type"], "4ws")

    def test_corrupt_crc_and_forced_decoder(self) -> None:
        frame = bytearray(make_status_frame())
        frame[0] = 0
        packet = decode_feedback_packet(bytes(frame), "4ws")
        self.assertFalse(packet.is_header_valid)
        self.assertFalse(packet.crc_valid)

    def test_unknown_payload_remains_inspectable(self) -> None:
        frame = bytearray(make_status_frame())
        struct.pack_into("<H", frame, 8, 4)
        struct.pack_into("<I", frame, 124, crc32c(frame[:124]))
        packet = decode_4ws_feedback_packet(bytes(frame))
        self.assertFalse(packet.has_known_status_layout)
        self.assertEqual(packet.payload, bytes(frame[12:16]))
        self.assertIsNone(packet.wheel_speed_mps)

    def test_orion_packet_still_uses_orion_decoder(self) -> None:
        frame = bytearray(128)
        frame[:2] = b"\xAB\xEA"
        packet = decode_feedback_packet(bytes(frame))
        self.assertIsInstance(packet, RobotFeedbackPacket)


if __name__ == "__main__":
    unittest.main()
