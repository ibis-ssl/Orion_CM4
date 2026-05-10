// Detector.cpp
#include "Detector.hpp"

Detector::Detector() : hsv_min_(5, 100, 100), hsv_max_(15, 255, 255) {}

Detector::~Detector() = default;

void Detector::process(const Frame & frame)
{
  cv::Mat hsv, mask;
  {  // 閾値部分は mutex で保護
    std::lock_guard<std::mutex> lock(mtx_);
    cv::cvtColor(frame.mat, hsv, cv::COLOR_BGR2HSV);
    cv::inRange(hsv, hsv_min_, hsv_max_, mask);
    // mask をキャッシュ
    mask.copyTo(last_mask_);
  }
  cv::morphologyEx(mask, mask, cv::MORPH_OPEN, cv::Mat::ones(3, 3, CV_8U));

  std::vector<std::vector<cv::Point>> cnts;
  cv::findContours(mask, cnts, cv::RETR_EXTERNAL, cv::CHAIN_APPROX_SIMPLE);
  if (!cnts.empty()) {
    auto c = *std::max_element(cnts.begin(), cnts.end(), [](auto & a, auto & b) { return cv::contourArea(a) < cv::contourArea(b); });
    cv::Moments m = cv::moments(c);
    if (m.m00 > 0) {
      int x = static_cast<int>(m.m10 / m.m00);
      int y = static_cast<int>(m.m01 / m.m00);
      double area = cv::contourArea(c);
      last_x_ = x;
      last_y_ = y;
      last_area_ = area;
      ++count_;
    }
  }
}

std::tuple<int, int, int> Detector::getLast() const { return {last_x_.load(), last_y_.load(), static_cast<int>(last_area_.load())}; }

std::size_t Detector::getCount() const { return count_.load(); }

void Detector::setHsvMin(int h, int s, int v)
{
  std::lock_guard<std::mutex> lock(mtx_);
  hsv_min_ = cv::Scalar(h, s, v);
}

void Detector::setHsvMax(int h, int s, int v)
{
  std::lock_guard<std::mutex> lock(mtx_);
  hsv_max_ = cv::Scalar(h, s, v);
}

std::tuple<int, int, int> Detector::getHsvMin() const
{
  std::lock_guard<std::mutex> lock(mtx_);
  return {static_cast<int>(hsv_min_[0]), static_cast<int>(hsv_min_[1]), static_cast<int>(hsv_min_[2])};
}

std::tuple<int, int, int> Detector::getHsvMax() const
{
  std::lock_guard<std::mutex> lock(mtx_);
  return {static_cast<int>(hsv_max_[0]), static_cast<int>(hsv_max_[1]), static_cast<int>(hsv_max_[2])};
}

cv::Mat Detector::getLastMask() const
{
  std::lock_guard<std::mutex> lock(mtx_);
  return last_mask_.clone();  // 呼び出し側で自由に加工できるようコピー
}
