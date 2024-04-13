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

  int count = 0;
  constexpr int PACKET_SIZE = 64;

  char Rxbuf[PACKET_SIZE];
  char buf[PACKET_SIZE];
  char Rxdata[PACKET_SIZE];

  /**
   * シリアル通信の設定
   */
  boost::asio::io_service io;
  boost::asio::serial_port serial(io, SERIAL_PORT);
  serial.set_option(boost::asio::serial_port_base::baud_rate(921600));
  serial.set_option(boost::asio::serial_port_base::character_size(8 /* data bits */));
  serial.set_option(boost::asio::serial_port_base::parity( boost::asio::serial_port_base::parity::none));
  serial.set_option(boost::asio::serial_port_base::stop_bits(boost::asio::serial_port_base::stop_bits::one));


  /**
   * UDP通信の設定
   */
  int sock;
  struct sockaddr_in addr;
  in_addr_t ipaddr;

  sock = socket(AF_INET, SOCK_DGRAM, 0);

  addr.sin_family = AF_INET;
  addr.sin_port = htons(50100);
  addr.sin_addr.s_addr = inet_addr("224.5.20.100");

  ipaddr = inet_addr("192.168.20.100");
  if (setsockopt(sock, IPPROTO_IP, IP_MULTICAST_IF, (char *)&ipaddr,
                 sizeof(ipaddr)) != 0) {
    perror("setsockopt");
    return 1;
  }

  size_t total_length = 0;


  while (1) {

    uint8_t start_byte_idx = 0;
    size_t n = serial.read_some(boost::asio::buffer(buf, sizeof(buf)));
    

    if(n==PACKET_SIZE){
        for (size_t j = 0; j < PACKET_SIZE; j++) {
          Rxbuf[j]=buf[j];
      }
    }

    if(n==PACKET_SIZE || total_length==PACKET_SIZE){ 
    
    total_length=0;
    
    while ((!(Rxbuf[start_byte_idx] == 0xAB &&
              Rxbuf[start_byte_idx + 1] == 0xEA)) &&
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

      printf(" S_id=%2d", start_byte_idx);

      printf(" Read %2x %2x %2x %2x %2x %2x %2x %2x %2x %2x %2x %2x %2x %2x %2x %2x", Rxbuf[0],
             Rxbuf[1],Rxbuf[2], Rxbuf[3], Rxbuf[4], Rxbuf[5], Rxbuf[6],
             Rxbuf[7], Rxbuf[8], Rxbuf[9], Rxbuf[10], Rxbuf[11],
             Rxbuf[12], Rxbuf[13], Rxbuf[14], Rxbuf[15]);

      printf(" Data %2x %2x %2x %2x %2x %2x %2x %2x %2x %2x %2x %2x %2x %2x %2x %2x", Rxdata[0],
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

      printf(" yaw=%5f", yaw.f);
      printf("\n");
      fflush(stdout);
      count = 0;
    }

    sendto(sock, Rxdata, PACKET_SIZE, 0, (struct sockaddr *)&addr,
           sizeof(addr));

  }
  else{

    for (size_t i = 0; i < n; ++i) {
				Rxbuf[total_length + i] = buf[i];
			}
			total_length += n;

      if(total_length>PACKET_SIZE){
        total_length=0;
        for (size_t i = 0; i < PACKET_SIZE; i++) {
            Rxbuf[i] = 0;
        }
      }
  }

    //printf("length =%2d total_length =%2d\n", n,total_length );

  }

  close(sock);

  return 0;
}