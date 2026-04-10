// このファイルはSTM32からUARTで受信した128バイトのロボット状態パケットを、
// そのままUDP multicastへ転送する処理とパケット構造の定義を担当する。
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
#include <termios.h>
#include <unistd.h>

#include <boost/array.hpp>
#include <boost/asio.hpp>
#include <cstring>

#define SERIAL_PORT "/dev/ttyS0"
constexpr int PACKET_SIZE = 128;

#pragma pack(push, 1)

struct RobotFeedbackPacketHeader
{
  uint8_t sync0;
  uint8_t sync1;
  uint8_t checksum;
  uint8_t check_counter;
};

struct RobotFeedbackPacket
{
  RobotFeedbackPacketHeader header;

  // STM32 Core/Src/ai_comm.c の sendRobotInfo() に対応するペイロード。
  // float 値は STM32 側 float_to_uchar4() の生バイト列がそのまま格納される。
  // したがって little-endian IEEE754 float 前提で読む必要がある。
  float imu_yaw_deg;                   // [4..7]
  float battery_voltage_bldc_right;   // [8..11]
  uint8_t ball_detection[3];           // [12..14]
  uint8_t kick_state_div10;           // [15]
  uint16_t current_error_id;          // [16..17] little-endian
  uint16_t current_error_info;        // [18..19] little-endian
  float current_error_value;          // [20..23]
  uint8_t motor_current_x10[4];       // [24..27]
  uint8_t ball_detection_extra;       // [28]
  uint8_t temp_motor[4];              // [29..32]
  uint8_t temp_fet;                   // [33]
  uint8_t temp_coil[2];               // [34..35]
  float diff_angle_deg;               // [36..39]
  float capacitor_boost_voltage;      // [40..43]
  float vision_based_position_x;      // [44..47]
  float vision_based_position_y;      // [48..51]
  float global_odom_speed_x;          // [52..55]
  float global_odom_speed_y;          // [56..59]
  uint8_t camera_pos_x_div2;          // [60]
  uint8_t camera_pos_y;               // [61]
  uint8_t camera_radius_div4;         // [62]
  uint8_t camera_fps;                 // [63]
  float tx_value_array[14];           // [64..119]
  uint8_t reserved[8];                // [120..127] 現状は未使用。送信側で明示初期化なし。
};

#pragma pack(pop)

static_assert(sizeof(RobotFeedbackPacket) == PACKET_SIZE, "RobotFeedbackPacket size must be 128 bytes");

enum TxValueIndex {
  TX_MOUSE_ODOM_X = 0,
  TX_MOUSE_ODOM_Y,
  TX_MOUSE_GLOBAL_VEL_X,
  TX_MOUSE_GLOBAL_VEL_Y,
  TX_OUTPUT_VEL_X,
  TX_OUTPUT_VEL_Y,
  TX_MOTOR_FEEDBACK_0,
  TX_MOTOR_FEEDBACK_1,
  TX_MOTOR_FEEDBACK_2,
  TX_MOTOR_FEEDBACK_3,
  TX_LOCAL_ODOM_SPEED_MVF_X,
  TX_LOCAL_ODOM_SPEED_MVF_Y,
  TX_LOCAL_ODOM_SPEED_MVF_W,
  TX_MOUSE_QUALITY,
};

/*
 * RobotFeedbackPacket.tx_value_array の意味:
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

  int count = 0;
  char Rxbuf[PACKET_SIZE];
  char buf[PACKET_SIZE];
  char Rxdata[PACKET_SIZE];

  /**
   * シリアル通信の設定
   */

startpoint:

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
  int cnt_nodata = 0;

  sock = socket(AF_INET, SOCK_DGRAM, 0);

  addr.sin_family = AF_INET;
  addr.sin_port = htons(50000 + machine_number);
  addr.sin_addr.s_addr = inet_addr(multicast_ip);

  ipaddr = inet_addr(machine_ip);
  if (setsockopt(sock, IPPROTO_IP, IP_MULTICAST_IF, (char *)&ipaddr, sizeof(ipaddr)) != 0) {
    perror("setsockopt");
    return 1;
  }

  size_t total_length = 0;

  char uart_rx_buf[PACKET_SIZE];
  uint32_t buf_idx = 0;

  while (1) {
    size_t n = serial.read_some(boost::asio::buffer(buf, sizeof(buf)));
    for (int i = 0; i < n; i++) {
      /*if(buf[i] == '\n'){
        printf("n = %3d ",n);
      }
      printf("%c",buf[i]);*/

      if (buf[i] == 0xAB && buf_idx == 0) {
        uart_rx_buf[buf_idx] = buf[i];
        buf_idx = 1;
      } else if (buf[i] == 0xEA && buf_idx == 1) {
        uart_rx_buf[buf_idx] = buf[i];
        buf_idx = 2;
      } else if (buf_idx >= 2) {
        uart_rx_buf[buf_idx] = buf[i];
        buf_idx++;
        if (buf_idx >= PACKET_SIZE) {
          buf_idx = 0;
          sendto(sock, uart_rx_buf, PACKET_SIZE, 0, (struct sockaddr *)&addr, sizeof(addr));
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
