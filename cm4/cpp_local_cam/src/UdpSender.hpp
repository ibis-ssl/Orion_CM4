// UdpSender.hpp
#ifndef UDPSENDER_HPP
#define UDPSENDER_HPP

#include <arpa/inet.h>   // inet_pton
#include <netinet/in.h>  // sockaddr_in
#include <sys/socket.h>  // socket, AF_INET

#include <string>

class UdpSender
{
public:
  UdpSender(const std::string & multicast_ip, int port);
  ~UdpSender();

  UdpSender(const UdpSender &) = delete;
  UdpSender & operator=(const UdpSender &) = delete;

  void send(int x, int y, double area);

private:
  int sock_;
  struct sockaddr_in addr_;
};

#endif  // UDPSENDER_HPP
