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

  // --- 制御則（crane calculateSimGlobalVelocity と同一） ---
  const float error_x = input.target_global_pos[0] - input.current_pos[0];
  const float error_y = input.target_global_pos[1] - input.current_pos[1];
  const float error_norm = std::hypot(error_x, error_y);

  float ff_x = input.terminal_velocity_xy[0];
  float ff_y = input.terminal_velocity_xy[1];
  const float terminal_limit = std::max(0.f, input.terminal_velocity);
  if (terminal_limit > 0.f) {
    clampNorm(ff_x, ff_y, terminal_limit);
  }
  const float ff_norm = std::hypot(ff_x, ff_y);

  if (error_norm <= config.position_tolerance && ff_norm < 1e-4f) {
    return stopped(PositionControllerReason::AtTarget, false);
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
  }
  return "Unknown";
}

}  // namespace orion
