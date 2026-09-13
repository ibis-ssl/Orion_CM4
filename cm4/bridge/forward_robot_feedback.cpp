// このファイルは STM32 から UART で受信した 128 バイトのロボット状態パケットを、
// host 側へ UDP multicast で転送し、あわせて同一 CM4 上の ai_cmd_v2.out へ
// loopback unicast で渡すブリッジを担当する。
//
// UART の読み手はこのプロセスだけにする。ai_cmd_v2.out が位置制御ループを
// 閉じるために feedback を必要とするが、/dev/serial0 の読み手を 2 プロセスに
// 割ると取り合いになるため、ここから loopback で配る。
// これにより ai_cmd_v2.out の feedback 受信コードとポート番号が、実機と
// シミュレータ (cm4_sim) で完全に同一になる。
#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <termios.h>  //ttyパラメータの構造体
#include <unistd.h>

#include <boost/array.hpp>
#include <boost/asio.hpp>
#include <cstring>

#include "robot_feedback_packet.h"

// CM4のprimary UARTを示す安定名。現在はPL011 (/dev/ttyAMA0) に割り当てる。
#define SERIAL_PORT "/dev/serial0"
// パケットのレイアウトは robot_feedback_packet.h が正本。
// ai_cmd_v2.out と cm4_sim.out も同じ定義を使う (消費者が 3 つに増えたため)。
constexpr int PACKET_SIZE = FEEDBACK_PACKET_SIZE;

/*
 * RobotFeedbackPacket.tx_value_array の意味:
 * STM32 Core/Src/ai_comm.c の sendRobotInfo() で enqueueFloatArray() した順番に対応する。
 * [0]  mouse->odom[0]
 * [1]  mouse->odom[1]
 * [2]  mouse->global_vel[0]
 * [3]  mouse->global_vel[1]
 * [4]  out->velocity[0]
 * [5]  out->velocity[1]
 * [6]  can_raw->motor_feedback[0]
 * [7]  can_raw->motor_feedback[1]
 * [8]  can_raw->motor_feedback[2]
 * [9]  can_raw->motor_feedback[3]
 * [10] omni->local_odom_speed_mvf[0]
 * [11] omni->local_odom_speed_mvf[1]
 * [12] omni->local_odom_speed_mvf[2]
 * [13] mouse->quality
 */

union Data {
  float f;
  char b[4];
};

/// @brief Different ways a serial port may be flushed.
enum flush_type { flush_receive = TCIFLUSH, flush_send = TCOFLUSH, flush_both = TCIOFLUSH };

void flush_serial_port(boost::asio::serial_port & serial, flush_type what, boost::system::error_code & error)
{
  if (0 == ::tcflush(serial.lowest_layer().native_handle(), what)) {
    error = boost::system::error_code();
  } else {
    error = boost::system::error_code(errno, boost::asio::error::get_system_category());
  }
}

int getMachineNumber(int argc, char * argv[])
{
  int number = 100;

  // Parse command line arguments
  for (int i = 1; i < argc; ++i) {
    if (strcmp(argv[i], "-n") == 0) {
      if (i + 1 < argc) {
        number = std::stoi(argv[++i]);
      } else {
        printf("Error: -n option requires an integer argument.");
      }
    }
  }
  return number;
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

int main(int argc, char * argv[])
{
  printf("start");
  printf("UART baud : 1000000 bps");

  int machine_number = getMachineNumber(argc, argv);
  int uart_baudrate = getUartBaudrate(argc, argv);

  char multicast_ip[100];
  sprintf(multicast_ip, "224.5.20.%d", machine_number);
  char machine_ip[100];
  sprintf(machine_ip, "192.168.20.%d", machine_number);

  printf("target_ip : %s", multicast_ip);
  printf("machine_ip : %s", machine_ip);

  printf("UART %d bps\n", uart_baudrate);

  char buf[PACKET_SIZE];

  /**
   * シリアル通信の設定
   */

  boost::asio::io_service io;
  boost::asio::serial_port serial(io, SERIAL_PORT);
  serial.set_option(boost::asio::serial_port_base::baud_rate(uart_baudrate));
  serial.set_option(boost::asio::serial_port_base::character_size(8 /* data bits */));
  serial.set_option(boost::asio::serial_port_base::parity(boost::asio::serial_port_base::parity::none));
  serial.set_option(boost::asio::serial_port_base::stop_bits(boost::asio::serial_port_base::stop_bits::one));
  boost::system::error_code error;

  flush_serial_port(serial, flush_receive, error);

  /**
   * UDP通信の設定
   */
  int sock;
  struct sockaddr_in addr;
  in_addr_t ipaddr;

  sock = socket(AF_INET, SOCK_DGRAM, 0);

  addr.sin_family = AF_INET;
  addr.sin_port = htons(50000 + machine_number);
  addr.sin_addr.s_addr = inet_addr(multicast_ip);

  ipaddr = inet_addr(machine_ip);
  if (setsockopt(sock, IPPROTO_IP, IP_MULTICAST_IF, (char *)&ipaddr, sizeof(ipaddr)) != 0) {
    perror("setsockopt");
    return 1;
  }

  // 同一 CM4 上の ai_cmd_v2.out へ渡す loopback unicast。
  // ポート番号は multicast と同じ (50000 + machine_number = 50100 + id)。
  // unicast は unicast ソケットへ、multicast は multicast ソケットへしか
  // 配送されないので、同じポート番号でも取り違えは起きない。
  struct sockaddr_in loopback_addr;
  memset(&loopback_addr, 0, sizeof(loopback_addr));
  loopback_addr.sin_family = AF_INET;
  loopback_addr.sin_port = htons(50000 + machine_number);
  loopback_addr.sin_addr.s_addr = inet_addr("127.0.0.1");
  printf("loopback : 127.0.0.1:%d (ai_cmd_v2.out の位置制御ループ用)\n", 50000 + machine_number);

  char uart_rx_buf[PACKET_SIZE];
  uint32_t buf_idx = 0;

  while (1) {
    size_t n = serial.read_some(boost::asio::buffer(buf, sizeof(buf)));
    for (size_t i = 0; i < n; i++) {
      /*if(buf[i] == '\n'){
        printf("n = %3d ",n);
      }
      printf("%c",buf[i]);*/

      // char は x86 で符号付き、ARM で符号なし。0x80 以上と比較するので
      // uint8_t へ明示キャストしないと x86 ビルドで常に false になる。
      const uint8_t byte = static_cast<uint8_t>(buf[i]);
      if (byte == 0xAB && buf_idx == 0) {
        uart_rx_buf[buf_idx] = buf[i];
        buf_idx = 1;
      } else if (byte == 0xEA && buf_idx == 1) {
        uart_rx_buf[buf_idx] = buf[i];
        buf_idx = 2;
      } else if (buf_idx >= 2) {
        uart_rx_buf[buf_idx] = buf[i];
        buf_idx++;
        if (buf_idx >= PACKET_SIZE) {
          buf_idx = 0;
          sendto(sock, uart_rx_buf, PACKET_SIZE, 0, (struct sockaddr *)&addr, sizeof(addr));
          sendto(sock, uart_rx_buf, PACKET_SIZE, 0, (struct sockaddr *)&loopback_addr, sizeof(loopback_addr));
          printf("check_counter : %3d / ", (uint8_t)uart_rx_buf[3]);

          for (int pi = 0; pi < PACKET_SIZE; pi++) {
            printf("0x%02x ", uart_rx_buf[pi]);
          }
          printf("\n");
        }
      }
    }
  }

  return 0;
}
