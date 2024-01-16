#include <stdio.h>
#include <stdlib.h>
#include <cstring>
#include <sys/types.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <stdint.h>
#include <termios.h>  //ttyパラメータの構造体
#include <sys/ioctl.h>
#include <fcntl.h>
#include <boost/asio.hpp>
#include <boost/array.hpp>


union Data
{
  float f;
  char b[4];
};

void generatePacket(uint8_t* senddata, int seq)
{
  // tx_msg_t msg;
  static uint8_t ring_counter = 0;

  ring_counter++;
  if (ring_counter > 200)
  {
    ring_counter = 0;
  }
  // char* temp;
  char temp[4];
  temp[0] = 25;
  temp[1] = 50;
  temp[2] = 75;
  temp[3] = 100;

  // uint8_t senddata[16];
  switch (seq)
  {
    case 0:
      senddata[0] = 0xFA;
      senddata[1] = 0xFB;
      senddata[2] = seq + 10;
      senddata[3] = ring_counter;
      // temp = (char*)&imu.yaw_angle;
      senddata[4] = temp[0];
      senddata[5] = temp[1];
      senddata[6] = temp[2];
      senddata[7] = temp[3];
      // msg.data.diff_angle = 0.0; //imu.yaw_angle - ai_cmd.global_vision_theta;
      // temp = (char*)&msg.data.diff_angle;
      senddata[8] = temp[0];
      senddata[9] = temp[1];
      senddata[10] = temp[2];
      senddata[11] = temp[3];
      senddata[12] = 50;  // can_raw.ball_detection[0];
      senddata[13] = 51;  // can_raw.ball_detection[1];
      senddata[14] = 52;  // can_raw.ball_detection[2];
      senddata[15] = 53;  // can_raw.ball_detection[3];
      break;
    case 1:
      senddata[0] = 0xFA;
      senddata[1] = 0xFB;
      senddata[2] = seq + 10;
      senddata[3] = ring_counter;
      senddata[4] = 1;   // can_raw.error_no[0];
      senddata[5] = 2;   // can_raw.error_no[1];
      senddata[6] = 3;   // can_raw.error_no[2];
      senddata[7] = 4;   // can_raw.error_no[3];
      senddata[8] = 5;   // can_raw.error_no[4];
      senddata[9] = 6;   // can_raw.error_no[5];
      senddata[10] = 7;  // can_raw.error_no[6];
      senddata[11] = 8;  // can_raw.error_no[7];
      // 小さすぎたので10倍してる
      senddata[12] = 9;   //(uint8_t)(can_raw.current[0] * 10);
      senddata[13] = 10;  //(uint8_t)(can_raw.current[1] * 10);
      senddata[14] = 11;  //(uint8_t)(can_raw.current[2] * 10);
      senddata[15] = 12;  //(uint8_t)(can_raw.current[3] * 10);
      break;
    case 2:
      senddata[0] = 0xFA;
      senddata[1] = 0xFB;
      senddata[2] = seq + 10;
      senddata[3] = ring_counter;
      senddata[4] = 10;    // kick_state / 10;
      senddata[5] = 100;   //(uint8_t)can_raw.temperature[0];
      senddata[6] = 101;   //(uint8_t)can_raw.temperature[1];
      senddata[7] = 102;   //(uint8_t)can_raw.temperature[2];
      senddata[8] = 103;   //(uint8_t)can_raw.temperature[3];
      senddata[9] = 104;   //(uint8_t)can_raw.temperature[4];
      senddata[10] = 105;  //(uint8_t)can_raw.temperature[5];
      senddata[11] = 106;  //(uint8_t)can_raw.temperature[6];
      // temp = (char*)&(can_raw.power_voltage[0]);
      senddata[12] = temp[0];
      senddata[13] = temp[1];
      senddata[14] = temp[2];
      senddata[15] = temp[3];
      break;
    case 3:
      senddata[0] = 0xFA;
      senddata[1] = 0xFB;
      senddata[2] = seq + 10;
      senddata[3] = ring_counter;
      // temp = (char*)&(can_raw.power_voltage[6]);
      senddata[4] = temp[0];
      senddata[5] = temp[1];
      senddata[6] = temp[2];
      senddata[7] = temp[3];
      // temp = (char*)&omni.odom[0];
      senddata[8] = temp[0];
      senddata[9] = temp[1];
      senddata[10] = temp[2];
      senddata[11] = temp[3];
      // temp = (char*)&omni.odom[1];
      senddata[12] = temp[0];
      senddata[13] = temp[1];
      senddata[14] = temp[2];
      senddata[15] = temp[3];
      break;
    case 4:
      senddata[0] = 0xFA;
      senddata[1] = 0xFB;
      senddata[2] = seq + 10;
      senddata[3] = ring_counter;
      // temp = (char*)&omni.odom_speed[0];
      // temp = (char *)&integ.vision_based_position[0];
      senddata[4] = temp[0];
      senddata[5] = temp[1];
      senddata[6] = temp[2];
      senddata[7] = temp[3];
      // temp = (char*)&omni.odom_speed[1];
      // temp = (char *)&integ.vision_based_position[1];
      senddata[8] = temp[0];
      senddata[9] = temp[1];
      senddata[10] = temp[2];
      senddata[11] = temp[3];
      senddata[12] = 1;  // connection.check_ver;
      senddata[13] = 0;
      senddata[14] = 0;
      senddata[15] = 0;
      break;
    default:
      senddata[0] = 0xFA;
      senddata[1] = 0xFB;
      senddata[2] = seq + 100;
      senddata[3] = ring_counter;
      senddata[4] = 1;  // connection.check_ver;
      senddata[5] = 0;
      senddata[6] = 0;
      senddata[7] = 0;
      senddata[8] = 0;
      senddata[9] = 0;
      senddata[10] = 0;
      senddata[11] = 0;
      senddata[12] = 0;
      senddata[13] = 0;
      senddata[14] = 0;
      senddata[15] = 0;
      break;
  }
}

int main()
{
  printf("start");

  int count = 0;
  constexpr int PACKET_SIZE = 16;

  uint8_t Rxbuf[PACKET_SIZE];
  uint8_t Rxdata[PACKET_SIZE];

  int sock;
  struct sockaddr_in addr;
  in_addr_t ipaddr;

  sock = socket(AF_INET, SOCK_DGRAM, 0);

  addr.sin_family = AF_INET;
  addr.sin_port = htons(50100);
  addr.sin_addr.s_addr = inet_addr("224.5.20.100");

  ipaddr = inet_addr("192.168.10.116");
  if (setsockopt(sock, IPPROTO_IP, IP_MULTICAST_IF, (char*)&ipaddr, sizeof(ipaddr)) != 0)
  {
    perror("setsockopt");
    return 1;
  }

int seq = 0;
  while (1)
  {
    count++;
    seq++;
    generatePacket(Rxdata, seq % 5);

    if (count > 20)
    {
      // printf(" S_id=%d", start_byte_idx);

      printf(" %x %x %x %x %x %x %x %x %x %x %x %x %x %x %x %x", Rxdata[0], Rxdata[1], Rxdata[2], Rxdata[3], Rxdata[4],
             Rxdata[5], Rxdata[6], Rxdata[7], Rxdata[8], Rxdata[9], Rxdata[10], Rxdata[11], Rxdata[12], Rxdata[13],
             Rxdata[14], Rxdata[15]);

      Data yaw;
      if (Rxdata[2] == 10)
      {
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

    sendto(sock, Rxdata, PACKET_SIZE, 0, (struct sockaddr*)&addr, sizeof(addr));
    // 10ms待つ
    usleep(10 * 1000);

  }
  close(sock);

  return 0;
}
