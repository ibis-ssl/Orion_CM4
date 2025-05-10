#include <algorithm>
#include <atomic>
#include <chrono>
#include <csignal>
#include <iostream>
#include <string>
#include <thread>

#include "ApiServer.hpp"
#include "Capture.hpp"
#include "Detector.hpp"
#include "Gui.hpp"
#include "UdpSender.hpp"
#include <QApplication>

std::atomic<bool> running(true);

void signal_handler(int) { running = false; }

void headless_report(Detector & detector)
{
  int prev_count = 0;
  while (running) {
    std::this_thread::sleep_for(std::chrono::seconds(1));
    int count = detector.getCount();
    int fps = count - prev_count;
    prev_count = count;
    auto [x, y, area] = detector.getLast();
    std::cout << "x=" << x << ", y=" << y << ", area=" << area << ", fps=" << fps << std::endl;
  }
}

int main(int argc, char * argv[])
{
  // 引数解析 (--gui)
  bool use_gui = false;
  for (int i = 1; i < argc; ++i) {
    std::string arg = argv[i];
    if (arg == "--gui") {
      use_gui = true;
      break;
    }
  }

  // シグナルハンドラセット
  std::signal(SIGINT, signal_handler);
  std::signal(SIGTERM, signal_handler);

  // モジュール初期化
  Capture capture(0, 320, 180, 120);
  Detector detector;
  UdpSender sender("239.255.0.1", 5005);
  ApiServer api(capture, detector, 8001);

  // スレッド起動
  std::thread cap_thread([&]() { capture.start(); });
  std::thread det_thread([&]() {
    while (running) {
      Frame frame;
      if (capture.getFrame(frame)) {
         detector.process(frame);
         auto [x, y, area] =detector.getLast(); sender.send(x, y, area);
      }
    }
  });
  std::thread api_thread([&]() { api.start(); });

  if (use_gui) {
    QApplication app(argc, argv);
    Gui gui(capture, detector);
    gui.run();
    running = false;
    app.exec();
  } else {
    // ヘッドレスレポート
    headless_report(detector);
  }

  // シャットダウン処理
  capture.stop();
  api.stop();
  if (cap_thread.joinable()) cap_thread.join();
  if (det_thread.joinable()) det_thread.join();
  if (api_thread.joinable()) api_thread.join();

  return 0;
}
