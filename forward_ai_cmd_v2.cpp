#include <arpa/inet.h>
#include <fcntl.h>
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
#include <cstring>

#include "robot_packet.h"

#define SERIAL_PORT "/dev/ttyS0"

constexpr int AI_CMD_V2_SIZE = 64;
constexpr int CAM_BUF_SIZE = 7;                                      // camera 7 + ck1
constexpr int UART_PACKET_SIZE = AI_CMD_V2_SIZE + CAM_BUF_SIZE + 1;  // local cam + ck

typedef struct
{
  int16_t pos_xy[2], radius;
  uint8_t fps;
} camera_t;

float two_to_float(char data[2]) { return (float)(((uint8_t)data[0] << 8 | (uint8_t)data[1]) - 32767.0) / 32767.0; }
float two_to_int(char data[2]) { return (((uint8_t)data[0] << 8 | (uint8_t)data[1]) - 32767.0); }

int getUartBaudrate(int argc, char * argv[])
{
  int speed = 2000000;

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

bool isDebugMode(int argc, char * argv[])
{
  bool debug = false;

  for (int i = 1; i < argc; ++i) {
    if (strcmp(argv[i], "--debug") == 0) {
      debug = true;
    }
  }
  return debug;
}

void pritBinData(char buf[])
{
  for (int i = 0; i < UART_PACKET_SIZE; i++) {
    printf("0x%02x ", buf[i]);
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
  printf("SpdLmt %4.2f OmgLmt %4.1f ", cmd_v2.speed_limit, cmd_v2.omega_limit);

  printf("dri %+4.2f ", cmd_v2.dribble_power);
  if (cmd_v2.lift_dribbler) {
    printf("UP ");
  } else {
    printf("DN ");
  }
  if (cmd_v2.enable_chip) {
    printf("chip %3.2f ", cmd_v2.kick_power);
  } else {
    printf("stlt %3.2f ", cmd_v2.kick_power);
  }

  if (cmd_v2.prioritize_accurate_acceleration) {
    printf("Pri-Acur ");
  }
  if (cmd_v2.prioritize_move) {
    printf("Pri-Move ");
  }

  printf("\n");
}

long long get_current_time_ms()
{
  struct timespec ts;
  clock_gettime(CLOCK_REALTIME, &ts);                // 現在の時刻を取得
  return ts.tv_sec * 1000LL + ts.tv_nsec / 1000000;  // 秒をミリ秒に変換し、ナノ秒をミリ秒に変換して加算
}

uint32_t calc_check_sum(char * buf, int buf_size)
{
  uint32_t data_ck = 0;
  for (int i = 0; i < buf_size - 1; i++) {
    data_ck += buf[i];
  }
  return data_ck;
}

int main(int argc, char * argv[])
{
  printf("start!! foward ai cmd V2 %d\n", argc);

  int uart_baudrate = getUartBaudrate(argc, argv);
  bool debug_mode_enabled = isDebugMode(argc, argv);
  printf("debug mode : %d\n", debug_mode_enabled);
  printf("UART %d bps\n", uart_baudrate);

  int local_cam_sock, ai_cmd_sock;
  struct sockaddr_in local_cam_addr;
  struct sockaddr_in ai_cmd_addr;

  char local_cam_buf[CAM_BUF_SIZE] = {};
  char ai_cmd_buf[AI_CMD_V2_SIZE] = {};

  char uart_tx_buf[UART_PACKET_SIZE] = {};

  local_cam_sock = socket(AF_INET, SOCK_DGRAM, 0);
  ai_cmd_sock = socket(AF_INET, SOCK_DGRAM, 0);

  local_cam_addr.sin_family = AF_INET;
  local_cam_addr.sin_port = htons(8890);
  local_cam_addr.sin_addr.s_addr = INADDR_ANY;

  bind(local_cam_sock, (struct sockaddr *)&local_cam_addr, sizeof(local_cam_addr));

  ai_cmd_addr.sin_family = AF_INET;
  ai_cmd_addr.sin_port = htons(12345);
  ai_cmd_addr.sin_addr.s_addr = INADDR_ANY;

  bind(ai_cmd_sock, (struct sockaddr *)&ai_cmd_addr, sizeof(ai_cmd_addr));

  int val = 1;
  ioctl(local_cam_sock, FIONBIO, &val);
  ioctl(ai_cmd_sock, FIONBIO, &val);

  boost::asio::io_service io;
  boost::asio::serial_port serial(io, SERIAL_PORT);
  serial.set_option(boost::asio::serial_port_base::baud_rate(uart_baudrate));
  serial.set_option(boost::asio::serial_port_base::character_size(8));
  serial.set_option(boost::asio::serial_port_base::parity(boost::asio::serial_port_base::parity::none));
  serial.set_option(boost::asio::serial_port_base::stop_bits(boost::asio::serial_port_base::stop_bits::one));

  char pre_check_cnt = 0;
  camera_t camera;

  long long pre_time, diff_time = 0;

  while (1) {
    int cmd_n, cam_n;
    cmd_n = recv(ai_cmd_sock, ai_cmd_buf, sizeof(ai_cmd_buf), 0);
    cam_n = recv(local_cam_sock, local_cam_buf, sizeof(local_cam_buf), 0);

    memcpy(uart_tx_buf, ai_cmd_buf, sizeof(ai_cmd_buf));

    uart_tx_buf[0] = 254;  //パケットヘッダ

    // camera.* はデバッグprint用のパース
    if (cam_n != -1) {
      camera.pos_xy[0] = (local_cam_buf[0] << 8) + local_cam_buf[1];
      camera.pos_xy[1] = (local_cam_buf[2] << 8) + local_cam_buf[3];
      camera.radius = (local_cam_buf[4] << 8) + local_cam_buf[5];
      camera.fps = local_cam_buf[6];

      memcpy(&uart_tx_buf[UART_PACKET_SIZE - CAM_BUF_SIZE - 1], local_cam_buf, CAM_BUF_SIZE);
      diff_time = get_current_time_ms() - pre_time;
      pre_time = get_current_time_ms();
    } else {
      camera.fps = 0;

      memset(&uart_tx_buf[UART_PACKET_SIZE - CAM_BUF_SIZE - 1], 0, CAM_BUF_SIZE);
    }

    // cksum計算
    uart_tx_buf[UART_PACKET_SIZE - 1] = calc_check_sum(uart_tx_buf, UART_PACKET_SIZE);

    if (debug_mode_enabled) {
      pritBinData(uart_tx_buf);
    } else if (pre_check_cnt != ai_cmd_buf[1]) {
      serial.write_some(boost::asio::buffer(uart_tx_buf, sizeof(uart_tx_buf)));
      // printより先にserial送信

      printf("cam %+4d %+4d %2d fps(rx)%2d / %3d / ", camera.pos_xy[0], camera.pos_xy[1], camera.radius, camera.fps, diff_time);
      printf("ck : %3d / ", uart_tx_buf[UART_PACKET_SIZE - 1]);
      printParcedData(ai_cmd_buf);
    }
    pre_check_cnt = ai_cmd_buf[1];

    /* 1kHz */
    usleep(1000);
  }

  close(local_cam_sock);
  close(ai_cmd_sock);

  return 0;
}
