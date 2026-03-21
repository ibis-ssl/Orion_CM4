#include <stdint.h>
#include <stdio.h>
#include <time.h>
#include <unistd.h>

#include <boost/asio.hpp>
#include <cstring>

#define SERIAL_PORT "/dev/ttyAMA0"

constexpr int DEFAULT_BAUDRATE = 1000000;
constexpr size_t READ_BUF_SIZE = 256;
constexpr int DEFAULT_DUMP_LINES = 64;

uint64_t get_unix_time_us()
{
  struct timespec ts;
  clock_gettime(CLOCK_REALTIME, &ts);
  return static_cast<uint64_t>(ts.tv_sec) * 1000000ULL + static_cast<uint64_t>(ts.tv_nsec / 1000);
}

int getUartBaudrate(int argc, char * argv[])
{
  int speed = DEFAULT_BAUDRATE;

  for (int i = 1; i < argc; ++i) {
    if (strcmp(argv[i], "-s") == 0 && i + 1 < argc) {
      speed = std::stoi(argv[++i]);
    }
  }
  return speed;
}

int getDumpLines(int argc, char * argv[])
{
  int dump_lines = DEFAULT_DUMP_LINES;

  for (int i = 1; i < argc; ++i) {
    if (strcmp(argv[i], "--lines") == 0 && i + 1 < argc) {
      dump_lines = std::stoi(argv[++i]);
    }
  }
  return dump_lines;
}

int main(int argc, char * argv[])
{
  int uart_baudrate = getUartBaudrate(argc, argv);
  int dump_lines = getDumpLines(argc, argv);

  printf("uart raw dump start\n");
  printf("port : %s\n", SERIAL_PORT);
  printf("baud : %d\n", uart_baudrate);
  printf("dump_lines : %d\n", dump_lines);

  boost::asio::io_service io;
  boost::asio::serial_port serial(io, SERIAL_PORT);
  serial.set_option(boost::asio::serial_port_base::baud_rate(uart_baudrate));
  serial.set_option(boost::asio::serial_port_base::character_size(8));
  serial.set_option(boost::asio::serial_port_base::parity(boost::asio::serial_port_base::parity::none));
  serial.set_option(boost::asio::serial_port_base::stop_bits(boost::asio::serial_port_base::stop_bits::one));
  serial.set_option(boost::asio::serial_port_base::flow_control(boost::asio::serial_port_base::flow_control::none));

  unsigned char buf[READ_BUF_SIZE];
  uint64_t stat_window_start_us = get_unix_time_us();
  uint64_t stat_read_calls = 0;
  uint64_t stat_rx_bytes = 0;
  int printed_lines = 0;

  while (1) {
    size_t n = serial.read_some(boost::asio::buffer(buf, sizeof(buf)));
    uint64_t now_us = get_unix_time_us();

    stat_read_calls++;
    stat_rx_bytes += n;

    if (printed_lines < dump_lines) {
      printf("read ts_us:%llu n:%3u data:",
        static_cast<unsigned long long>(now_us),
        static_cast<unsigned int>(n));
      for (size_t i = 0; i < n; ++i) {
        printf(" %02x", static_cast<unsigned int>(buf[i]));
      }
      printf("\n");
      printed_lines++;
    }

    if (now_us - stat_window_start_us >= 1000000ULL) {
      printf("uart raw 1s read_calls:%llu rx_bytes:%llu printed:%d\n",
        static_cast<unsigned long long>(stat_read_calls),
        static_cast<unsigned long long>(stat_rx_bytes),
        printed_lines);
      stat_window_start_us = now_us;
      stat_read_calls = 0;
      stat_rx_bytes = 0;
    }
  }

  return 0;
}
