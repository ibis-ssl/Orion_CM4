// このファイルは位置制御ループ（位置指令 -> 速度指令）の制御則と、
// crane / G474 feedback の途絶に対する安全停止判定を定義する責務を持つ。
//
// 【最重要の制約】transport 非依存であること。
// UART・UDP・boost::asio・ROS への依存を一切持ってはならない。
// 実機バイナリ (cm4/bridge/forward_ai_cmd_v2.cpp) とシミュレータ用バイナリ
// (cm4/bridge/cm4_sim.cpp) が「同一の翻訳単位」を使うことが、実機とシミュレータの
// 挙動が一致することの唯一の保証になる。ここに socket や fd が入り込んだ瞬間、
// シミュレータ用に第二の制御経路を書く羽目になり、この設計が防ごうとしている
// 重複そのものが再発する。
//
// 制御則の参照実装は crane の
//   crane/crane_sender/src/sim_position_controller.cpp calculateSimGlobalVelocity()
// で、既定ゲインもそこに合わせてある (position_gain = 2.0, deceleration = 3.0)。
// crane 版は crane_msgs に依存しているのでそのままは使えず、ここで再実装している。
// 同一入力で同一出力になることは cm4/control/test_position_controller.cpp が
// crane 側の単体テストと同じ数値ケースで固定している。

#ifndef ORION_CM4__CONTROL__POSITION_CONTROLLER_H_
#define ORION_CM4__CONTROL__POSITION_CONTROLLER_H_

#include <stdint.h>

namespace orion
{

struct PositionControllerConfig
{
  // crane の position_control.kp と一致させること (crane.launch.xml)
  float position_gain = 2.0f;
  // crane の position_control.deceleration と一致させること [m/s^2]
  float deceleration = 3.0f;
  // 目標位置の許容誤差 [m]。crane では PositionTargetMode.position_tolerance として
  // プランナが決めるが 715 バイトパケットには載らないので CM4 側の設定値にする。
  float position_tolerance = 0.01f;
  // crane からのパケットが途絶してから速度指令をゼロにするまで [ms]。
  //
  // 新構成では check_counter を CM4 が採番するため、G474 の connected_ai は
  // crane の生存を意味しなくなる。crane 断の安全停止はここで明示的に行う。
  uint32_t command_timeout_ms = 100;
  // G474 feedback が途絶してから速度指令をゼロにするまで [ms]。
  // feedback は位置制御ループ内で唯一の位置信号なので、途絶したら止めるしかない。
  uint32_t feedback_timeout_ms = 100;
  // crane の vision がこのロボットを最後に捉えてからの許容経過時間 [ms]。
  //
  // これは実機ファームウェアの定数と一致させるための値であって、調整パラメータでは
  // ない。G474 の state_func.c:314 が `> 500` で止めるので 500 にしてある。
  // CLI オプションを生やしていないのは、実機と食い違った値を現地で設定できて
  // しまうと「CM4 は走らせているのに G474 は止めている」状態を作れるからである。
  uint32_t vision_age_limit_ms = 500;
};

struct PositionControllerInput
{
  // --- crane からの mode 4 指令（デコード済み。パケットの生バイトは渡さない） ---
  float target_global_pos[2] = {0.f, 0.f};   // byte 32..35
  float terminal_velocity_xy[2] = {0.f, 0.f};  // byte 24..27 (mode 4 の mode_args)
  float terminal_velocity = 0.f;             // byte 36..37 (到達時の速度上限スカラー)
  float linear_velocity_limit = 0.f;         // byte 14..15
  bool stop_emergency = false;               // byte 22 bit3
  // byte 22 bit0。crane の vision がこのロボットを捉えているか。
  //
  // false のとき crane の target_global_pos は「見えていないロボット」に対する
  // 推測値なので、そこへ向かって走ってはいけない。G474 は state_func.c:314 で
  // 同じ条件でホイールを止めるが、simulator-cli はこのビットを復号するだけで
  // 何もしない (src/simulator/ibis_protocol.h:145)。ここで止めないと実機は
  // 止まり sim は走るという食い違いが生まれ、A/B 比較の数値が意味を失う。
  bool vision_available = false;
  // byte 20..21。crane の vision がこのロボットを最後に捉えてからの経過時間 [ms]。
  //
  // 素の uint16 なので、2 バイト固定小数のフィールドと違ってゼロ埋めは正しく 0
  // （= 最新）になる。同じパケットに 2 種類の符号化が同居しているので注意すること。
  uint16_t elapsed_time_ms_since_last_vision = 0;
  bool has_command = false;                  // crane パケットを 1 度でも受けたか
  uint64_t command_time_ms = 0;              // 最後に crane パケットを受けた時刻

  // --- G474 feedback（実機は UART 経由のループバック、sim は UDP。どちらもデコード済み） ---
  //
  // 位置は feedback の byte 44..51 (vision_based_position_x/y) を使うこと。
  // crane のパケットに入っている vision_global_pos で閉じてはならない。
  // それは今回ループの外へ出そうとしている無線経路そのものである。
  //
  // yaw と速度は制御則が使わないので受け取らない。feedback byte 4..7 の yaw は
  // 実機が度・シミュレータがラジアンでずれており、入力に含めると 57.3 倍の
  // 食い違いを実機と sim の間に作り込むことになる。
  float current_pos[2] = {0.f, 0.f};
  bool has_feedback = false;
  uint64_t feedback_time_ms = 0;

  uint64_t now_ms = 0;
};

// 出力が 0 のとき「なぜ止まっているのか」が分からないデバッグを避けるため、
// 理由を明示的に返す。ログと単体テストの両方が使う。
enum class PositionControllerReason {
  Ok,              // 制御則が速度を出した
  AtTarget,        // 許容誤差内かつ終端速度ゼロ
  StopEmergency,   // crane が STOP_EMERGENCY を立てた
  CommandStale,    // crane からのパケットが途絶した
  FeedbackStale,   // G474 feedback が途絶した（起動直後の未受信を含む）
  VisionUnavailable,  // crane がこのロボットを vision で捉えていない (byte 22 bit0 = 0)
  VisionStale,        // crane の vision がこのロボットを捉えてから時間が経ちすぎた
  InvalidCommand,  // 位置が物理的にありえない値（未設定フィールドの復号結果）
};

// 2 バイト固定小数 (range 32.767) の「未設定フィールド」は 0.0 ではなく -32.767 として
// 復号される。encode が 0.0 を 0x7FFF へ写すので、memset でゼロ埋めしたフィールドは
// 最大級の負値になる。座標としても速度としても物理的にありえない大きさなので、
// これを「未設定」のシグネチャとして扱う。
//
// 実測（framework セッション、実チェーン）: crane 役が terminal_velocity_x/y を
// 書き忘れただけで feedforward が (-32.767, -32.767) になり、位置制御がそれに支配されて
// ロボットが目標と無関係な方向へ場外まで走った。終端速度スカラも -32.767（負）なので
// 「スカラ > 0 のときだけクランプ」の規則に入らずクランプもされない。
constexpr float kImplausibleMagnitude = 32.0f;

struct PositionControllerOutput
{
  float polar_velocity_r = 0.f;      // mode 3 args: target_global_velocity_r
  float polar_velocity_theta = 0.f;  // mode 3 args: target_global_velocity_theta（グローバル方向 [rad]）
  bool stop_emergency = false;
  PositionControllerReason reason = PositionControllerReason::FeedbackStale;
  // 終端速度が未設定シグネチャだったので 0 とみなした。制御は続行している。
  // ログに出すこと。crane 側のフィールド書き忘れはこれでしか気付けない。
  bool feedforward_rejected = false;
};

// 位置指令から速度指令を計算する。状態を持たない純関数なので決定論的。
PositionControllerOutput computePositionControl(const PositionControllerInput & input, const PositionControllerConfig & config);

// 理由のログ用文字列。
const char * toString(PositionControllerReason reason);

}  // namespace orion

#endif  // ORION_CM4__CONTROL__POSITION_CONTROLLER_H_
