#ifndef APISERVER_HPP
#define APISERVER_HPP

#include <pistache/endpoint.h>
#include <pistache/router.h>

#include <thread>

#include "Capture.hpp"
#include "Detector.hpp"

class ApiServer
{
public:
  ApiServer(Capture & capture, Detector & detector, int port);
  ~ApiServer();

  // サーバ開始／停止
  void start();
  void stop();

private:
  // ルート登録
  void setupRoutes(Pistache::Rest::Router & router);

  Capture & capture_;
  Detector & detector_;
  int port_;
  std::unique_ptr<Pistache::Http::Endpoint> httpEndpoint_;
  std::thread serverThread_;
};

#endif  // APISERVER_HPP
