// このファイルは robot_packet.h のバイトレイアウトが crane 側の正本から
// ドリフトしていないことを検査する責務を持つ。
//
// 正本: crane/crane_sender/include/crane_sender/robot_packet.h
// crane は本リポジトリの submodule ではないので CI から正本を直接読めない。
// 代わりにここへ「正本から算出した固定値」を埋め込み、機械検査する。
//
// 検査するもの:
//   1. byte 0..37 の全オフセット (static_assert)
//   2. RobotCommandSerializedV2 のサイズ (static_assert)
//   3. ControlMode / FlagAddress の数値 (static_assert)
//   4. ゴールデンベクタ: 既知のコマンドをシリアライズしたバイト列。
//      オフセットだけでなく量子化幅 (range 32.767 -> 約 1mm) と
//      「丸めず切り捨て」という crane と bit 一致すべき挙動を固定する。
//   5. mode 3 / mode 4 の CONTROL_MODE_ARGS union の往復
//   6. 範囲外クランプの挙動
//
// 失敗時は非ゼロ終了する。cm4/build.sh と CI から実行される。

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "robot_packet.h"
#include "robot_feedback_packet.h"

// ---------------------------------------------------------------------------
// 1. バイトオフセット (crane 版 enum Address の数値)
// ---------------------------------------------------------------------------
static_assert(HEADER == 0, "HEADER");
static_assert(CHECK_COUNTER == 1, "CHECK_COUNTER");
static_assert(VISION_GLOBAL_X_HIGH == 2, "VISION_GLOBAL_X_HIGH");
static_assert(VISION_GLOBAL_X_LOW == 3, "VISION_GLOBAL_X_LOW");
static_assert(VISION_GLOBAL_Y_HIGH == 4, "VISION_GLOBAL_Y_HIGH");
static_assert(VISION_GLOBAL_Y_LOW == 5, "VISION_GLOBAL_Y_LOW");
static_assert(VISION_GLOBAL_THETA_HIGH == 6, "VISION_GLOBAL_THETA_HIGH");
static_assert(VISION_GLOBAL_THETA_LOW == 7, "VISION_GLOBAL_THETA_LOW");
static_assert(TARGET_GLOBAL_THETA_HIGH == 8, "TARGET_GLOBAL_THETA_HIGH");
static_assert(TARGET_GLOBAL_THETA_LOW == 9, "TARGET_GLOBAL_THETA_LOW");
static_assert(KICK_POWER == 10, "KICK_POWER");
static_assert(DRIBBLE_POWER == 11, "DRIBBLE_POWER");
// ここから下が 2026-09 に発覚した 2 バイトずれの発生箇所。
// 旧 CM4 版は ACCELERATION_LIMIT を持たず SPEED_LIMIT(12,13) / OMEGA_LIMIT(14,15) だった。
static_assert(ACCELERATION_LIMIT_HIGH == 12, "ACCELERATION_LIMIT_HIGH");
static_assert(ACCELERATION_LIMIT_LOW == 13, "ACCELERATION_LIMIT_LOW");
static_assert(LINEAR_VELOCITY_LIMIT_HIGH == 14, "LINEAR_VELOCITY_LIMIT_HIGH");
static_assert(LINEAR_VELOCITY_LIMIT_LOW == 15, "LINEAR_VELOCITY_LIMIT_LOW");
static_assert(ANGULAR_VELOCITY_LIMIT_HIGH == 16, "ANGULAR_VELOCITY_LIMIT_HIGH");
static_assert(ANGULAR_VELOCITY_LIMIT_LOW == 17, "ANGULAR_VELOCITY_LIMIT_LOW");
static_assert(LATENCY_TIME_MS_HIGH == 18, "LATENCY_TIME_MS_HIGH");
static_assert(LATENCY_TIME_MS_LOW == 19, "LATENCY_TIME_MS_LOW");
static_assert(ELAPSED_TIME_MS_SINCE_LAST_VISION_HIGH == 20, "ELAPSED_TIME_MS_SINCE_LAST_VISION_HIGH");
static_assert(ELAPSED_TIME_MS_SINCE_LAST_VISION_LOW == 21, "ELAPSED_TIME_MS_SINCE_LAST_VISION_LOW");
static_assert(FLAGS == 22, "FLAGS");
static_assert(CONTROL_MODE == 23, "CONTROL_MODE");
static_assert(CONTROL_MODE_ARGS == 24, "CONTROL_MODE_ARGS");
static_assert(MODE_ARGS_SIZE == 8, "MODE_ARGS_SIZE");
static_assert(TARGET_GLOBAL_POS_X_HIGH == 32, "TARGET_GLOBAL_POS_X_HIGH");
static_assert(TARGET_GLOBAL_POS_X_LOW == 33, "TARGET_GLOBAL_POS_X_LOW");
static_assert(TARGET_GLOBAL_POS_Y_HIGH == 34, "TARGET_GLOBAL_POS_Y_HIGH");
static_assert(TARGET_GLOBAL_POS_Y_LOW == 35, "TARGET_GLOBAL_POS_Y_LOW");
static_assert(TERMINAL_VELOCITY_HIGH == 36, "TERMINAL_VELOCITY_HIGH");
static_assert(TERMINAL_VELOCITY_LOW == 37, "TERMINAL_VELOCITY_LOW");

// ---------------------------------------------------------------------------
// 2. サイズ
// ---------------------------------------------------------------------------
static_assert(sizeof(RobotCommandSerializedV2) == 64, "RobotCommandSerializedV2 must be 64 bytes");

// --- G474 feedback パケット (robot_feedback_packet.h) ---
//
// 制御パケットと違い、こちらは過去にドリフトしていない。だが CM4 が位置ループを
// 閉じるようになって消費者が 1 つから 3 つに増え、byte 44..51 は位置制御ループ内で
// 唯一の位置信号になった。G474 がこの手前にフィールドを 1 つ挿入すると、実機は
// 目標と無関係な位置へ走り出すのに単体テストは緑のままになる。ここで止める。
static_assert(sizeof(RobotFeedbackPacket) == 128, "feedback packet must be 128 bytes");
static_assert(offsetof(RobotFeedbackPacket, header) == 0, "feedback header offset");
static_assert(offsetof(RobotFeedbackPacket, imu_yaw_deg) == 4, "feedback imu_yaw_deg offset");
static_assert(offsetof(RobotFeedbackPacket, battery_voltage_bldc_right) == 8, "feedback battery offset");
static_assert(offsetof(RobotFeedbackPacket, ball_detection) == 12, "feedback ball_detection offset");
// byte 14 は ball_detection の 3 つ目ではなく送信サイクルカウンタ
// (STM32 ai_comm.c の `buf[14] = tx_cycle_count;`)。doc/feedback_packet.md 参照。
static_assert(offsetof(RobotFeedbackPacket, tx_cycle_count) == 14, "feedback tx_cycle_count offset");
static_assert(offsetof(RobotFeedbackPacket, kick_state_div10) == 15, "feedback kick_state offset");
static_assert(offsetof(RobotFeedbackPacket, capacitor_boost_voltage) == 40, "feedback capacitor offset");
static_assert(offsetof(RobotFeedbackPacket, vision_based_position_x) == 44, "feedback pos x offset");
static_assert(offsetof(RobotFeedbackPacket, vision_based_position_y) == 48, "feedback pos y offset");
static_assert(offsetof(RobotFeedbackPacket, global_odom_speed_x) == 52, "feedback odom x offset");
static_assert(offsetof(RobotFeedbackPacket, global_odom_speed_y) == 56, "feedback odom y offset");
static_assert(offsetof(RobotFeedbackPacket, camera_pos_x_div2) == 60, "feedback camera offset");
static_assert(offsetof(RobotFeedbackPacket, tx_value_array) == 64, "feedback tx_value_array offset");
static_assert(offsetof(RobotFeedbackPacket, reserved) == 120, "feedback reserved offset");
static_assert(FEEDBACK_POS_X_OFFSET == 44, "decodeFeedbackPosition reads byte 44");
static_assert(FEEDBACK_POS_Y_OFFSET == 48, "decodeFeedbackPosition reads byte 48");
static_assert(TERMINAL_VELOCITY_LOW < 64, "packet fields must fit in 64 bytes");

// ---------------------------------------------------------------------------
// 3. ControlMode / FlagAddress
// ---------------------------------------------------------------------------
static_assert(POLAR_VELOCITY_TARGET_MODE == 3, "mode 3 = CM4/cm4_sim -> G474/simulator-cli");
static_assert(POSITION_TARGET_WITH_TERMINAL_VELOCITY_MODE == 4, "mode 4 = crane -> CM4/cm4_sim");
static_assert(IS_VISION_AVAILABLE == 0, "FLAGS bit0");
static_assert(ENABLE_CHIP == 1, "FLAGS bit1");
static_assert(STOP_EMERGENCY == 3, "FLAGS bit3");

// ---------------------------------------------------------------------------
// テストハーネス (リポジトリにテストフレームワークが無いので素の assert + 終了コード)
// ---------------------------------------------------------------------------
static int g_failures = 0;

static void check(bool ok, const char * name)
{
  if (!ok) {
    printf("[FAIL] %s\n", name);
    g_failures++;
  } else {
    printf("[ok]   %s\n", name);
  }
}

static void checkClose(float actual, float expected, float tol, const char * name)
{
  const bool ok = fabsf(actual - expected) <= tol;
  if (!ok) {
    printf("[FAIL] %s: actual %.6f expected %.6f (tol %.6f)\n", name, actual, expected, tol);
    g_failures++;
  } else {
    printf("[ok]   %s (%.6f)\n", name, actual);
  }
}

static void checkBytes(const uint8_t * actual, const uint8_t * expected, int n, const char * name)
{
  if (memcmp(actual, expected, n) == 0) {
    printf("[ok]   %s\n", name);
    return;
  }
  printf("[FAIL] %s\n", name);
  for (int i = 0; i < n; i++) {
    if (actual[i] != expected[i]) {
      printf("       byte %2d: actual 0x%02X expected 0x%02X\n", i, actual[i], expected[i]);
    }
  }
  g_failures++;
}

// ---------------------------------------------------------------------------
// 4. ゴールデンベクタ
//
// 期待値は crane 版 convertFloatToTwoByte の式
//   u = (uint16_t)(32767.f * (float)(val / range) + 32767.f)   ※丸めなし・切り捨て
// を float32 で厳密に評価して算出した。crane のヘッダを読んで生成したのではなく、
// 式から独立に計算した値なので、実装のコピーではなく検査になっている。
// ---------------------------------------------------------------------------
static RobotCommandV2 makeGoldenCommand(void)
{
  RobotCommandV2 c;
  memset(&c, 0, sizeof(c));
  c.header = 0xFE;
  c.check_counter = 123;
  c.vision_global_pos[0] = 1.5f;
  c.vision_global_pos[1] = -2.25f;
  c.vision_global_theta = 0.75f;
  c.is_vision_available = true;
  c.target_global_theta = -1.25f;
  c.kick_power = 0.5f;
  c.dribble_power = 0.25f;
  c.enable_chip = true;
  c.stop_emergency = false;
  c.acceleration_limit = 4.0f;
  c.linear_velocity_limit = 3.0f;
  c.angular_velocity_limit = 5.0f;
  c.latency_time_ms = 100;
  c.elapsed_time_ms_since_last_vision = 33;
  c.target_global_pos[0] = 2.0f;
  c.target_global_pos[1] = -1.0f;
  c.terminal_velocity = 0.5f;
  return c;
}

static const uint8_t kGoldenMode4[38] = {
  0xFE, 0x7B, 0x85, 0xDB, 0x77, 0x35, 0x9E, 0x8D,  // 0..7
  0x4D, 0x11, 0x0A, 0x05, 0x8F, 0x9F, 0x8B, 0xB7,  // 8..15
  0x93, 0x87, 0x00, 0x64, 0x00, 0x21, 0x03, 0x04,  // 16..23
  0x81, 0x8F, 0x7E, 0xD3, 0x00, 0x00, 0x00, 0x00,  // 24..31
  0x87, 0xCF, 0x7C, 0x17, 0x81, 0xF3,              // 32..37
};

// mode 3 は CONTROL_MODE と CONTROL_MODE_ARGS だけが mode 4 と異なる。
static const uint8_t kGoldenMode3ControlMode = 0x03;
static const uint8_t kGoldenMode3Args[4] = {0x86, 0xD5, 0x76, 0x3B};

static void testGoldenMode4(void)
{
  RobotCommandV2 c = makeGoldenCommand();
  c.control_mode = POSITION_TARGET_WITH_TERMINAL_VELOCITY_MODE;
  c.mode_args.position_target.terminal_velocity_x = 0.4f;
  c.mode_args.position_target.terminal_velocity_y = -0.3f;

  RobotCommandSerializedV2 s;
  memset(&s, 0, sizeof(s));  // serialize は 28..31 / 38..63 を書かない
  RobotCommandSerializedV2_serialize(&s, &c);
  checkBytes(s.data, kGoldenMode4, 38, "golden vector: mode 4 byte 0..37");

  RobotCommandV2 back = RobotCommandSerializedV2_deserialize(&s);
  check(back.header == 0xFE, "mode4 roundtrip: header");
  check(back.check_counter == 123, "mode4 roundtrip: check_counter");
  check(back.control_mode == POSITION_TARGET_WITH_TERMINAL_VELOCITY_MODE, "mode4 roundtrip: control_mode");
  check(back.is_vision_available && back.enable_chip && !back.stop_emergency, "mode4 roundtrip: flags");
  check(back.latency_time_ms == 100, "mode4 roundtrip: latency_time_ms");
  check(back.elapsed_time_ms_since_last_vision == 33, "mode4 roundtrip: elapsed_time_ms");
  // 量子化幅は range 32.767 で 2*32.767/65534 ≒ 1.0mm。角度は 2*PI/65534 ≒ 0.0001rad。
  checkClose(back.vision_global_pos[0], 1.5f, 1e-3f, "mode4 roundtrip: vision_global_pos[0]");
  checkClose(back.vision_global_pos[1], -2.25f, 1e-3f, "mode4 roundtrip: vision_global_pos[1]");
  checkClose(back.vision_global_theta, 0.75f, 1e-3f, "mode4 roundtrip: vision_global_theta");
  checkClose(back.target_global_theta, -1.25f, 1e-3f, "mode4 roundtrip: target_global_theta");
  checkClose(back.kick_power, 0.5f, 1e-6f, "mode4 roundtrip: kick_power");
  checkClose(back.dribble_power, 0.25f, 1e-6f, "mode4 roundtrip: dribble_power");
  checkClose(back.acceleration_limit, 4.0f, 1e-3f, "mode4 roundtrip: acceleration_limit");
  checkClose(back.linear_velocity_limit, 3.0f, 1e-3f, "mode4 roundtrip: linear_velocity_limit");
  checkClose(back.angular_velocity_limit, 5.0f, 1e-3f, "mode4 roundtrip: angular_velocity_limit");
  checkClose(back.target_global_pos[0], 2.0f, 1e-3f, "mode4 roundtrip: target_global_pos[0]");
  checkClose(back.target_global_pos[1], -1.0f, 1e-3f, "mode4 roundtrip: target_global_pos[1]");
  checkClose(back.terminal_velocity, 0.5f, 1e-3f, "mode4 roundtrip: terminal_velocity");
  checkClose(back.mode_args.position_target.terminal_velocity_x, 0.4f, 1e-3f, "mode4 roundtrip: terminal_velocity_x");
  checkClose(back.mode_args.position_target.terminal_velocity_y, -0.3f, 1e-3f, "mode4 roundtrip: terminal_velocity_y");
}

static void testGoldenMode3(void)
{
  RobotCommandV2 c = makeGoldenCommand();
  c.control_mode = POLAR_VELOCITY_TARGET_MODE;
  c.mode_args.polar_velocity.target_global_velocity_r = 1.75f;
  c.mode_args.polar_velocity.target_global_velocity_theta = -2.5f;

  RobotCommandSerializedV2 s;
  memset(&s, 0, sizeof(s));
  RobotCommandSerializedV2_serialize(&s, &c);

  // mode 3 と mode 4 の差は CONTROL_MODE と CONTROL_MODE_ARGS(24..27) だけであること。
  uint8_t expected[38];
  memcpy(expected, kGoldenMode4, sizeof(expected));
  expected[CONTROL_MODE] = kGoldenMode3ControlMode;
  memcpy(&expected[CONTROL_MODE_ARGS], kGoldenMode3Args, sizeof(kGoldenMode3Args));
  checkBytes(s.data, expected, 38, "golden vector: mode 3 byte 0..37");

  RobotCommandV2 back = RobotCommandSerializedV2_deserialize(&s);
  check(back.control_mode == POLAR_VELOCITY_TARGET_MODE, "mode3 roundtrip: control_mode");
  checkClose(back.mode_args.polar_velocity.target_global_velocity_r, 1.75f, 1e-3f, "mode3 roundtrip: velocity_r");
  checkClose(back.mode_args.polar_velocity.target_global_velocity_theta, -2.5f, 1e-3f, "mode3 roundtrip: velocity_theta");
}

// ---------------------------------------------------------------------------
// 5. union の取り違え検出
// mode 4 のパケットを mode 3 として復号すると terminal_velocity_x/y が r/theta として
// 読まれて無言で暴走する。deserialize が CONTROL_MODE を見ていることを固定する。
// ---------------------------------------------------------------------------
static void testModeArgsUnion(void)
{
  RobotCommandV2 c = makeGoldenCommand();
  c.control_mode = POSITION_TARGET_WITH_TERMINAL_VELOCITY_MODE;
  c.mode_args.position_target.terminal_velocity_x = 0.4f;
  c.mode_args.position_target.terminal_velocity_y = -0.3f;
  RobotCommandSerializedV2 s;
  memset(&s, 0, sizeof(s));
  RobotCommandSerializedV2_serialize(&s, &c);

  // CONTROL_MODE を 3 に書き換えると、同じバイトが r/theta として読まれる。
  // これは「mode を見ずに復号すると何が起きるか」の実証であり、
  // deserialize が mode 依存であることの裏返しの確認。
  s.data[CONTROL_MODE] = POLAR_VELOCITY_TARGET_MODE;
  RobotCommandV2 wrong = RobotCommandSerializedV2_deserialize(&s);
  checkClose(wrong.mode_args.polar_velocity.target_global_velocity_r, 0.4f, 1e-3f, "union: mode4 args read as mode3 r (誤復号の実証)");

  // 未知の mode では mode_args をゼロにして返すこと (crane 版との意図的差分 2)
  s.data[CONTROL_MODE] = 99;
  RobotCommandV2 unknown = RobotCommandSerializedV2_deserialize(&s);
  check(unknown.mode_args.polar_velocity.target_global_velocity_r == 0.0f, "union: unknown mode -> args zeroed (r)");
  check(unknown.mode_args.polar_velocity.target_global_velocity_theta == 0.0f, "union: unknown mode -> args zeroed (theta)");
}

// ---------------------------------------------------------------------------
// 6. クランプ挙動
// crane は範囲外で std::cout 警告を出すが CM4 はカウンタで数えるだけ。
// クランプそのものが残っていることを固定する。
// ---------------------------------------------------------------------------
static void testClamp(void)
{
  const uint32_t before = *robotPacketClampCount();

  TwoByte over = convertFloatToTwoByte(40.0f, 32.767f);
  check(over.high == 0xFF && over.low == 0xFE, "clamp: +40 -> 0xFFFE (range と同値、0xFFFF ではない)");

  TwoByte under = convertFloatToTwoByte(-40.0f, 32.767f);
  check(under.high == 0x00 && under.low == 0x00, "clamp: -40 -> 0x0000");

  TwoByte zero = convertFloatToTwoByte(0.0f, 32.767f);
  check(zero.high == 0x7F && zero.low == 0xFF, "encode: 0.0 -> 0x7FFF");

  check(*robotPacketClampCount() == before + 2, "clamp: カウンタが 2 増える");

  // 切り捨て (丸めない) こと: roundf() を入れると値が 1 ずれるケース
  checkClose(convertTwoByteToFloat(0xFF, 0xFE, 32.767f), 32.767f, 1e-3f, "decode: 0xFFFE -> +range");
  checkClose(convertTwoByteToFloat(0x00, 0x00, 32.767f), -32.767f, 1e-3f, "decode: 0x0000 -> -range");
}

// ---------------------------------------------------------------------------
// 7. uint16 生値フィールドが float 経路を通っていないこと
// ---------------------------------------------------------------------------
static void testUInt16Fields(void)
{
  RobotCommandV2 c = makeGoldenCommand();
  c.control_mode = POLAR_VELOCITY_TARGET_MODE;
  c.latency_time_ms = 0xABCD;
  c.elapsed_time_ms_since_last_vision = 0x1234;
  RobotCommandSerializedV2 s;
  memset(&s, 0, sizeof(s));
  RobotCommandSerializedV2_serialize(&s, &c);
  check(s.data[LATENCY_TIME_MS_HIGH] == 0xAB && s.data[LATENCY_TIME_MS_LOW] == 0xCD, "uint16: latency_time_ms は生値 big-endian");
  check(s.data[ELAPSED_TIME_MS_SINCE_LAST_VISION_HIGH] == 0x12 && s.data[ELAPSED_TIME_MS_SINCE_LAST_VISION_LOW] == 0x34, "uint16: elapsed_time_ms は生値 big-endian");
  RobotCommandV2 back = RobotCommandSerializedV2_deserialize(&s);
  check(back.latency_time_ms == 0xABCD, "uint16: latency_time_ms roundtrip");
  check(back.elapsed_time_ms_since_last_vision == 0x1234, "uint16: elapsed_time_ms roundtrip");
}

int main(void)
{
  printf("robot_packet.h layout test (SSOT: crane/crane_sender/include/crane_sender/robot_packet.h)\n\n");
  testGoldenMode4();
  testGoldenMode3();
  testModeArgsUnion();
  testClamp();
  testUInt16Fields();
  printf("\n%s (%d failure%s)\n", g_failures == 0 ? "PASS" : "FAIL", g_failures, g_failures == 1 ? "" : "s");
  return g_failures == 0 ? 0 : 1;
}
