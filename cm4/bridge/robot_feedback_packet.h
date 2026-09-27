// このファイルはOrionMainと4WS Mainで共通の128バイトfeedback配置と検証を定義する。
// CM4の位置制御とUDP配信は、同じ同期・CRC・バイト位置を参照する。
#ifndef ORION_CM4__BRIDGE__ROBOT_FEEDBACK_PACKET_H_
#define ORION_CM4__BRIDGE__ROBOT_FEEDBACK_PACKET_H_

#include <cmath>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

constexpr int FEEDBACK_PACKET_SIZE = 128;
constexpr uint8_t FEEDBACK_SYNC0 = 0xAB;
constexpr uint8_t FEEDBACK_SYNC1 = 0xEA;

#pragma pack(push, 1)

struct RobotFeedbackPacketHeader
{
  uint8_t sync0;              // [0]
  uint8_t sync1;              // [1]
  uint8_t crc8;               // [2] CRC-8/ATM over byte 3..127
  uint8_t check_counter;      // [3]
};

struct RobotFeedbackPacket
{
  RobotFeedbackPacketHeader header;
  uint8_t tx_cycle_count;            // [4]
  uint16_t current_error_id;         // [5..6] little-endian
  uint16_t current_error_info;       // [7..8] little-endian
  float current_error_value;         // [9..12]
  float imu_yaw_deg;                 // [13..16]
  uint8_t ball_detection[2];        // [17..18]
  uint8_t ball_detection_extra;      // [19]
  float diff_angle_deg;              // [20..23]
  float battery_voltage;             // [24..27]
  uint8_t kick_state_div10;          // [28]
  uint8_t temp_fet;                  // [29]
  uint8_t temp_coil[2];              // [30..31]
  float capacitor_boost_voltage;     // [32..35]
  float mouse_odom_x;                // [36..39]
  float mouse_odom_y;                // [40..43]
  float mouse_global_vel_x;          // [44..47]
  float mouse_global_vel_y;          // [48..51]
  float mouse_quality;               // [52..55]
  uint8_t motor_current_x10[4];     // [56..59]
  uint8_t temp_motor[4];            // [60..63]
  float output_vel_x;                // [64..67]
  float output_vel_y;                // [68..71]
  float motor_feedback[4];           // [72..87]
  float local_odom_speed_mvf[3];     // [88..99]
  uint8_t steering_angle[4][2];     // [100..107] high byte first, ±10π rad
  uint8_t temp_steering_motor[4];   // [108..111]
  float vision_based_position_x;     // [112..115]
  float vision_based_position_y;     // [116..119]
  float global_odom_speed_x;         // [120..123]
  float global_odom_speed_y;         // [124..127]
};

#pragma pack(pop)

static_assert(sizeof(RobotFeedbackPacket) == FEEDBACK_PACKET_SIZE, "RobotFeedbackPacket must be 128 bytes");
constexpr size_t FEEDBACK_POS_X_OFFSET = offsetof(RobotFeedbackPacket, vision_based_position_x);
constexpr size_t FEEDBACK_POS_Y_OFFSET = offsetof(RobotFeedbackPacket, vision_based_position_y);

inline uint8_t feedbackCrc8(const uint8_t * data, size_t size)
{
  uint8_t crc = 0;
  for (size_t i = 0; i < size; i++) {
    crc ^= data[i];
    for (int bit = 0; bit < 8; bit++) {
      crc = (crc & 0x80U) ? static_cast<uint8_t>((crc << 1) ^ 0x07U) : static_cast<uint8_t>(crc << 1);
    }
  }
  return crc;
}

inline bool isFeedbackPacketValid(const void * buf, size_t size)
{
  if (size != FEEDBACK_PACKET_SIZE) return false;
  const uint8_t * bytes = static_cast<const uint8_t *>(buf);
  return bytes[0] == FEEDBACK_SYNC0 && bytes[1] == FEEDBACK_SYNC1 &&
         bytes[2] == feedbackCrc8(bytes + 3, FEEDBACK_PACKET_SIZE - 3);
}

inline bool decodeFeedbackPosition(const void * buf, size_t size, float out_pos[2])
{
  if (!isFeedbackPacketValid(buf, size)) return false;
  const uint8_t * bytes = static_cast<const uint8_t *>(buf);
  float pos[2];
  memcpy(&pos[0], bytes + FEEDBACK_POS_X_OFFSET, sizeof(float));
  memcpy(&pos[1], bytes + FEEDBACK_POS_Y_OFFSET, sizeof(float));
  if (!std::isfinite(pos[0]) || !std::isfinite(pos[1])) return false;
  out_pos[0] = pos[0];
  out_pos[1] = pos[1];
  return true;
}

#endif  // ORION_CM4__BRIDGE__ROBOT_FEEDBACK_PACKET_H_
