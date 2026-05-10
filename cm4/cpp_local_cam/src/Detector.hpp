// Detector.hpp
#ifndef DETECTOR_HPP
#define DETECTOR_HPP

#include <atomic>
#include <mutex>
#include <opencv2/opencv.hpp>
#include <tuple>

#include "Capture.hpp"  // Frame 定義

class Detector
{
public:
  Detector();
  ~Detector();

  // フレーム処理（内部で mask_ にキャッシュ）
  void process(const Frame & frame);

  // 検出結果取得
  std::tuple<int, int, int> getLast() const;
  std::size_t getCount() const;

  // HSV 閾値設定／取得
  void setHsvMin(int h, int s, int v);
  void setHsvMax(int h, int s, int v);
  std::tuple<int, int, int> getHsvMin() const;
  std::tuple<int, int, int> getHsvMax() const;

  // ←新規追加：最新マスクを取得（GUI 用）
  cv::Mat getLastMask() const;

private:
  mutable std::mutex mtx_;
  cv::Scalar hsv_min_, hsv_max_;
  std::atomic<int> last_x_{0}, last_y_{0};
  std::atomic<double> last_area_{0};
  std::atomic<std::size_t> count_{0};

  // キャッシュされた二値化マスク
  cv::Mat last_mask_;
};

#endif  // DETECTOR_HPP
