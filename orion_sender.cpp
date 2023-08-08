#include <stdio.h>
#include <string.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <sys/ioctl.h>
#include <unistd.h>
#include <stdint.h>
#include <wiringPi.h>
#include <wiringSerial.h>
#include <termios.h>		//ttyパラメータの構造体
#include <iostream>
#include <sstream>

int
main()
{
	printf("start");


	int sock1,sock2;
	struct sockaddr_in addr1;
	struct sockaddr_in addr2;

	char rx_buf_ball[7]= {};
	constexpr int PACKET_SIZE = 64;
	char buf[PACKET_SIZE-2] = {};
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

	/*
	ここで、ノンブロッキングに設定しています。
	val = 0でブロッキングモードに設定できます。
	ソケットの初期設定はブロッキングモードです。
	*/
	val = 1;
	ioctl(sock1, FIONBIO, &val);
	ioctl(sock2, FIONBIO, &val);


	int fd = serialOpen("/dev/serial0",921600);    

	wiringPiSetup();
	fflush(stdout);

	if(fd<0){
		printf("can not open serialport");
	}


	while (1) {
		n = recv(sock1, buf, sizeof(buf), 0);
		n = recv(sock2, rx_buf_ball, sizeof(rx_buf_ball), 0);

		if(cnt>100){
			std::stringstream ss;
			for(int i=0; i< PACKET_SIZE-2; i++){
				ss << buf[i] << ", ";
			}
			std::cout << ss.str() << std::endl;

			printf("%x %x %x %x %x %x %d\n", rx_buf_ball[0], rx_buf_ball[1], rx_buf_ball[2],
					 rx_buf_ball[3], rx_buf_ball[4], rx_buf_ball[5], rx_buf_ball[6]);
			cnt=0;
		}
		cnt++;

		senddata[0] = 254;
		// contennt: 1~30 
		for(int i = 1; i< PACKET_SIZE - 6; i++){
			senddata[i]=buf[i-1];	
		}

		senddata[PACKET_SIZE - 8]=rx_buf_ball[0];
		senddata[PACKET_SIZE - 7]=rx_buf_ball[1];
		senddata[PACKET_SIZE - 6]=rx_buf_ball[2];
		senddata[PACKET_SIZE - 5]=rx_buf_ball[3];
		senddata[PACKET_SIZE - 4]=rx_buf_ball[4];
		senddata[PACKET_SIZE - 3]=rx_buf_ball[5];
		senddata[PACKET_SIZE - 2]=rx_buf_ball[6];

		senddata[PACKET_SIZE - 1]=253;
	
		for(int i=0; i< 64; i++){
			serialPutchar(fd,senddata[i]);	
		}


		/* 100Hz */
		sleep(0.005);
}

close(sock1);
close(sock2);

 return 0;
}
