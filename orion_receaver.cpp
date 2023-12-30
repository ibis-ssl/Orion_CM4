#include <stdio.h>
#include <stdlib.h>
#include <cstring>
#include <sys/types.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <stdint.h>
#include <termios.h>		//ttyパラメータの構造体
#include <sys/ioctl.h>
#include <fcntl.h>

#define SERIAL_PORT "/dev/serial0"

union Data
{
    float f;
    char b[4];
};

int main()
{

	printf("start");

 int sock;
 struct sockaddr_in addr;
 int count=0;
constexpr int PACKET_SIZE = 16;

 char Rxbuf[PACKET_SIZE];
 char Rxdata[PACKET_SIZE];


	unsigned char msg[] = "serial port open...\n";
	int fd;                             // ファイルディスクリプタ
	struct termios tio;                 // シリアル通信設定
	int baudRate = B115200;
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

	ioctl(fd, TCSETS, &tio);            // ポートの設定を有効にする


while(1){
		
        read(fd, Rxbuf, sizeof(Rxbuf));
	    
		uint8_t start_byte_idx = 0;

		while ((!(Rxbuf[start_byte_idx] == 0xFA && Rxbuf[start_byte_idx+1] == 0xFB)) && start_byte_idx < sizeof(Rxbuf)) {
			start_byte_idx++;
			}
		if (start_byte_idx >= sizeof(Rxbuf)) {
		for (uint8_t k = 0; k < (sizeof(Rxdata)); k++) {
			Rxdata[k] = 0;
		}
		//受信なしデータクリア
		} else {
			for (uint8_t k = 0; k < sizeof(Rxbuf); k++) {
						if ((start_byte_idx + k) >= sizeof(Rxbuf)) {
							Rxdata[k] = Rxbuf[k - (sizeof(Rxbuf) - start_byte_idx )];
						}

						else {
							Rxdata[k] = Rxbuf[start_byte_idx + k];
						}

					}

		}

		count++;
		
		if(count>20){
			
			printf(" S_id=%d",start_byte_idx);
			
			printf(" %x %x %x %x %x %x %x %x %x %x %x %x %x %x %x %x",Rxdata[0],Rxdata[1],Rxdata[2],Rxdata[3],Rxdata[4],Rxdata[5],Rxdata[6]
			,Rxdata[7],Rxdata[8],Rxdata[9],Rxdata[10],Rxdata[11],Rxdata[12],Rxdata[13],Rxdata[14],Rxdata[15]);
			
			Data yaw;
			if(Rxdata[2]==10){
				yaw.b[0]=Rxdata[4];
				yaw.b[1]=Rxdata[5];
				yaw.b[2]=Rxdata[6];
				yaw.b[3]=Rxdata[7];
				}

			printf(" yaw=%3.3f" ,yaw.f);
			printf("\n");
			fflush (stdout) ;
			count=0;
		}
		
		
			int sock;
			struct sockaddr_in addr;
			in_addr_t ipaddr;

			sock = socket(AF_INET, SOCK_DGRAM, 0);

			addr.sin_family = AF_INET;
			addr.sin_port = htons(50102);
			addr.sin_addr.s_addr = inet_addr("224.5.20.102");

			ipaddr = inet_addr("192.168.20.102");
			if (setsockopt(sock,
					IPPROTO_IP,
					IP_MULTICAST_IF,
					(char *)&ipaddr, sizeof(ipaddr)) != 0) {
				perror("setsockopt");
				return 1;
			 }

			sendto(sock, Rxdata, PACKET_SIZE, 0, (struct sockaddr *)&addr, sizeof(addr));
			 close(sock);
		   
}
close(fd);
 return 0;
}

