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

  const PositionControllerOutput out = computePositionControl(in, cfg);
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

  const PositionControllerOutput out = computePositionControl(in, cfg);
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

  const PositionControllerOutput out = computePositionControl(in, cfg);
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
  const PositionControllerOutput out = computePositionControl(in, cfg);
  checkClose(out.polar_velocity_r, 1.0f, 1e-4f, "ClampsToBrakingEnvelope: |v| = sqrt(2*decel*|e|)");
}

static void testStopsInsideToleranceWithoutFeedforward(void)
{
  PositionControllerConfig cfg;
  cfg.position_tolerance = 0.01f;
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = 0.005f;
  in.linear_velocity_limit = 5.0f;

  const PositionControllerOutput out = computePositionControl(in, cfg);
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

    const PositionControllerOutput out = computePositionControl(in, cfg);
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
  const PositionControllerOutput settled = computePositionControl(in, cfg);
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
    const PositionControllerOutput out = computePositionControl(in, cfg);
    if (out.polar_velocity_r > 1.25f + 1e-5f) exceeded = true;
  }
  check(!exceeded, "VelocityLimit: 経路上のどの位置でも上限を超えない");
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

  const PositionControllerOutput out = computePositionControl(in, cfg);
  checkClose(out.polar_velocity_r, 0.0f, 1e-6f, "ZeroVelocityLimit: 0 は「無制限」ではなく「停止」");
}

static void testStopEmergency(void)
{
  PositionControllerConfig cfg;
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = 5.0f;
  in.linear_velocity_limit = 3.0f;
  in.stop_emergency = true;

  const PositionControllerOutput out = computePositionControl(in, cfg);
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
  checkReason(computePositionControl(in, cfg).reason, PositionControllerReason::Ok, "CommandTimeout: 100ms ちょうどはまだ動く");

  in.now_ms = 1101;  // 101ms 経過 = タイムアウト
  const PositionControllerOutput out = computePositionControl(in, cfg);
  checkClose(out.polar_velocity_r, 0.0f, 1e-6f, "CommandTimeout: 101ms で速度ゼロ");
  checkReason(out.reason, PositionControllerReason::CommandStale, "CommandTimeout: reason");
  check(out.stop_emergency, "CommandTimeout: STOP_EMERGENCY を立てる");

  // 一度も受けていない場合も停止
  PositionControllerInput never = freshInput();
  never.has_command = false;
  never.target_global_pos[0] = 5.0f;
  never.linear_velocity_limit = 3.0f;
  checkReason(computePositionControl(never, cfg).reason, PositionControllerReason::CommandStale, "CommandTimeout: 未受信も CommandStale");
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

  const PositionControllerOutput out = computePositionControl(in, cfg);
  checkClose(out.polar_velocity_r, 0.0f, 1e-6f, "FeedbackTimeout: 速度ゼロ");
  checkReason(out.reason, PositionControllerReason::FeedbackStale, "FeedbackTimeout: reason");

  PositionControllerInput never = freshInput();
  never.has_feedback = false;
  never.target_global_pos[0] = 5.0f;
  never.linear_velocity_limit = 3.0f;
  checkReason(computePositionControl(never, cfg).reason, PositionControllerReason::FeedbackStale, "FeedbackTimeout: 未受信 (起動直後) も FeedbackStale");
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

  const PositionControllerOutput out = computePositionControl(in, cfg);
  checkClose(out.polar_velocity_r, 0.0f, 1e-6f, "VisionUnavailable: 速度ゼロ");
  checkReason(out.reason, PositionControllerReason::VisionUnavailable, "VisionUnavailable: reason");
  check(out.stop_emergency, "VisionUnavailable: STOP_EMERGENCY を立てる");

  // 途絶判定のほうが優先されること（止まる理由として先に来る）。
  PositionControllerInput stale = in;
  stale.now_ms = 2000;  // crane から 1000ms
  checkReason(computePositionControl(stale, cfg).reason, PositionControllerReason::CommandStale, "VisionUnavailable: crane 断のほうが優先");
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

  checkReason(computePositionControl(in, cfg).reason, PositionControllerReason::Ok, "NoUnderflow: now < since でも停止扱いにしない");
}

// 出力方向がグローバル座標であること。
static void testOutputDirectionIsGlobal(void)
{
  PositionControllerConfig cfg;
  PositionControllerInput in = freshInput();
  in.target_global_pos[0] = -1.0f;
  in.target_global_pos[1] = 1.0f;
  in.linear_velocity_limit = 3.0f;

  const PositionControllerOutput out = computePositionControl(in, cfg);
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

  const PositionControllerOutput out = computePositionControl(in, cfg);
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

  const PositionControllerOutput out = computePositionControl(in, cfg);
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

  const PositionControllerOutput out = computePositionControl(in, cfg);
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

  const PositionControllerOutput out = computePositionControl(in, cfg);
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

  const PositionControllerOutput out = computePositionControl(in, cfg);
  checkReason(out.reason, PositionControllerReason::Ok, "PlausibleValues: 正常値は弾かれない");
  check(!out.feedforward_rejected, "PlausibleValues: feedforward も弾かれない");
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
  testZeroVelocityLimitMeansStop();
  testOutputDirectionIsGlobal();
  testTerminalVelocityClamp();

  printf("\n-- 未設定フィールドの防御 --\n");
  testUnsetFeedforwardIsIgnored();
  testUnsetTargetStops();
  testNanFeedbackStops();
  testPlausibleValuesAreNotRejected();

  printf("\n-- 安全停止 --\n");
  testStopEmergency();
  testCommandTimeout();
  testFeedbackTimeout();
  testNoUnsignedUnderflow();
  testVisionUnavailableStops();

  printf("\n%s (%d failure%s)\n", g_failures == 0 ? "PASS" : "FAIL", g_failures, g_failures == 1 ? "" : "s");
  return g_failures == 0 ? 0 : 1;
}
