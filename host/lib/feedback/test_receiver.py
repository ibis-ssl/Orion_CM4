# このファイルは共通フィードバックのCRCとUDP同期値による機体判別を検証する。
import unittest
from unittest.mock import MagicMock, patch

from host.lib.feedback.packet import calc_feedback_crc8
from host.lib.feedback.receiver import (
    DEFAULT_INTERFACE_IP,
    decode_feedback_packet,
    packet_to_dict,
    resolve_feedback_interface_ip,
)


def make_frame(sync1: int) -> bytes:
    frame = bytearray(128)
    frame[:2] = bytes((0xAB, sync1))
    frame[3] = 42
    frame[2] = calc_feedback_crc8(frame)
    return bytes(frame)


class CommonFeedbackTest(unittest.TestCase):
    def test_interface_ip_uses_route_to_target_machine(self) -> None:
        sock = MagicMock()
        sock.getsockname.return_value = ("192.168.20.176", 49152)
        with patch("host.lib.feedback.receiver.socket.socket", return_value=sock):
            self.assertEqual(resolve_feedback_interface_ip(4), "192.168.20.176")
        sock.connect.assert_called_once_with(("192.168.20.104", 8000))
        sock.close.assert_called_once()

    def test_interface_ip_explicit_override_skips_route_probe(self) -> None:
        with patch("host.lib.feedback.receiver.socket.socket") as socket_factory:
            self.assertEqual(resolve_feedback_interface_ip(4, " 192.168.20.200 "), "192.168.20.200")
        socket_factory.assert_not_called()

    def test_interface_ip_falls_back_when_route_probe_fails(self) -> None:
        sock = MagicMock()
        sock.connect.side_effect = OSError("no route")
        with patch("host.lib.feedback.receiver.socket.socket", return_value=sock):
            self.assertEqual(resolve_feedback_interface_ip(4), DEFAULT_INTERFACE_IP)
        sock.close.assert_called_once()

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
