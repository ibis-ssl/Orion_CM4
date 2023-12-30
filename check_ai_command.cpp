#include <stdio.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <unistd.h>
#include <stdint.h>
#include <wiringPi.h>
#include <wiringSerial.h>
#include <termios.h>		//ttyパラメータの構造体
#include <iostream>
#include <sstream>

int main()
{
	printf("start");

 	int sock;
 	struct sockaddr_in addr;

	constexpr int PACKET_SIZE = 32;
 	char buf[PACKET_SIZE-2] = {};
 	char senddata[PACKET_SIZE] = {};

    
    wiringPiSetup();
    fflush(stdout);


	while(1){
 		sock = socket(AF_INET, SOCK_DGRAM, 0);

		 addr.sin_family = AF_INET;
		 addr.sin_port = htons(12345);
		 addr.sin_addr.s_addr = INADDR_ANY;
		
		 bind(sock, (struct sockaddr *)&addr, sizeof(addr));
		
		 recv(sock, buf, sizeof(buf), 0);
 	
		std::stringstream ss;
		for(int i=0; i< PACKET_SIZE-2; i++){
			ss << int(buf[i]) << ", ";
		}
		std::cout << ss.str() << std::endl;
		
		senddata[0] = 254;
		// contennt: 1~30 
		for(int i = 1; i< PACKET_SIZE - 1; i++){
			senddata[i]=buf[i-1];	
		}
		senddata[PACKET_SIZE - 1]=253;
	

 		close(sock);
	}

 	return 0;
}

