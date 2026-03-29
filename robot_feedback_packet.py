# このファイルはforward_robot_feedback.cppが転送する128バイトの状態パケットを
# Python でデコードし、Windows/Linux 共通の受信ツールから扱える形へ変換する。
from __future__ import annotations

from dataclasses import dataclass
import struct

PACKET_SIZE = 128
SYNC0 = 0xAB
SYNC1 = 0xEA
FLOAT_BLOCK_OFFSET = 64
FLOAT_BLOCK_COUNT = 14


@dataclass(slots=True)
class RobotFeedbackPacket:
    sync0: int
    sync1: int
    checksum: int
    check_counter: int
    imu_yaw_deg: float
    battery_voltage_bldc_right: float
    ball_detection: tuple[int, int, int]
    kick_state_div10: int
    current_error_id: int
    current_error_info: int
    current_error_value: float
    motor_current_x10: tuple[int, int, int, int]
    ball_detection_extra: int
    temp_motor: tuple[int, int, int, int]
    temp_fet: int
    temp_coil: tuple[int, int]
    diff_angle_deg: float
    capacitor_boost_voltage: float
    vision_based_position_x: float
    vision_based_position_y: float
    global_odom_speed_x: float
    global_odom_speed_y: float
    camera_pos_x_div2: int
    camera_pos_y: int
    camera_radius_div4: int
    camera_fps: int
    tx_value_array: tuple[float, ...]
    reserved: bytes

    @property
    def is_sync_valid(self) -> bool:
        return self.sync0 == SYNC0 and self.sync1 == SYNC1

    @property
    def is_checksum_valid(self) -> bool:
        return self.checksum == calc_checksum(self.to_bytes())

    @property
    def camera_pos_x(self) -> int:
        return self.camera_pos_x_div2 * 2

    @property
    def camera_radius(self) -> int:
        return self.camera_radius_div4 * 4

    @property
    def kick_state(self) -> int:
        return self.kick_state_div10 * 10

    @property
    def motor_current(self) -> tuple[float, float, float, float]:
        return tuple(value / 10.0 for value in self.motor_current_x10)

    def to_bytes(self) -> bytes:
        data = bytearray(PACKET_SIZE)
        data[0] = self.sync0
        data[1] = self.sync1
        data[2] = self.checksum
        data[3] = self.check_counter
        struct.pack_into("<f", data, 4, self.imu_yaw_deg)
        struct.pack_into("<f", data, 8, self.battery_voltage_bldc_right)
        data[12:15] = bytes(self.ball_detection)
        data[15] = self.kick_state_div10
        struct.pack_into("<H", data, 16, self.current_error_id)
        struct.pack_into("<H", data, 18, self.current_error_info)
        struct.pack_into("<f", data, 20, self.current_error_value)
        data[24:28] = bytes(self.motor_current_x10)
        data[28] = self.ball_detection_extra
        data[29:33] = bytes(self.temp_motor)
        data[33] = self.temp_fet
        data[34:36] = bytes(self.temp_coil)
        struct.pack_into("<f", data, 36, self.diff_angle_deg)
        struct.pack_into("<f", data, 40, self.capacitor_boost_voltage)
        struct.pack_into("<f", data, 44, self.vision_based_position_x)
        struct.pack_into("<f", data, 48, self.vision_based_position_y)
        struct.pack_into("<f", data, 52, self.global_odom_speed_x)
        struct.pack_into("<f", data, 56, self.global_odom_speed_y)
        data[60] = self.camera_pos_x_div2
        data[61] = self.camera_pos_y
        data[62] = self.camera_radius_div4
        data[63] = self.camera_fps
        struct.pack_into("<14f", data, FLOAT_BLOCK_OFFSET, *self.tx_value_array)
        data[120:128] = self.reserved
        return bytes(data)


def calc_checksum(data: bytes) -> int:
    if len(data) != PACKET_SIZE:
        raise ValueError(f"packet size must be {PACKET_SIZE}, got {len(data)}")
    return sum(data[3:]) & 0xFF


def decode_robot_feedback_packet(data: bytes) -> RobotFeedbackPacket:
    if len(data) != PACKET_SIZE:
        raise ValueError(f"packet size must be {PACKET_SIZE}, got {len(data)}")

    tx_value_array = struct.unpack_from("<14f", data, FLOAT_BLOCK_OFFSET)
    return RobotFeedbackPacket(
        sync0=data[0],
        sync1=data[1],
        checksum=data[2],
        check_counter=data[3],
        imu_yaw_deg=struct.unpack_from("<f", data, 4)[0],
        battery_voltage_bldc_right=struct.unpack_from("<f", data, 8)[0],
        ball_detection=(data[12], data[13], data[14]),
        kick_state_div10=data[15],
        current_error_id=struct.unpack_from("<H", data, 16)[0],
        current_error_info=struct.unpack_from("<H", data, 18)[0],
        current_error_value=struct.unpack_from("<f", data, 20)[0],
        motor_current_x10=(data[24], data[25], data[26], data[27]),
        ball_detection_extra=data[28],
        temp_motor=(data[29], data[30], data[31], data[32]),
        temp_fet=data[33],
        temp_coil=(data[34], data[35]),
        diff_angle_deg=struct.unpack_from("<f", data, 36)[0],
        capacitor_boost_voltage=struct.unpack_from("<f", data, 40)[0],
        vision_based_position_x=struct.unpack_from("<f", data, 44)[0],
        vision_based_position_y=struct.unpack_from("<f", data, 48)[0],
        global_odom_speed_x=struct.unpack_from("<f", data, 52)[0],
        global_odom_speed_y=struct.unpack_from("<f", data, 56)[0],
        camera_pos_x_div2=data[60],
        camera_pos_y=data[61],
        camera_radius_div4=data[62],
        camera_fps=data[63],
        tx_value_array=tx_value_array,
        reserved=data[120:128],
    )


TX_VALUE_LABELS = (
    "mouse_odom_x",
    "mouse_odom_y",
    "mouse_global_vel_x",
    "mouse_global_vel_y",
    "output_vel_x",
    "output_vel_y",
    "motor_feedback_0",
    "motor_feedback_1",
    "motor_feedback_2",
    "motor_feedback_3",
    "local_odom_speed_mvf_x",
    "local_odom_speed_mvf_y",
    "local_odom_speed_mvf_w",
    "mouse_quality",
)
