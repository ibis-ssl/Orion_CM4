// このファイルはシミュレータ用の CM4 相当プロセスを担当する。
//
// crane から mode 4 (位置指令) の 715 バイトを UDP で受け、実機と同一の
// position_controller で位置制御ループを閉じ、mode 3 (速度指令) の 715 バイトを
// simulator-cli へ UDP で送る。simulator-cli は G474 とロボット物理を担当し、
// 位置制御は行わない。
//
//   実機:  crane --UDP:12345 mode4--> ai_cmd_v2.out --UART mode3--> G474
//   sim :  crane --UDP:12345 mode4--> cm4_sim       --UDP:12346 mode3--> simulator-cli
//
// feedback は simulator-cli から 127.0.0.1:(50100+id) へ届き、実機の
// robot_feedback.out と同じ multicast 224.5.20.(100+id):(50100+id) へ再配信する。
// crane と host ツールはそこを見るので、実機と同じポートマップのまま使える。
//
// 【制約】制御則は cm4/control/position_controller.cpp を実機バイナリと
// 同一ソースとしてリンクする。ここに制御則を書いてはならない。
// このファイルの責務は transport (ソケット・ペーシング・劣化注入) だけ。
//
// 依存は POSIX ソケットのみ。boost は使わない (ホスト PC でそのままビルドするため)。
//
// 詳細な統合仕様: framework/docs/robot-side-position-control.md

#include <arpa/inet.h>
#include <errno.h>
#include <math.h>
#include <netinet/in.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#include <algorithm>
#include <deque>
#include <random>
#include <string>
#include <vector>

#include "../control/position_controller.h"
#include "robot_packet.h"

namespace
{

constexpr int kRobotSlots = 11;
constexpr int kCmdSize = 64;
constexpr int kSlotSize = kCmdSize + 1;
constexpr int kPacketSize = kSlotSize * kRobotSlots;  // 715
constexpr int kFeedbackSize = 128;

// feedback 128 バイトのうち位置だけを使う (byte 44..51, little-endian float)。
// yaw (byte 4..7) は実機が度・simulator-cli がラジアンでずれているので使わない。
// 詳細は doc/feedback_packet.md を参照。
constexpr int kFeedbackPosXOffset = 44;
constexpr int kFeedbackPosYOffset = 48;

constexpr uint8_t kEmptySlotRobotId = 0xFF;  // 担当しないスロットの印

// ---------------------------------------------------------------------------
// オプション
// ---------------------------------------------------------------------------

struct Options
{
  std::vector<int> robot_ids;
  int in_port = 12345;
  std::string out_addr = "127.0.0.1";
  int out_port = 12346;
  int feedback_port_base = 50100;
  // 再配信先は入力ポートと独立。simulator-cli の feedback を別ポートで受けても、
  // crane の crane_robot_receiver は 50100+id 固定なので再配信先は動かせない。
  int feedback_relay_port_base = 50100;
  std::string multicast_if;  // 空なら OS 任せ (開発 PC に 192.168.20.x は無い)
  bool feedback_relay = true;

  int rate_hz = 1000;

  double rx_delay_ms = 0.0;
  double rx_jitter_ms = 0.0;
  double rx_loss_rate = 0.0;
  uint32_t seed = 0;

  bool vision_echo_feedback = true;  // --vision-echo crane で false

  orion::PositionControllerConfig control;
};

void printUsage(const char * argv0)
{
  printf(
    "使い方: %s [オプション]\n"
    "\n"
    "  --robot-ids 0,1,2         担当するスロット (既定: 0..10 の全 11 台)\n"
    "  --in-port 12345           crane からの mode 4 を受ける UDP ポート\n"
    "  --out-addr 127.0.0.1      simulator-cli の待つアドレス\n"
    "  --out-port 12346          simulator-cli の --ibis-port と揃える\n"
    "  --feedback-port-base 50100 simulator-cli の --ibis-feedback-port-base と揃える\n"
    "  --feedback-relay-port-base 50100\n"
    "                            multicast 再配信の宛先ポート。crane の\n"
    "                            crane_robot_receiver は 50100+id 固定なので通常は既定のまま\n"
    "  --multicast-if <ip>       feedback 再配信の送出インタフェース (省略時 OS 任せ)\n"
    "  --no-feedback-relay       multicast 再配信を行わない\n"
    "  --rate-hz 1000            制御レート\n"
    "  --rx-delay-ms 0           crane -> CM4 経路への固定遅延\n"
    "  --rx-jitter-ms 0          同 ジッタ (一様分布 +-)\n"
    "  --rx-loss-rate 0.0        同 パケットロス率 0.0..1.0\n"
    "  --seed 0                  劣化注入の乱数シード (再現性のため)\n"
    "  --vision-echo feedback|crane\n"
    "                            出力パケットの VISION_GLOBAL_X/Y に何を入れるか\n"
    "  --kp / --decel / --tolerance / --command-timeout-ms / --feedback-timeout-ms\n"
    "  -h, --help\n",
    argv0);
}

bool parseIntList(const char * s, std::vector<int> * out)
{
  out->clear();
  const char * p = s;
  while (*p) {
    char * end = nullptr;
    const long v = strtol(p, &end, 10);
    if (end == p) return false;
    if (v < 0 || v >= kRobotSlots) {
      fprintf(stderr, "robot id %ld は 0..%d の範囲外です\n", v, kRobotSlots - 1);
      return false;
    }
    out->push_back(static_cast<int>(v));
    p = end;
    while (*p == ',' || *p == ' ') p++;
  }
  return !out->empty();
}

bool parseOptions(int argc, char * argv[], Options * o)
{
  for (int i = 0; i < kRobotSlots; ++i) o->robot_ids.push_back(i);
  bool relay_port_explicit = false;

  for (int i = 1; i < argc; ++i) {
    const std::string a = argv[i];
    auto next = [&](const char ** v) {
      if (i + 1 >= argc) {
        fprintf(stderr, "%s には引数が必要です\n", a.c_str());
        return false;
      }
      *v = argv[++i];
      return true;
    };
    const char * v = nullptr;

    if (a == "-h" || a == "--help") {
      printUsage(argv[0]);
      exit(0);
    } else if (a == "--robot-ids") {
      if (!next(&v)) return false;
      if (!parseIntList(v, &o->robot_ids)) return false;
    } else if (a == "--in-port") {
      if (!next(&v)) return false;
      o->in_port = atoi(v);
    } else if (a == "--out-addr") {
      if (!next(&v)) return false;
      o->out_addr = v;
    } else if (a == "--out-port") {
      if (!next(&v)) return false;
      o->out_port = atoi(v);
    } else if (a == "--feedback-port-base") {
      if (!next(&v)) return false;
      o->feedback_port_base = atoi(v);
    } else if (a == "--feedback-relay-port-base") {
      if (!next(&v)) return false;
      o->feedback_relay_port_base = atoi(v);
      relay_port_explicit = true;
    } else if (a == "--multicast-if") {
      if (!next(&v)) return false;
      o->multicast_if = v;
    } else if (a == "--no-feedback-relay") {
      o->feedback_relay = false;
    } else if (a == "--rate-hz") {
      if (!next(&v)) return false;
      o->rate_hz = atoi(v);
    } else if (a == "--rx-delay-ms") {
      if (!next(&v)) return false;
      o->rx_delay_ms = atof(v);
    } else if (a == "--rx-jitter-ms") {
      if (!next(&v)) return false;
      o->rx_jitter_ms = atof(v);
    } else if (a == "--rx-loss-rate") {
      if (!next(&v)) return false;
      o->rx_loss_rate = atof(v);
    } else if (a == "--seed") {
      if (!next(&v)) return false;
      o->seed = static_cast<uint32_t>(strtoul(v, nullptr, 10));
    } else if (a == "--vision-echo") {
      if (!next(&v)) return false;
      const std::string mode = v;
      if (mode == "feedback") {
        o->vision_echo_feedback = true;
      } else if (mode == "crane") {
        o->vision_echo_feedback = false;
      } else {
        fprintf(stderr, "--vision-echo は feedback か crane です\n");
        return false;
      }
    } else if (a == "--kp") {
      if (!next(&v)) return false;
      o->control.position_gain = atof(v);
    } else if (a == "--decel") {
      if (!next(&v)) return false;
      o->control.deceleration = atof(v);
    } else if (a == "--tolerance") {
      if (!next(&v)) return false;
      o->control.position_tolerance = atof(v);
    } else if (a == "--command-timeout-ms") {
      if (!next(&v)) return false;
      o->control.command_timeout_ms = atoi(v);
    } else if (a == "--feedback-timeout-ms") {
      if (!next(&v)) return false;
      o->control.feedback_timeout_ms = atoi(v);
    } else {
      fprintf(stderr, "不明なオプション: %s\n", a.c_str());
      printUsage(argv[0]);
      return false;
    }
  }

  if (o->rate_hz <= 0) o->rate_hz = 1000;
  // --feedback-port-base だけを動かしたときに再配信先が一緒に動くと、
  // crane の crane_robot_receiver (50100+id 固定) から外れて
  // 「再配信が来ない」という誤診を招く。既定のまま据え置く。
  (void)relay_port_explicit;
  if (o->rx_loss_rate < 0.0) o->rx_loss_rate = 0.0;
  if (o->rx_loss_rate > 1.0) o->rx_loss_rate = 1.0;
  return true;
}

// ---------------------------------------------------------------------------
// 時計
//
// 起動時からの経過ミリ秒。劣化注入の遅延も制御器のタイムアウトも同じ時計を使う。
// ---------------------------------------------------------------------------

class Clock
{
public:
  Clock()
  {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    origin_ms_ = static_cast<uint64_t>(ts.tv_sec) * 1000ULL + ts.tv_nsec / 1000000ULL;
  }

  uint64_t nowMs() const
  {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return static_cast<uint64_t>(ts.tv_sec) * 1000ULL + ts.tv_nsec / 1000000ULL - origin_ms_;
  }

private:
  uint64_t origin_ms_ = 0;
};

// ---------------------------------------------------------------------------
// 無線劣化の注入 (crane -> CM4 の入力側にのみ実装する)
//
// 受信したデータグラムを配送予定時刻付きでキューに積み、制御ループ側で期限到来分を
// 取り出す。ロスは受信時に確率で捨てる。--seed で完全に再現する。
// ---------------------------------------------------------------------------

class RxDegrader
{
public:
  RxDegrader(const Options & o) : rng_(o.seed), delay_ms_(o.rx_delay_ms), jitter_ms_(o.rx_jitter_ms), loss_rate_(o.rx_loss_rate) {}

  bool enabled() const { return delay_ms_ > 0.0 || jitter_ms_ > 0.0 || loss_rate_ > 0.0; }

  // 受信したパケットを取り込む。捨てた場合は false。
  bool push(const uint8_t * data, uint64_t now_ms)
  {
    if (loss_rate_ > 0.0) {
      std::uniform_real_distribution<double> coin(0.0, 1.0);
      if (coin(rng_) < loss_rate_) {
        dropped_++;
        pushed_++;
        mixHash(0xD120D120D120D120ULL);
        return false;
      }
    }
    double delay = delay_ms_;
    if (jitter_ms_ > 0.0) {
      std::uniform_real_distribution<double> j(-jitter_ms_, jitter_ms_);
      delay += j(rng_);
    }
    if (delay < 0.0) delay = 0.0;
    pushed_++;
    mixHash(static_cast<uint64_t>(delay * 1000.0 + 0.5));
    Entry e;
    e.deliver_ms = now_ms + static_cast<uint64_t>(delay + 0.5);
    memcpy(e.data, data, kPacketSize);
    queue_.push_back(e);
    // ジッタで順序が入れ替わりうるので配送時刻順に保つ
    std::stable_sort(queue_.begin(), queue_.end(), [](const Entry & a, const Entry & b) { return a.deliver_ms < b.deliver_ms; });
    return true;
  }

  // 期限の到来したパケットを 1 つ取り出す。無ければ false。
  bool pop(uint64_t now_ms, uint8_t * out)
  {
    if (queue_.empty() || queue_.front().deliver_ms > now_ms) return false;
    memcpy(out, queue_.front().data, kPacketSize);
    queue_.pop_front();
    return true;
  }

  uint64_t dropped() const { return dropped_; }
  uint64_t pushed() const { return pushed_; }
  // 割り当てた遅延の列のローリングハッシュ。
  // 同じ seed と同じパケット数なら必ず同じ値になるので、--seed の再現性を
  // 実時間のサンプリングに依存せず決定論的に検証できる。
  uint64_t decisionHash() const { return hash_; }

private:
  void mixHash(uint64_t v)
  {
    hash_ ^= v + 0x9e3779b97f4a7c15ULL + (hash_ << 6) + (hash_ >> 2);
  }

  struct Entry
  {
    uint64_t deliver_ms;
    uint8_t data[kPacketSize];
  };
  std::mt19937 rng_;
  double delay_ms_, jitter_ms_, loss_rate_;
  std::deque<Entry> queue_;
  uint64_t dropped_ = 0;
  uint64_t pushed_ = 0;
  uint64_t hash_ = 1469598103934665603ULL;
};

// ---------------------------------------------------------------------------
// ロボットごとの状態
// ---------------------------------------------------------------------------

struct RobotState
{
  bool handled = false;
  int feedback_sock = -1;

  // crane から最後に届いたコマンド (64 バイトの生バイト列)。
  // 出力はこれをコピーして 4 箇所だけ差し替えて作る。ゼロから組み立てると
  // target_global_theta / angular_velocity_limit / kick / dribble / flags を
  // 取りこぼす (simulator-cli と G474 はこれらをすべて使う)。
  uint8_t command[kCmdSize] = {};
  bool has_command = false;
  uint64_t command_time_ms = 0;

  float feedback_pos[2] = {0.f, 0.f};
  bool has_feedback = false;
  uint64_t feedback_time_ms = 0;

  orion::PositionControllerReason last_reason = orion::PositionControllerReason::FeedbackStale;
};

// ---------------------------------------------------------------------------
// ソケット
// ---------------------------------------------------------------------------

int makeUdpSocket(bool reuse_addr)
{
  const int s = socket(AF_INET, SOCK_DGRAM, 0);
  if (s < 0) {
    perror("socket");
    return -1;
  }
  if (reuse_addr) {
    int on = 1;
    // feedback ポートにのみ設定する。実機と同じポートマップのまま、同一ホスト上で
    // cm4_sim の unicast bind (127.0.0.1:50100+id) と crane_robot_receiver の
    // multicast bind (224.5.20.(100+i):50100+i) を同居させるために必要。
    if (setsockopt(s, SOL_SOCKET, SO_REUSEADDR, &on, sizeof(on)) != 0) {
      perror("setsockopt(SO_REUSEADDR)");
    }
  }
  return s;
}

// crane 入力ポートには SO_REUSEADDR を付けない。
// UDP で SO_REUSEADDR を両方に付けると二重 bind が成立してしまい、
// cm4_sim を二重起動したときにデータグラムが片方にしか届かず、
// 「起動しているのに半分しか動かない」無言の故障になる。二重起動は失敗させる。
int bindUdp(const char * addr, int port, bool reuse_addr)
{
  const int s = makeUdpSocket(reuse_addr);
  if (s < 0) return -1;
  struct sockaddr_in sa;
  memset(&sa, 0, sizeof(sa));
  sa.sin_family = AF_INET;
  sa.sin_port = htons(static_cast<uint16_t>(port));
  sa.sin_addr.s_addr = addr ? inet_addr(addr) : INADDR_ANY;
  if (bind(s, reinterpret_cast<struct sockaddr *>(&sa), sizeof(sa)) != 0) {
    fprintf(stderr, "bind(%s:%d) に失敗しました: %s\n", addr ? addr : "0.0.0.0", port, strerror(errno));
    if (errno == EADDRINUSE) {
      fprintf(stderr, "  cm4_sim が既に起動していないか確認してください (pgrep -x cm4_sim.out)\n");
    }
    close(s);
    return -1;
  }
  return s;
}

// ---------------------------------------------------------------------------
// 出力パケットの組み立て
// ---------------------------------------------------------------------------

void writeTwoByte(uint8_t * data, int offset, float value, float range)
{
  forward(&data[offset], &data[offset + 1], value, range);
}

// 担当スロットは受信した 64 バイトをコピーして必要な箇所だけ差し替える。
//
// passthrough は受信 mode 3 をそのまま転送する経路 (旧構成の A/B 基準)。
// check_counter は crane 由来のまま触らない。simulator-cli は同じ check_counter を
// 無視するので、結果として「crane のレートでコマンドが適用される」という
// 旧構成そのものの挙動になる。
void buildPassthroughSlot(uint8_t * slot_cmd, const RobotState & st, bool stale, bool vision_echo_feedback)
{
  memcpy(slot_cmd, st.command, kCmdSize);

  if (vision_echo_feedback && st.has_feedback) {
    writeTwoByte(slot_cmd, VISION_GLOBAL_X_HIGH, st.feedback_pos[0], 32.767f);
    writeTwoByte(slot_cmd, VISION_GLOBAL_Y_HIGH, st.feedback_pos[1], 32.767f);
  }

  if (stale) {
    // 旧構成では crane 断を G474 の connected_ai タイムアウトが拾って止める。
    // simulator-cli はそれを模擬しないので、ここで代行する。代行しないと
    // A/B 比較の基準側だけが crane 断で走り続けてしまう。
    // check_counter を変えないと simulator-cli に無視されるので、ここだけ変える。
    slot_cmd[CHECK_COUNTER] = static_cast<uint8_t>(slot_cmd[CHECK_COUNTER] + 1);
    writeTwoByte(slot_cmd, CONTROL_MODE_ARGS + 0, 0.f, 32.767f);
    writeTwoByte(slot_cmd, CONTROL_MODE_ARGS + 2, 0.f, 32.767f);
    slot_cmd[FLAGS] |= static_cast<uint8_t>(1u << STOP_EMERGENCY);
    slot_cmd[KICK_POWER] = 0;
    slot_cmd[DRIBBLE_POWER] = 0;
  }
}

// mode 4 を受けた担当スロットは位置制御ループの出力で 4 箇所を差し替える。
void buildSlot(uint8_t * slot_cmd, const RobotState & st, const orion::PositionControllerOutput & out, uint8_t check_counter, bool vision_echo_feedback)
{
  memcpy(slot_cmd, st.command, kCmdSize);

  // CM4 が採番する。simulator-cli も G474 も「前回と同じ check_counter」を
  // 無視するので、制御周期ごとに必ず変えなければならない。
  slot_cmd[CHECK_COUNTER] = check_counter;

  // simulator-cli はコマンドの vision_global_pos を実位置と 0.5m 以内で照合して
  // チーム判定する。一致しないとコマンドを無言で捨てる。
  // crane 由来の値は劣化注入で古くなるため、feedback 由来の実位置を詰める。
  // feedback 未受信の間は crane 由来の値をそのまま流す (ブートストラップ)。
  if (vision_echo_feedback && st.has_feedback) {
    writeTwoByte(slot_cmd, VISION_GLOBAL_X_HIGH, st.feedback_pos[0], 32.767f);
    writeTwoByte(slot_cmd, VISION_GLOBAL_Y_HIGH, st.feedback_pos[1], 32.767f);
  }

  slot_cmd[CONTROL_MODE] = POLAR_VELOCITY_TARGET_MODE;
  writeTwoByte(slot_cmd, CONTROL_MODE_ARGS + 0, out.polar_velocity_r, 32.767f);
  writeTwoByte(slot_cmd, CONTROL_MODE_ARGS + 2, out.polar_velocity_theta, 32.767f);

  if (out.stop_emergency) {
    slot_cmd[FLAGS] |= static_cast<uint8_t>(1u << STOP_EMERGENCY);
    // crane 断・feedback 断で古いキック/ドリブル指令を撃ち続けない (実機と同じ)。
    slot_cmd[KICK_POWER] = 0;
    slot_cmd[DRIBBLE_POWER] = 0;
    slot_cmd[FLAGS] &= static_cast<uint8_t>(~(1u << ENABLE_CHIP));
  }
}

volatile sig_atomic_t g_stop = 0;
void onSignal(int) { g_stop = 1; }

}  // namespace

// ---------------------------------------------------------------------------

int main(int argc, char * argv[])
{
  Options opt;
  if (!parseOptions(argc, argv, &opt)) return 1;

  signal(SIGINT, onSignal);
  signal(SIGTERM, onSignal);

  Clock clock;
  RxDegrader degrader(opt);

  RobotState robots[kRobotSlots];
  for (int id : opt.robot_ids) robots[id].handled = true;

  // crane からの入力
  const int in_sock = bindUdp(nullptr, opt.in_port, /*reuse_addr=*/false);
  if (in_sock < 0) return 1;

  // simulator-cli への出力
  const int out_sock = makeUdpSocket(/*reuse_addr=*/false);
  if (out_sock < 0) return 1;
  struct sockaddr_in out_addr;
  memset(&out_addr, 0, sizeof(out_addr));
  out_addr.sin_family = AF_INET;
  out_addr.sin_port = htons(static_cast<uint16_t>(opt.out_port));
  out_addr.sin_addr.s_addr = inet_addr(opt.out_addr.c_str());

  // simulator-cli からの feedback (id ごとに別ポートへ届く)
  for (int id : opt.robot_ids) {
    robots[id].feedback_sock = bindUdp("127.0.0.1", opt.feedback_port_base + id, /*reuse_addr=*/true);
    if (robots[id].feedback_sock < 0) return 1;
  }

  // feedback の multicast 再配信 (実機の robot_feedback.out と同じ)
  int relay_sock = -1;
  if (opt.feedback_relay) {
    relay_sock = makeUdpSocket(/*reuse_addr=*/false);
    if (relay_sock < 0) return 1;
    const int loop = 1;  // 同一ホストの crane / host ツールへ届かせる
    setsockopt(relay_sock, IPPROTO_IP, IP_MULTICAST_LOOP, &loop, sizeof(loop));
    if (!opt.multicast_if.empty()) {
      const in_addr_t ifaddr = inet_addr(opt.multicast_if.c_str());
      if (setsockopt(relay_sock, IPPROTO_IP, IP_MULTICAST_IF, &ifaddr, sizeof(ifaddr)) != 0) {
        perror("setsockopt(IP_MULTICAST_IF)");
      }
    }
  }

  printf("cm4_sim 開始\n");
  printf("  crane 入力      : 0.0.0.0:%d (mode 4, 715B)\n", opt.in_port);
  printf("  simulator 出力  : %s:%d (mode 3, 715B)\n", opt.out_addr.c_str(), opt.out_port);
  printf("  feedback 入力   : 127.0.0.1:%d+id\n", opt.feedback_port_base);
  if (opt.feedback_relay) printf("  feedback 再配信 : 224.5.20.(100+id):%d+id\n", opt.feedback_relay_port_base);
  printf("  担当スロット    :");
  for (int id : opt.robot_ids) printf(" %d", id);
  printf("\n");
  printf("  ペーシング      : 自走 %d Hz (feedback 待ちでブロックしない。実機と同じ構造)\n", opt.rate_hz);
  if (degrader.enabled()) {
    printf("  劣化注入 (入力) : delay %.1fms jitter %.1fms loss %.3f seed %u\n", opt.rx_delay_ms, opt.rx_jitter_ms, opt.rx_loss_rate, opt.seed);
  }
  printf("  制御            : kp %.2f decel %.2f tol %.3f cmd_timeout %ums fb_timeout %ums\n", static_cast<double>(opt.control.position_gain),
    static_cast<double>(opt.control.deceleration), static_cast<double>(opt.control.position_tolerance), opt.control.command_timeout_ms,
    opt.control.feedback_timeout_ms);
  printf("  feedback を 1 度も受けるまでは r=0 を出し続ける (simulator の vision キャッシュを埋めるため)\n");
  fflush(stdout);

  uint8_t rx[kPacketSize + 64];
  uint8_t tx[kPacketSize];
  uint8_t fb[kFeedbackSize + 64];
  uint8_t check_counter = 0;
  uint64_t ff_rejected_count = 0;
  bool ff_rejected_warned = false;

  // --- crane からの受信を劣化キューへ積む（ノンブロッキング） ---
  auto drainCraneInput = [&]() {
    int arrived = 0;
    while (true) {
      // MSG_TRUNC でデータグラムの実長を得る。付けないと 716 バイトが
      // 715 バイトへ切り詰められ「正常な全ゼロパケット」に化ける。
      const ssize_t n = recv(in_sock, rx, sizeof(rx), MSG_DONTWAIT | MSG_TRUNC);
      if (n < 0) break;
      if (n != kPacketSize) continue;  // 715 バイト以外は捨てる
      arrived++;
      degrader.push(rx, clock.nowMs());
    }
    return arrived;
  };

  // --- 劣化キューから期限到来分を取り出してロボット状態へ反映 ---
  auto applyDeliveredCommands = [&]() {
    uint8_t pkt[kPacketSize];
    int applied = 0;
    while (degrader.pop(clock.nowMs(), pkt)) {
      applied++;
      for (int slot = 0; slot < kRobotSlots; ++slot) {
        const int offset = slot * kSlotSize;
        const uint8_t robot_id = pkt[offset];
        if (robot_id >= kRobotSlots || !robots[robot_id].handled) continue;
        const uint8_t * cmd = pkt + offset + 1;
        // 空スロット (コマンド 64 バイトが全ゼロ) はスキップ
        bool empty = true;
        for (int i = 0; i < kCmdSize; ++i) {
          if (cmd[i] != 0) {
            empty = false;
            break;
          }
        }
        if (empty) continue;
        memcpy(robots[robot_id].command, cmd, kCmdSize);
        robots[robot_id].has_command = true;
        robots[robot_id].command_time_ms = clock.nowMs();
      }
    }
    return applied;
  };

  // --- feedback をノンブロッキングで全部吸い出し、multicast へ再配信 ---
  auto drainFeedback = [&]() {
    int received = 0;
    for (int id : opt.robot_ids) {
      while (true) {
        const ssize_t n = recv(robots[id].feedback_sock, fb, sizeof(fb), MSG_DONTWAIT);
        if (n < 0) break;
        if (n != kFeedbackSize || fb[0] != 0xAB || fb[1] != 0xEA) continue;
        float x = 0.f, y = 0.f;
        memcpy(&x, &fb[kFeedbackPosXOffset], sizeof(float));
        memcpy(&y, &fb[kFeedbackPosYOffset], sizeof(float));
        robots[id].feedback_pos[0] = x;
        robots[id].feedback_pos[1] = y;
        robots[id].has_feedback = true;
        robots[id].feedback_time_ms = clock.nowMs();
        received++;

        if (relay_sock >= 0) {
          char group[32];
          snprintf(group, sizeof(group), "224.5.20.%d", 100 + id);
          struct sockaddr_in dst;
          memset(&dst, 0, sizeof(dst));
          dst.sin_family = AF_INET;
          dst.sin_port = htons(static_cast<uint16_t>(opt.feedback_relay_port_base + id));
          dst.sin_addr.s_addr = inet_addr(group);
          sendto(relay_sock, fb, kFeedbackSize, 0, reinterpret_cast<struct sockaddr *>(&dst), sizeof(dst));
        }
      }
    }
    return received;
  };

  // --- 1 制御周期ぶんの出力データグラムを作って送る ---
  auto sendControlDatagram = [&]() {
    memset(tx, 0, sizeof(tx));
    check_counter = (check_counter >= 200) ? 0 : static_cast<uint8_t>(check_counter + 1);

    for (int slot = 0; slot < kRobotSlots; ++slot) {
      uint8_t * out_slot = tx + slot * kSlotSize;
      RobotState & st = robots[slot];
      if (!st.handled || !st.has_command) {
        // 担当外・未受信は robot_id を範囲外にしてコマンドをゼロ埋め。
        // simulator-cli は robot_id >= 11 と「64 バイト全ゼロ」の二重で弾く。
        out_slot[0] = kEmptySlotRobotId;
        continue;
      }

      const RobotCommandSerializedV2 * serialized = reinterpret_cast<const RobotCommandSerializedV2 *>(st.command);
      const RobotCommandV2 cmd = RobotCommandSerializedV2_deserialize(serialized);

      // 受信 mode で分岐する。実機の forward_ai_cmd_v2.cpp と同じ扱い。
      //   mode 3 -> 位置制御せずそのまま転送 (旧構成。A/B 比較の基準側)
      //   mode 4 -> 位置制御ループを回して mode 3 を生成 (新構成)
      // 劣化注入は mode によらず入力側で常に適用されるので、転送経路・注入点・
      // 注入実装が両構成で完全に一致し、独立変数が「位置ループをどこで閉じるか」
      // だけになる。
      if (cmd.control_mode != POSITION_TARGET_WITH_TERMINAL_VELOCITY_MODE) {
        const bool stale = (clock.nowMs() - st.command_time_ms) > opt.control.command_timeout_ms;
        out_slot[0] = static_cast<uint8_t>(slot);
        buildPassthroughSlot(out_slot + 1, st, stale, opt.vision_echo_feedback);
        st.last_reason = stale ? orion::PositionControllerReason::CommandStale : orion::PositionControllerReason::Ok;
        continue;
      }

      orion::PositionControllerInput in;
      in.target_global_pos[0] = cmd.target_global_pos[0];
      in.target_global_pos[1] = cmd.target_global_pos[1];
      in.terminal_velocity_xy[0] = cmd.mode_args.position_target.terminal_velocity_x;
      in.terminal_velocity_xy[1] = cmd.mode_args.position_target.terminal_velocity_y;
      in.terminal_velocity = cmd.terminal_velocity;
      in.linear_velocity_limit = cmd.linear_velocity_limit;
      in.stop_emergency = cmd.stop_emergency;
      in.vision_available = cmd.is_vision_available;
      in.has_command = st.has_command;
      in.command_time_ms = st.command_time_ms;
      in.current_pos[0] = st.feedback_pos[0];
      in.current_pos[1] = st.feedback_pos[1];
      in.has_feedback = st.has_feedback;
      in.feedback_time_ms = st.feedback_time_ms;
      in.now_ms = clock.nowMs();

      const orion::PositionControllerOutput out = computePositionControl(in, opt.control);
      st.last_reason = out.reason;
      if (out.feedforward_rejected) {
        // 2 バイト固定小数の未設定フィールドは 0.0 ではなく -32.767 として復号される。
        // 黙って無視すると crane 側のフィールド書き忘れに誰も気付かない。
        ff_rejected_count++;
        if (!ff_rejected_warned) {
          ff_rejected_warned = true;
          fprintf(stderr,
            "cm4_sim: robot %d の terminal_velocity_x/y が未設定です (-32.767 として復号されました)。"
            "0 とみなして P 制御を継続します。crane 側が mode 4 の ARGS 24..27 を書いているか確認してください\n",
            slot);
        }
      }

      out_slot[0] = static_cast<uint8_t>(slot);
      buildSlot(out_slot + 1, st, out, check_counter, opt.vision_echo_feedback);
    }

    sendto(out_sock, tx, kPacketSize, 0, reinterpret_cast<struct sockaddr *>(&out_addr), sizeof(out_addr));
  };

  uint64_t loop_count = 0;

  // 自走のみ。最後に受信した feedback を使い、feedback 待ちでブロックしない。
  // 実機の forward_ai_cmd_v2.cpp の usleep(1000) ポーリングと同じ構造にしてある。
  // (simulator-cli の ibis ブランチに lockstep は存在しないので、同期モードは持たない)
  const useconds_t period_us = static_cast<useconds_t>(1000000 / opt.rate_hz);
  while (!g_stop) {
    drainCraneInput();
    applyDeliveredCommands();
    drainFeedback();
    sendControlDatagram();
    loop_count++;
    usleep(period_us);
  }

  printf("\ncm4_sim 終了: %llu 周期送出\n", static_cast<unsigned long long>(loop_count));
  if (ff_rejected_count > 0) {
    printf("terminal_velocity 未設定として無視した回数: %llu\n", static_cast<unsigned long long>(ff_rejected_count));
  }
  if (degrader.enabled()) {
    // seed と受信パケット数が同じなら必ず同じ行になる。--seed の再現性検証に使う。
    printf("rx-degrader: seed=%u pushed=%llu dropped=%llu decision-hash=0x%016llx\n", opt.seed,
      static_cast<unsigned long long>(degrader.pushed()), static_cast<unsigned long long>(degrader.dropped()),
      static_cast<unsigned long long>(degrader.decisionHash()));
  }
  fflush(stdout);

  close(in_sock);
  close(out_sock);
  if (relay_sock >= 0) close(relay_sock);
  for (int id : opt.robot_ids) close(robots[id].feedback_sock);
  return 0;
}
