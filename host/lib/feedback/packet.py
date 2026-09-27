# このファイルはMain共通の128バイトfeedbackの配置、CRC、復号と再符号化を担当する。
from __future__ import annotations

from dataclasses import dataclass
import math
import struct

PACKET_SIZE = 128
SYNC0 = 0xAB
SYNC1 = 0xEA
UDP_4WS_SYNC1 = 0xEB
STEERING_RANGE_RAD = 10.0 * math.pi


def decode_steering_angle(raw: int) -> float:
    if raw == 0xFFFF:
        raise ValueError("invalid steering angle encoding: 0xffff")
    return (raw - 32767) * STEERING_RANGE_RAD / 32767


def encode_steering_angle(angle: float) -> int:
    if not math.isfinite(angle) or not -STEERING_RANGE_RAD <= angle <= STEERING_RANGE_RAD:
        raise ValueError(f"steering angle out of range: {angle}")
    return int(32767 * angle / STEERING_RANGE_RAD + 32767)


@dataclass(slots=True)
class RobotFeedbackPacket:
    sync0: int
    sync1: int
    crc8: int
    check_counter: int
    tx_cycle_count: int
    current_error_id: int
    current_error_info: int
    current_error_value: float
    imu_yaw_deg: float
    ball_detection: tuple[int, int]
    ball_detection_extra: int
    diff_angle_deg: float
    battery_voltage: float
    kick_state_div10: int
    temp_fet: int
    temp_coil: tuple[int, int]
    capacitor_boost_voltage: float
    mouse_odom_x: float
    mouse_odom_y: float
    mouse_global_vel_x: float
    mouse_global_vel_y: float
    mouse_quality: float
    motor_current_x10: tuple[int, int, int, int]
    temp_motor: tuple[int, int, int, int]
    output_vel_x: float
    output_vel_y: float
    motor_feedback_0: float
    motor_feedback_1: float
    motor_feedback_2: float
    motor_feedback_3: float
    local_odom_speed_mvf_x: float
    local_odom_speed_mvf_y: float
    local_odom_speed_mvf_w: float
    steering_angle: tuple[float, float, float, float]
    temp_steering_motor: tuple[int, int, int, int]
    vision_based_position_x: float
    vision_based_position_y: float
    global_odom_speed_x: float
    global_odom_speed_y: float
    crc_valid: bool

    @property
    def is_sync_valid(self) -> bool:
        return self.sync0 == SYNC0 and self.sync1 in (SYNC1, UDP_4WS_SYNC1)

    @property
    def machine_type(self) -> str:
        return "4ws" if self.sync1 == UDP_4WS_SYNC1 else "orion"

    @property
    def is_crc_valid(self) -> bool:
        return self.crc_valid

    @property
    def kick_state(self) -> int:
        return self.kick_state_div10 * 10

    @property
    def motor_current(self) -> tuple[float, float, float, float]:
        return tuple(value / 10.0 for value in self.motor_current_x10)

    def to_bytes(self) -> bytes:
        data = bytearray(PACKET_SIZE)
        data[0:4] = bytes((self.sync0, self.sync1, self.crc8, self.check_counter))
        data[4] = self.tx_cycle_count
        struct.pack_into("<HHff", data, 5, self.current_error_id, self.current_error_info,
                         self.current_error_value, self.imu_yaw_deg)
        data[17:19] = bytes(self.ball_detection)
        data[19] = self.ball_detection_extra
        struct.pack_into("<ff", data, 20, self.diff_angle_deg, self.battery_voltage)
        data[28:32] = bytes((self.kick_state_div10, self.temp_fet, *self.temp_coil))
        struct.pack_into("<6f", data, 32, self.capacitor_boost_voltage, self.mouse_odom_x,
                         self.mouse_odom_y, self.mouse_global_vel_x, self.mouse_global_vel_y,
                         self.mouse_quality)
        data[56:60] = bytes(self.motor_current_x10)
        data[60:64] = bytes(self.temp_motor)
        struct.pack_into("<9f", data, 64, self.output_vel_x, self.output_vel_y,
                         self.motor_feedback_0, self.motor_feedback_1, self.motor_feedback_2,
                         self.motor_feedback_3, self.local_odom_speed_mvf_x,
                         self.local_odom_speed_mvf_y, self.local_odom_speed_mvf_w)
        for index, angle in enumerate(self.steering_angle):
            struct.pack_into(">H", data, 100 + 2 * index, encode_steering_angle(angle))
        data[108:112] = bytes(self.temp_steering_motor)
        struct.pack_into("<4f", data, 112, self.vision_based_position_x,
                         self.vision_based_position_y, self.global_odom_speed_x,
                         self.global_odom_speed_y)
        return bytes(data)


def crc8_atm(data: bytes) -> int:
    crc = 0
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def calc_feedback_crc8(data: bytes) -> int:
    if len(data) != PACKET_SIZE:
        raise ValueError(f"packet size must be {PACKET_SIZE}, got {len(data)}")
    return crc8_atm(data[3:])


def decode_robot_feedback_packet(data: bytes) -> RobotFeedbackPacket:
    if len(data) != PACKET_SIZE:
        raise ValueError(f"packet size must be {PACKET_SIZE}, got {len(data)}")

    f = lambda offset: struct.unpack_from("<f", data, offset)[0]
    return RobotFeedbackPacket(
        sync0=data[0], sync1=data[1], crc8=data[2], check_counter=data[3],
        tx_cycle_count=data[4],
        current_error_id=struct.unpack_from("<H", data, 5)[0],
        current_error_info=struct.unpack_from("<H", data, 7)[0],
        current_error_value=f(9), imu_yaw_deg=f(13),
        ball_detection=(data[17], data[18]), ball_detection_extra=data[19],
        diff_angle_deg=f(20), battery_voltage=f(24), kick_state_div10=data[28],
        temp_fet=data[29], temp_coil=(data[30], data[31]),
        capacitor_boost_voltage=f(32), mouse_odom_x=f(36), mouse_odom_y=f(40),
        mouse_global_vel_x=f(44), mouse_global_vel_y=f(48), mouse_quality=f(52),
        motor_current_x10=tuple(data[56:60]), temp_motor=tuple(data[60:64]),
        output_vel_x=f(64), output_vel_y=f(68),
        motor_feedback_0=f(72), motor_feedback_1=f(76),
        motor_feedback_2=f(80), motor_feedback_3=f(84),
        local_odom_speed_mvf_x=f(88), local_odom_speed_mvf_y=f(92),
        local_odom_speed_mvf_w=f(96),
        steering_angle=tuple(decode_steering_angle(struct.unpack_from(">H", data, 100 + 2 * i)[0])
                             for i in range(4)),
        temp_steering_motor=tuple(data[108:112]),
        vision_based_position_x=f(112), vision_based_position_y=f(116),
        global_odom_speed_x=f(120), global_odom_speed_y=f(124),
        crc_valid=data[2] == calc_feedback_crc8(data),
    )
