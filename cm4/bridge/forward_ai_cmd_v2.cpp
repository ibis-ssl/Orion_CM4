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

#include "../control/position_controller.h"
#include "robot_packet.h"

// CM4のprimary UARTを示す安定名。現在はPL011 (/dev/ttyAMA0) に割り当てる。
// --serial-port で上書きできる（ホストPCでの疑似端末テスト用）。
#define DEFAULT_SERIAL_PORT "/dev/serial0"

constexpr int AI_CMD_V2_SIZE = 64;
constexpr int AI_CMD_V2_ROBOT_NUM = 11;
constexpr int AI_CMD_V2_SLOT_SIZE = AI_CMD_V2_SIZE + 1;  // robot_id 1B + コマンド 64B
constexpr int AI_CMD_V2_PACKET_SIZE = AI_CMD_V2_SLOT_SIZE * AI_CMD_V2_ROBOT_NUM;  // 715B
constexpr int CAM_BUF_SIZE = 7;                                      // camera 7 + ck1
constexpr int UART_PACKET_SIZE = AI_CMD_V2_SIZE + CAM_BUF_SIZE + 1;  // local cam + ck
constexpr long long LOCAL_CAMERA_TIMEOUT_MS = 100;

// G474 feedback パケット。robot_feedback.out が loopback unicast で渡してくる。
constexpr int FEEDBACK_PACKET_SIZE = 128;
constexpr int FEEDBACK_POS_X_OFFSET = 44;  // vision_based_position_x (float LE)
constexpr int FEEDBACK_POS_Y_OFFSET = 48;  // vision_based_position_y (float LE)

// 位置制御パスの既定 UART 送信レート [Hz]。
//
// 500 Hz (G474 メインループ相当、UART 占有率 36%) を既定にはしない。9 倍の UART
// 負荷増を ST-Link での ORE/FE/NE/PE カウンタ確認なしに投入しないため。
// 100 Hz は 720us x 100 = 7.2% で現行（crane レート追随、約 55Hz = 4%）の約 2 倍に
// とどまり、かつ crane 断から 10ms 以内に停止指令を G474 へ届けられる。
// 500 Hz は --tx-rate-hz 500 で opt-in する。詳細は doc/overview.md。
constexpr int DEFAULT_TX_RATE_HZ = 100;

typedef struct
{
  int16_t pos_xy[2], radius;
  uint8_t fps;
} camera_t;

float two_to_float(char data[2]) { return (float)(((uint8_t)data[0] << 8 | (uint8_t)data[1]) - 32767.0) / 32767.0; }
float two_to_int(char data[2]) { return (((uint8_t)data[0] << 8 | (uint8_t)data[1]) - 32767.0); }

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

int getUartBaudrate(int argc, char * argv[])
{
  int speed = 1000000;

  // Parse command line arguments
  for (int i = 1; i < argc; ++i) {
    if (strcmp(argv[i], "-s") == 0) {
      if (i + 1 < argc) {
        speed = std::stoi(argv[++i]);
      } else {
        printf("Error: -s option requires an integer argument.");
      }
    }
  }
  return speed;
}

const char * getSerialPort(int argc, char * argv[])
{
  const char * port = DEFAULT_SERIAL_PORT;

  for (int i = 1; i < argc; ++i) {
    if (strcmp(argv[i], "--serial-port") == 0) {
      if (i + 1 < argc) {
        port = argv[++i];
      } else {
        printf("Error: --serial-port option requires a path argument.");
      }
    }
  }
  return port;
}

// --ai-cmd-port / --local-cam-port はホストPCでのテスト用。
// 既定値は実機構成（AI 指令 12345 / ローカルカメラ 8890）。
int getIntOption(int argc, char * argv[], const char * name, int default_value)
{
  int value = default_value;

  for (int i = 1; i < argc; ++i) {
    if (strcmp(argv[i], name) == 0) {
      if (i + 1 < argc) {
        value = std::stoi(argv[++i]);
      } else {
        printf("Error: %s option requires an integer argument.", name);
      }
    }
  }
  return value;
}

float getFloatOption(int argc, char * argv[], const char * name, float default_value)
{
  float value = default_value;

  for (int i = 1; i < argc; ++i) {
    if (strcmp(argv[i], name) == 0) {
      if (i + 1 < argc) {
        value = std::stof(argv[++i]);
      } else {
        printf("Error: %s option requires a float argument.", name);
      }
    }
  }
  return value;
}

bool hasFlag(int argc, char * argv[], const char * name)
{
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
    "  --ai-cmd-port <port>        crane からの 715B 受信ポート (既定 12345)\n"
    "  --local-cam-port <port>     ローカルカメラ受信ポート (既定 8890)\n"
    "  --feedback-port <port>      G474 feedback の loopback 受信ポート (既定 50000+100+id)\n"
    "  --tx-rate-hz <hz>           位置制御パスの UART 送信レート (既定 %d)\n"
    "  --passthrough               mode 4 でも位置制御せず素通しする (A/B 比較用)\n"
    "  --kp / --decel / --tolerance                位置制御のゲイン・許容誤差\n"
    "  --command-timeout-ms / --feedback-timeout-ms  安全停止までの無通信時間\n"
    "  --debug                     UART へ送らず 72 バイトを 16 進表示する\n"
    "  -h, --help                  この表示\n",
    DEFAULT_SERIAL_PORT, DEFAULT_TX_RATE_HZ);
}

int main(int argc, char * argv[])
{
  if (hasFlag(argc, argv, "-h") || hasFlag(argc, argv, "--help")) {
    printUsage();
    return 0;
  }

  printf("start!! foward ai cmd V2 (multi cast packet), arg : %d\n", argc);

  int uart_baudrate = getUartBaudrate(argc, argv);
  const char * serial_port_path = getSerialPort(argc, argv);
  int ai_cmd_port = getIntOption(argc, argv, "--ai-cmd-port", 12345);
  int local_cam_port = getIntOption(argc, argv, "--local-cam-port", 8890);
  bool debug_mode_enabled = isDebugMode(argc, argv);
  bool passthrough_forced = hasFlag(argc, argv, "--passthrough");
  int tx_rate_hz = getIntOption(argc, argv, "--tx-rate-hz", DEFAULT_TX_RATE_HZ);
  if (tx_rate_hz <= 0) {
    fprintf(stderr, "--tx-rate-hz は 1 以上にしてください (指定値 %d)\n", tx_rate_hz);
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

  printf("debug mode : %d\n", debug_mode_enabled);
  printf("UART %s %d bps\n", serial_port_path, uart_baudrate);
  printf("ID %d%s\n", machine_id, robot_id_explicit ? " (--robot-id 指定)" : " (wlan0 から検出)");
  printf("AI cmd UDP %d / local cam UDP %d / feedback UDP 127.0.0.1:%d\n", ai_cmd_port, local_cam_port, feedback_port);
  printf("passthrough %d / tx rate %d Hz (%lld ms)\n", passthrough_forced, tx_rate_hz, tx_period_ms);
  printf("control kp %.2f decel %.2f tol %.3f cmd-timeout %u ms fb-timeout %u ms\n", control_config.position_gain, control_config.deceleration,
    control_config.position_tolerance, control_config.command_timeout_ms, control_config.feedback_timeout_ms);

  int local_cam_sock, ai_cmd_sock, feedback_sock;
  struct sockaddr_in local_cam_addr;
  struct sockaddr_in ai_cmd_addr;
  struct sockaddr_in feedback_addr;

  char local_cam_buf[CAM_BUF_SIZE] = {};
  char latest_local_cam_buf[CAM_BUF_SIZE] = {};
  char ai_cmd_buf[AI_CMD_V2_PACKET_SIZE] = {};
  char feedback_buf[FEEDBACK_PACKET_SIZE] = {};

  // 自機スロットの最新コマンド 64 バイト。uart_tx_buf とは分けて持つ。
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
  orion::PositionControllerReason pre_reason = orion::PositionControllerReason::FeedbackStale;

  while (1) {
    const long long now_ms = get_current_time_ms();

    // --- crane からの 715 バイト ---
    // 715 バイト以外は捨てる。旧実装は recv の戻り値を見ておらず、短いパケットや
    // 受信失敗 (-1) でも前回バッファのまま回り続けていた。
    while (1) {
      // MSG_TRUNC を付けるとデータグラムの実長が返る。付けないと 716 バイトが
      // 715 バイトに切り詰められて「正常な全ゼロパケット」に化ける。
      const int cmd_n = recv(ai_cmd_sock, ai_cmd_buf, sizeof(ai_cmd_buf), MSG_TRUNC);
      if (cmd_n < 0) break;  // EAGAIN: 受信キューが空
      if (cmd_n != AI_CMD_V2_PACKET_SIZE) {
        rx_discard_count++;
        continue;
      }
      for (int i = 0; i < AI_CMD_V2_ROBOT_NUM; i++) {
        const int offset = i * AI_CMD_V2_SLOT_SIZE;
        if ((uint8_t)ai_cmd_buf[offset] != (uint8_t)machine_id) continue;
        // 空スロット (コマンド 64 バイトが全ゼロ) は指令ではないので採用しない。
        // framework の ibisSlotIsEmpty() と同じ判定で、cm4_sim も同じ扱いをする。
        // 全ゼロを採用してしまうと、位置制御では target_global_pos が
        // (-32.767, -32.767) として復号される。
        bool empty = true;
        for (int b = 0; b < AI_CMD_V2_SIZE; b++) {
          if (ai_cmd_buf[offset + 1 + b] != 0) {
            empty = false;
            break;
          }
        }
        if (empty) continue;
        // コマンドは 64 バイト。旧実装は sizeof(uart_tx_buf) = 72 バイト読んでおり、
        // i == 10 で ai_cmd_buf[651..722] の 8 バイト境界外読み出しになっていた。
        memcpy(latest_cmd, &ai_cmd_buf[offset + 1], AI_CMD_V2_SIZE);
        has_command = true;
        command_time_ms = now_ms;
      }
    }

    // --- G474 feedback (robot_feedback.out からの loopback unicast) ---
    while (1) {
      const int fb_n = recv(feedback_sock, feedback_buf, sizeof(feedback_buf), MSG_TRUNC);
      if (fb_n < 0) break;
      if (fb_n != FEEDBACK_PACKET_SIZE || (uint8_t)feedback_buf[0] != 0xAB || (uint8_t)feedback_buf[1] != 0xEA) {
        feedback_discard_count++;
        continue;
      }
      // byte 44..51 = vision_based_position_x/y [m]。yaw (byte 4..7) は実機が度・
      // シミュレータがラジアンでずれており、制御則も使わないので読まない。
      memcpy(&feedback_pos[0], &feedback_buf[FEEDBACK_POS_X_OFFSET], sizeof(float));
      memcpy(&feedback_pos[1], &feedback_buf[FEEDBACK_POS_Y_OFFSET], sizeof(float));
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
      in.target_global_pos[0] = cmd.target_global_pos[0];
      in.target_global_pos[1] = cmd.target_global_pos[1];
      in.terminal_velocity_xy[0] = cmd.mode_args.position_target.terminal_velocity_x;
      in.terminal_velocity_xy[1] = cmd.mode_args.position_target.terminal_velocity_y;
      in.terminal_velocity = cmd.terminal_velocity;
      in.linear_velocity_limit = cmd.linear_velocity_limit;
      in.stop_emergency = cmd.stop_emergency;
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

      control_out = computePositionControl(in, control_config);

      // check_counter は CM4 が採番する。G474 の checkConnect2AI() は
      // 「変化していること」だけを見るので、毎送信で変えなければならない。
      // 1 バイトなので 100Hz なら約 2 秒で一巡する。ロス検出用のシーケンス番号には
      // 使えない（doc/control_packet.md）。
      tx_check_counter = (tx_check_counter >= 200) ? 0 : (uint8_t)(tx_check_counter + 1);
      uart_tx_buf[CHECK_COUNTER] = (char)tx_check_counter;

      uart_tx_buf[CONTROL_MODE] = (char)POLAR_VELOCITY_TARGET_MODE;
      forward((uint8_t *)&uart_tx_buf[CONTROL_MODE_ARGS + 0], (uint8_t *)&uart_tx_buf[CONTROL_MODE_ARGS + 1], control_out.polar_velocity_r, 32.767);
      forward((uint8_t *)&uart_tx_buf[CONTROL_MODE_ARGS + 2], (uint8_t *)&uart_tx_buf[CONTROL_MODE_ARGS + 3], control_out.polar_velocity_theta, 32.767);

      if (control_out.stop_emergency) {
        uart_tx_buf[FLAGS] = (char)((uint8_t)uart_tx_buf[FLAGS] | (uint8_t)(1u << STOP_EMERGENCY));
        // crane 断・feedback 断のときに古いキック/ドリブル指令を撃ち続けない。
        uart_tx_buf[KICK_POWER] = 0;
        uart_tx_buf[DRIBBLE_POWER] = 0;
        uart_tx_buf[FLAGS] = (char)((uint8_t)uart_tx_buf[FLAGS] & (uint8_t)~(1u << ENABLE_CHIP));
      }

      // vision_global_pos (byte 2..5) は crane 由来のまま流す。
      // G474 は vision 融合に使うので、CM4 が feedback 位置を書き戻すと自己帰還になる。
      // （cm4_sim は simulator-cli の 0.5m 照合ゲートを通すため書き換えるが、
      //   実機ではその照合が無いので書き換えない。doc/overview.md に記載）
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
    bool do_send = false;
    if (position_control_active) {
      if (now_ms - last_tx_time_ms >= tx_period_ms) {
        do_send = true;
      }
    } else if (pre_check_cnt != uart_tx_buf[CHECK_COUNTER]) {
      do_send = true;
    }

    if (debug_mode_enabled) {
      if (do_send) {
        pritBinData(uart_tx_buf);
        last_tx_time_ms = now_ms;
      }
    } else if (do_send) {
      serial.write_some(boost::asio::buffer(uart_tx_buf, sizeof(uart_tx_buf)));
      // printより先にserial送信
      last_tx_time_ms = now_ms;

      // 位置制御パスは既定 100Hz で送るので、毎回表示するとログが溢れる。
      // crane からの新規コマンドか、停止理由が変わったときだけ出す。
      const bool reason_changed = position_control_active && control_out.reason != pre_reason;
      const bool crane_updated = pre_check_cnt != latest_cmd[CHECK_COUNTER];
      if (!position_control_active || crane_updated || reason_changed) {
        printf("cam %+4d %+4d %2d fps(rx)%2d / %3lld / ", camera.pos_xy[0], camera.pos_xy[1], camera.radius, camera.fps, diff_time);
        printf("ck : %3d / ", (uint8_t)uart_tx_buf[UART_PACKET_SIZE - 1]);
        if (position_control_active) {
          printf("POS[%s] fbXY %+6.2f %+6.2f / ", orion::toString(control_out.reason), feedback_pos[0], feedback_pos[1]);
        }
        if (rx_discard_count > 0 || feedback_discard_count > 0) {
          printf("drop cmd %llu fb %llu / ", (unsigned long long)rx_discard_count, (unsigned long long)feedback_discard_count);
        }
        printParcedData(uart_tx_buf);
      }
    }
    pre_reason = control_out.reason;
    // passthrough のゲートと表示の両方が crane 由来の check_counter を見る。
    pre_check_cnt = latest_cmd[CHECK_COUNTER];

    /* 1kHz */
    usleep(1000);
  }

  close(local_cam_sock);
  close(ai_cmd_sock);
  close(feedback_sock);

  return 0;
}
