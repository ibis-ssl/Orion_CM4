// このファイルは position_controller の単体テストを担当する。
//
// 前半 5 ケースは crane の
//   crane/crane_sender/test/test_sim_position_controller.cpp
// から数値ごと移植したもの。「同じ入力で同じ出力になる」ことが、実機 CM4 と
// crane の位置制御が同じ挙動をすることの根拠になる。
// (crane 版の field_coordinate_theta_offset 関連ケースは除外した。crane の
//  createRobotPacket は回転を一切適用しないので実機経路は offset = 0 運用であり、
//  CM4 はパケットの座標系でそのまま閉じる。)
//
// 後半は本構成に固有の要件 (収束・オーバーシュート・速度制限・安全停止)。
//
// 失敗時は非ゼロ終了する。cm4/build.sh と CI から実行される。

#include <math.h>
#include <stdio.h>

#include "position_controller.h"

using orion::PositionControllerConfig;
using orion::PositionControllerInput;
using orion::PositionControllerOutput;
using orion::PositionControllerReason;
using orion::PositionControllerState;

static int g_failures = 0;

static void check(bool ok, const char * name)
{
  printf(ok ? "[ok]   %s\n" : "[FAIL] %s\n", name);
  if (!ok) g_failures++;
}

static void checkClose(float actual, float expected, float tol, const char * name)
{
  const bool ok = fabsf(actual - expected) <= tol;
  if (ok) {
    printf("[ok]   %s (%.6f)\n", name, actual);
  } else {
    printf("[FAIL] %s: actual %.6f expected %.6f (tol %.6f)\n", name, actual, expected, tol);
    g_failures++;
  }
}

static void checkReason(PositionControllerReason actual, PositionControllerReason expected, const char * name)
{
  const bool ok = actual == expected;
  if (ok) {
    printf("[ok]   %s (%s)\n", name, orion::toString(actual));
  } else {
    printf("[FAIL] %s: actual %s expected %s\n", name, orion::toString(actual), orion::toString(expected));
    g_failures++;
  }
}

// 出力は極座標 (r, theta) なので直交成分に戻して比較する。
static float vx(const PositionControllerOutput & o) { return o.polar_velocity_r * cosf(o.polar_velocity_theta); }
static float vy(const PositionControllerOutput & o) { return o.polar_velocity_r * sinf(o.polar_velocity_theta); }

// 既定ゲイン (ki = kd = 0) では PID が P 制御へ縮退し、状態は出力に一切影響しない。
// そのため P 制御の数値ケースは毎回まっさらな状態を渡して 1 周期だけを見る。
// 状態を持ち回る必要があるのは PID のケースだけで、そちらは明示的に State を宣言する。
static PositionControllerOutput computeOnce(const PositionControllerInput & in, const PositionControllerConfig & cfg)
{
  PositionControllerState state;
  return computePositionControl(in, cfg, &state);
}

// 指令も feedback も新鮮な、制御則だけを見るための入力。
static PositionControllerInput freshInput(void)
{
  PositionControllerInput in;
  in.has_command = true;
  in.command_time_ms = 1000;
  in.has_feedback = true;
  in.feedback_time_ms = 1000;
  in.vision_available = true;
  in.now_ms = 1000;
  in.current_pos[0] = 0.f;
  in.current_pos[1] = 0.f;
  return in;
}

// ---------------------------------------------------------------------------
// crane 版テストからの移植
// ---------------------------------------------------------------------------

static void testAppliesPositionGain(void)
{
  PositionControllerConfig cfg;
  cfg.position_gain = 2.0f;
  cfg.deceleration = 100.0f;
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = 0.1f;
  in.linear_velocity_limit = 5.0f;

  const PositionControllerOutput out = computeOnce(in, cfg);
  checkClose(vx(out), 0.2f, 1e-4f, "AppliesPositionGain: vx");
  checkClose(vy(out), 0.0f, 1e-4f, "AppliesPositionGain: vy");
  checkReason(out.reason, PositionControllerReason::Ok, "AppliesPositionGain: reason");
}

static void testAddsTerminalVelocityFeedforward(void)
{
  PositionControllerConfig cfg;
  cfg.position_gain = 2.0f;
  cfg.deceleration = 100.0f;
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = 0.1f;
  in.linear_velocity_limit = 5.0f;
  in.terminal_velocity_xy[0] = 0.4f;
  in.terminal_velocity = 0.4f;  // speed_limit_at_target

  const PositionControllerOutput out = computeOnce(in, cfg);
  checkClose(vx(out), 0.6f, 1e-4f, "AddsTerminalVelocityFeedforward: vx = kp*e + ff");
}

static void testClampsToMaximumVelocity(void)
{
  PositionControllerConfig cfg;
  cfg.position_gain = 2.0f;
  cfg.deceleration = 100.0f;
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = 10.0f;
  in.linear_velocity_limit = 1.5f;

  const PositionControllerOutput out = computeOnce(in, cfg);
  checkClose(out.polar_velocity_r, 1.5f, 1e-4f, "ClampsToMaximumVelocity: |v|");
}

static void testClampsToBrakingEnvelope(void)
{
  PositionControllerConfig cfg;
  cfg.position_gain = 10.0f;
  cfg.deceleration = 0.5f;
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = 1.0f;
  in.linear_velocity_limit = 10.0f;

  // sqrt(2 * 0.5 * 1.0) = 1.0
  const PositionControllerOutput out = computeOnce(in, cfg);
  checkClose(out.polar_velocity_r, 1.0f, 1e-4f, "ClampsToBrakingEnvelope: |v| = sqrt(2*decel*|e|)");
}

static void testStopsInsideToleranceWithoutFeedforward(void)
{
  PositionControllerConfig cfg;
  cfg.position_tolerance = 0.01f;
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = 0.005f;
  in.linear_velocity_limit = 5.0f;

  const PositionControllerOutput out = computeOnce(in, cfg);
  checkClose(out.polar_velocity_r, 0.0f, 1e-6f, "StopsInsideTolerance: |v|");
  checkReason(out.reason, PositionControllerReason::AtTarget, "StopsInsideTolerance: reason");
}

// ---------------------------------------------------------------------------
// 本構成に固有の要件
// ---------------------------------------------------------------------------

// 目標へ 1 kHz で反復適用すると速度がゼロへ収束し、オーバーシュートしないこと。
// 速度指令を単純積分するモデルで回す (G474 の加速度制限は入れない。
// 制御則そのものが減速エンベロープでオーバーシュートを防いでいることの検査)。
static void testConvergesWithoutOvershoot(void)
{
  PositionControllerConfig cfg;  // 既定値: kp=2.0, decel=3.0, tol=0.01
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = 2.0f;
  in.target_global_pos[1] = 1.0f;
  in.linear_velocity_limit = 3.0f;

  const float dt = 0.001f;  // 1 kHz
  float px = 0.f, py = 0.f;
  float max_r = 0.f;
  bool overshot = false;
  float last_error = hypotf(in.target_global_pos[0] - px, in.target_global_pos[1] - py);
  bool error_increased = false;

  for (int step = 0; step < 20000; ++step) {  // 20 秒ぶん
    in.current_pos[0] = px;
    in.current_pos[1] = py;
    in.now_ms = 1000 + static_cast<uint64_t>(step);
    in.command_time_ms = in.now_ms;
    in.feedback_time_ms = in.now_ms;

    const PositionControllerOutput out = computeOnce(in, cfg);
    if (out.polar_velocity_r > max_r) max_r = out.polar_velocity_r;

    const float dx_before = in.target_global_pos[0] - px;
    const float dy_before = in.target_global_pos[1] - py;
    px += vx(out) * dt;
    py += vy(out) * dt;
    const float dx_after = in.target_global_pos[0] - px;
    const float dy_after = in.target_global_pos[1] - py;

    // 目標を跨いだら符号が反転する = オーバーシュート
    if (dx_before * dx_after < 0.f || dy_before * dy_after < 0.f) overshot = true;

    const float error = hypotf(dx_after, dy_after);
    if (error > last_error + 1e-6f) error_increased = true;
    last_error = error;
  }

  const float final_error = hypotf(in.target_global_pos[0] - px, in.target_global_pos[1] - py);
  checkClose(final_error, 0.0f, 0.011f, "Converges: 20 秒後の残差が許容誤差内");
  check(!overshot, "Converges: 目標を跨がない (オーバーシュートしない)");
  check(!error_increased, "Converges: 誤差が単調に減少する");
  check(max_r <= 3.0f + 1e-4f, "Converges: 全ステップで linear_velocity_limit を超えない");

  // 収束後は AtTarget で完全停止していること
  in.current_pos[0] = px;
  in.current_pos[1] = py;
  const PositionControllerOutput settled = computeOnce(in, cfg);
  checkClose(settled.polar_velocity_r, 0.0f, 1e-6f, "Converges: 収束後の速度がゼロ");
}

static void testRespectsVelocityLimitEveryStep(void)
{
  PositionControllerConfig cfg;
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = 5.0f;
  in.linear_velocity_limit = 1.25f;

  bool exceeded = false;
  for (int i = 0; i <= 500; ++i) {
    in.current_pos[0] = 5.0f * static_cast<float>(i) / 500.0f;
    const PositionControllerOutput out = computeOnce(in, cfg);
    if (out.polar_velocity_r > 1.25f + 1e-5f) exceeded = true;
  }
  check(!exceeded, "VelocityLimit: 経路上のどの位置でも上限を超えない");
}

// フィードフォワードがどれだけ大きくても出力は linear_velocity_limit を超えないこと。
//
// 未設定シグネチャ (|v| >= 32) は InvalidCommand / feedforward_rejected で弾くが、
// 「もっともらしいが間違っている」終端速度はどんな検査でも見分けられない。
// その場合でも出力の大きさは linear_velocity_limit で頭打ちになる、というのが
// この制御則の最後の砦なので、明示的に固定しておく。
static void testFeedforwardCannotExceedVelocityLimit(void)
{
  PositionControllerConfig cfg;
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = 0.05f;  // ほぼ到達済み。速度は ff が支配する
  in.linear_velocity_limit = 1.25f;
  // 終端速度スカラを 0 (= クランプしない) にしたうえで、もっともらしい範囲で
  // 目標と無関係な向きの巨大な ff を入れる。
  in.terminal_velocity = 0.0f;
  in.terminal_velocity_xy[0] = -20.0f;
  in.terminal_velocity_xy[1] = -20.0f;

  const PositionControllerOutput out = computeOnce(in, cfg);
  check(!out.feedforward_rejected, "FeedforwardLimit: もっともらしい値は未設定扱いしない");
  check(out.polar_velocity_r <= 1.25f + 1e-5f, "FeedforwardLimit: ff が巨大でも上限を超えない");
}

// linear_velocity_limit = 0 の解釈を固定する。
// crane の clampNorm は max_norm <= 0 で零ベクトルを返す = 「停止」。
// framework の ibis_protocol.h は同じフィールドを「0 = 無制限」と定義しているが、
// 上流であるこちらの解釈が先に勝つ。詳細は doc/control_packet.md を参照。
static void testZeroVelocityLimitMeansStop(void)
{
  PositionControllerConfig cfg;
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = 5.0f;
  in.linear_velocity_limit = 0.0f;

  const PositionControllerOutput out = computeOnce(in, cfg);
  checkClose(out.polar_velocity_r, 0.0f, 1e-6f, "ZeroVelocityLimit: 0 は「無制限」ではなく「停止」");
}

static void testStopEmergency(void)
{
  PositionControllerConfig cfg;
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = 5.0f;
  in.linear_velocity_limit = 3.0f;
  in.stop_emergency = true;

  const PositionControllerOutput out = computeOnce(in, cfg);
  checkClose(out.polar_velocity_r, 0.0f, 1e-6f, "StopEmergency: |v|");
  check(out.stop_emergency, "StopEmergency: 出力にも stop_emergency が立つ");
  checkReason(out.reason, PositionControllerReason::StopEmergency, "StopEmergency: reason");
}

// crane 無通信に対する安全停止。検収条件 4 の単体側。
static void testCommandTimeout(void)
{
  PositionControllerConfig cfg;
  cfg.command_timeout_ms = 100;
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = 5.0f;
  in.linear_velocity_limit = 3.0f;

  in.command_time_ms = 1000;
  in.feedback_time_ms = 1100;

  in.now_ms = 1100;  // 100ms 経過 = 境界。まだ動く
  checkReason(computeOnce(in, cfg).reason, PositionControllerReason::Ok, "CommandTimeout: 100ms ちょうどはまだ動く");

  in.now_ms = 1101;  // 101ms 経過 = タイムアウト
  const PositionControllerOutput out = computeOnce(in, cfg);
  checkClose(out.polar_velocity_r, 0.0f, 1e-6f, "CommandTimeout: 101ms で速度ゼロ");
  checkReason(out.reason, PositionControllerReason::CommandStale, "CommandTimeout: reason");
  check(out.stop_emergency, "CommandTimeout: STOP_EMERGENCY を立てる");

  // 一度も受けていない場合も停止
  PositionControllerInput never = freshInput();
  never.has_command = false;
  never.target_global_pos[0] = 5.0f;
  never.linear_velocity_limit = 3.0f;
  checkReason(computeOnce(never, cfg).reason, PositionControllerReason::CommandStale, "CommandTimeout: 未受信も CommandStale");
}

// G474 feedback 途絶。起動直後の未受信 (cm4_sim のブートストラップ) もここ。
static void testFeedbackTimeout(void)
{
  PositionControllerConfig cfg;
  cfg.feedback_timeout_ms = 100;
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = 5.0f;
  in.linear_velocity_limit = 3.0f;
  in.command_time_ms = 1200;
  in.feedback_time_ms = 1000;
  in.now_ms = 1200;  // feedback から 200ms

  const PositionControllerOutput out = computeOnce(in, cfg);
  checkClose(out.polar_velocity_r, 0.0f, 1e-6f, "FeedbackTimeout: 速度ゼロ");
  checkReason(out.reason, PositionControllerReason::FeedbackStale, "FeedbackTimeout: reason");

  PositionControllerInput never = freshInput();
  never.has_feedback = false;
  never.target_global_pos[0] = 5.0f;
  never.linear_velocity_limit = 3.0f;
  checkReason(computeOnce(never, cfg).reason, PositionControllerReason::FeedbackStale, "FeedbackTimeout: 未受信 (起動直後) も FeedbackStale");
}

// crane が vision でロボットを捉えていないときは止めること。
//
// 実機 G474 は state_func.c:314 の同じ条件でホイールを止めるが、simulator-cli は
// このビットを復号するだけで何もしない。CM4 で止めることで実機と sim が揃う。
static void testVisionUnavailableStops(void)
{
  PositionControllerConfig cfg;
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = 5.0f;
  in.linear_velocity_limit = 3.0f;
  in.vision_available = false;

  const PositionControllerOutput out = computeOnce(in, cfg);
  checkClose(out.polar_velocity_r, 0.0f, 1e-6f, "VisionUnavailable: 速度ゼロ");
  checkReason(out.reason, PositionControllerReason::VisionUnavailable, "VisionUnavailable: reason");
  check(out.stop_emergency, "VisionUnavailable: STOP_EMERGENCY を立てる");

  // 途絶判定のほうが優先されること（止まる理由として先に来る）。
  PositionControllerInput stale = in;
  stale.now_ms = 2000;  // crane から 1000ms
  checkReason(computeOnce(stale, cfg).reason, PositionControllerReason::CommandStale, "VisionUnavailable: crane 断のほうが優先");
}

// crane の vision が古すぎるときは止めること。
//
// 実機 G474 は state_func.c:314 で `elapsed_time_ms_since_last_vision > 500` を
// 停止条件に入れている。境界 (500 は動く / 501 は止まる) まで実機と揃える。
// 無線劣化を注入すると真っ先に発火する条件なので、A/B 比較の前提として重要。
static void testStaleVisionStops(void)
{
  PositionControllerConfig cfg;
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = 5.0f;
  in.linear_velocity_limit = 3.0f;

  in.elapsed_time_ms_since_last_vision = 501;
  const PositionControllerOutput out = computeOnce(in, cfg);
  checkClose(out.polar_velocity_r, 0.0f, 1e-6f, "VisionStale: 速度ゼロ");
  checkReason(out.reason, PositionControllerReason::VisionStale, "VisionStale: reason");
  check(out.stop_emergency, "VisionStale: STOP_EMERGENCY を立てる");

  // 実機の `> 500` と同じ境界。500 はまだ動く。
  in.elapsed_time_ms_since_last_vision = 500;
  checkReason(computeOnce(in, cfg).reason, PositionControllerReason::Ok, "VisionStale: 境界 500 は動く");

  // 素の uint16 なのでゼロ埋めは正しく「最新」になる
  // (2 バイト固定小数のフィールドと違って -32.767 に化けない)。
  in.elapsed_time_ms_since_last_vision = 0;
  checkReason(computeOnce(in, cfg).reason, PositionControllerReason::Ok, "VisionStale: 0 は最新として扱う");
}

// 時刻が巻き戻っても unsigned underflow で誤判定しないこと。
// G474 の USART2 パーサが 2026-08 に同じ形のバグで不安定化した前例がある。
static void testNoUnsignedUnderflow(void)
{
  PositionControllerConfig cfg;
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = 1.0f;
  in.linear_velocity_limit = 3.0f;
  in.command_time_ms = 2000;
  in.feedback_time_ms = 2000;
  in.now_ms = 1000;  // 時刻が巻き戻っている

  checkReason(computeOnce(in, cfg).reason, PositionControllerReason::Ok, "NoUnderflow: now < since でも停止扱いにしない");
}

// 出力方向がグローバル座標であること。
static void testOutputDirectionIsGlobal(void)
{
  PositionControllerConfig cfg;
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = -1.0f;
  in.target_global_pos[1] = 1.0f;
  in.linear_velocity_limit = 3.0f;

  const PositionControllerOutput out = computeOnce(in, cfg);
  checkClose(out.polar_velocity_theta, 3.0f * static_cast<float>(M_PI) / 4.0f, 1e-4f, "Direction: theta = atan2(dy, dx) = 3pi/4");
}

// 終端速度ベクトルが terminal_velocity (スカラー上限) でクランプされること。
static void testTerminalVelocityClamp(void)
{
  PositionControllerConfig cfg;
  cfg.position_gain = 0.0f;   // フィードフォワードだけを見る
  cfg.deceleration = 100.0f;
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = 1.0f;
  in.linear_velocity_limit = 10.0f;
  in.terminal_velocity_xy[0] = 3.0f;
  in.terminal_velocity_xy[1] = 4.0f;  // |ff| = 5.0
  in.terminal_velocity = 2.0f;        // 上限 2.0 へ縮む

  const PositionControllerOutput out = computeOnce(in, cfg);
  checkClose(out.polar_velocity_r, 2.0f, 1e-4f, "TerminalVelocityClamp: |ff| が terminal_velocity へクランプされる");
}

// --- 未設定フィールドの防御 ---------------------------------------------
//
// 2 バイト固定小数 (range 32.767) の未設定フィールドは 0.0 ではなく -32.767 として
// 復号される。encode が 0.0 を 0x7FFF へ写すためで、memset でゼロ埋めしたフィールドは
// 最大級の負値になる。実チェーンで crane 役が terminal_velocity_x/y を書き忘れた結果、
// feedforward が (-32.767, -32.767) になってロボットが場外まで走った実績がある。

// 未設定の終端速度は 0 とみなして制御を続ける。素の P 制御で目標へ向かうのが正しい。
static void testUnsetFeedforwardIsIgnored(void)
{
  PositionControllerConfig cfg;
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = 2.8f;
  in.target_global_pos[1] = -1.8f;
  in.current_pos[0] = 4.3f;
  in.current_pos[1] = -2.8f;
  in.linear_velocity_limit = 2.0f;
  // crane 役が書き忘れたときに実際に復号される値
  in.terminal_velocity_xy[0] = -32.767f;
  in.terminal_velocity_xy[1] = -32.767f;
  in.terminal_velocity = -32.767f;

  const PositionControllerOutput out = computeOnce(in, cfg);
  checkReason(out.reason, PositionControllerReason::Ok, "UnsetFeedforward: 停止せず制御を続ける");
  check(out.feedforward_rejected, "UnsetFeedforward: 呼び出し側へ印を返す");
  // 素の P 制御なので方向は誤差方向 atan2(1.0, -1.5) = 2.5536 rad と一致する。
  checkClose(out.polar_velocity_theta, atan2f(1.0f, -1.5f), 1e-4f, "UnsetFeedforward: 目標方向を向く");
  checkClose(out.polar_velocity_r, 2.0f, 1e-4f, "UnsetFeedforward: linear_velocity_limit で頭打ち");
}

// 未設定の目標位置は安全な代替値が無いので止める。
static void testUnsetTargetStops(void)
{
  PositionControllerConfig cfg;
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = -32.767f;
  in.target_global_pos[1] = -32.767f;
  in.linear_velocity_limit = 3.0f;

  const PositionControllerOutput out = computeOnce(in, cfg);
  checkReason(out.reason, PositionControllerReason::InvalidCommand, "UnsetTarget: InvalidCommand で止まる");
  checkClose(out.polar_velocity_r, 0.0f, 1e-6f, "UnsetTarget: r = 0");
  check(out.stop_emergency, "UnsetTarget: STOP_EMERGENCY を立てる");
}

// feedback が NaN でも走り出さない。
static void testNanFeedbackStops(void)
{
  PositionControllerConfig cfg;
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = 1.0f;
  in.current_pos[0] = NAN;
  in.linear_velocity_limit = 3.0f;

  const PositionControllerOutput out = computeOnce(in, cfg);
  checkReason(out.reason, PositionControllerReason::InvalidCommand, "NanFeedback: InvalidCommand で止まる");
  checkClose(out.polar_velocity_r, 0.0f, 1e-6f, "NanFeedback: r = 0");
}

// 正常なフィールドは防御に引っかからない（crane 版との一致を壊していないこと）。
static void testPlausibleValuesAreNotRejected(void)
{
  PositionControllerConfig cfg;
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = 6.0f;   // フィールド端でも 6m 程度
  in.target_global_pos[1] = -4.5f;
  in.current_pos[0] = -6.0f;
  in.current_pos[1] = 4.5f;
  in.linear_velocity_limit = 4.0f;
  in.terminal_velocity_xy[0] = 3.0f;  // ありうる最大級の終端速度
  in.terminal_velocity_xy[1] = -3.0f;
  in.terminal_velocity = 5.0f;

  const PositionControllerOutput out = computeOnce(in, cfg);
  checkReason(out.reason, PositionControllerReason::Ok, "PlausibleValues: 正常値は弾かれない");
  check(!out.feedforward_rejected, "PlausibleValues: feedforward も弾かれない");
}

// ---------------------------------------------------------------------------
// PID (積分・微分) 固有の要件
// ---------------------------------------------------------------------------

// 既定値 (ki = kd = 0) では PID が P 制御へ完全に縮退すること。
static void testDefaultGainsAreIdenticalToProportional(void)
{
  PositionControllerConfig cfg;
  check(cfg.integral_gain == 0.0f, "Defaults: ki の既定は 0");
  check(cfg.derivative_gain == 0.0f, "Defaults: kd の既定は 0");

  PositionControllerState state;
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = 1.0f;
  in.linear_velocity_limit = 3.0f;
  float first = 0.f;
  bool same = true;
  for (int step = 0; step < 100; ++step) {
    in.now_ms = 1000 + static_cast<uint64_t>(step);
    in.command_time_ms = in.now_ms;
    in.feedback_time_ms = in.now_ms;
    const PositionControllerOutput out = computePositionControl(in, cfg, &state);
    if (step == 0) first = out.polar_velocity_r;
    else if (fabsf(out.polar_velocity_r - first) > 1e-6f) same = false;
  }
  check(same, "Defaults: ki = kd = 0 なら状態を持ち回っても出力が変わらない");
  checkClose(first, 2.0f, 1e-4f, "Defaults: 出力は kp * error のまま");
}

// ki > 0 のとき誤差積分により速度が増加すること
static void testIntegralAccumulates(void)
{
  PositionControllerConfig cfg;
  cfg.position_gain = 0.f;
  cfg.integral_gain = 2.0f;
  cfg.position_tolerance = 0.f;

  PositionControllerState state;
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = 1.0f;
  in.linear_velocity_limit = 3.0f;
  in.now_ms = 1000;
  in.command_time_ms = 1000;
  in.feedback_time_ms = 1000;

  // 1ステップ目 (初期化)
  computePositionControl(in, cfg, &state);

  // 2ステップ目 (積分蓄積: dt = 0.1s, error = 1.0m -> integral = 0.1 m*s)
  in.now_ms = 1100;
  in.command_time_ms = 1100;
  in.feedback_time_ms = 1100;
  computePositionControl(in, cfg, &state);

  // 3ステップ目 (出力に反映: ki * 0.1 = 0.2 m/s)
  in.now_ms = 1200;
  in.command_time_ms = 1200;
  in.feedback_time_ms = 1200;
  const PositionControllerOutput out = computePositionControl(in, cfg, &state);
  checkClose(out.polar_velocity_r, 0.2f, 1e-3f, "Integral: 誤差積分により速度が出力される");
}

// 速度上限に達している間はアンチワインドアップにより積分が進まないこと
static void testAntiWindupDuringSaturation(void)
{
  PositionControllerConfig cfg;
  cfg.position_gain = 0.f;
  cfg.integral_gain = 10.0f;
  cfg.integral_velocity_limit = 0.5f;
  cfg.position_tolerance = 0.f;

  PositionControllerState state;
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = 1.0f;
  in.linear_velocity_limit = 0.2f;  // 0.2 m/s で飽和させる

  for (int step = 0; step < 50; ++step) {
    in.now_ms = 1000 + step * 20;
    in.command_time_ms = in.now_ms;
    in.feedback_time_ms = in.now_ms;
    const PositionControllerOutput out = computePositionControl(in, cfg, &state);
    check(out.polar_velocity_r <= 0.2f + 1e-4f, "AntiWindup: 速度上限を超えない");
  }
  check(state.integral[0] < 0.05f, "AntiWindup: 飽和中は積分が増加しない");
}

// I 項単独の速度は integral_velocity_limit で頭打ちになること
static void testIntegralVelocityLimit(void)
{
  PositionControllerConfig cfg;
  cfg.position_gain = 0.f;
  cfg.integral_gain = 10.0f;
  cfg.integral_velocity_limit = 0.3f;
  cfg.position_tolerance = 0.f;

  PositionControllerState state;
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = 1.0f;
  in.linear_velocity_limit = 3.0f;

  for (int step = 0; step < 200; ++step) {
    in.now_ms = 1000 + step * 10;
    in.command_time_ms = in.now_ms;
    in.feedback_time_ms = in.now_ms;
    computePositionControl(in, cfg, &state);
  }
  checkClose(state.integral[0] * cfg.integral_gain, 0.3f, 1e-3f, "IntegralLimit: I項速度上限でクランプされる");
}

// AtTarget では積分を捨てないこと
static void testAtTargetKeepsIntegral(void)
{
  PositionControllerConfig cfg;
  cfg.position_gain = 0.f;
  cfg.integral_gain = 2.0f;
  cfg.position_tolerance = 0.05f;

  PositionControllerState state;
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = 1.0f;
  in.linear_velocity_limit = 3.0f;

  in.now_ms = 1000;
  in.command_time_ms = 1000;
  in.feedback_time_ms = 1000;
  computePositionControl(in, cfg, &state);

  in.now_ms = 1100;
  in.command_time_ms = 1100;
  in.feedback_time_ms = 1100;
  computePositionControl(in, cfg, &state);
  const float accumulated = state.integral[0];
  check(accumulated > 0.f, "AtTargetKeepsIntegral: 積分が蓄積されている");

  // 許容誤差内に入る
  in.current_pos[0] = 0.98f;
  in.now_ms = 1200;
  in.command_time_ms = 1200;
  in.feedback_time_ms = 1200;
  const PositionControllerOutput out = computePositionControl(in, cfg, &state);
  checkReason(out.reason, PositionControllerReason::AtTarget, "AtTargetKeepsIntegral: AtTarget になる");
  checkClose(out.polar_velocity_r, 0.f, 1e-6f, "AtTargetKeepsIntegral: 速度はゼロ");
  checkClose(state.integral[0], accumulated, 1e-6f, "AtTargetKeepsIntegral: 積分は捨てない");
}

// 安全停止に入った周期で状態が捨てられること。
// 積分が残ると、crane 断から復帰した瞬間に溜まったぶんが一気に出る。
static void testStateResetOnStop(void)
{
  PositionControllerConfig cfg;
  cfg.position_gain = 0.f;
  cfg.integral_gain = 10.0f;
  cfg.position_tolerance = 0.f;

  PositionControllerState state;
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = 1.0f;
  in.linear_velocity_limit = 3.0f;

  // まず積分を溜める
  for (int step = 0; step < 500; ++step) {
    in.now_ms = 1000 + static_cast<uint64_t>(step);
    in.command_time_ms = in.now_ms;
    in.feedback_time_ms = in.now_ms;
    computePositionControl(in, cfg, &state);
  }
  check(state.integral[0] > 0.f, "StateReset: 前提として積分が溜まっている");

  // crane 断で停止
  PositionControllerInput stale = in;
  stale.now_ms = in.now_ms + 1000;
  stale.feedback_time_ms = stale.now_ms;
  const PositionControllerOutput stopped_out = computePositionControl(stale, cfg, &state);
  checkReason(stopped_out.reason, PositionControllerReason::CommandStale, "StateReset: crane 断で停止する");
  checkClose(state.integral[0], 0.f, 1e-9f, "StateReset: 停止時に積分が捨てられる");

  // 復帰の 1 周期目に溜まっていたぶんが出ないこと
  PositionControllerInput resumed = in;
  resumed.now_ms = stale.now_ms + 1;
  resumed.command_time_ms = resumed.now_ms;
  resumed.feedback_time_ms = resumed.now_ms;
  const PositionControllerOutput resumed_out = computePositionControl(resumed, cfg, &state);
  checkClose(resumed_out.polar_velocity_r, 0.f, 1e-6f, "StateReset: 復帰直後に積分が吐き出されない");
}

// 微分は feedback が更新された周期だけ計算し、間はその値を保持すること。
// 零次ホールドの current_pos を 1 kHz の毎周期で微分すると、サンプルが来た周期だけ
// 巨大なスパイクが立ち、他の周期はゼロになる。出力がそう暴れないことを固定する。
static void testDerivativeIsNotComputedOnHeldFeedback(void)
{
  PositionControllerConfig cfg;
  cfg.position_gain = 0.f;  // D 項だけを見る
  cfg.derivative_gain = 1.0f;
  cfg.position_tolerance = 0.f;

  PositionControllerState state;
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = 10.0f;  // 遠方 = 減速エンベロープで頭打ちにならない
  in.linear_velocity_limit = 5.0f;

  // 100 Hz の feedback で等速 1.0 m/s に相当する位置更新を与える
  float r_values[300];
  int n = 0;
  float pos = 0.f;
  uint64_t feedback_time = 1000;
  for (int step = 0; step < 300; ++step) {
    const uint64_t now = 1000 + static_cast<uint64_t>(step);
    if (step % 10 == 0) {
      pos = 1.0f * static_cast<float>(step) * 0.001f;
      feedback_time = now;
    }
    in.current_pos[0] = pos;
    in.now_ms = now;
    in.command_time_ms = now;
    in.feedback_time_ms = feedback_time;
    r_values[n++] = computePositionControl(in, cfg, &state).polar_velocity_r;
  }

  // 後半 (フィルタが落ち着いた後) は「実測速度 1.0 m/s を打ち消す向きに 1.0 m/s」で
  // ほぼ一定になるはず。サンプル間で 0 に落ちたりスパイクが立ったりしないこと。
  float min_r = r_values[200], max_r = r_values[200];
  for (int i = 200; i < n; ++i) {
    if (r_values[i] < min_r) min_r = r_values[i];
    if (r_values[i] > max_r) max_r = r_values[i];
  }
  check(max_r - min_r < 0.05f, "Derivative: feedback 保持中も出力が跳ねない");
  checkClose(min_r, 1.0f, 0.1f, "Derivative: 実測速度ぶんの D 項が出ている");
}

// 目標が跳んでも微分キックが出ないこと (微分先行形であることの検査)。
// crane からの目標は約 55 Hz で階段状に更新されるので、誤差微分だと毎回跳ねる。
static void testDerivativeDoesNotKickOnTargetStep(void)
{
  PositionControllerConfig cfg;
  cfg.position_gain = 1.0f;
  cfg.derivative_gain = 1.0f;

  PositionControllerState state;
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = 0.5f;
  in.linear_velocity_limit = 5.0f;
  in.current_pos[0] = 0.f;

  // 静止したまま目標だけを跳ばす
  for (int step = 0; step < 50; ++step) {
    in.now_ms = 1000 + static_cast<uint64_t>(step);
    in.command_time_ms = in.now_ms;
    in.feedback_time_ms = in.now_ms;
    computePositionControl(in, cfg, &state);
  }
  in.target_global_pos[0] = 2.0f;  // 目標が 1.5 m 跳ぶ
  in.now_ms += 1;
  in.command_time_ms = in.now_ms;
  in.feedback_time_ms = in.now_ms;
  const PositionControllerOutput out = computePositionControl(in, cfg, &state);
  // 実測速度は 0 のままなので D 項は 0。出力は kp * error だけ (減速エンベロープ内)。
  checkClose(out.polar_velocity_r, 2.0f, 1e-3f, "DerivativeKick: 目標が跳んでも D 項は出ない");
}

// ms 分解能のタイムスタンプが同一でも NaN/inf を出さないこと。
// 同じ ms に 2 サンプル届くと dt = 0 になり、素朴に割ると inf が clampNorm を
// 素通りして UART へ出る。
static void testZeroDtDoesNotProduceNaN(void)
{
  PositionControllerConfig cfg;
  cfg.integral_gain = 2.0f;
  cfg.derivative_gain = 0.5f;

  PositionControllerState state;
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = 1.0f;
  in.linear_velocity_limit = 3.0f;

  bool finite = true;
  for (int step = 0; step < 100; ++step) {
    // now_ms は進むが feedback のタイムスタンプは固定 (= 同一 ms の連続サンプル)
    in.now_ms = 1000 + static_cast<uint64_t>(step);
    in.command_time_ms = in.now_ms;
    in.feedback_time_ms = 1000;
    in.current_pos[0] += 0.001f;  // 位置だけは動く
    const PositionControllerOutput out = computePositionControl(in, cfg, &state);
    if (!(isfinite(out.polar_velocity_r) && isfinite(out.polar_velocity_theta))) finite = false;
  }
  check(finite, "ZeroDt: dt = 0 でも NaN/inf を出さない");
}

int main(void)
{
  printf("position_controller unit test\n");
  printf("(参照実装: crane/crane_sender/src/sim_position_controller.cpp)\n\n");

  printf("-- crane 版テストからの移植 --\n");
  testAppliesPositionGain();
  testAddsTerminalVelocityFeedforward();
  testClampsToMaximumVelocity();
  testClampsToBrakingEnvelope();
  testStopsInsideToleranceWithoutFeedforward();

  printf("\n-- 本構成に固有の要件 --\n");
  testConvergesWithoutOvershoot();
  testRespectsVelocityLimitEveryStep();
  testFeedforwardCannotExceedVelocityLimit();
  testZeroVelocityLimitMeansStop();
  testOutputDirectionIsGlobal();
  testTerminalVelocityClamp();

  printf("\n-- 未設定フィールドの防御 --\n");
  testUnsetFeedforwardIsIgnored();
  testUnsetTargetStops();
  testNanFeedbackStops();
  testPlausibleValuesAreNotRejected();

  printf("\n-- PID (積分・微分) --\n");
  testDefaultGainsAreIdenticalToProportional();
  testIntegralAccumulates();
  testAntiWindupDuringSaturation();
  testIntegralVelocityLimit();
  testAtTargetKeepsIntegral();
  testStateResetOnStop();
  testDerivativeIsNotComputedOnHeldFeedback();
  testDerivativeDoesNotKickOnTargetStep();
  testZeroDtDoesNotProduceNaN();

  printf("\n-- 安全停止 --\n");
  testStopEmergency();
  testCommandTimeout();
  testFeedbackTimeout();
  testNoUnsignedUnderflow();
  testVisionUnavailableStops();
  testStaleVisionStops();

  printf("\n%s (%d failure%s)\n", g_failures == 0 ? "PASS" : "FAIL", g_failures, g_failures == 1 ? "" : "s");
  return g_failures == 0 ? 0 : 1;
}
