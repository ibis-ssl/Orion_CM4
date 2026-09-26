# -*- coding: utf-8 -*-
"""テストが使うパケットのオフセットと符号化を 1 箇所に集める。

このリポジトリは C++ 側のレイアウトを robot_packet_layout_test.cpp の
static_assert で守っているが、Python テストが独自にオフセット表を持つと
そちらは誰も検査しないまま静かにずれる。表を 1 つにして、ずれたときに
両方のテストが同時に落ちるようにする。

正本は cm4/bridge/robot_packet.h の enum Address / FlagAddress / ControlMode。
"""

import struct

CMD_SIZE = 64
SLOTS = 11
SLOT_SIZE = CMD_SIZE + 1
INPUT_PACKET_SIZE = SLOT_SIZE  # crane -> CM4: 機体ごとに65バイト
PACKET_SIZE = SLOT_SIZE * SLOTS  # cm4_sim -> simulator-cli: 715バイト
FEEDBACK_SIZE = 128
FEEDBACK_SYNC = (0xAB, 0xEA)
FEEDBACK_POS_X_OFFSET = 44
FEEDBACK_POS_Y_OFFSET = 48

# robot_packet.h の enum Address
CHECK_COUNTER = 1
VISION_GLOBAL_X_HIGH = 2
VISION_GLOBAL_Y_HIGH = 4
VISION_GLOBAL_THETA_HIGH = 6
TARGET_GLOBAL_THETA_HIGH = 8
KICK_POWER = 10
DRIBBLE_POWER = 11
ACCELERATION_LIMIT_HIGH = 12
LINEAR_VELOCITY_LIMIT_HIGH = 14
ANGULAR_VELOCITY_LIMIT_HIGH = 16
LATENCY_TIME_MS_HIGH = 18
ELAPSED_TIME_MS_SINCE_LAST_VISION_HIGH = 20
FLAGS = 22
CONTROL_MODE = 23
CONTROL_MODE_ARGS = 24
TARGET_GLOBAL_POS_X_HIGH = 32
TARGET_GLOBAL_POS_Y_HIGH = 34
TERMINAL_VELOCITY_HIGH = 36

# robot_packet.h の enum FlagAddress
IS_VISION_AVAILABLE_BIT = 0
ENABLE_CHIP_BIT = 1
STOP_EMERGENCY_BIT = 3

# robot_packet.h の enum ControlMode
MODE_POLAR_VELOCITY = 3
MODE_POSITION_TARGET = 4


def encode_two_byte(value, value_range):
    """robot_packet.h convertFloatToTwoByte と同じ。

    range でクランプしてから量子化し、丸めずに切り捨てる。クランプを
    量子化後の整数に対して行うと範囲外の値で 1 LSB ずれる。
    """
    clamped = max(-value_range, min(value_range, value))
    raw = int(32767.0 * (clamped / value_range) + 32767.0)
    raw = max(0, min(65535, raw))
    return bytes([(raw >> 8) & 0xFF, raw & 0xFF])


def decode_two_byte(data, offset, value_range):
    raw = (data[offset] << 8) | data[offset + 1]
    return (raw - 32767.0) / 32767.0 * value_range


def build_packet(robot_id, command):
    """craneからCM4へ送る65バイトの機体別指令を作る。"""
    if not 0 <= robot_id < SLOTS or len(command) != CMD_SIZE:
        raise ValueError("robot_idは0..10、commandは64バイトが必要")
    return bytes([robot_id]) + bytes(command)


# --- 位置制御の設定パケット (config_packet.h) ---
# crane が位置制御ゲイン (PID) を稼働中に変更するための 28 バイト。指令パケットとは
# 別ポートで、2 バイト固定小数ではなく素の float32 little endian を使う。
# 設定も各機体のIP宛てへ送る。robot_idフィールドを指定して受信側で照合する。
CONFIG_PACKET_SIZE = 28
CONFIG_PACKET_VERSION = 2
CONFIG_BROADCAST_ID = 0xFF
CONFIG_PACKET_FORMAT = "<4sBBHfffff"


def build_config_packet(kp, decel, tolerance, robot_id=CONFIG_BROADCAST_ID, ki=0.0, kd=0.0):
    return struct.pack(CONFIG_PACKET_FORMAT, b"OC4C", CONFIG_PACKET_VERSION, robot_id, 0, kp, decel, tolerance, ki, kd)
