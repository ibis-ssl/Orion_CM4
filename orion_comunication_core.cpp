#include <iostream>
#include <netinet/in.h>
#include <sstream>
#include <stdint.h>
#include <stdio.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <termios.h> //ttyパラメータの構造体
#include <unistd.h>
#include <wiringPi.h>
#include <wiringSerial.h>

int main() {
  printf("start");

  int sock;
  struct sockaddr_in addr;

  constexpr int PACKET_SIZE = 32;
  char buf[PACKET_SIZE - 2] = {};
  char senddata[PACKET_SIZE] = {};

  int fd = serialOpen("/dev/serial0", 921600);

  wiringPiSetup();
  fflush(stdout);

  if (fd < 0) {
    printf("can not open serialport");
  }

  while (1) {
    sock = socket(AF_INET, SOCK_DGRAM, 0);

    addr.sin_family = AF_INET;
    addr.sin_port = htons(12345);
    addr.sin_addr.s_addr = INADDR_ANY;

    bind(sock, (struct sockaddr *)&addr, sizeof(addr));

    recv(sock, buf, sizeof(buf), 0);

    std::stringstream ss;
    for (int i = 0; i < PACKET_SIZE - 2; i++) {
      ss << buf[i] << ", ";
    }
    std::cout << ss.str() << std::endl;

    senddata[0] = 254;
    // contennt: 1~30
    for (int i = 1; i < PACKET_SIZE - 1; i++) {
      senddata[i] = buf[i - 1];
    }
    senddata[PACKET_SIZE - 1] = 253;

    for (int i = 0; i < 32; i++) {
      serialPutchar(fd, senddata[i]);
    }

    close(sock);
  }

  return 0;
}
