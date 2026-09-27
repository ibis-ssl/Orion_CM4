# このファイルは共通feedbackの128バイト配置とステア角符号化を検証する。
import math
import struct
import unittest

from host.lib.feedback.packet import calc_feedback_crc8, crc8_atm
from host.lib.feedback.receiver import decode_feedback_packet, packet_to_dict


class PacketLayoutTest(unittest.TestCase):
    def test_all_fields(self) -> None:
        frame = bytearray(128)
        frame[:5] = bytes((0xAB, 0xEA, 0, 42, 21))
        struct.pack_into("<HHff", frame, 5, 0x1234, 0x5678, 1.25, -45.5)
        frame[17:20] = bytes((1, 2, 3))
        struct.pack_into("<ff", frame, 20, 4.5, 24.5)
        frame[28:32] = bytes((7, 8, 9, 10))
        struct.pack_into("<6f", frame, 32, 120.0, 1.0, 2.0, 3.0, 4.0, 5.0)
        frame[56:64] = bytes((11, 12, 13, 14, 15, 16, 17, 18))
        struct.pack_into("<9f", frame, 64, *range(20, 29))
        struct.pack_into(">4H", frame, 100, 0, 0x7FFF, 0xBFFF, 0xFFFE)
        frame[108:112] = bytes((31, 32, 33, 34))
        struct.pack_into("<4f", frame, 112, 35.0, 36.0, 37.0, 38.0)
        frame[2] = calc_feedback_crc8(frame)
        packet = decode_feedback_packet(bytes(frame))
        fields = packet_to_dict(packet)

        self.assertEqual(crc8_atm(b"123456789"), 0xF4)
        self.assertTrue(packet.is_crc_valid)
        self.assertEqual(packet.to_bytes(), bytes(frame))
        self.assertEqual((packet.tx_cycle_count, packet.current_error_id, packet.current_error_info),
                         (21, 0x1234, 0x5678))
        self.assertEqual((packet.current_error_value, packet.imu_yaw_deg, packet.diff_angle_deg,
                          packet.battery_voltage), (1.25, -45.5, 4.5, 24.5))
        self.assertEqual((packet.ball_detection, packet.ball_detection_extra), ((1, 2), 3))
        self.assertEqual((packet.kick_state_div10, packet.temp_fet, packet.temp_coil), (7, 8, (9, 10)))
        self.assertEqual((packet.capacitor_boost_voltage, packet.mouse_odom_x, packet.mouse_odom_y,
                          packet.mouse_global_vel_x, packet.mouse_global_vel_y, packet.mouse_quality),
                         (120.0, 1.0, 2.0, 3.0, 4.0, 5.0))
        self.assertEqual((packet.motor_current_x10, packet.temp_motor),
                         ((11, 12, 13, 14), (15, 16, 17, 18)))
        self.assertEqual((packet.output_vel_x, packet.output_vel_y, packet.motor_feedback_0,
                          packet.motor_feedback_1, packet.motor_feedback_2, packet.motor_feedback_3,
                          packet.local_odom_speed_mvf_x, packet.local_odom_speed_mvf_y,
                          packet.local_odom_speed_mvf_w), tuple(float(i) for i in range(20, 29)))
        self.assertAlmostEqual(packet.steering_angle[0], -10 * math.pi)
        self.assertEqual(packet.steering_angle[1], 0.0)
        self.assertAlmostEqual(packet.steering_angle[2], 5 * math.pi, places=3)
        self.assertEqual(packet.temp_steering_motor, (31, 32, 33, 34))
        self.assertEqual((packet.vision_based_position_x, packet.vision_based_position_y,
                          packet.global_odom_speed_x, packet.global_odom_speed_y),
                         (35.0, 36.0, 37.0, 38.0))
        self.assertNotIn("camera_pos_x", fields)
        self.assertNotIn("tx_values", fields)

    def test_invalid_steering_encoding(self) -> None:
        frame = bytearray(128)
        frame[:2] = bytes((0xAB, 0xEA))
        frame[100:102] = bytes((0xFF, 0xFF))
        frame[2] = calc_feedback_crc8(frame)
        with self.assertRaisesRegex(ValueError, "invalid steering angle"):
            decode_feedback_packet(bytes(frame))


if __name__ == "__main__":
    unittest.main()
