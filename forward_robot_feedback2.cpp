#include <arpa/inet.h>
#include <boost/array.hpp>
#include <boost/asio.hpp>
#include <boost/bind/bind.hpp>
#include <iostream>
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


using namespace boost::asio;

using namespace boost::asio;


#define SERIAL_PORT "/dev/serial0"

union Data {
    float f;
    char b[4];
};
int count = 0;
constexpr int PACKET_SIZE = 16;

char Rxbuf[PACKET_SIZE];
char Rxdata[PACKET_SIZE];
uint8_t start_byte_idx = 0;

int sock;
struct sockaddr_in addr;
in_addr_t ipaddr;

void read_some_handler(const boost::system::error_code& error, std::size_t len) {

}


int main() {

printf("start");

/**
   * シリアル通信の設定
   */
boost::asio::io_service io;
  // Open serial port
boost::asio::serial_port serial(io, SERIAL_PORT);
// Configure basic serial port parameters: 115.2kBaud, 8N1
serial.set_option(boost::asio::serial_port_base::baud_rate(921600));
serial.set_option(boost::asio::serial_port_base::character_size(8 /* data bits */));
serial.set_option(boost::asio::serial_port_base::parity(boost::asio::serial_port_base::parity::none));
serial.set_option(boost::asio::serial_port_base::stop_bits(boost::asio::serial_port_base::stop_bits::one));


/**
   * UDP通信の設定
   */

sock = socket(AF_INET, SOCK_DGRAM, 0);

addr.sin_family = AF_INET;
addr.sin_port = htons(50104);
addr.sin_addr.s_addr = inet_addr("224.5.20.104");

ipaddr = inet_addr("192.168.20.104");
  if (setsockopt(sock, IPPROTO_IP, IP_MULTICAST_IF, (char *)&ipaddr,
                sizeof(ipaddr)) != 0) {
    perror("setsockopt");
    return 1;
    }

boost::array<char, 16> receive_api_frame;
boost::array<char, 16> buf;

while(1){

		size_t total_length = 0;
		//for(;;) {
			size_t length = serial.read_some(buffer(buf));
			for (size_t i = 0; i < length; ++i){
				receive_api_frame[total_length + i] = buf[i];
			}
			total_length += length;

			// 受信データ長チェック
			const int NOT_COUNTED_FRAME_LENGTH = 4;
			int received_length = static_cast<size_t>(receive_api_frame[2]) + NOT_COUNTED_FRAME_LENGTH;
			if (receive_api_frame[2] != 0xff && total_length == received_length) break;
		//}
        

       // std::cout.write(buf.data(), len);
        for (std::size_t i = 0; i < receive_api_frame.size() - 1; ++i){
            Rxbuf[i]=receive_api_frame[i];
        };


    while ((!(Rxbuf[start_byte_idx] == 0xAB &&
                Rxbuf[start_byte_idx + 1] == 0xEA)) &&
                start_byte_idx < sizeof(Rxbuf)) {
                start_byte_idx++;
    }
    if (start_byte_idx >= sizeof(Rxbuf)) {
        for (uint8_t k = 0; k < (sizeof(Rxdata)); k++) {
            Rxdata[k] = 0;
        }
    } else {
    for (uint8_t k = 0; k < sizeof(Rxbuf); k++) {
        if ((start_byte_idx + k) >= sizeof(Rxbuf)){
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

    printf(" yaw=%3.3f", yaw.f);
    printf("\n");
    fflush(stdout);
    count = 0;
    }

    sendto(sock, Rxdata, PACKET_SIZE, 0, (struct sockaddr *)&addr,sizeof(addr));

}


close(sock);

return 0;
}