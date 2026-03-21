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
#include <time.h>

#include <boost/array.hpp>
#include <boost/asio.hpp>
#include <algorithm>
#include <cstring>

#define SERIAL_PORT "/dev/ttyAMA0"

union Data {
  float f;
  char b[4];
};

/// @brief Different ways a serial port may be flushed.
enum flush_type { flush_receive = TCIFLUSH, flush_send = TCOFLUSH, flush_both = TCIOFLUSH };

constexpr int PACKET_SIZE = 128;
constexpr unsigned char PACKET_HEADER_0 = 0xAB;
constexpr unsigned char PACKET_HEADER_1 = 0xEA;

void flush_serial_port(boost::asio::serial_port & serial, flush_type what, boost::system::error_code & error)
{
  if (0 == ::tcflush(serial.lowest_layer().native_handle(), what)) {
    error = boost::system::error_code();
  } else {
    error = boost::system::error_code(errno, boost::asio::error::get_system_category());
  }
}

bool isTimestampEnabled(int argc, char * argv[])
{
  for (int i = 1; i < argc; ++i) {
    if (strcmp(argv[i], "--timestamp") == 0) {
      return true;
    }
  }
  return false;
}

uint64_t get_unix_time_us()
{
  struct timespec ts;
  clock_gettime(CLOCK_REALTIME, &ts);
  return static_cast<uint64_t>(ts.tv_sec) * 1000000ULL + static_cast<uint64_t>(ts.tv_nsec / 1000);
}

uint64_t get_unix_time_ms()
{
  return get_unix_time_us() / 1000ULL;
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
  bool timestamp_enabled = isTimestampEnabled(argc, argv);

  char multicast_ip[100];
  sprintf(multicast_ip, "224.5.20.%d", machine_number);
  char machine_ip[100];
  sprintf(machine_ip, "192.168.20.%d", machine_number);

  printf("target_ip : %s", multicast_ip);
  printf("machine_ip : %s", machine_ip);

  printf("UART %d bps\n", uart_baudrate);
  printf("timestamp print : %s\n", timestamp_enabled ? "enabled" : "disabled");

  char buf[PACKET_SIZE];

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

  unsigned char packet_buf[PACKET_SIZE] = {};
  int packet_size = 0;
  uint32_t sequence = 0;
  uint64_t stat_window_start_us = get_unix_time_us();
  uint64_t stat_read_calls = 0;
  uint64_t stat_rx_bytes = 0;
  uint64_t stat_tx_packets = 0;
  uint64_t stat_good_packets = 0;
  uint64_t stat_header_errors = 0;

  while (1) {
    size_t n = serial.read_some(boost::asio::buffer(buf, sizeof(buf)));
    stat_read_calls++;
    stat_rx_bytes += n;

    int src_offset = 0;
    while (src_offset < static_cast<int>(n)) {
      int copy_size = std::min(PACKET_SIZE - packet_size, static_cast<int>(n) - src_offset);
      memcpy(packet_buf + packet_size, buf + src_offset, copy_size);
      packet_size += copy_size;
      src_offset += copy_size;

      if (packet_size < PACKET_SIZE) {
        continue;
      }

      if (packet_buf[0] == PACKET_HEADER_0 && packet_buf[1] == PACKET_HEADER_1) {
        uint64_t tx_time_ms = get_unix_time_ms();
        uint32_t current_sequence = sequence++;
        sendto(sock, packet_buf, PACKET_SIZE, 0, (struct sockaddr *)&addr, sizeof(addr));
        stat_tx_packets++;
        stat_good_packets++;

        if (timestamp_enabled) {
          printf("seq:%10u tx_ms:%llu ck:%3u\n",
            current_sequence,
            static_cast<unsigned long long>(tx_time_ms),
            static_cast<unsigned int>(packet_buf[3]));
        }
      } else {
        stat_header_errors++;
      }

      packet_size = 0;
    }

    uint64_t now_us = get_unix_time_us();
    if (now_us - stat_window_start_us >= 1000000ULL) {
      printf(
        "uart 1s read_calls:%llu rx_bytes:%llu tx_packets:%llu good:%llu header_errors:%llu buffered:%d\n",
        static_cast<unsigned long long>(stat_read_calls),
        static_cast<unsigned long long>(stat_rx_bytes),
        static_cast<unsigned long long>(stat_tx_packets),
        static_cast<unsigned long long>(stat_good_packets),
        static_cast<unsigned long long>(stat_header_errors),
        packet_size);

      stat_window_start_us = now_us;
      stat_read_calls = 0;
      stat_rx_bytes = 0;
      stat_tx_packets = 0;
      stat_good_packets = 0;
      stat_header_errors = 0;
    }
  }

  return 0;
}
