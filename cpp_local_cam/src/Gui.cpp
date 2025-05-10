
// Gui.cpp
#include "Gui.hpp"

#include <QApplication>
#include <QImage>
#include <QPainter>
#include <opencv2/imgproc.hpp>

Gui::Gui(Capture & capture, Detector & detector, QWidget * parent) : QMainWindow(parent), capture_(capture), detector_(detector)
{
  QWidget * central = new QWidget(this);
  QVBoxLayout * mainLayout = new QVBoxLayout(central);

  // Video display
  frameLabel_ = new QLabel;
  maskLabel_ = new QLabel;
  QHBoxLayout * videoLayout = new QHBoxLayout;
  videoLayout->addWidget(frameLabel_);
  videoLayout->addWidget(maskLabel_);
  mainLayout->addLayout(videoLayout);

  // Info
  infoLabel_ = new QLabel("FPS: -- Area: --");
  mainLayout->addWidget(infoLabel_);

  // Controls
  QGroupBox * ctrlBox = new QGroupBox("HSV Thresholds");
  QGridLayout * grid = new QGridLayout(ctrlBox);
  hMinSlider_ = new QSlider(Qt::Horizontal);
  hMaxSlider_ = new QSlider(Qt::Horizontal);
  sMinSlider_ = new QSlider(Qt::Horizontal);
  sMaxSlider_ = new QSlider(Qt::Horizontal);
  vMinSlider_ = new QSlider(Qt::Horizontal);
  vMaxSlider_ = new QSlider(Qt::Horizontal);
  // configure sliders
  struct
  {
    QSlider * s;
    int max;
    int val;
  } cfg[] = {
    {hMinSlider_, 180, 5}, {hMaxSlider_, 180, 15}, {sMinSlider_, 255, 100}, {sMaxSlider_, 255, 255}, {vMinSlider_, 255, 100}, {vMaxSlider_, 255, 255},
  };
  for (int i = 0; i < 6; i++) {
    cfg[i].s->setRange(0, cfg[i].max);
    cfg[i].s->setValue(cfg[i].val);
    connect(cfg[i].s, &QSlider::valueChanged, this, &Gui::onThresholdChanged);
    grid->addWidget(new QLabel(QString::fromStdString(std::string(" ") + "")), i, 0);
    grid->addWidget(cfg[i].s, i, 1);
  }
  mainLayout->addWidget(ctrlBox);

  setCentralWidget(central);

  // Timer
  timer_ = new QTimer(this);
  connect(timer_, &QTimer::timeout, this, &Gui::updateFrame);
  timer_->start(50);
}

Gui::~Gui() {}

void Gui::run() { show(); }
void Gui::updateFrame()
{
  // キャプチャされた最新フレームは既に Detector::process スレッドで処理済みと仮定
  Frame frame;
  if (!capture_.getFrame(frame)) return;

  // ① 検出処理は外部スレッドで済んでいる想定 → ここでは結果だけ取得
  auto [x, y, area] = detector_.getLast();

  // ② マスクを取得して GUI 上に表示
  cv::Mat mask = detector_.getLastMask();  // 8-bit single channel
  if (!mask.empty()) {
    // QImage に変換して maskLabel_ にセット
    QImage qmask(mask.data, mask.cols, mask.rows, static_cast<int>(mask.step), QImage::Format_Grayscale8);
    maskLabel_->setPixmap(QPixmap::fromImage(qmask).scaled(maskLabel_->size(), Qt::KeepAspectRatio));
  }

  // ③ 元画像の上に十字線と FPS/Area 表示
  QImage qim(frame.mat.data, frame.mat.cols, frame.mat.rows, static_cast<int>(frame.mat.step), QImage::Format_BGR888);
  QPixmap pix = QPixmap::fromImage(qim).scaled(frameLabel_->size(), Qt::KeepAspectRatio);
  QPainter painter(&pix);
  painter.setPen(QPen(Qt::red, 1));
  painter.drawLine(x, 0, x, pix.height());
  painter.drawLine(0, y, pix.width(), y);
  painter.end();
  frameLabel_->setPixmap(pix);

  // Update info
  static double fps = 0;
  static auto last = std::chrono::steady_clock::now();
  auto now = std::chrono::steady_clock::now();
  double dt = std::chrono::duration<double>(now - last).count();
  last = now;
  fps = 0.9 * fps + 0.1 * (1.0 / dt);
  infoLabel_->setText(QString("FPS: %1 Area: %2").arg(fps, 0, 'f', 1).arg(static_cast<int>(area)));
}

void Gui::onThresholdChanged()
{
  detector_.setHsvMin(hMinSlider_->value(), sMinSlider_->value(), vMinSlider_->value());
  detector_.setHsvMax(hMaxSlider_->value(), sMaxSlider_->value(), vMaxSlider_->value());
}

// main integration in main.cpp will call Gui::run() inside QApplication
