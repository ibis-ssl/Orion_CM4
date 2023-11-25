#include <stdio.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <stdint.h>
#include <wiringPi.h>
#include <wiringSerial.h>
#include <termios.h>		//ttyパラメータの構造体

int main()
{

	printf("start");

 int sock;
 struct sockaddr_in addr;
 int count=0;
constexpr int PACKET_SIZE = 128;

 char Rxbuf[PACKET_SIZE];
 char Rxdata[PACKET_SIZE-2];

  int fd = serialOpen("/dev/serial0",921600);    
    
    wiringPiSetup();
    fflush(stdout);
    
    if(fd<0){
        printf("can not open serialport");
    }


while(1){
 while(serialDataAvail(fd)>PACKET_SIZE){
	
		for(int i = 0; i< PACKET_SIZE; i++){
			Rxbuf[i]=serialGetchar(fd);;	
		}
		
		uint8_t start_byte_idx = 0;

		while ((Rxbuf[start_byte_idx] != 0xFE  && Rxbuf[start_byte_idx+1] != 0xFC ) && start_byte_idx < sizeof(Rxbuf)) {
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
							Rxdata[k] = Rxbuf[k - (sizeof(Rxbuf) - start_byte_idx)];
						}

						else {
							Rxdata[k] = Rxbuf[start_byte_idx + k + 2];
						}

					}

		}

		count++;
		
		if(count>100){
			int yaw=Rxdata[3]+(Rxdata[2]<<8)-360;
			
			printf(" S_id=%d",start_byte_idx);
			printf(" %d %d %d %d %d %d %d",Rxdata[0],Rxdata[1],Rxdata[2],Rxdata[3],Rxdata[4],Rxdata[5],Rxdata[6]);
			printf(" yaw=%d Power_V=%d",yaw,Rxdata[6]);
			printf("\n");
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

			sendto(sock, Rxdata, PACKET_SIZE-2, 0, (struct sockaddr *)&addr, sizeof(addr));
			 close(sock);
    
    
    
    }
		   
}

 return 0;
}

