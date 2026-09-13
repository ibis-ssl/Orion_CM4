// このファイルは G474 から届く 128 バイト feedback パケットのレイアウトを
// 定義する責務を持つ。正本は STM32 の Core/Src/ai_comm.c sendRobotInfo()。
//
// 【なぜ共有ヘッダにするか】
// この構造体はもともと forward_robot_feedback.cpp の中だけにあったが、CM4 が
// 位置制御ループを閉じるようになって消費者が 1 つから 3 つに増えた
// (robot_feedback.out / ai_cmd_v2.out / cm4_sim.out)。しかも byte 44..51 は
// 位置制御ループ内で唯一の位置信号である。
//
// オフセット 44 / 48 を 3 ファイルにマジックナンバーで散らすと、G474 が byte 44 の
// 手前にフィールドを 1 つ挿入しただけで実機が場外へ走り、しかも単体テストも
// レイアウト検査も緑のままになる。制御パケット側 (robot_packet.h) は過去に
// 2 度ドリフトした経験から robot_packet_layout_test.cpp で守られているので、
// feedback 側も同じ保護下に置く。
//
// 下位の offsetof 検査は robot_packet_layout_test.cpp が実行する。

#ifndef ORION_CM4__BRIDGE__ROBOT_FEEDBACK_PACKET_H_
#define ORION_CM4__BRIDGE__ROBOT_FEEDBACK_PACKET_H_

#include <stddef.h>
#include <stdint.h>
#include <string.h>

constexpr int FEEDBACK_PACKET_SIZE = 128;
constexpr uint8_t FEEDBACK_SYNC0 = 0xAB;
constexpr uint8_t FEEDBACK_SYNC1 = 0xEA;

#pragma pack(push, 1)

struct RobotFeedbackPacketHeader
{
  uint8_t sync0;
  uint8_t sync1;
  // 実機は定数 10 を書くだけで、実際のチェックサムは計算していない
  // (ai_comm.c の `buf[2] = 10;  // CRC, 10:dummy`)。検証に使ってはいけない。
  uint8_t checksum;
  uint8_t check_counter;
};

struct RobotFeedbackPacket
{
  RobotFeedbackPacketHeader header;

  // STM32 Core/Src/ai_comm.c の sendRobotInfo() に対応するペイロード。
  // float 値は STM32 側 float_to_uchar4() の生バイト列がそのまま格納される。
  // したがって little-endian IEEE754 float 前提で読む必要がある。
  float imu_yaw_deg;                   // [4..7]
  float battery_voltage_bldc_right;   // [8..11]
  uint8_t ball_detection[2];           // [12..13]
  uint8_t tx_cycle_count;             // [14] 送信ごとにインクリメント。ball_detection ではない
  uint8_t kick_state_div10;           // [15]
  uint16_t current_error_id;          // [16..17] little-endian
  uint16_t current_error_info;        // [18..19] little-endian
  float current_error_value;          // [20..23]
  uint8_t motor_current_x10[4];       // [24..27]
  uint8_t ball_detection_extra;       // [28]
  uint8_t temp_motor[4];              // [29..32]
  uint8_t temp_fet;                   // [33]
  uint8_t temp_coil[2];               // [34..35]
  float diff_angle_deg;               // [36..39]
  float capacitor_boost_voltage;      // [40..43]
  float vision_based_position_x;      // [44..47]
  float vision_based_position_y;      // [48..51]
  float global_odom_speed_x;          // [52..55]
  float global_odom_speed_y;          // [56..59]
  uint8_t camera_pos_x_div2;          // [60]
  uint8_t camera_pos_y;               // [61]
  uint8_t camera_radius_div4;         // [62]
  uint8_t camera_fps;                 // [63]
  float tx_value_array[14];           // [64..119]
  uint8_t reserved[8];                // [120..127] 現状は未使用。送信側で明示初期化なし。
};

#pragma pack(pop)

static_assert(sizeof(RobotFeedbackPacket) == FEEDBACK_PACKET_SIZE, "RobotFeedbackPacket size must be 128 bytes");

// 位置制御ループが使う唯一のフィールド。ここがずれるとロボットが場外へ走る。
constexpr size_t FEEDBACK_POS_X_OFFSET = offsetof(RobotFeedbackPacket, vision_based_position_x);
constexpr size_t FEEDBACK_POS_Y_OFFSET = offsetof(RobotFeedbackPacket, vision_based_position_y);

// 受信バッファから位置 [m] を取り出す。長さと同期バイトを検査する。
//
// yaw (byte 4..7) は実機が度・シミュレータがラジアンでずれており、制御則も
// 使わないので読まない。入力に含めると 57.3 倍の食い違いを作り込むことになる。
inline bool decodeFeedbackPosition(const void * buf, size_t size, float out_pos[2])
{
  if (size != FEEDBACK_PACKET_SIZE) return false;
  const uint8_t * bytes = static_cast<const uint8_t *>(buf);
  if (bytes[0] != FEEDBACK_SYNC0 || bytes[1] != FEEDBACK_SYNC1) return false;
  memcpy(&out_pos[0], bytes + FEEDBACK_POS_X_OFFSET, sizeof(float));
  memcpy(&out_pos[1], bytes + FEEDBACK_POS_Y_OFFSET, sizeof(float));
  return true;
}

enum TxValueIndex {
  TX_MOUSE_ODOM_X = 0,
  TX_MOUSE_ODOM_Y,
  TX_MOUSE_GLOBAL_VEL_X,
  TX_MOUSE_GLOBAL_VEL_Y,
  TX_OUTPUT_VEL_X,
  TX_OUTPUT_VEL_Y,
  TX_MOTOR_FEEDBACK_0,
  TX_MOTOR_FEEDBACK_1,
  TX_MOTOR_FEEDBACK_2,
  TX_MOTOR_FEEDBACK_3,
  TX_LOCAL_ODOM_SPEED_MVF_X,
  TX_LOCAL_ODOM_SPEED_MVF_Y,
  TX_LOCAL_ODOM_SPEED_MVF_W,
  TX_MOUSE_QUALITY,
};

#endif  // ORION_CM4__BRIDGE__ROBOT_FEEDBACK_PACKET_H_
