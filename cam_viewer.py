# このファイルはCM4カメラ GUI の Qt エントリポイントを担当し、
# 共通通信処理 cm4_camera.py を利用して画像表示、座標表示、HSV 調整を行う。
import io
import sys
import threading
import time

from PIL import Image

from cm4_camera import DEFAULT_MACHINE_NO, apply_hsv_params, build_connection_config, create_coord_socket, fetch_frame

try:
    from PySide6.QtCore import QObject, Qt, Signal
    from PySide6.QtGui import QImage, QPainter, QPen, QPixmap
    from PySide6.QtWidgets import (
        QApplication,
        QFormLayout,
        QGridLayout,
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


FRAME_SIZE = (320, 240)
FRAME_FETCH_INTERVAL = 0.1
HSV_DEFAULTS = {"h_min": 0, "h_max": 15, "s_min": 100, "s_max": 255, "v_min": 100, "v_max": 255}
HSV_LIMITS = {"h_min": 180, "h_max": 180, "s_min": 255, "s_max": 255, "v_min": 255, "v_max": 255}


class ViewerSignals(QObject):
    frame_ready = Signal(str, bytes)
    coords_ready = Signal(str)
    connection_ready = Signal(str)
    message_ready = Signal(str)


class CameraWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.machine_no = DEFAULT_MACHINE_NO
        self.coords_text = "000,000,000,000"
        self.running = True
        self.signals = ViewerSignals()
        self.slider_map = {}

        self._setup_ui()
        self.signals.frame_ready.connect(self.update_frame)
        self.signals.coords_ready.connect(self.update_coords)
        self.signals.connection_ready.connect(self.update_connection_label)
        self.signals.message_ready.connect(self.set_message)

        self.apply_connection(self.machine_no)
        threading.Thread(target=self.frame_loop, args=("raw",), daemon=True).start()
        threading.Thread(target=self.frame_loop, args=("mask",), daemon=True).start()
        threading.Thread(target=self.coord_loop, daemon=True).start()

    def _setup_ui(self):
        self.setWindowTitle("Ball Tracker")
        self.resize(900, 720)

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
        self.raw_label = QLabel()
        self.raw_label.setFixedSize(*FRAME_SIZE)
        self.raw_label.setAlignment(Qt.AlignCenter)
        image_layout.addWidget(self.raw_label)

        self.mask_label = QLabel()
        self.mask_label.setFixedSize(*FRAME_SIZE)
        self.mask_label.setAlignment(Qt.AlignCenter)
        image_layout.addWidget(self.mask_label)
        root_layout.addLayout(image_layout)

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

        apply_button = QPushButton("Apply")
        apply_button.clicked.connect(self.on_apply_hsv)
        root_layout.addWidget(apply_button)

    def closeEvent(self, event):
        self.running = False
        super().closeEvent(event)

    def on_apply_connection(self):
        self.apply_connection(self.machine_spin.value())

    def apply_connection(self, machine_no):
        self.machine_no = machine_no
        config = build_connection_config(machine_no)
        self.coords_text = "000,000,000,000"
        self.signals.connection_ready.emit(
            f"機体{machine_no}: {config['api_server']} / {config['mcast_group']}:{config['mcast_port']}"
        )
        self.signals.coords_ready.emit(self.coords_text)

    def update_connection_label(self, text):
        self.connection_label.setText(text)

    def set_message(self, text):
        self.message_label.setText(text)

    def on_apply_hsv(self):
        hsv_min = [self.slider_map["h_min"].value(), self.slider_map["s_min"].value(), self.slider_map["v_min"].value()]
        hsv_max = [self.slider_map["h_max"].value(), self.slider_map["s_max"].value(), self.slider_map["v_max"].value()]
        machine_no = self.machine_no

        def worker():
            try:
                apply_hsv_params(machine_no, hsv_min, hsv_max)
                self.signals.message_ready.emit(f"機体{machine_no}へ HSV を送信しました")
            except Exception as exc:
                self.signals.message_ready.emit(f"HSV 送信失敗: {exc}")

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
        self.coords_label.setText(f"Coords: {coords_text}")

    def update_frame(self, image_name, image_bytes):
        try:
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB").resize(FRAME_SIZE)
            image_data = image.tobytes("raw", "RGB")
            qimage = QImage(image_data, FRAME_SIZE[0], FRAME_SIZE[1], FRAME_SIZE[0] * 3, QImage.Format_RGB888).copy()
            pixmap = QPixmap.fromImage(qimage)

            coords = self.coords_text.split(",")
            if len(coords) >= 2 and coords[0].isdigit() and coords[1].isdigit():
                x = int(coords[0])
                y = int(coords[1])
                if 0 <= x < FRAME_SIZE[0] and 0 <= y < FRAME_SIZE[1]:
                    painter = QPainter(pixmap)
                    painter.setPen(QPen(Qt.red, 1))
                    painter.drawLine(x, 0, x, FRAME_SIZE[1] - 1)
                    painter.drawLine(0, y, FRAME_SIZE[0] - 1, y)
                    painter.end()

            if image_name == "raw":
                self.raw_label.setPixmap(pixmap)
            else:
                self.mask_label.setPixmap(pixmap)
        except Exception as exc:
            self.set_message(f"Frame render error: {exc}")


def main():
    app = QApplication(sys.argv)
    window = CameraWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
