# このファイルはCM4カメラ GUI の Qt エントリポイントを担当し、
# 共通通信処理 cm4_camera.py を利用して画像表示、座標表示、HSV 調整、ROI からの自動推定を行う。
import argparse
import io
import sys
import threading
import time

from PIL import Image

from cm4_camera import (
    DEFAULT_MACHINE_NO,
    apply_hsv_params,
    build_connection_config,
    create_coord_socket,
    estimate_hsv_params_from_frame_bytes,
    fetch_frame,
    fetch_hsv_params,
)

try:
    from PySide6.QtCore import QObject, QRect, Qt, Signal
    from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
    from PySide6.QtWidgets import (
        QApplication,
        QFormLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QSlider,
        QSpinBox,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:
    raise SystemExit(f"PySide6 is required for cam_viewer.py: {exc}")


SOURCE_FRAME_SIZE = (320, 240)
DISPLAY_FRAME_SIZE = (480, 360)
FRAME_FETCH_INTERVAL = 0.1
HSV_DEFAULTS = {"h_min": 0, "h_max": 15, "s_min": 100, "s_max": 255, "v_min": 100, "v_max": 255}
HSV_LIMITS = {"h_min": 180, "h_max": 180, "s_min": 255, "s_max": 255, "v_min": 255, "v_max": 255}


class FrameLabel(QLabel):
    roi_selected = Signal(tuple)

    def __init__(self):
        super().__init__()
        self.setFixedSize(*DISPLAY_FRAME_SIZE)
        self.setAlignment(Qt.AlignCenter)
        self._pixmap = None
        self._selection_start = None
        self._selection_end = None

    def set_frame_pixmap(self, pixmap):
        self._pixmap = pixmap
        self.update()

    def clear_selection(self):
        self._selection_start = None
        self._selection_end = None
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._selection_start = event.position().toPoint()
            self._selection_end = self._selection_start
            self.update()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._selection_start is not None:
            self._selection_end = event.position().toPoint()
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._selection_start is not None:
            self._selection_end = event.position().toPoint()
            rect = QRect(self._selection_start, self._selection_end).normalized()
            rect = rect.intersected(self.rect())
            if rect.width() > 3 and rect.height() > 3:
                scale_x = SOURCE_FRAME_SIZE[0] / DISPLAY_FRAME_SIZE[0]
                scale_y = SOURCE_FRAME_SIZE[1] / DISPLAY_FRAME_SIZE[1]
                roi = (
                    int(rect.x() * scale_x),
                    int(rect.y() * scale_y),
                    max(1, int(rect.width() * scale_x)),
                    max(1, int(rect.height() * scale_y)),
                )
                self.roi_selected.emit(roi)
            self.update()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        if self._pixmap is not None:
            painter.drawPixmap(0, 0, self._pixmap)
        else:
            painter.fillRect(self.rect(), QColor("#202020"))

        if self._selection_start is not None and self._selection_end is not None:
            rect = QRect(self._selection_start, self._selection_end).normalized()
            painter.setPen(QPen(QColor("#00ff88"), 2))
            painter.drawRect(rect)
        painter.end()


class ViewerSignals(QObject):
    frame_ready = Signal(str, bytes)
    coords_ready = Signal(str)
    connection_ready = Signal(str)
    message_ready = Signal(str)
    params_ready = Signal(dict)
    roi_estimated = Signal(dict)


class CameraWindow(QWidget):
    def __init__(self, machine_no=DEFAULT_MACHINE_NO):
        super().__init__()
        self.machine_no = machine_no
        self.coords_text = "000,000,000,000"
        self.coords_received = False
        self.last_coord_time = 0.0
        self.running = True
        self.signals = ViewerSignals()
        self.slider_map = {}
        self.last_raw_frame_bytes = None

        self._setup_ui()
        self.signals.frame_ready.connect(self.update_frame)
        self.signals.coords_ready.connect(self.update_coords)
        self.signals.connection_ready.connect(self.update_connection_label)
        self.signals.message_ready.connect(self.set_message)
        self.signals.params_ready.connect(self.apply_server_params)
        self.signals.roi_estimated.connect(self.apply_estimated_params)

        self.apply_connection(self.machine_no)
        threading.Thread(target=self.frame_loop, args=("raw",), daemon=True).start()
        threading.Thread(target=self.frame_loop, args=("mask",), daemon=True).start()
        threading.Thread(target=self.coord_loop, daemon=True).start()

    def _setup_ui(self):
        self.setWindowTitle("Ball Tracker")
        self.resize(920, 760)

        root_layout = QVBoxLayout(self)

        header_layout = QHBoxLayout()
        header_layout.addWidget(QLabel("機体番号"))

        self.machine_spin = QSpinBox()
        self.machine_spin.setRange(0, 155)
        self.machine_spin.setValue(self.machine_no)
        header_layout.addWidget(self.machine_spin)

        update_button = QPushButton("接続先を更新")
        update_button.clicked.connect(self.on_apply_connection)
        header_layout.addWidget(update_button)
        header_layout.addStretch()
        root_layout.addLayout(header_layout)

        self.connection_label = QLabel("")
        root_layout.addWidget(self.connection_label)

        self.message_label = QLabel("Ready")
        root_layout.addWidget(self.message_label)

        image_layout = QHBoxLayout()
        self.raw_label = FrameLabel()
        self.raw_label.roi_selected.connect(self.on_roi_selected)
        image_layout.addWidget(self.raw_label)

        self.mask_label = QLabel()
        self.mask_label.setFixedSize(*DISPLAY_FRAME_SIZE)
        self.mask_label.setAlignment(Qt.AlignCenter)
        image_layout.addWidget(self.mask_label)
        root_layout.addLayout(image_layout)

        root_layout.addWidget(QLabel("raw 画像をドラッグすると ROI から HSV を推定します。"))
        self.coords_label = QLabel(f"Coords: {self.coords_text}")
        root_layout.addWidget(self.coords_label)

        hsv_group = QGroupBox("HSV")
        hsv_layout = QFormLayout(hsv_group)
        for key in ("h_min", "h_max", "s_min", "s_max", "v_min", "v_max"):
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, HSV_LIMITS[key])
            slider.setValue(HSV_DEFAULTS[key])
            self.slider_map[key] = slider
            hsv_layout.addRow(key.upper(), slider)
        root_layout.addWidget(hsv_group)

        button_layout = QHBoxLayout()
        estimate_button = QPushButton("ROI 推定値を再適用")
        estimate_button.clicked.connect(self.on_reapply_last_roi)
        button_layout.addWidget(estimate_button)

        apply_button = QPushButton("Apply")
        apply_button.clicked.connect(self.on_apply_hsv)
        button_layout.addWidget(apply_button)
        button_layout.addStretch()
        root_layout.addLayout(button_layout)

        self.last_estimated_params = None

    def closeEvent(self, event):
        self.running = False
        super().closeEvent(event)

    def on_apply_connection(self):
        self.apply_connection(self.machine_spin.value())

    def apply_connection(self, machine_no):
        self.machine_no = machine_no
        config = build_connection_config(machine_no)
        self.coords_text = "000,000,000,000"
        self.coords_received = False
        self.last_coord_time = 0.0
        self.last_raw_frame_bytes = None
        self.last_estimated_params = None
        self.raw_label.clear_selection()
        self.signals.connection_ready.emit(
            f"機体{machine_no}: {config['api_server']} / {config['mcast_group']}:{config['mcast_port']}"
        )
        self.signals.coords_ready.emit(self.coords_text)

        def worker():
            try:
                params = fetch_hsv_params(machine_no)
                self.signals.params_ready.emit(params)
                self.signals.message_ready.emit(f"機体{machine_no} の現在パラメータを取得しました")
            except Exception as exc:
                self.signals.message_ready.emit(f"パラメータ取得失敗: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def update_connection_label(self, text):
        self.connection_label.setText(text)

    def set_message(self, text):
        self.message_label.setText(text)

    def apply_server_params(self, params):
        hsv_min = params.get("hsv_min", [])
        hsv_max = params.get("hsv_max", [])
        if len(hsv_min) != 3 or len(hsv_max) != 3:
            return

        self.slider_map["h_min"].setValue(int(hsv_min[0]))
        self.slider_map["h_max"].setValue(int(hsv_max[0]))
        self.slider_map["s_min"].setValue(int(hsv_min[1]))
        self.slider_map["s_max"].setValue(int(hsv_max[1]))
        self.slider_map["v_min"].setValue(int(hsv_min[2]))
        self.slider_map["v_max"].setValue(int(hsv_max[2]))

    def on_apply_hsv(self):
        hsv_min = [self.slider_map["h_min"].value(), self.slider_map["s_min"].value(), self.slider_map["v_min"].value()]
        hsv_max = [self.slider_map["h_max"].value(), self.slider_map["s_max"].value(), self.slider_map["v_max"].value()]
        machine_no = self.machine_no

        def worker():
            try:
                apply_hsv_params(machine_no, hsv_min, hsv_max)
                self.signals.message_ready.emit(f"機体{machine_no} へ HSV を送信しました")
            except Exception as exc:
                self.signals.message_ready.emit(f"HSV 送信失敗: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def on_roi_selected(self, roi):
        if self.last_raw_frame_bytes is None:
            self.set_message("raw 画像が未取得のため ROI 推定できません")
            return

        frame_bytes = self.last_raw_frame_bytes
        machine_no = self.machine_no

        def worker():
            try:
                estimated = estimate_hsv_params_from_frame_bytes(frame_bytes, roi)
                self.signals.roi_estimated.emit(estimated)
                apply_hsv_params(machine_no, estimated["hsv_min"], estimated["hsv_max"])
                self.signals.message_ready.emit(
                    f"ROI 推定を適用: H={estimated['hsv_min'][0]}..{estimated['hsv_max'][0]} "
                    f"S={estimated['hsv_min'][1]}..{estimated['hsv_max'][1]} "
                    f"V={estimated['hsv_min'][2]}..{estimated['hsv_max'][2]}"
                )
            except Exception as exc:
                self.signals.message_ready.emit(f"ROI 推定失敗: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def apply_estimated_params(self, estimated):
        self.last_estimated_params = estimated
        self.slider_map["h_min"].setValue(int(estimated["hsv_min"][0]))
        self.slider_map["h_max"].setValue(int(estimated["hsv_max"][0]))
        self.slider_map["s_min"].setValue(int(estimated["hsv_min"][1]))
        self.slider_map["s_max"].setValue(int(estimated["hsv_max"][1]))
        self.slider_map["v_min"].setValue(int(estimated["hsv_min"][2]))
        self.slider_map["v_max"].setValue(int(estimated["hsv_max"][2]))

    def on_reapply_last_roi(self):
        if self.last_estimated_params is None:
            self.set_message("再適用できる ROI 推定値がありません")
            return

        estimated = self.last_estimated_params
        machine_no = self.machine_no

        def worker():
            try:
                apply_hsv_params(machine_no, estimated["hsv_min"], estimated["hsv_max"])
                self.signals.message_ready.emit(f"ROI 推定値を機体{machine_no}へ再適用しました")
            except Exception as exc:
                self.signals.message_ready.emit(f"ROI 推定値の再適用失敗: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def frame_loop(self, image_name):
        while self.running:
            machine_no = self.machine_no
            try:
                frame_bytes = fetch_frame(machine_no, image_name)
                if machine_no == self.machine_no:
                    self.signals.frame_ready.emit(image_name, frame_bytes)
            except Exception:
                pass
            time.sleep(FRAME_FETCH_INTERVAL)

    def coord_loop(self):
        sock = None
        active_machine_no = None

        while self.running:
            try:
                if active_machine_no != self.machine_no:
                    if sock is not None:
                        sock.close()
                    sock = create_coord_socket(self.machine_no, receive_timeout=1.0)
                    active_machine_no = self.machine_no

                data, _address = sock.recvfrom(1024)
                self.signals.coords_ready.emit(data.decode())
            except OSError:
                if sock is not None:
                    sock.close()
                sock = None
                active_machine_no = None
                time.sleep(0.5)
            except Exception:
                time.sleep(0.1)

        if sock is not None:
            sock.close()

    def update_coords(self, coords_text):
        self.coords_text = coords_text
        self.coords_received = True
        if coords_text != "000,000,000,000":
            self.last_coord_time = time.time()
        self.coords_label.setText(f"Coords: {coords_text}")

    def update_coords_from_mask(self, image_bytes):
        if time.time() - self.last_coord_time < 1.5:
            return

        mask = Image.open(io.BytesIO(image_bytes)).convert("L")
        bbox = mask.point(lambda value: 255 if value > 0 else 0).getbbox()
        if bbox is None:
            self.coords_text = "000,000,000,mask"
            self.coords_received = True
            self.coords_label.setText(f"Coords: {self.coords_text}")
            return

        left, top, right, bottom = bbox
        x = (left + right - 1) // 2
        y = (top + bottom - 1) // 2
        area = sum(1 for value in mask.getdata() if value > 0)
        self.coords_text = f"{x},{y},{area},mask"
        self.coords_received = True
        self.coords_label.setText(f"Coords: {self.coords_text}")

    def update_frame(self, image_name, image_bytes):
        try:
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB").resize(DISPLAY_FRAME_SIZE)
            image_data = image.tobytes("raw", "RGB")
            qimage = QImage(
                image_data,
                DISPLAY_FRAME_SIZE[0],
                DISPLAY_FRAME_SIZE[1],
                DISPLAY_FRAME_SIZE[0] * 3,
                QImage.Format_RGB888,
            ).copy()
            pixmap = QPixmap.fromImage(qimage)

            coords = self.coords_text.split(",")
            if (
                self.coords_received
                and len(coords) >= 3
                and coords[0].isdigit()
                and coords[1].isdigit()
                and coords[2].isdigit()
                and int(coords[2]) > 0
            ):
                x = int(int(coords[0]) * DISPLAY_FRAME_SIZE[0] / SOURCE_FRAME_SIZE[0])
                y = int(int(coords[1]) * DISPLAY_FRAME_SIZE[1] / SOURCE_FRAME_SIZE[1])
                if 0 <= x < DISPLAY_FRAME_SIZE[0] and 0 <= y < DISPLAY_FRAME_SIZE[1]:
                    painter = QPainter(pixmap)
                    painter.setPen(QPen(Qt.red, 1))
                    painter.drawLine(x, 0, x, DISPLAY_FRAME_SIZE[1] - 1)
                    painter.drawLine(0, y, DISPLAY_FRAME_SIZE[0] - 1, y)
                    painter.end()

            if image_name == "raw":
                self.last_raw_frame_bytes = image_bytes
                self.raw_label.set_frame_pixmap(pixmap)
            else:
                self.update_coords_from_mask(image_bytes)
                self.mask_label.setPixmap(pixmap)
        except Exception as exc:
            self.set_message(f"Frame render error: {exc}")


def main():
    parser = argparse.ArgumentParser(description="CM4 camera debug viewer")
    parser.add_argument("--machine-no", type=int, default=DEFAULT_MACHINE_NO)
    args, qt_args = parser.parse_known_args()

    app = QApplication([sys.argv[0], *qt_args])
    window = CameraWindow(args.machine_no)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
