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

#define SERIAL_PORT "/dev/serial0"

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

int main()
{
  printf("start");
  printf("UART baud : 2000000 bps");

  int count = 0;
  constexpr int PACKET_SIZE = 64;

  char Rxbuf[PACKET_SIZE];
  char buf[PACKET_SIZE];
  char Rxdata[PACKET_SIZE];

  /**
   * シリアル通信の設定
   */

startpoint:

  boost::asio::io_service io;
  boost::asio::serial_port serial(io, SERIAL_PORT);
  serial.set_option(boost::asio::serial_port_base::baud_rate(2000000));
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
  addr.sin_port = htons(50104);
  addr.sin_addr.s_addr = inet_addr("224.5.20.104");

  ipaddr = inet_addr("192.168.20.104");
  if (setsockopt(sock, IPPROTO_IP, IP_MULTICAST_IF, (char *)&ipaddr, sizeof(ipaddr)) != 0) {
    perror("setsockopt");
    return 1;
  }

  size_t total_length = 0;

  char uart_rx_buf[PACKET_SIZE];
  uint32_t buf_idx = 0, cycle_div = 0;

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
          cycle_div++;
        }
        if (cycle_div >= 2) {
          cycle_div = 0;
          for (int pi = 0; pi < PACKET_SIZE; pi++) {
            printf("0x%02x ", uart_rx_buf[pi]);
          }
          printf("\n");
          sendto(sock, uart_rx_buf, PACKET_SIZE, 0, (struct sockaddr *)&addr, sizeof(addr));
        }
      }
    }
  }

  return 0;
}