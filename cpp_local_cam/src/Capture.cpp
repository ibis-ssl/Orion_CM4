
// Capture.cpp
#include "Capture.hpp"

Capture::Capture(int device_id, int width, int height, int fps)
    : cap_(device_id)
{
  cap_.open(device_id, cv::CAP_V4L2);
  if (!cap_.isOpened()) {
    throw std::runtime_error("Cannot open camera");
  }
  cap_.set(cv::CAP_PROP_FRAME_WIDTH, width);
  cap_.set(cv::CAP_PROP_FRAME_HEIGHT, height);
  cap_.set(cv::CAP_PROP_FPS, fps);
  cap_.set(cv::CAP_PROP_FOURCC, cv::VideoWriter::fourcc('M', 'J', 'P', 'G'));
  cap_.set(cv::CAP_PROP_BUFFERSIZE, 1);
}

Capture::~Capture() {
    stop();
    if (cap_.isOpened()) cap_.release();
}

void Capture::start() {
    running_ = true;
    while (running_) {
        if (!cap_.grab()) continue;
        cv::Mat frame;
        if (!cap_.retrieve(frame)) continue;
        std::lock_guard<std::mutex> lock(mtx_);
        latest_.mat = frame;
        latest_.timestamp = std::chrono::steady_clock::now();
    }
}

void Capture::stop() {
    running_ = false;
}

bool Capture::getFrame(Frame &out) {
    std::lock_guard<std::mutex> lock(mtx_);
    if (latest_.mat.empty()) return false;
    out = latest_;
    return true;
}
