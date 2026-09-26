// このファイルは CM4 上で crane からの AI 制御 UDP とローカルカメラ UDP を受け取り、
// STM32(G474) へ送る 72 バイト UART パケットを生成・送信するブリッジを担当する。
//
// 経路は 2 つあり、受信パケットの CONTROL_MODE で切り替わる。
//
//   mode 4 (POSITION_TARGET_WITH_TERMINAL_VELOCITY):
//       CM4 が位置制御ループを閉じ、mode 3 (POLAR_VELOCITY_TARGET) へ変換して送る。
//       制御則は cm4/control/position_controller.cpp（cm4_sim.out と同一ソース）。
//       ロボットの現在位置は G474 feedback の byte 44..51 を使う。feedback は
//       robot_feedback.out が UART から読んで 127.0.0.1:(50000+機体番号) へ
//       loopback unicast したものを受ける（/dev/serial0 の読み手は増やさない）。
//
//   mode 3 (POLAR_VELOCITY_TARGET) または --passthrough:
//       従来どおり 64 バイトをそのまま転送する。旧構成との A/B 比較用。
//
// 2 つの経路は意図的に統合していない。passthrough は check_counter が crane 由来
// なので「crane の check_counter が変化したときだけ送る」既存のゲートがそのまま
// 正しい（crane 断で G474 の connected_ai が false になるのが旧構成の期待挙動）。
// 位置制御パスは CM4 が check_counter を採番するのでそのゲートが成立せず、
// 時間ベースのレート（--tx-rate-hz）で送る。
#include <arpa/inet.h>
#include <fcntl.h>
#include <ifaddrs.h>
#include <net/if.h>
#include <netinet/in.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <signal.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

#include <boost/array.hpp>
#include <boost/asio.hpp>
#include <cmath>
#include <cstring>
#include <string>
#include <vector>

#include "../control/position_controller.h"
#include "config_packet.h"
#include "robot_command_ops.h"
#include "robot_feedback_packet.h"
#include "robot_packet.h"

// CM4のprimary UARTを示す安定名。現在はPL011 (/dev/ttyAMA0) に割り当てる。
// --serial-port で上書きできる（ホストPCでの疑似端末テスト用）。
#define DEFAULT_SERIAL_PORT "/dev/serial0"

constexpr int AI_CMD_V2_SIZE = 64;
constexpr int AI_CMD_V2_ROBOT_NUM = 11;
constexpr int AI_CMD_V2_PACKET_SIZE = AI_CMD_V2_SIZE + 1;  // robot_id 1B + コマンド 64B
constexpr int CAM_BUF_SIZE = 7;                                      // camera 7 + ck1
constexpr int UART_PACKET_SIZE = AI_CMD_V2_SIZE + CAM_BUF_SIZE + 1;  // local cam + ck
constexpr long long LOCAL_CAMERA_TIMEOUT_MS = 100;

// G474 feedback パケット (robot_feedback.out が loopback unicast で渡してくる) の
// レイアウトと位置の取り出しは robot_feedback_packet.h が正本。

// 位置制御パスの既定 UART 送信レート [Hz]。
//
// 500 Hz (G474 メインループ相当、UART 占有率 36%) を既定にはしない。9 倍の UART
// 負荷増を ST-Link での ORE/FE/NE/PE カウンタ確認なしに投入しないため。
// 100 Hz は 720us x 100 = 7.2% で現行（crane レート追随、約 55Hz = 4%）の約 2 倍に
// とどまり、かつ crane 断から 10ms 以内に停止指令を G474 へ届けられる。
// 500 Hz は --tx-rate-hz 500 で opt-in する。詳細は doc/overview.md。
constexpr int DEFAULT_TX_RATE_HZ = 100;

// This optional diagnostic trace is bounded so it cannot grow memory indefinitely.
constexpr size_t TIMING_TRACE_MAX_RECORDS = 100000;

#ifndef SO_RXQ_OVFL
#define SO_RXQ_OVFL 40
#endif

struct TimingTraceRecord
{
  const char * event;
  uint32_t sequence;
  uint8_t counter;
  bool has_sequence;
  bool valid_self;
  bool adopted;
  int64_t realtime_ns;
  int64_t kernel_ns;
  int64_t monotonic_ns;
  int64_t write_end_ns;
  size_t written;
  uint32_t socket_drop_total;
};

struct TimingTrace
{
  FILE * file = nullptr;
  std::vector<TimingTraceRecord> records;
  int64_t deadline_ns = 0;
  uint64_t record_overflow = 0;
  uint32_t socket_drop_total = 0;
  bool enabled = false;
};

volatile sig_atomic_t g_stop_requested = 0;

void requestStop(int) { g_stop_requested = 1; }

int64_t clockNs(clockid_t id)
{
  timespec ts = {};
  clock_gettime(id, &ts);
  return (int64_t)ts.tv_sec * 1000000000LL + ts.tv_nsec;
}

void appendTimingRecord(TimingTrace * trace, const TimingTraceRecord & record)
{
  if (!trace->enabled) return;
  if (trace->records.size() < TIMING_TRACE_MAX_RECORDS) {
    trace->records.push_back(record);
  } else {
    trace->record_overflow++;
  }
}

void finishTimingTrace(TimingTrace * trace)
{
  if (!trace->enabled) return;
  fprintf(trace->file,
    "event,sequence,counter,valid_self,adopted,realtime_ns,kernel_ns,monotonic_ns,write_end_ns,written,socket_drop_total,trace_record_overflow\n");
  for (const TimingTraceRecord & r : trace->records) {
    fprintf(trace->file, "%s,", r.event);
    if (r.has_sequence) fprintf(trace->file, "%u", r.sequence);
    fprintf(trace->file, ",%u,%d,%d,%lld,%lld,%lld,%lld,%zu,%u,%llu\n",
      r.counter, r.valid_self, r.adopted, (long long)r.realtime_ns,
      (long long)r.kernel_ns, (long long)r.monotonic_ns,
      (long long)r.write_end_ns, r.written, r.socket_drop_total,
      (unsigned long long)trace->record_overflow);
  }
  const bool failed = fflush(trace->file) != 0;
  if (fclose(trace->file) != 0 || failed) perror("timing trace write");
  trace->file = nullptr;
  trace->enabled = false;
  printf("timing trace: %zu records, %llu records omitted\n", trace->records.size(),
    (unsigned long long)trace->record_overflow);
}

typedef struct
{
  int16_t pos_xy[2], radius;
  uint8_t fps;
} camera_t;

// wlan0 の最終オクテット - 100 をロボット ID とする。
// 決定できないときは 0 ではなく -1 を返すこと。位置制御では ID が feedback の
// bind ポート (50000+100+id) も決めるので、黙って 0 に落ちると「自分の G474 へ
// 送りながら 0 号機の feedback で位置ループを閉じる」機体跨ぎの制御になる。
int get_machine_id()
{
  ifaddrs * ifaddr = nullptr;
  if (getifaddrs(&ifaddr) == -1) {
    return -1;  // エラー
  }

  int y_value = -1;

  for (auto * ifa = ifaddr; ifa != nullptr; ifa = ifa->ifa_next) {
    if (!ifa || !ifa->ifa_addr) continue;
    if (std::strcmp(ifa->ifa_name, "wlan0") != 0) continue;
    if (ifa->ifa_addr->sa_family != AF_INET) continue;

    auto * sa = reinterpret_cast<sockaddr_in *>(ifa->ifa_addr);

    uint32_t ip = ntohl(sa->sin_addr.s_addr);

    // /24想定なら最後のオクテット
    y_value = ip & 0xFF;

    break;
  }

  freeifaddrs(ifaddr);
  if (y_value > 100) {
    return y_value - 100;
  }
  return -1;
}

// 問い合わせのあったオプション名をそのまま「既知オプション」の正本にする。
// 別に一覧表を持つと、オプションを足したときに片方だけ更新して、正しい指定を
// 黙って弾く / 打ち間違いを黙って通す、のどちらかの事故になる。
std::vector<std::string> g_value_options;  // 値を 1 つ取るもの
std::vector<std::string> g_flag_options;   // 値を取らないもの
bool g_option_error = false;

bool isKnown(const std::vector<std::string> & names, const char * arg)
{
  for (const std::string & n : names) {
    if (n == arg) return true;
  }
  return false;
}

// コマンドライン引数の走査。値の欠落時の扱いを 1 箇所に閉じる。
//
// --ai-cmd-port / --local-cam-port / --robot-id などはホスト PC でのテスト用。
// 既定値は実機構成 (AI 指令 12345 / ローカルカメラ 8890)。
const char * getRawOption(int argc, char * argv[], const char * name)
{
  if (!isKnown(g_value_options, name)) g_value_options.push_back(name);
  for (int i = 1; i < argc; ++i) {
    if (strcmp(argv[i], name) != 0) continue;
    if (i + 1 < argc) return argv[i + 1];
    // 既定値で続行しない。--kp / --command-timeout-ms などの制御定数がここに
    // 載っているので、値を書き忘れたまま既定ゲインで走らせてはならない。
    fprintf(stderr, "%s には引数が必要です。\n", name);
    g_option_error = true;
    return nullptr;
  }
  return nullptr;
}

// 既知オプションの正本 (g_value_options / g_flag_options) が出そろったあとに
// 1 度だけ呼ぶ。打ち間違いを黙って既定値で走らせないための最後の関門。
bool validateOptions(int argc, char * argv[])
{
  bool ok = !g_option_error;
  for (int i = 1; i < argc; ++i) {
    if (isKnown(g_value_options, argv[i])) {
      ++i;  // その値は消費済み
      continue;
    }
    if (isKnown(g_flag_options, argv[i])) continue;
    fprintf(stderr, "不明なオプションです: %s\n", argv[i]);
    ok = false;
  }
  if (!ok) fprintf(stderr, "  -h で全オプションを表示します。\n");
  return ok;
}

int getIntOption(int argc, char * argv[], const char * name, int default_value)
{
  const char * raw = getRawOption(argc, argv, name);
  return raw ? std::stoi(raw) : default_value;
}

float getFloatOption(int argc, char * argv[], const char * name, float default_value)
{
  const char * raw = getRawOption(argc, argv, name);
  return raw ? std::stof(raw) : default_value;
}

const char * getStringOption(int argc, char * argv[], const char * name, const char * default_value)
{
  const char * raw = getRawOption(argc, argv, name);
  return raw ? raw : default_value;
}

bool hasFlag(int argc, char * argv[], const char * name)
{
  if (!isKnown(g_flag_options, name)) g_flag_options.push_back(name);
  for (int i = 1; i < argc; ++i) {
    if (strcmp(argv[i], name) == 0) {
      return true;
    }
  }
  return false;
}

bool isDebugMode(int argc, char * argv[]) { return hasFlag(argc, argv, "--debug"); }

void pritBinData(char buf[])
{
  for (int i = 0; i < UART_PACKET_SIZE; i++) {
    printf("0x%02x ", (uint8_t)buf[i]);
  }
  printf("\n");
}

void printParcedData(char buf[])
{
  RobotCommandSerializedV2 cmd_buf;
  memcpy(cmd_buf.data, buf, sizeof(cmd_buf));
  RobotCommandV2 cmd_v2 = RobotCommandSerializedV2_deserialize(&cmd_buf);
  if (cmd_v2.stop_emergency) {
    printf("STOP ");
  } else {
    printf("MOVE ");
  }

  printf("check %3d vision %d time %5d ", cmd_v2.check_counter, cmd_v2.is_vision_available, cmd_v2.elapsed_time_ms_since_last_vision);

  printf("VisionX %+6.2f Y %+6.2f ", cmd_v2.vision_global_pos[0], cmd_v2.vision_global_pos[1]);
  printf("theta %+6.1f ", cmd_v2.vision_global_theta * 180 / M_PI);
  printf("elt %4d ", cmd_v2.elapsed_time_ms_since_last_vision);
  printf("Ltcy %3d ", cmd_v2.latency_time_ms);

  printf("TarTheta %+6.2f ", cmd_v2.target_global_theta);
  printf("AccLmt %4.2f VelLmt %4.2f OmgLmt %4.1f ", cmd_v2.acceleration_limit, cmd_v2.linear_velocity_limit, cmd_v2.angular_velocity_limit);

  // CONTROL_MODE_ARGS は union なので、mode を見ずに復号してはならない。
  // 位置制御のデバッグでは「今どちらの経路を通っているか」が最初の手掛かりになる。
  printf("mode %d ", (int)cmd_v2.control_mode);
  switch (cmd_v2.control_mode) {
    case POLAR_VELOCITY_TARGET_MODE:
      printf("vel r %+5.2f th %+6.2f ", cmd_v2.mode_args.polar_velocity.target_global_velocity_r, cmd_v2.mode_args.polar_velocity.target_global_velocity_theta);
      break;
    case POSITION_TARGET_WITH_TERMINAL_VELOCITY_MODE:
      printf("tarPos %+6.2f %+6.2f termV %+5.2f (%+5.2f %+5.2f) ", cmd_v2.target_global_pos[0], cmd_v2.target_global_pos[1], cmd_v2.terminal_velocity,
        cmd_v2.mode_args.position_target.terminal_velocity_x, cmd_v2.mode_args.position_target.terminal_velocity_y);
      break;
    default:
      printf("UNKNOWN-MODE ");
      break;
  }

  printf("dri %+4.2f ", cmd_v2.dribble_power);
  if (cmd_v2.enable_chip) {
    printf("chip %3.2f ", cmd_v2.kick_power);
  } else {
    printf("stlt %3.2f ", cmd_v2.kick_power);
  }

  // robot_packet.h は範囲外クランプ時に警告を出さずカウンタを回すだけなので、ここで可視化する。
  const uint32_t clamp_count = *robotPacketClampCount();
  if (clamp_count > 0) {
    printf("clamp %u ", clamp_count);
  }

  printf("\n");
}

long long get_current_time_ms()
{
  struct timespec ts;
  clock_gettime(CLOCK_REALTIME, &ts);                // 現在の時刻を取得
  return ts.tv_sec * 1000LL + ts.tv_nsec / 1000000;  // 秒をミリ秒に変換し、ナノ秒をミリ秒に変換して加算
}

uint8_t calc_check_sum(const char * buf, int buf_size)
{
  uint32_t data_ck = 0;
  for (int i = 0; i < buf_size - 1; i++) {
    data_ck += (uint8_t)buf[i];
  }
  return data_ck & 0xFF;
}

void printUsage()
{
  printf(
    "usage: ai_cmd_v2.out [options]\n"
    "  -s <bps>                    UART ボーレート (既定 1000000)\n"
    "  --serial-port <path>        UART デバイス (既定 %s)\n"
    "  --robot-id <n>              ロボット ID を明示指定 (既定: wlan0 の最終オクテット-100)\n"
    "  --ai-cmd-port <port>        crane からの 65B 受信ポート (既定 12345)\n"
    "  --local-cam-port <port>     ローカルカメラ受信ポート (既定 8890)\n"
    "  --feedback-port <port>      G474 feedback の loopback 受信ポート (既定 50000+100+id)\n"
    "  --config-port <port>        crane からの位置制御設定パケット受信ポート (既定 %d)\n"
    "  --tx-rate-hz <hz>           位置制御パスの UART 送信レート (既定 %d)\n"
    "  --timing-trace <path>       UDP/UART timing CSV (新規ファイルのみ)\n"
    "  --timing-seconds <seconds>  timing trace 記録時間 (既定 30 秒)\n"
    "  --passthrough               mode 4 でも位置制御せず素通しする (A/B 比較用)\n"
    "  --kp / --decel / --tolerance                位置制御のゲイン・許容誤差\n"
    "  --command-timeout-ms / --feedback-timeout-ms  安全停止までの無通信時間\n"
    "  --debug                     UART へ送らず 72 バイトを 16 進表示する\n"
    "  -h, --help                  この表示\n",
    DEFAULT_SERIAL_PORT, orion::kDefaultConfigPort, DEFAULT_TX_RATE_HZ);
}

int main(int argc, char * argv[])
{
  // stdout が端末でないとき (docker のログ、systemd の journal、テストのパイプ)
  // 既定はブロックバッファリングになる。異常時に SIGTERM で落とされると、その
  // 直前の数十行がバッファごと消える。現地で一番読みたいログが一番消えやすい
  // ので、行バッファへ固定する。呼び出し側の stdbuf -oL に頼らない。
  //
  // stderr は触らない。glibc の既定が「バッファ無し」で、行バッファより強い。
  setvbuf(stdout, nullptr, _IOLBF, 0);

  if (hasFlag(argc, argv, "-h") || hasFlag(argc, argv, "--help")) {
    printUsage();
    return 0;
  }

  printf("start!! foward ai cmd V2 (multi cast packet), arg : %d\n", argc);

  int uart_baudrate = getIntOption(argc, argv, "-s", 1000000);
  const char * serial_port_path = getStringOption(argc, argv, "--serial-port", DEFAULT_SERIAL_PORT);
  int ai_cmd_port = getIntOption(argc, argv, "--ai-cmd-port", 12345);
  int local_cam_port = getIntOption(argc, argv, "--local-cam-port", 8890);
  bool debug_mode_enabled = isDebugMode(argc, argv);
  bool passthrough_forced = hasFlag(argc, argv, "--passthrough");
  int tx_rate_hz = getIntOption(argc, argv, "--tx-rate-hz", DEFAULT_TX_RATE_HZ);
  const char * timing_trace_path = getStringOption(argc, argv, "--timing-trace", nullptr);
  int timing_seconds = getIntOption(argc, argv, "--timing-seconds", 30);
  if (tx_rate_hz <= 0) {
    fprintf(stderr, "--tx-rate-hz は 1 以上にしてください (指定値 %d)\n", tx_rate_hz);
    return 1;
  }
  if (timing_trace_path && timing_seconds <= 0) {
    fprintf(stderr, "--timing-seconds は 1 以上にしてください\n");
    return 1;
  }
  const long long tx_period_ms = (1000 + tx_rate_hz - 1) / tx_rate_hz;

  int machine_id = getIntOption(argc, argv, "--robot-id", -1);
  const bool robot_id_explicit = (machine_id >= 0);
  if (!robot_id_explicit) {
    machine_id = get_machine_id();
  }
  // ID が決まらないまま 0 として動くと、位置制御では 0 号機の feedback で
  // 自分の G474 を回すことになる。黙って続けず落とす。
  if (machine_id < 0 || machine_id >= AI_CMD_V2_ROBOT_NUM) {
    fprintf(stderr,
      "ロボット ID を決定できません (wlan0 の IPv4 最終オクテットが 101..111 である必要があります)。\n"
      "  wlan0 が未起動なら起動を待ってください。テスト時は --robot-id <n> で明示指定できます。\n");
    return 1;
  }

  // robot_feedback.out は 50000 + 機体番号 (= 50000 + 100 + id) へ loopback unicast する。
  int feedback_port = getIntOption(argc, argv, "--feedback-port", 50000 + 100 + machine_id);

  orion::PositionControllerConfig control_config;
  control_config.position_gain = getFloatOption(argc, argv, "--kp", control_config.position_gain);
  control_config.deceleration = getFloatOption(argc, argv, "--decel", control_config.deceleration);
  control_config.position_tolerance = getFloatOption(argc, argv, "--tolerance", control_config.position_tolerance);
  control_config.command_timeout_ms = (uint32_t)getIntOption(argc, argv, "--command-timeout-ms", (int)control_config.command_timeout_ms);
  control_config.feedback_timeout_ms = (uint32_t)getIntOption(argc, argv, "--feedback-timeout-ms", (int)control_config.feedback_timeout_ms);
  // --kp / --decel / --tolerance は起動時の初期値。crane が設定パケットを送ってくると
  // 稼働中に上書きされる (config_packet.h)。タイムアウト 2 つは遠隔から変えられない。
  int config_port = getIntOption(argc, argv, "--config-port", orion::kDefaultConfigPort);

  // 全オプションの問い合わせが終わったここで検証する。
  if (!validateOptions(argc, argv)) return 1;

  printf("debug mode : %d\n", debug_mode_enabled);
  printf("UART %s %d bps\n", serial_port_path, uart_baudrate);
  printf("ID %d%s\n", machine_id, robot_id_explicit ? " (--robot-id 指定)" : " (wlan0 から検出)");
  printf("AI cmd UDP %d / local cam UDP %d / feedback UDP 127.0.0.1:%d / config UDP %d\n", ai_cmd_port, local_cam_port, feedback_port, config_port);
  printf("passthrough %d / tx rate %d Hz (%lld ms)\n", passthrough_forced, tx_rate_hz, tx_period_ms);
  printf("control kp %.2f decel %.2f tol %.3f cmd-timeout %u ms fb-timeout %u ms\n", control_config.position_gain, control_config.deceleration,
    control_config.position_tolerance, control_config.command_timeout_ms, control_config.feedback_timeout_ms);

  TimingTrace timing_trace;
  if (timing_trace_path) {
    const int trace_fd = open(timing_trace_path, O_WRONLY | O_CREAT | O_EXCL, 0644);
    if (trace_fd < 0) {
      perror("open(--timing-trace)");
      return 1;
    }
    timing_trace.file = fdopen(trace_fd, "w");
    if (!timing_trace.file) {
      perror("fdopen(--timing-trace)");
      close(trace_fd);
      return 1;
    }
    timing_trace.records.reserve(TIMING_TRACE_MAX_RECORDS);
    timing_trace.enabled = true;
    signal(SIGINT, requestStop);
    signal(SIGTERM, requestStop);
    printf("timing trace %s / %d seconds / max %zu records\n", timing_trace_path,
      timing_seconds, TIMING_TRACE_MAX_RECORDS);
  }

  int local_cam_sock, ai_cmd_sock, feedback_sock;
  struct sockaddr_in local_cam_addr;
  struct sockaddr_in ai_cmd_addr;
  struct sockaddr_in feedback_addr;

  char local_cam_buf[CAM_BUF_SIZE] = {};
  char latest_local_cam_buf[CAM_BUF_SIZE] = {};
  char ai_cmd_buf[AI_CMD_V2_PACKET_SIZE] = {};
  char feedback_buf[FEEDBACK_PACKET_SIZE] = {};

  // 自機宛ての最新コマンド 64 バイト。uart_tx_buf とは分けて持つ。
  // uart_tx_buf は header / camera / checksum で上書きされるため、次周期の
  // 入力として使い回すと位置制御の入力が汚れる。
  char latest_cmd[AI_CMD_V2_SIZE] = {};
  char uart_tx_buf[UART_PACKET_SIZE] = {};

  local_cam_sock = socket(AF_INET, SOCK_DGRAM, 0);
  ai_cmd_sock = socket(AF_INET, SOCK_DGRAM, 0);
  feedback_sock = socket(AF_INET, SOCK_DGRAM, 0);
  if (local_cam_sock < 0 || ai_cmd_sock < 0 || feedback_sock < 0) {
    perror("socket");
    return 1;
  }

  local_cam_addr.sin_family = AF_INET;
  local_cam_addr.sin_port = htons(local_cam_port);
  local_cam_addr.sin_addr.s_addr = INADDR_ANY;

  if (bind(local_cam_sock, (struct sockaddr *)&local_cam_addr, sizeof(local_cam_addr)) != 0) {
    perror("bind(local_cam_sock)");
    return 1;
  }

  ai_cmd_addr.sin_family = AF_INET;
  ai_cmd_addr.sin_port = htons(ai_cmd_port);
  ai_cmd_addr.sin_addr.s_addr = INADDR_ANY;

  if (bind(ai_cmd_sock, (struct sockaddr *)&ai_cmd_addr, sizeof(ai_cmd_addr)) != 0) {
    perror("bind(ai_cmd_sock)");
    return 1;
  }

  if (timing_trace.enabled) {
    int timestamp_enabled = 1;
    if (setsockopt(ai_cmd_sock, SOL_SOCKET, SO_TIMESTAMPNS, &timestamp_enabled,
          sizeof(timestamp_enabled)) != 0 ||
      setsockopt(ai_cmd_sock, SOL_SOCKET, SO_RXQ_OVFL, &timestamp_enabled,
          sizeof(timestamp_enabled)) != 0) {
      perror("setsockopt(timing trace)");
      finishTimingTrace(&timing_trace);
      return 1;
    }
  }

  // INADDR_ANY ではなく 127.0.0.1 に bind する。
  // feedback ポート番号は multicast 再配信 (224.5.20.x:50000+機体番号) と同じなので、
  // INADDR_ANY で bind すると構成によっては再配信されたコピーまで受け取り、
  // 位置制御の入力に自分の再配信が混ざる。cm4_sim.cpp と同じ扱いに揃えてある。
  feedback_addr.sin_family = AF_INET;
  feedback_addr.sin_port = htons(feedback_port);
  feedback_addr.sin_addr.s_addr = inet_addr("127.0.0.1");

  if (bind(feedback_sock, (struct sockaddr *)&feedback_addr, sizeof(feedback_addr)) != 0) {
    perror("bind(feedback_sock)");
    fprintf(stderr, "  robot_feedback.out が既に同じポートを bind していないか確認してください\n");
    return 1;
  }

  int val = 1;
  ioctl(local_cam_sock, FIONBIO, &val);
  ioctl(ai_cmd_sock, FIONBIO, &val);
  ioctl(feedback_sock, FIONBIO, &val);

  // 設定パケットは自機IP宛てのユニキャストで受ける。診断用loopbackも受けられるようINADDR_ANYにbindする。
  const int config_sock = orion::openConfigSocket(config_port);
  if (config_sock < 0) return 1;

  boost::asio::io_service io;
  boost::asio::serial_port serial(io, serial_port_path);
  serial.set_option(boost::asio::serial_port_base::baud_rate(uart_baudrate));
  serial.set_option(boost::asio::serial_port_base::character_size(8));
  serial.set_option(boost::asio::serial_port_base::parity(boost::asio::serial_port_base::parity::none));
  serial.set_option(boost::asio::serial_port_base::stop_bits(boost::asio::serial_port_base::stop_bits::one));

  char pre_check_cnt = 0;
  uint8_t tx_check_counter = 0;
  long long last_tx_time_ms = 0;
  camera_t camera;

  long long pre_time = get_current_time_ms();
  long long diff_time = 0;
  long long last_cam_time = 0;

  bool has_command = false;
  long long command_time_ms = 0;
  bool has_feedback = false;
  long long feedback_time_ms = 0;
  float feedback_pos[2] = {0.f, 0.f};

  uint64_t rx_discard_count = 0;
  uint64_t feedback_discard_count = 0;
  orion::ConfigReceiver config_receiver;
  // 位置制御の積分・微分の状態。実機は 1 プロセス 1 台なのでここに 1 つ持つ。
  orion::PositionControllerState control_state;
  orion::PositionControllerReason pre_reason = orion::PositionControllerReason::FeedbackStale;
  bool pre_ff_rejected = false;
  // 「最後に表示したときの crane 由来 check_counter」。pre_check_cnt とは別に持つ。
  // pre_check_cnt は 1 kHz の毎周期で更新されるので、100 Hz の送信ゲートが開く頃には
  // 必ず最新値と一致してしまい、表示条件として使うと位置制御パスがほぼ無言になる。
  int last_logged_check_cnt = -1;

  int64_t latest_rx_kernel_ns = 0;
  uint32_t latest_rx_sequence = 0;
  bool latest_rx_has_sequence = false;
  if (timing_trace.enabled) {
    timing_trace.deadline_ns = clockNs(CLOCK_MONOTONIC) +
      (int64_t)timing_seconds * 1000000000LL;
  }

  while (!g_stop_requested) {
    if (timing_trace.enabled && clockNs(CLOCK_MONOTONIC) >= timing_trace.deadline_ns) {
      finishTimingTrace(&timing_trace);
    }
    const long long now_ms = get_current_time_ms();

    // --- crane からの位置制御設定 (28 バイト) ---
    // 受信・検証・適用・ログは cm4_sim と共有する (config_packet.h)。
    // 途絶しても最後の値を保持する。ゲインは安全信号ではないので、
    // 設定が届かないことを理由に既定値へ戻すとかえって挙動が飛ぶ。
    orion::drainConfigSocket(config_sock, &machine_id, 1, &control_config, &config_receiver);

    // --- crane からの自機宛て65バイト ---
    // データグラム長とrobot_idを検査し、自機の最新コマンドだけを採用する。
    size_t last_trace_adopt_index = SIZE_MAX;
    while (1) {
      // MSG_TRUNCを付けると、65バイトより長いデータグラムも実長で拒否できる。
      iovec iov = {};
      char control[CMSG_SPACE(sizeof(timespec)) + CMSG_SPACE(sizeof(uint32_t))] = {};
      msghdr message = {};
      int cmd_n;
      if (timing_trace.enabled) {
        iov.iov_base = ai_cmd_buf;
        iov.iov_len = sizeof(ai_cmd_buf);
        message.msg_iov = &iov;
        message.msg_iovlen = 1;
        message.msg_control = control;
        message.msg_controllen = sizeof(control);
        cmd_n = recvmsg(ai_cmd_sock, &message, MSG_TRUNC);
      } else {
        // Preserve the normal path exactly when tracing is not requested.
        cmd_n = recv(ai_cmd_sock, ai_cmd_buf, sizeof(ai_cmd_buf), MSG_TRUNC);
      }
      // The first userspace timestamp is taken immediately after recvmsg returns.
      const int64_t rx_monotonic_ns = timing_trace.enabled ? clockNs(CLOCK_MONOTONIC) : 0;
      if (cmd_n < 0) break;  // EAGAIN: 受信キューが空
      const int64_t rx_realtime_ns = timing_trace.enabled ? clockNs(CLOCK_REALTIME) : 0;
      int64_t rx_kernel_ns = 0;
      if (timing_trace.enabled) {
        for (cmsghdr * cmsg = CMSG_FIRSTHDR(&message); cmsg;
             cmsg = CMSG_NXTHDR(&message, cmsg)) {
          if (cmsg->cmsg_level != SOL_SOCKET) continue;
          if (cmsg->cmsg_type == SO_TIMESTAMPNS &&
            cmsg->cmsg_len >= CMSG_LEN(sizeof(timespec))) {
            timespec stamp = {};
            memcpy(&stamp, CMSG_DATA(cmsg), sizeof(stamp));
            rx_kernel_ns = (int64_t)stamp.tv_sec * 1000000000LL + stamp.tv_nsec;
          } else if (cmsg->cmsg_type == SO_RXQ_OVFL &&
            cmsg->cmsg_len >= CMSG_LEN(sizeof(uint32_t))) {
            memcpy(&timing_trace.socket_drop_total, CMSG_DATA(cmsg), sizeof(uint32_t));
          }
        }
        if (message.msg_flags & MSG_CTRUNC) rx_kernel_ns = 0;
      }
      bool trace_valid_self = false;
      bool trace_has_sequence = false;
      uint32_t trace_sequence = 0;
      uint8_t trace_counter = 0;
      if (cmd_n != AI_CMD_V2_PACKET_SIZE) {
        rx_discard_count++;
        appendTimingRecord(&timing_trace, {"rx", 0, 0, false, false, false,
          rx_realtime_ns, rx_kernel_ns, rx_monotonic_ns, 0, 0,
          timing_trace.socket_drop_total});
        continue;
      }
      if ((uint8_t)ai_cmd_buf[0] == (uint8_t)machine_id &&
        !commandSlotIsEmpty((const uint8_t *)&ai_cmd_buf[1])) {
        const uint8_t * command = (const uint8_t *)&ai_cmd_buf[1];
        if (timing_trace.enabled) {
          trace_valid_self = true;
          trace_counter = command[CHECK_COUNTER];
          trace_has_sequence = memcmp(command + 38, "TPRB", 4) == 0;
          if (trace_has_sequence) memcpy(&trace_sequence, command + 42, sizeof(trace_sequence));
        }
        memcpy(latest_cmd, command, AI_CMD_V2_SIZE);
        has_command = true;
        command_time_ms = now_ms;
        if (timing_trace.enabled) {
          latest_rx_kernel_ns = rx_kernel_ns;
          latest_rx_sequence = trace_sequence;
          latest_rx_has_sequence = trace_has_sequence;
        }
      }
      if (trace_valid_self) last_trace_adopt_index = SIZE_MAX;
      const size_t trace_record_index = timing_trace.records.size();
      appendTimingRecord(&timing_trace,
        {"rx", trace_sequence, trace_counter, trace_has_sequence, trace_valid_self,
          false, rx_realtime_ns, rx_kernel_ns, rx_monotonic_ns, 0, 0,
          timing_trace.socket_drop_total});
      if (trace_valid_self && timing_trace.records.size() > trace_record_index) {
        last_trace_adopt_index = trace_record_index;
      }
    }
    // Only the newest valid command surviving this drain is adopted by the control loop.
    if (last_trace_adopt_index != SIZE_MAX) {
      timing_trace.records[last_trace_adopt_index].adopted = true;
    }

    // --- G474 feedback (robot_feedback.out からの loopback unicast) ---
    while (1) {
      const int fb_n = recv(feedback_sock, feedback_buf, sizeof(feedback_buf), MSG_TRUNC);
      if (fb_n < 0) break;
      if (!decodeFeedbackPosition(feedback_buf, (size_t)fb_n, feedback_pos)) {
        feedback_discard_count++;
        continue;
      }
      has_feedback = true;
      feedback_time_ms = now_ms;
    }

    // --- ローカルカメラ ---
    const int cam_n = recv(local_cam_sock, local_cam_buf, sizeof(local_cam_buf), 0);
    if (cam_n == CAM_BUF_SIZE) {
      memcpy(latest_local_cam_buf, local_cam_buf, CAM_BUF_SIZE);
      diff_time = now_ms - pre_time;
      pre_time = now_ms;
      last_cam_time = now_ms;
    }

    // --- 送信パケットの組み立て ---
    memcpy(uart_tx_buf, latest_cmd, AI_CMD_V2_SIZE);
    uart_tx_buf[0] = 254;  //パケットヘッダ

    // mode 4 を G474 へ流してはならない。G474 は mode 4 を解釈できず、
    // CONTROL_MODE_ARGS を union として mode 3 の (r, theta) に読み違える。
    const uint8_t rx_control_mode = (uint8_t)latest_cmd[CONTROL_MODE];
    const bool position_control_active = has_command && !passthrough_forced && rx_control_mode == POSITION_TARGET_WITH_TERMINAL_VELOCITY_MODE;

    orion::PositionControllerOutput control_out;
    if (position_control_active) {
      RobotCommandSerializedV2 serialized;
      memcpy(serialized.data, latest_cmd, AI_CMD_V2_SIZE);
      const RobotCommandV2 cmd = RobotCommandSerializedV2_deserialize(&serialized);

      orion::PositionControllerInput in;
      fillCommandFields(&in, cmd);
      in.has_command = has_command;
      in.command_time_ms = (uint64_t)command_time_ms;
      // crane の vision_global_pos ではなく G474 feedback の位置で閉じる。
      // crane 由来の位置で閉じると、今回ループの外へ出そうとしている無線経路が
      // そのままループの内側に戻ってしまう。
      in.current_pos[0] = feedback_pos[0];
      in.current_pos[1] = feedback_pos[1];
      in.has_feedback = has_feedback;
      in.feedback_time_ms = (uint64_t)feedback_time_ms;
      in.now_ms = (uint64_t)now_ms;

      control_out = computePositionControl(in, control_config, &control_state);

      // check_counter は CM4 が採番する。G474 の checkConnect2AI() は
      // 「変化していること」だけを見るので、毎送信で変えなければならない。
      // 1 バイトなので 100Hz なら約 2 秒で一巡する。ロス検出用のシーケンス番号には
      // 使えない（doc/control_packet.md）。
      tx_check_counter = nextCheckCounter(tx_check_counter);
      uart_tx_buf[CHECK_COUNTER] = (char)tx_check_counter;

      applyPolarVelocity((uint8_t *)uart_tx_buf, control_out.polar_velocity_r, control_out.polar_velocity_theta);

      if (control_out.stop_emergency) {
        // 古いキック/ドリブル指令を撃ち続けないよう kick・dribble・chip も落とす。
        applySafetyStop((uint8_t *)uart_tx_buf);
      }

      // vision_global_pos (byte 2..5) は crane 由来のまま流す。
      // G474 は vision 融合に使うので、CM4 が feedback 位置を書き戻すと自己帰還になる。
      // （cm4_sim は simulator-cli の 0.5m 照合ゲートを通すため書き換えるが、
      //   実機ではその照合が無いので書き換えない。doc/overview.md に記載）
    } else {
      // 素通し (mode 3) や passthrough 強制の間は制御器が呼ばれない。状態を残すと、
      // mode 4 へ戻った 1 周期目に素通しだった間の古い積分と古い位置が効く。
      orion::resetPositionControllerState(&control_state);
    }

    const bool has_recent_camera = last_cam_time > 0 && (now_ms - last_cam_time) <= LOCAL_CAMERA_TIMEOUT_MS;
    if (has_recent_camera) {
      camera.pos_xy[0] = ((uint8_t)latest_local_cam_buf[0] << 8) + (uint8_t)latest_local_cam_buf[1];
      camera.pos_xy[1] = ((uint8_t)latest_local_cam_buf[2] << 8) + (uint8_t)latest_local_cam_buf[3];
      camera.radius = ((uint8_t)latest_local_cam_buf[4] << 8) + (uint8_t)latest_local_cam_buf[5];
      camera.fps = (uint8_t)latest_local_cam_buf[6];
      memcpy(&uart_tx_buf[UART_PACKET_SIZE - CAM_BUF_SIZE - 1], latest_local_cam_buf, CAM_BUF_SIZE);
    } else {
      camera.pos_xy[0] = 0;
      camera.pos_xy[1] = 0;
      camera.radius = 0;
      camera.fps = 0;
      memset(&uart_tx_buf[UART_PACKET_SIZE - CAM_BUF_SIZE - 1], 0, CAM_BUF_SIZE);
    }

    // cksum計算
    uart_tx_buf[UART_PACKET_SIZE - 1] = calc_check_sum(uart_tx_buf, UART_PACKET_SIZE);

    // --- UART 送信ゲート ---
    // 2 つの経路を 1 つのパラメータ化された送信にまとめない。
    // passthrough は crane 由来の check_counter 変化ゲートがそのまま正しく、
    // 位置制御は CM4 採番なのでそのゲートが成立しない（常に変化してしまう）。
    // 停止理由が変わった周期。送信と表示の両方がこれを見る。
    const bool safety_changed = position_control_active && (control_out.reason != pre_reason || control_out.feedforward_rejected != pre_ff_rejected);

    bool do_send = false;
    if (position_control_active) {
      // 安全停止の状態が変わったらレートゲートを待たずに送る。待たせると
      // crane 断の検出から最大 1/tx_rate_hz (既定 10ms) だけ停止指令が遅れ、
      // 「crane 断から command_timeout_ms 以内に止まる」を満たせなくなる。
      do_send = safety_changed || (now_ms - last_tx_time_ms >= tx_period_ms);
    } else if (pre_check_cnt != latest_cmd[CHECK_COUNTER]) {
      do_send = true;
    }

    if (debug_mode_enabled) {
      if (do_send) {
        pritBinData(uart_tx_buf);
        last_tx_time_ms = now_ms;
      }
    } else if (do_send) {
      const int64_t write_realtime_ns = timing_trace.enabled ? clockNs(CLOCK_REALTIME) : 0;
      const int64_t write_begin_ns = timing_trace.enabled ? clockNs(CLOCK_MONOTONIC) : 0;
      const size_t written = serial.write_some(boost::asio::buffer(uart_tx_buf, sizeof(uart_tx_buf)));
      const int64_t write_end_ns = timing_trace.enabled ? clockNs(CLOCK_MONOTONIC) : 0;
      appendTimingRecord(&timing_trace,
        {"tx", latest_rx_sequence, (uint8_t)uart_tx_buf[CHECK_COUNTER],
          latest_rx_has_sequence, true, true, write_realtime_ns,
          latest_rx_kernel_ns, write_begin_ns, write_end_ns, written,
          timing_trace.socket_drop_total});
      // printより先にserial送信
      last_tx_time_ms = now_ms;

      // 位置制御パスは既定 100Hz で送るので、毎回表示するとログが溢れる。
      // crane からの新規コマンドか、停止理由が変わったときだけ出す。
      const bool crane_updated = last_logged_check_cnt != (int)(uint8_t)latest_cmd[CHECK_COUNTER];
      if (!position_control_active || crane_updated || safety_changed) {
        last_logged_check_cnt = (int)(uint8_t)latest_cmd[CHECK_COUNTER];
        printf("cam %+4d %+4d %2d fps(rx)%2d / %3lld / ", camera.pos_xy[0], camera.pos_xy[1], camera.radius, camera.fps, diff_time);
        printf("ck : %3d / ", (uint8_t)uart_tx_buf[UART_PACKET_SIZE - 1]);
        if (position_control_active) {
          printf("POS[%s] fbXY %+6.2f %+6.2f / ", orion::toString(control_out.reason), feedback_pos[0], feedback_pos[1]);
          if (control_out.feedforward_rejected) {
            // 2 バイト固定小数の未設定フィールドは -32.767 として復号される。
            // crane 側のフィールド書き忘れはこの表示でしか気付けない。
            printf("!! terminal_velocity 未設定 (無視して P 制御を継続) / ");
          }
        }
        if (rx_discard_count > 0 || feedback_discard_count > 0) {
          printf("drop cmd %llu fb %llu / ", (unsigned long long)rx_discard_count, (unsigned long long)feedback_discard_count);
        }
        printParcedData(uart_tx_buf);
      }
    }
    pre_reason = control_out.reason;
    pre_ff_rejected = control_out.feedforward_rejected;
    // passthrough のゲートと表示の両方が crane 由来の check_counter を見る。
    pre_check_cnt = latest_cmd[CHECK_COUNTER];

    /* 1kHz */
    usleep(1000);
  }

  finishTimingTrace(&timing_trace);

  close(config_sock);
  close(local_cam_sock);
  close(ai_cmd_sock);
  close(feedback_sock);

  return 0;
}
