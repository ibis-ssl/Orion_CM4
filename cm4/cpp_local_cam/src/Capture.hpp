// Capture.hpp
#ifndef CAPTURE_HPP
#define CAPTURE_HPP

#include <opencv2/opencv.hpp>
#include <mutex>
#include <atomic>

struct Frame {
    cv::Mat mat;
    std::chrono::steady_clock::time_point timestamp;
};

class Capture {
public:
    /**
     * @brief Construct Capture with device index and settings
     * @param device_id Camera index (e.g., 0)
     * @param width Desired frame width
     * @param height Desired frame height
     * @param fps Desired capture frame rate
     */
    Capture(int device_id, int width, int height, int fps);
    ~Capture();

    // Non-copyable
    Capture(const Capture&) = delete;
    Capture& operator=(const Capture&) = delete;

    /** Start the capture loop in calling thread */
    void start();
    /** Stop the capture loop */
    void stop();
    /** Retrieve latest frame; returns true if available */
    bool getFrame(Frame &out);

  private:
    cv::VideoCapture cap_;
    std::mutex  mtx_;
    Frame       latest_;
    std::atomic<bool> running_{false};
};

#endif // CAPTURE_HPP
