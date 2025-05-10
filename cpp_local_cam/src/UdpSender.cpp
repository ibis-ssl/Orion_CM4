
// UdpSender.cpp
#include "UdpSender.hpp"
#include <arpa/inet.h>
#include <cstring>
#include <unistd.h>
#include <stdexcept>
#include <sstream>

UdpSender::UdpSender(const std::string &multicast_ip, int port) {
    // Create UDP socket
    sock_ = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (sock_ < 0) {
        throw std::runtime_error("Failed to create UDP socket");
    }
    
    // Set multicast TTL to 1
    unsigned char ttl = 1;
    if (setsockopt(sock_, IPPROTO_IP, IP_MULTICAST_TTL, &ttl, sizeof(ttl)) < 0) {
        close(sock_);
        throw std::runtime_error("Failed to set multicast TTL");
    }

    // Initialize destination address
    std::memset(&addr_, 0, sizeof(addr_));
    addr_.sin_family = AF_INET;
    addr_.sin_port = htons(port);
    if (inet_pton(AF_INET, multicast_ip.c_str(), &addr_.sin_addr) != 1) {
        close(sock_);
        throw std::runtime_error("Invalid multicast IP address");
    }
}

UdpSender::~UdpSender() {
    if (sock_ >= 0) {
        close(sock_);
    }
}

void UdpSender::send(int x, int y, double area) {
    // Format message
    std::ostringstream oss;
    oss << x << "," << y << "," << static_cast<int>(area);
    std::string msg = oss.str();
    
    // Send
    int ret = ::sendto(sock_, msg.c_str(), msg.size(), 0,
                       reinterpret_cast<struct sockaddr*>(&addr_), sizeof(addr_));
    if (ret < 0) {
        // Silent failure or log as needed
    }
}