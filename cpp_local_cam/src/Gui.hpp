// Gui.hpp
#ifndef GUI_HPP
#define GUI_HPP

#include <QGridLayout>
#include <QGroupBox>
#include <QImage>
#include <QLabel>
#include <QMainWindow>
#include <QPainter>
#include <QPen>
#include <QPixmap>
#include <QSlider>
#include <QTimer>

#include "Capture.hpp"
#include "Detector.hpp"

// GUI クラスは Qt のメタオブジェクトシステムを使うため Q_OBJECT を追加
class Gui : public QMainWindow
{
  Q_OBJECT
public:
  Gui(Capture & capture, Detector & detector, QWidget * parent = nullptr);
  ~Gui() override;

  // Qt アプリケーションの実行
  void run();

private slots:
  void updateFrame();
  void onThresholdChanged();

private:
  Capture & capture_;
  Detector & detector_;
  QTimer * timer_;
  QLabel * frameLabel_;
  QLabel * maskLabel_;
  QLabel * infoLabel_;
  QSlider *hMinSlider_, *hMaxSlider_;
  QSlider *sMinSlider_, *sMaxSlider_;
  QSlider *vMinSlider_, *vMaxSlider_;

  void setupControls(QGridLayout * layout);
};

#endif  // GUI_HPP
