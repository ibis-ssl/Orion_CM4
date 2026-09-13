// このファイルは 64 バイトのロボット指令バッファに対する共有操作を定義する責務を持つ。
//
// 【なぜブリッジ層に置くか】
// robot_packet.h は crane / G474_Orion_main / framework の 3 者と「4 者一致」を
// 保つことが不変条件で、ヘッダ自身が crane 版との意図的な差分を 2 点だけと
// 宣言している。CM4 専用のヘルパをそちらへ足すとその差分リストが育ち、
// robot_packet_layout_test.cpp が守っているドリフト検査の前提が緩む。
// また position_controller.h は transport 非依存を HARD CONSTRAINT にしており、
// パケットのバイト列を知ってはならない。両者の「あいだ」がここである。
//
// 【なぜ共有するか】
// 安全停止の副作用 (STOP_EMERGENCY を立てる / kick・dribble・chip を落とす) は
// 実機経路・sim の位置制御経路・sim の素通し経路の 3 箇所で必要になる。
// 実際、素通し経路だけ ENABLE_CHIP のクリアが漏れていた。制御則は共有ソースに
// したのに「制御則の結論をパケットへ書き戻す部分」が共有されていなかったのが
// 原因なので、そこを閉じる。
//
// mode 3 / mode 4 の送信フローを統合するものではない。あれは A/B 比較のために
// 意図的に分けてある (doc/control_packet.md の「2 つの経路」)。ここで共有するのは
// フローではなくバイト操作だけである。

#ifndef ORION_CM4__BRIDGE__ROBOT_COMMAND_OPS_H_
#define ORION_CM4__BRIDGE__ROBOT_COMMAND_OPS_H_

#include <stdint.h>

#include "position_controller.h"
#include "robot_packet.h"

// 2 バイト固定小数をオフセット指定で書く。
inline void writeTwoByte(uint8_t * cmd, int offset, float value, float range)
{
  forward(&cmd[offset], &cmd[offset + 1], value, range);
}

// mode 3 (極座標速度) を書き込む。CONTROL_MODE_ARGS は union なので、
// CONTROL_MODE を 3 にしたうえで対応する args を書くこと。
inline void applyPolarVelocity(uint8_t * cmd, float r, float theta)
{
  cmd[CONTROL_MODE] = (uint8_t)POLAR_VELOCITY_TARGET_MODE;
  writeTwoByte(cmd, CONTROL_MODE_ARGS + 0, r, 32.767f);
  writeTwoByte(cmd, CONTROL_MODE_ARGS + 2, theta, 32.767f);
}

// 安全停止の副作用。速度をゼロにするのは呼び出し側 (経路ごとに書き方が違う)。
//
// 古いキック/ドリブル指令を撃ち続けないために kick・dribble・chip も落とす。
// crane 断で止めたのにコンデンサが溜まっていてキックだけ飛ぶ、という事故を防ぐ。
inline void applySafetyStop(uint8_t * cmd)
{
  cmd[FLAGS] = (uint8_t)(cmd[FLAGS] | (uint8_t)(1u << STOP_EMERGENCY));
  cmd[KICK_POWER] = 0;
  cmd[DRIBBLE_POWER] = 0;
  cmd[FLAGS] = (uint8_t)(cmd[FLAGS] & (uint8_t)~(1u << ENABLE_CHIP));
}

// CM4 が採番する check_counter の次の値。
//
// G474 の checkConnect2AI() も simulator-cli も「前回と同じ値」を無視するので、
// 送信ごとに必ず変えなければならない。crane と同じ 0..200 の巡回にしてある。
inline uint8_t nextCheckCounter(uint8_t current) { return (current >= 200) ? 0 : (uint8_t)(current + 1); }

// 空スロット (コマンド 64 バイトが全ゼロ) か。
//
// crane は担当しないスロットをゼロ埋めする。framework の ibisSlotIsEmpty() と
// 同じ判定で、実機経路と cm4_sim が同じ扱いをし続ける必要がある。
inline bool commandSlotIsEmpty(const uint8_t * cmd)
{
  for (int i = 0; i < (int)sizeof(RobotCommandSerializedV2); i++) {
    if (cmd[i] != 0) return false;
  }
  return true;
}

// デコード済みの mode 4 指令から、位置制御器の入力のうち
// 「パケット由来のフィールドだけ」を埋める。
//
// 時刻と feedback は transport ごとに持ち方が違うので呼び出し側が埋める。
// パケット → 制御器入力の対応をここに 1 本化するのが重要で、実機と sim で
// 別々に書くと、crane が mode 4 にフィールドを足したときに片方だけ直されて
// 挙動が静かに分岐する。この変更が防ごうとしている失敗様式そのものである。
inline void fillCommandFields(orion::PositionControllerInput * in, const RobotCommandV2 & cmd)
{
  in->target_global_pos[0] = cmd.target_global_pos[0];
  in->target_global_pos[1] = cmd.target_global_pos[1];
  in->terminal_velocity_xy[0] = cmd.mode_args.position_target.terminal_velocity_x;
  in->terminal_velocity_xy[1] = cmd.mode_args.position_target.terminal_velocity_y;
  in->terminal_velocity = cmd.terminal_velocity;
  in->linear_velocity_limit = cmd.linear_velocity_limit;
  in->stop_emergency = cmd.stop_emergency;
  in->vision_available = cmd.is_vision_available;
  in->elapsed_time_ms_since_last_vision = cmd.elapsed_time_ms_since_last_vision;
}

#endif  // ORION_CM4__BRIDGE__ROBOT_COMMAND_OPS_H_
