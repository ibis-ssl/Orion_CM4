#include <stdio.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <netinet/in.h>
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

 char buf[12];
 char senddata[14];

  int fd = serialOpen("/dev/serial0",921600);    
    
    wiringPiSetup();
    fflush(stdout);
    
    if(fd<0){
        printf("can not open serialport");
    }


while(1){
 sock = socket(AF_INET, SOCK_DGRAM, 0);

 addr.sin_family = AF_INET;
 addr.sin_port = htons(12345);
 addr.sin_addr.s_addr = INADDR_ANY;

 bind(sock, (struct sockaddr *)&addr, sizeof(addr));

 recv(sock, buf, sizeof(buf), 0);
 
	printf(
	  "=%x =%x =%x =%x =%x =%x =%x =%x =%x =%x =%x =%x", static_cast<int>(buf[0]),
	  buf[1], buf[2], buf[3], buf[4], buf[5],
	  buf[6], buf[7], buf[8], buf[9], buf[10],
	  buf[11]);
	printf("\n");
	
	senddata[0]=254;
	senddata[1]=buf[0];
	senddata[2]=buf[1];
	senddata[3]=buf[2];
	senddata[4]=buf[3];
	senddata[5]=buf[4];
	senddata[6]=buf[5];
	senddata[7]=buf[6];
	senddata[8]=buf[7];
	senddata[9]=buf[8];
	senddata[10]=buf[9];
	senddata[11]=buf[10];
	senddata[12]=buf[11];
	senddata[13]=253;
	
	serialPutchar(fd,senddata[0]);
	serialPutchar(fd,senddata[1]);
	serialPutchar(fd,senddata[2]);
	serialPutchar(fd,senddata[3]);
	serialPutchar(fd,senddata[4]);
	serialPutchar(fd,senddata[5]);
	serialPutchar(fd,senddata[6]);
	serialPutchar(fd,senddata[7]);
	serialPutchar(fd,senddata[8]);
	serialPutchar(fd,senddata[9]);
	serialPutchar(fd,senddata[10]);
	serialPutchar(fd,senddata[11]);
	serialPutchar(fd,senddata[12]);
	serialPutchar(fd,senddata[13]);

 close(sock);
}

 return 0;
}

