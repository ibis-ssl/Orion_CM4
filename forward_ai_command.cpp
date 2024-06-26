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

struct
{
  float target_theta, global_vision_theta;
  float drible_power;
  float kick_power;
  uint8_t chip_en;
  float local_target_speed[2];
  int global_robot_position[2];
  int global_global_target_position[2];
  int global_ball_position[2];
  uint8_t allow_local_flags;
  int ball_local_x, ball_local_y, ball_local_radius, ball_local_FPS;
} ai_cmd;

float two_to_float(char data[2]) { return (float)(((uint8_t)data[0] << 8 | (uint8_t)data[1]) - 32767.0) / 32767.0; }
float two_to_int(char data[2]) { return (((uint8_t)data[0] << 8 | (uint8_t)data[1]) - 32767.0); }

int main()
{
  printf("start");

  int sock1, sock2;
  struct sockaddr_in addr1;
  struct sockaddr_in addr2;

  char rx_buf_ball[7] = {};
  constexpr int PACKET_SIZE = 64;
  char buf[PACKET_SIZE - 2] = {};
  char senddata[PACKET_SIZE] = {};
  int val;
  int n;
  int cnt;

  sock1 = socket(AF_INET, SOCK_DGRAM, 0);
  sock2 = socket(AF_INET, SOCK_DGRAM, 0);

  addr1.sin_family = AF_INET;
  addr1.sin_port = htons(12345);
  addr1.sin_addr.s_addr = INADDR_ANY;

  bind(sock1, (struct sockaddr *)&addr1, sizeof(addr1));

  addr2.sin_family = AF_INET;
  addr2.sin_port = htons(8890);
  addr2.sin_addr.s_addr = INADDR_ANY;

  bind(sock2, (struct sockaddr *)&addr2, sizeof(addr2));

  val = 1;
  ioctl(sock1, FIONBIO, &val);
  ioctl(sock2, FIONBIO, &val);

  boost::asio::io_service io;
  boost::asio::serial_port serial(io, SERIAL_PORT);
  serial.set_option(boost::asio::serial_port_base::baud_rate(921600));
  serial.set_option(boost::asio::serial_port_base::character_size(8));
  serial.set_option(boost::asio::serial_port_base::parity(boost::asio::serial_port_base::parity::none));
  serial.set_option(boost::asio::serial_port_base::stop_bits(boost::asio::serial_port_base::stop_bits::one));

  while (1) {
    n = recv(sock1, buf, sizeof(buf), 0);
    n = recv(sock2, rx_buf_ball, sizeof(rx_buf_ball), 0);

    if (cnt > 10) {
      ai_cmd.local_target_speed[0] = two_to_float(&buf[1]) * 7.0;
      ai_cmd.local_target_speed[1] = two_to_float(&buf[3]) * 7.0;
      ai_cmd.global_vision_theta = two_to_float(&buf[5]) * M_PI;
      ai_cmd.target_theta = two_to_float(&buf[7]) * M_PI;

      if (buf[9] > 100) {
        ai_cmd.chip_en = 1;
      } else {
        ai_cmd.chip_en = 0;
      }
      ai_cmd.kick_power = (float)buf[9] / 20.0;
      ai_cmd.drible_power = (float)buf[10] / 20.0;

      ai_cmd.allow_local_flags = buf[11];

      ai_cmd.global_ball_position[0] = two_to_int(&buf[12]);
      ai_cmd.global_ball_position[1] = two_to_int(&buf[14]);
      ai_cmd.global_robot_position[0] = two_to_int(&buf[16]);
      ai_cmd.global_robot_position[1] = two_to_int(&buf[18]);
      ai_cmd.global_global_target_position[0] = two_to_int(&buf[20]);
      ai_cmd.global_global_target_position[1] = two_to_int(&buf[22]);

      printf(
        " check=%3d vx=%+4.1f vy=%+4.1f vision_theta=%4.2f "
        "target_theta=%4.2f local_EN=%d ",
        buf[0], ai_cmd.local_target_speed[0], ai_cmd.local_target_speed[1], ai_cmd.global_vision_theta, ai_cmd.target_theta, ai_cmd.allow_local_flags);

      printf(" /ball %x %x %x %x %x %x %d \n", rx_buf_ball[0], rx_buf_ball[1], rx_buf_ball[2], rx_buf_ball[3], rx_buf_ball[4], rx_buf_ball[5], rx_buf_ball[6]);
      cnt = 0;
    }
    cnt++;

    senddata[0] = 254;
    // contennt: 1~30
    for (int i = 1; i < PACKET_SIZE - 6; i++) {
      senddata[i] = buf[i - 1];
    }

    senddata[PACKET_SIZE - 7] = rx_buf_ball[0];
    senddata[PACKET_SIZE - 6] = rx_buf_ball[1];
    senddata[PACKET_SIZE - 5] = rx_buf_ball[2];
    senddata[PACKET_SIZE - 4] = rx_buf_ball[3];
    senddata[PACKET_SIZE - 3] = rx_buf_ball[4];
    senddata[PACKET_SIZE - 2] = rx_buf_ball[5];
    senddata[PACKET_SIZE - 1] = rx_buf_ball[6];

    serial.write_some(boost::asio::buffer(senddata, sizeof(senddata)));

    /* 100Hz */
    usleep(10000);
  }

  close(sock1);
  close(sock2);

  return 0;
}
