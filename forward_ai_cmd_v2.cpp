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
#include <unistd.h>

#include <boost/array.hpp>
#include <boost/asio.hpp>
#include <cstring>

#define SERIAL_PORT "/dev/serial0"

constexpr int AI_CMD_V2_SIZE = 64;
constexpr int CAM_BUF_SIZE = 7;
constexpr int UART_PACKET_SIZE = AI_CMD_V2_SIZE + CAM_BUF_SIZE + 1;  // local cam + header

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

void sendToTerminal(char buf[])
{
  for (int i = 0; i < UART_PACKET_SIZE; i++) {
    printf("0x%02x ", buf[i]);
  }
  printf("\n");
}

int main(int argc, char * argv[])
{
  printf("start!! foward ai cmd V2 %d\n", argc);

  int uart_baudrate = getUartBaudrate(argc, argv);
  bool debug_mode_enabled = isDebugMode(argc, argv);
  printf("speed : %d\n", uart_baudrate);
  printf("debug : %d\n", debug_mode_enabled);

  int local_cam_sock, ai_cmd_sock;
  struct sockaddr_in local_cam_addr;
  struct sockaddr_in ai_cmd_addr;

  char local_cam_buf[CAM_BUF_SIZE] = {};
  char ai_cmd_buf[AI_CMD_V2_SIZE] = {};

  char uart_tx_buf[UART_PACKET_SIZE] = {};

  local_cam_sock = socket(AF_INET, SOCK_DGRAM, 0);
  ai_cmd_sock = socket(AF_INET, SOCK_DGRAM, 0);

  local_cam_addr.sin_family = AF_INET;
  local_cam_addr.sin_port = htons(12345);
  local_cam_addr.sin_addr.s_addr = INADDR_ANY;

  bind(local_cam_sock, (struct sockaddr *)&local_cam_addr, sizeof(local_cam_addr));

  ai_cmd_addr.sin_family = AF_INET;
  ai_cmd_addr.sin_port = htons(8890);
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

  while (1) {
    int n;
    n = recv(local_cam_sock, ai_cmd_buf, sizeof(ai_cmd_buf), 0);
    n = recv(ai_cmd_sock, local_cam_buf, sizeof(local_cam_buf), 0);

    for (int i = 0; i < sizeof(ai_cmd_buf); i++) {
      uart_tx_buf[i] = ai_cmd_buf[i];
    }
    uart_tx_buf[0] = 254;  //パケットヘッダ

    for (int i = 0; i < CAM_BUF_SIZE; i++) {
      uart_tx_buf[UART_PACKET_SIZE - CAM_BUF_SIZE - 1 + i] = local_cam_buf[i];
    }

    if (debug_mode_enabled) {
      sendToTerminal(uart_tx_buf);
    } else {
      serial.write_some(boost::asio::buffer(uart_tx_buf, sizeof(uart_tx_buf)));
    }

    /* 100Hz */
    usleep(10000);
  }

  close(local_cam_sock);
  close(ai_cmd_sock);

  return 0;
}
