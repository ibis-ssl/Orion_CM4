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

// 安全停止した周期は状態をリセットする。
PositionControllerOutput stopped(PositionControllerReason reason, bool stop_emergency, PositionControllerState * state)
{
  resetPositionControllerState(state);
  PositionControllerOutput out;
  out.polar_velocity_r = 0.f;
  out.polar_velocity_theta = 0.f;
  out.stop_emergency = stop_emergency;
  out.reason = reason;
  return out;
}

constexpr float kGainEpsilon = 1e-6f;

}  // namespace

void resetPositionControllerState(PositionControllerState * state)
{
  if (state == nullptr) return;
  *state = PositionControllerState();
}

bool isCommandStale(bool has_command, uint64_t command_time_ms, uint64_t now_ms, const PositionControllerConfig & config)
{
  return !has_command || elapsedMs(now_ms, command_time_ms) > config.command_timeout_ms;
}

PositionControllerOutput computePositionControl(
  const PositionControllerInput & input, const PositionControllerConfig & config, PositionControllerState * state)
{
  // --- 安全停止の判定 ---
  if (input.stop_emergency) {
    return stopped(PositionControllerReason::StopEmergency, true, state);
  }
  if (isCommandStale(input.has_command, input.command_time_ms, input.now_ms, config)) {
    return stopped(PositionControllerReason::CommandStale, true, state);
  }
  if (!input.has_feedback || elapsedMs(input.now_ms, input.feedback_time_ms) > config.feedback_timeout_ms) {
    return stopped(PositionControllerReason::FeedbackStale, true, state);
  }
  if (!input.vision_available) {
    return stopped(PositionControllerReason::VisionUnavailable, true, state);
  }
  if (input.elapsed_time_ms_since_last_vision > config.vision_age_limit_ms) {
    return stopped(PositionControllerReason::VisionStale, true, state);
  }

  // --- 未設定フィールドの防御 ---
  if (implausible(input.target_global_pos[0]) || implausible(input.target_global_pos[1]) || implausible(input.current_pos[0]) ||
      implausible(input.current_pos[1])) {
    return stopped(PositionControllerReason::InvalidCommand, true, state);
  }

  // --- 制御則 (PID) ---
  const float error_x = input.target_global_pos[0] - input.current_pos[0];
  const float error_y = input.target_global_pos[1] - input.current_pos[1];
  const float error_norm = std::hypot(error_x, error_y);

  // 実測速度の更新 (feedback 更新周期のみ。目標変化によるキックを防ぐため微分先行形)
  const float dt = state->has_last_feedback ? (elapsedMs(input.feedback_time_ms, state->last_feedback_time_ms) / 1000.f) : 0.f;
  if (!state->has_last_feedback) {
    state->last_pos[0] = input.current_pos[0];
    state->last_pos[1] = input.current_pos[1];
    state->last_feedback_time_ms = input.feedback_time_ms;
    state->has_last_feedback = true;
  } else if (dt > 0.f) {
    const float raw_vx = (input.current_pos[0] - state->last_pos[0]) / dt;
    const float raw_vy = (input.current_pos[1] - state->last_pos[1]) / dt;
    const float alpha = dt / (kDerivativeFilterTimeConstantS + dt);
    state->measured_velocity[0] += alpha * (raw_vx - state->measured_velocity[0]);
    state->measured_velocity[1] += alpha * (raw_vy - state->measured_velocity[1]);
    state->last_pos[0] = input.current_pos[0];
    state->last_pos[1] = input.current_pos[1];
    state->last_feedback_time_ms = input.feedback_time_ms;
  }

  float ff_x = input.terminal_velocity_xy[0];
  float ff_y = input.terminal_velocity_xy[1];
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
    // 許容誤差内: 速度はゼロ。定常偏差解消のため積分は保持し、微分の基準のみ取り直す。
    state->measured_velocity[0] = 0.f;
    state->measured_velocity[1] = 0.f;
    state->has_last_feedback = false;
    PositionControllerOutput at_target;
    at_target.polar_velocity_r = 0.f;
    at_target.polar_velocity_theta = 0.f;
    at_target.stop_emergency = false;
    at_target.reason = PositionControllerReason::AtTarget;
    at_target.feedforward_rejected = feedforward_rejected;
    return at_target;
  }

  // PID + FF 出力計算
  const float i_term_x = config.integral_gain * state->integral[0];
  const float i_term_y = config.integral_gain * state->integral[1];
  const float d_term_x = -config.derivative_gain * state->measured_velocity[0];
  const float d_term_y = -config.derivative_gain * state->measured_velocity[1];

  float vx = config.position_gain * error_x + i_term_x + d_term_x + ff_x;
  float vy = config.position_gain * error_y + i_term_y + d_term_y + ff_y;

  const float max_velocity = std::max(0.f, input.linear_velocity_limit);
  const float braking_limit = std::sqrt(ff_norm * ff_norm + 2.f * std::max(0.f, config.deceleration) * error_norm);
  const float speed_limit = std::min(max_velocity, braking_limit);
  const bool saturated = std::hypot(vx, vy) > speed_limit;
  clampNorm(vx, vy, speed_limit);

  // 積分更新 (飽和時はワインドアップ防止のため積分停止)
  if (config.integral_gain <= kGainEpsilon) {
    state->integral[0] = 0.f;
    state->integral[1] = 0.f;
  } else if (!saturated && dt > 0.f) {
    state->integral[0] += error_x * dt;
    state->integral[1] += error_y * dt;
    const float max_integral = config.integral_velocity_limit / config.integral_gain;
    clampNorm(state->integral[0], state->integral[1], max_integral);
  }

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
