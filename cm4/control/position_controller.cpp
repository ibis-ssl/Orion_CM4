// このファイルは位置制御則の実装を担当する。責務と制約は
// cm4/control/position_controller.h の冒頭コメントを参照。
//
// transport 非依存を保つため、ここで include してよいのは標準の数値ヘッダだけ。

#include "position_controller.h"

#include <algorithm>
#include <cmath>

namespace orion
{
namespace
{

// crane の crane_geometry/geometry_operations.hpp clampNorm と同一セマンティクス。
//
// max_norm <= 0 で零ベクトルを返すのが重要。linear_velocity_limit = 0 で停止する
// 挙動はここに依存している。framework の ibis_protocol.h は同じフィールドを
// 「0 = 無制限」と定義しているが、上流であるこちらの解釈が先に勝つ。
// 詳細は doc/control_packet.md の「LINEAR_VELOCITY_LIMIT = 0 の扱い」を参照。
void clampNorm(float & x, float & y, float max_norm)
{
  if (max_norm <= 0.f) {
    x = 0.f;
    y = 0.f;
    return;
  }
  const float norm = std::hypot(x, y);
  if (norm > max_norm && norm > 1e-9f) {
    const float scale = max_norm / norm;
    x *= scale;
    y *= scale;
  }
}

// 単調時刻の差分。now < since は起動直後や時刻の巻き戻りで起こりうる。
//
// 符号なし減算をそのまま書くとアンダーフローで巨大値になる。停止側に倒れるので
// 安全ではあるが、G474 の USART2 パーサが 2026-08 に同じ形の
// unsigned underflow で不安定化した前例がある (doc/overview.md) ため明示的に潰す。
uint64_t elapsedMs(uint64_t now, uint64_t since)
{
  return (now >= since) ? (now - since) : 0;
}

// 物理的にありえない大きさ（未設定フィールドの復号結果）か。
// 否定形で書いてあるのは NaN も弾くため。
bool implausible(float v) { return !(std::fabs(v) < kImplausibleMagnitude); }

PositionControllerOutput stopped(PositionControllerReason reason, bool stop_emergency)
{
  PositionControllerOutput out;
  out.polar_velocity_r = 0.f;
  out.polar_velocity_theta = 0.f;
  out.stop_emergency = stop_emergency;
  out.reason = reason;
  return out;
}

}  // namespace

PositionControllerOutput computePositionControl(const PositionControllerInput & input, const PositionControllerConfig & config)
{
  // --- 安全停止の判定（実機バイナリと cm4_sim が必ず同じ判定を通る） ---
  if (input.stop_emergency) {
    return stopped(PositionControllerReason::StopEmergency, true);
  }
  if (!input.has_command || elapsedMs(input.now_ms, input.command_time_ms) > config.command_timeout_ms) {
    // crane 無通信。G474 の connected_ai は CM4 が check_counter を採番する以上
    // crane の生存を意味しないので、ここで止めるしかない。
    return stopped(PositionControllerReason::CommandStale, true);
  }
  if (!input.has_feedback || elapsedMs(input.now_ms, input.feedback_time_ms) > config.feedback_timeout_ms) {
    // feedback が位置制御ループ内で唯一の位置信号。起動直後の未受信もここに入る。
    return stopped(PositionControllerReason::FeedbackStale, true);
  }
  if (!input.vision_available) {
    // crane が見失っている間の target_global_pos は推測値。実機 G474 も同条件で
    // 止める (state_func.c:314) ので、CM4 が先に止めても実機の挙動は変わらない。
    // 変わるのは sim 側で、これで実機と揃う。
    return stopped(PositionControllerReason::VisionUnavailable, true);
  }
  if (input.elapsed_time_ms_since_last_vision > config.vision_age_limit_ms) {
    // vision が古すぎる target_global_pos も同じく推測値。実機 G474 は
    // state_func.c:314 の同じ式 (`> 500`) で止める。
    //
    // 無線劣化を注入すると真っ先に発火する条件なので、ここを見ないと
    // 「実機なら停まる状況で CM4 だけが走らせ続ける」ことになり、A/B 比較の
    // 数値が意味を失う。
    return stopped(PositionControllerReason::VisionStale, true);
  }

  // --- 未設定フィールドの防御（kImplausibleMagnitude の説明を参照） ---
  //
  // 位置は安全な代替値が無いので止める。目標が分からないまま動いてはならない。
  if (implausible(input.target_global_pos[0]) || implausible(input.target_global_pos[1]) || implausible(input.current_pos[0]) ||
      implausible(input.current_pos[1])) {
    return stopped(PositionControllerReason::InvalidCommand, true);
  }

  // --- 制御則（crane calculateSimGlobalVelocity と同一） ---
  const float error_x = input.target_global_pos[0] - input.current_pos[0];
  const float error_y = input.target_global_pos[1] - input.current_pos[1];
  const float error_norm = std::hypot(error_x, error_y);

  float ff_x = input.terminal_velocity_xy[0];
  float ff_y = input.terminal_velocity_xy[1];
  // 終端速度には安全な代替値がある。0 とみなせば素の P 制御で目標へ向かうので、
  // 止めるより走らせたほうが正しい。呼び出し側がログに出せるよう印は残す。
  bool feedforward_rejected = false;
  if (implausible(ff_x) || implausible(ff_y)) {
    ff_x = 0.f;
    ff_y = 0.f;
    feedforward_rejected = true;
  }
  const float terminal_limit = std::max(0.f, input.terminal_velocity);
  if (terminal_limit > 0.f) {
    clampNorm(ff_x, ff_y, terminal_limit);
  }
  const float ff_norm = std::hypot(ff_x, ff_y);

  if (error_norm <= config.position_tolerance && ff_norm < 1e-4f) {
    PositionControllerOutput at_target = stopped(PositionControllerReason::AtTarget, false);
    at_target.feedforward_rejected = feedforward_rejected;
    return at_target;
  }

  float vx = config.position_gain * error_x + ff_x;
  float vy = config.position_gain * error_y + ff_y;

  const float max_velocity = std::max(0.f, input.linear_velocity_limit);
  // 台形減速エンベロープ。残距離 error_norm を deceleration で詰めたときに
  // 終端速度 ff_norm へ収まる速度の上限。これがオーバーシュートを防ぐ。
  const float braking_limit = std::sqrt(ff_norm * ff_norm + 2.f * std::max(0.f, config.deceleration) * error_norm);
  clampNorm(vx, vy, std::min(max_velocity, braking_limit));

  PositionControllerOutput out;
  out.polar_velocity_r = std::hypot(vx, vy);
  out.polar_velocity_theta = std::atan2(vy, vx);
  out.stop_emergency = false;
  out.reason = PositionControllerReason::Ok;
  out.feedforward_rejected = feedforward_rejected;
  return out;
}

const char * toString(PositionControllerReason reason)
{
  switch (reason) {
    case PositionControllerReason::Ok:
      return "Ok";
    case PositionControllerReason::AtTarget:
      return "AtTarget";
    case PositionControllerReason::StopEmergency:
      return "StopEmergency";
    case PositionControllerReason::CommandStale:
      return "CommandStale";
    case PositionControllerReason::FeedbackStale:
      return "FeedbackStale";
    case PositionControllerReason::VisionUnavailable:
      return "VisionUnavailable";
    case PositionControllerReason::VisionStale:
      return "VisionStale";
    case PositionControllerReason::InvalidCommand:
      return "InvalidCommand";
  }
  return "Unknown";
}

}  // namespace orion
