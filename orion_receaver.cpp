#include <arpa/inet.h>
#include <boost/array.hpp>
#include <boost/asio.hpp>
#include <cstring>
#include <fcntl.h>
#include <netinet/in.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <termios.h> //ttyパラメータの構造体
#include <unistd.h>

#define SERIAL_PORT "/dev/serial0"

union Data {
  float f;
  char b[4];
};

int main() {

  printf("start");

  int sock;
  struct sockaddr_in addr;
  int count = 0;
  constexpr int PACKET_SIZE = 16;

  char Rxbuf[PACKET_SIZE];
  char Rxdata[PACKET_SIZE];

  /*unsigned char msg[] = "serial port open...\n";
  int fd;                             // ファイルディスクリプタ
  struct termios tio;                 // シリアル通信設定
  int baudRate = B921600;
  int i;
  int len;
  int ret;
  int size;

  fd = open(SERIAL_PORT, O_RDWR);     // デバイスをオープンする
  if (fd < 0) {
          printf("open error\n");
          return -1;
  }

  tio.c_cflag += CREAD;               // 受信有効
  tio.c_cflag += CLOCAL;              // ローカルライン（モデム制御なし）
  tio.c_cflag += CS8;                 // データビット:8bit
  tio.c_cflag += 0;                   // ストップビット:1bit
  tio.c_cflag += 0;                   // パリティ:None

  cfsetispeed( &tio, baudRate );
  cfsetospeed( &tio, baudRate );

  cfmakeraw(&tio);                    // RAWモード

  tcsetattr( fd, TCSANOW, &tio );     // デバイスに設定を行う

  ioctl(fd, TCSETS, &tio);            // ポートの設定を有効にする*/

  /**
   * シリアル通信の設定
   */
  boost::asio::io_service io;
  // Open serial port
  boost::asio::serial_port serial(io, SERIAL_PORT);
  // Configure basic serial port parameters: 115.2kBaud, 8N1
  serial.set_option(boost::asio::serial_port_base::baud_rate(921600));
  serial.set_option(
      boost::asio::serial_port_base::character_size(8 /* data bits */));
  serial.set_option(boost::asio::serial_port_base::parity(
      boost::asio::serial_port_base::parity::none));
  serial.set_option(boost::asio::serial_port_base::stop_bits(
      boost::asio::serial_port_base::stop_bits::one));


  /**
   * UDP通信の設定
   */
  int sock;
  struct sockaddr_in addr;
  in_addr_t ipaddr;

  sock = socket(AF_INET, SOCK_DGRAM, 0);

  addr.sin_family = AF_INET;
  addr.sin_port = htons(50102);
  addr.sin_addr.s_addr = inet_addr("224.5.20.102");

  ipaddr = inet_addr("192.168.20.102");
  if (setsockopt(sock, IPPROTO_IP, IP_MULTICAST_IF, (char *)&ipaddr,
                 sizeof(ipaddr)) != 0) {
    perror("setsockopt");
    return 1;
  }

  while (1) {

    size_t n = serial.read_some(boost::asio::buffer(Rxbuf, sizeof(Rxbuf)));

    uint8_t start_byte_idx = 0;

    while ((!(Rxbuf[start_byte_idx] == 0xFA &&
              Rxbuf[start_byte_idx + 1] == 0xFB)) &&
           start_byte_idx < sizeof(Rxbuf)) {
      start_byte_idx++;
    }
    if (start_byte_idx >= sizeof(Rxbuf)) {
      for (uint8_t k = 0; k < (sizeof(Rxdata)); k++) {
        Rxdata[k] = 0;
      }
      // 受信なしデータクリア
    } else {
      for (uint8_t k = 0; k < sizeof(Rxbuf); k++) {
        if ((start_byte_idx + k) >= sizeof(Rxbuf)) {
          Rxdata[k] = Rxbuf[k - (sizeof(Rxbuf) - start_byte_idx)];
        }

        else {
          Rxdata[k] = Rxbuf[start_byte_idx + k];
        }
      }
    }

    count++;

    if (count > 20) {

      printf(" S_id=%d", start_byte_idx);

      printf(" %x %x %x %x %x %x %x %x %x %x %x %x %x %x %x %x", Rxdata[0],
             Rxdata[1], Rxdata[2], Rxdata[3], Rxdata[4], Rxdata[5], Rxdata[6],
             Rxdata[7], Rxdata[8], Rxdata[9], Rxdata[10], Rxdata[11],
             Rxdata[12], Rxdata[13], Rxdata[14], Rxdata[15]);

      Data yaw;
      if (Rxdata[2] == 10) {
        yaw.b[0] = Rxdata[4];
        yaw.b[1] = Rxdata[5];
        yaw.b[2] = Rxdata[6];
        yaw.b[3] = Rxdata[7];
      }

      printf(" yaw=%3.3f", yaw.f);
      printf("\n");
      fflush(stdout);
      count = 0;
    }

    sendto(sock, Rxdata, PACKET_SIZE, 0, (struct sockaddr *)&addr,
           sizeof(addr));

  }

  close(sock);

  return 0;
}
