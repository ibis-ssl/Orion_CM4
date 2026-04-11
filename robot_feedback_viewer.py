# このファイルは robot feedback の受信結果を Qt GUI で表示し、
# 主要な時系列値をグラフにプロットするホスト PC 側ツールを担当する。
from __future__ import annotations

import argparse
from collections import deque
import socket
import sys
import threading
import time

from robot_feedback_packet import PACKET_SIZE, RobotFeedbackPacket, TX_VALUE_LABELS, decode_robot_feedback_packet
from robot_feedback_receiver import (
    DEFAULT_INTERFACE_IP,
    RECEIVE_BUFFER_SIZE,
    multicast_endpoint,
    open_multicast_socket,
)

try:
    from PySide6.QtCore import QObject, QTimer, Qt, Signal
    from PySide6.QtGui import QColor, QPainter, QPen
    from PySide6.QtWidgets import (
        QApplication,
        QComboBox,
        QFormLayout,
        QGridLayout,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QSpinBox,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:
    raise SystemExit(f"PySide6 is required for robot_feedback_viewer.py: {exc}")


DEFAULT_MACHINE_NO = 10
DEFAULT_HISTORY_SIZE = 300
DEFAULT_RECEIVE_TIMEOUT = 1.0
CONNECT_PROBE_PORT = 8000
PLOT_BACKGROUND = QColor("#f7f7f2")
PLOT_AXIS = QColor("#4a4f54")
PLOT_GRID = QColor("#d4d6d0")
PLOT_COLORS = (
    QColor("#006b5f"),
    QColor("#c43c35"),
    QColor("#1f6fb2"),
    QColor("#8c6a00"),
)


def infer_interface_ip(machine_no: int) -> str:
    target_ip = f"192.168.20.{100 + machine_no}"
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((target_ip, CONNECT_PROBE_PORT))
        return sock.getsockname()[0]
    except OSError:
        return DEFAULT_INTERFACE_IP
    finally:
        sock.close()


class PlotWidget(QWidget):
    def __init__(self, title: str, labels: tuple[str, ...], history_size: int, y_range: tuple[float, float] | None = None):
        super().__init__()
        self.title = title
        self.labels = labels
        self.history_size = history_size
        self.y_range = y_range
        self.series = {label: deque(maxlen=history_size) for label in labels}
        self.setMinimumHeight(170)

    def clear(self) -> None:
        for values in self.series.values():
            values.clear()
        self.update()

    def append(self, values: dict[str, float]) -> None:
        for label in self.labels:
            self.series[label].append(float(values[label]))
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), PLOT_BACKGROUND)

        margin_left = 54
        margin_right = 12
        margin_top = 28
        margin_bottom = 28
        plot_rect = self.rect().adjusted(margin_left, margin_top, -margin_right, -margin_bottom)

        painter.setPen(QPen(PLOT_AXIS, 1))
        painter.drawRect(plot_rect)
        painter.drawText(10, 18, self.title)

        all_values = [value for values in self.series.values() for value in values]
        if not all_values:
            painter.drawText(plot_rect, Qt.AlignCenter, "waiting for packets")
            painter.end()
            return

        if self.y_range is None:
            min_value = min(all_values)
            max_value = max(all_values)
            if min_value == max_value:
                min_value -= 1.0
                max_value += 1.0
            padding = (max_value - min_value) * 0.08
            min_value -= padding
            max_value += padding
        else:
            min_value, max_value = self.y_range

        for i in range(1, 4):
            y = plot_rect.top() + int(plot_rect.height() * i / 4)
            painter.setPen(QPen(PLOT_GRID, 1))
            painter.drawLine(plot_rect.left(), y, plot_rect.right(), y)

        painter.setPen(QPen(PLOT_AXIS, 1))
        painter.drawText(4, plot_rect.top() + 8, f"{max_value:.2f}")
        painter.drawText(4, plot_rect.bottom(), f"{min_value:.2f}")

        for index, label in enumerate(self.labels):
            values = list(self.series[label])
            color = PLOT_COLORS[index % len(PLOT_COLORS)]
            painter.setPen(QPen(color, 2))

            if len(values) >= 2:
                last_x = plot_rect.left()
                last_y = self._map_y(values[0], min_value, max_value, plot_rect)
                for value_index, value in enumerate(values[1:], start=1):
                    x = plot_rect.left() + int(plot_rect.width() * value_index / max(1, self.history_size - 1))
                    y = self._map_y(value, min_value, max_value, plot_rect)
                    painter.drawLine(last_x, last_y, x, y)
                    last_x = x
                    last_y = y

            legend_x = plot_rect.left() + 10 + index * 150
            legend_y = self.height() - 8
            painter.drawText(legend_x, legend_y, f"{label}={values[-1]:.2f}" if values else label)

        painter.end()

    @staticmethod
    def _map_y(value: float, min_value: float, max_value: float, plot_rect) -> int:
        ratio = (value - min_value) / (max_value - min_value)
        return plot_rect.bottom() - int(plot_rect.height() * ratio)


class FeedbackSignals(QObject):
    packet_ready = Signal(int, object)
    status_ready = Signal(str)


class FeedbackWindow(QWidget):
    def __init__(self, machine_no: int, interface_ip: str, history_size: int, exit_after: float):
        super().__init__()
        self.machine_no = machine_no
        self.interface_ip = interface_ip
        self.history_size = history_size
        self.exit_after = exit_after
        self.running = True
        self.connection_id = 0
        self.packet_count = 0
        self.packet_timestamps = deque()
        self.signals = FeedbackSignals()

        self._setup_ui()
        self.signals.packet_ready.connect(self.on_packet_ready)
        self.signals.status_ready.connect(self.status_label.setText)
        self.apply_connection(machine_no, interface_ip)

        if exit_after > 0:
            QTimer.singleShot(int(exit_after * 1000), self.close)

    def _setup_ui(self) -> None:
        self.setWindowTitle("Robot Feedback Viewer")
        self.resize(980, 860)

        root_layout = QVBoxLayout(self)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("機体番号"))
        self.machine_spin = QSpinBox()
        self.machine_spin.setRange(0, 155)
        self.machine_spin.setValue(self.machine_no)
        controls.addWidget(self.machine_spin)

        controls.addWidget(QLabel("interface IP"))
        self.interface_combo = QComboBox()
        self.interface_combo.setEditable(True)
        inferred_ip = infer_interface_ip(self.machine_no)
        for value in (self.interface_ip, inferred_ip, DEFAULT_INTERFACE_IP):
            if value and self.interface_combo.findText(value) < 0:
                self.interface_combo.addItem(value)
        self.interface_combo.setCurrentText(self.interface_ip)
        controls.addWidget(self.interface_combo)

        reconnect_button = QPushButton("接続")
        reconnect_button.clicked.connect(self.on_reconnect)
        controls.addWidget(reconnect_button)
        controls.addStretch()
        root_layout.addLayout(controls)

        self.connection_label = QLabel("")
        root_layout.addWidget(self.connection_label)
        self.status_label = QLabel("waiting")
        root_layout.addWidget(self.status_label)

        value_layout = QGridLayout()
        self.value_labels = {}
        value_names = (
            "counter",
            "sync",
            "checksum",
            "battery",
            "capacitor",
            "yaw",
            "diff_angle",
            "camera",
            "motor_current",
            "error",
            "mouse_quality",
            "mouse_global_vel",
            "local_odom_speed_mvf",
            "packet_count",
            "packet_rate",
        )
        for index, name in enumerate(value_names):
            value_layout.addWidget(QLabel(name), index // 2, (index % 2) * 2)
            label = QLabel("-")
            self.value_labels[name] = label
            value_layout.addWidget(label, index // 2, (index % 2) * 2 + 1)
        root_layout.addLayout(value_layout)

        self.plots = (
            PlotWidget("Power", ("battery", "capacitor/10"), self.history_size, y_range=(0.0, 40.0)),
            PlotWidget("Angle", ("yaw", "diff_angle"), self.history_size, y_range=(-180.0, 180.0)),
            PlotWidget("Camera", ("camera_x", "camera_y", "camera_radius"), self.history_size),
            PlotWidget("Motor Current", ("motor_0", "motor_1", "motor_2", "motor_3"), self.history_size, y_range=(0.0, 3.0)),
            PlotWidget("Velocity X", ("mouse_global_vel_x100", "local_odom_speed_mvf_x"), self.history_size, y_range=(-3.0, 3.0)),
            PlotWidget("Velocity Y", ("mouse_global_vel_y100", "local_odom_speed_mvf_y"), self.history_size, y_range=(-3.0, 3.0)),
            PlotWidget("Receive Rate", ("packets/s",), self.history_size, y_range=(0.0, 150.0)),
        )
        plot_layout = QGridLayout()
        plot_layout.addWidget(self.plots[0], 0, 0)
        plot_layout.addWidget(self.plots[6], 0, 1)
        plot_layout.addWidget(self.plots[1], 1, 0)
        plot_layout.addWidget(self.plots[2], 1, 1)
        plot_layout.addWidget(self.plots[3], 2, 0, 1, 2)
        plot_layout.addWidget(self.plots[4], 3, 0)
        plot_layout.addWidget(self.plots[5], 3, 1)
        root_layout.addLayout(plot_layout)

    def closeEvent(self, event) -> None:
        self.running = False
        super().closeEvent(event)

    def on_reconnect(self) -> None:
        self.apply_connection(self.machine_spin.value(), self.interface_combo.currentText().strip() or DEFAULT_INTERFACE_IP)

    def apply_connection(self, machine_no: int, interface_ip: str) -> None:
        self.connection_id += 1
        connection_id = self.connection_id
        self.machine_no = machine_no
        self.interface_ip = interface_ip
        self.packet_count = 0
        self.packet_timestamps.clear()
        for plot in self.plots:
            plot.clear()

        group, port = multicast_endpoint(machine_no)
        self.connection_label.setText(f"機体{machine_no}: {group}:{port} / interface {interface_ip}")
        self.signals.status_ready.emit("connecting")
        threading.Thread(target=self.receive_loop, args=(connection_id, machine_no, interface_ip), daemon=True).start()

    def receive_loop(self, connection_id: int, machine_no: int, interface_ip: str) -> None:
        group, port = multicast_endpoint(machine_no)
        sock = None
        try:
            sock = open_multicast_socket(group, port, interface_ip)
            sock.settimeout(DEFAULT_RECEIVE_TIMEOUT)
            self.signals.status_ready.emit(f"listening {group}:{port}")
            while self.running and connection_id == self.connection_id:
                try:
                    payload, _sender = sock.recvfrom(RECEIVE_BUFFER_SIZE)
                except socket.timeout:
                    if not self.running or connection_id != self.connection_id:
                        break
                    self.signals.status_ready.emit(f"waiting {group}:{port}")
                    continue
                if len(payload) != PACKET_SIZE:
                    continue
                if not self.running or connection_id != self.connection_id:
                    break
                packet = decode_robot_feedback_packet(payload)
                self.signals.packet_ready.emit(connection_id, packet)
        except socket.timeout:
            if self.running and connection_id == self.connection_id:
                self.signals.status_ready.emit("receive timeout")
        except OSError as exc:
            if self.running and connection_id == self.connection_id:
                self.signals.status_ready.emit(f"receive error: {exc}")
        finally:
            if sock is not None:
                sock.close()

    def on_packet_ready(self, connection_id: int, packet: RobotFeedbackPacket) -> None:
        if connection_id != self.connection_id:
            return

        self.packet_count += 1
        now = time.monotonic()
        self.packet_timestamps.append(now)
        while self.packet_timestamps and self.packet_timestamps[0] < now - 1.0:
            self.packet_timestamps.popleft()
        packet_rate = len(self.packet_timestamps)
        tx_values = dict(zip(TX_VALUE_LABELS, packet.tx_value_array))

        self.value_labels["counter"].setText(str(packet.check_counter))
        self.value_labels["sync"].setText(str(packet.is_sync_valid))
        self.value_labels["checksum"].setText(str(packet.is_checksum_valid))
        self.value_labels["battery"].setText(f"{packet.battery_voltage_bldc_right:.3f}")
        self.value_labels["capacitor"].setText(f"{packet.capacitor_boost_voltage:.3f}")
        self.value_labels["yaw"].setText(f"{packet.imu_yaw_deg:.3f}")
        self.value_labels["diff_angle"].setText(f"{packet.diff_angle_deg:.3f}")
        self.value_labels["camera"].setText(
            f"x={packet.camera_pos_x}, y={packet.camera_pos_y}, r={packet.camera_radius}, fps={packet.camera_fps}"
        )
        self.value_labels["motor_current"].setText(", ".join(f"{value:.1f}" for value in packet.motor_current))
        self.value_labels["error"].setText(
            f"id={packet.current_error_id}, info={packet.current_error_info}, value={packet.current_error_value:.3f}"
        )
        self.value_labels["mouse_quality"].setText(f"{tx_values['mouse_quality']:.1f}")
        self.value_labels["mouse_global_vel"].setText(
            f"x={tx_values['mouse_global_vel_x'] * 100.0:.3f}, y={tx_values['mouse_global_vel_y'] * 100.0:.3f}"
        )
        self.value_labels["local_odom_speed_mvf"].setText(
            f"x={tx_values['local_odom_speed_mvf_x']:.3f}, y={tx_values['local_odom_speed_mvf_y']:.3f}"
        )
        self.value_labels["packet_count"].setText(str(self.packet_count))
        self.value_labels["packet_rate"].setText(f"{packet_rate:.0f} packets/s")
        self.status_label.setText("receiving")

        self.plots[0].append(
            {"battery": packet.battery_voltage_bldc_right, "capacitor/10": packet.capacitor_boost_voltage / 10.0}
        )
        self.plots[1].append({"yaw": packet.imu_yaw_deg, "diff_angle": packet.diff_angle_deg})
        self.plots[2].append(
            {"camera_x": packet.camera_pos_x, "camera_y": packet.camera_pos_y, "camera_radius": packet.camera_radius}
        )
        self.plots[3].append({f"motor_{index}": value for index, value in enumerate(packet.motor_current)})
        self.plots[4].append(
            {
                "mouse_global_vel_x100": tx_values["mouse_global_vel_x"] * 100.0,
                "local_odom_speed_mvf_x": tx_values["local_odom_speed_mvf_x"],
            }
        )
        self.plots[5].append(
            {
                "mouse_global_vel_y100": tx_values["mouse_global_vel_y"] * 100.0,
                "local_odom_speed_mvf_y": tx_values["local_odom_speed_mvf_y"],
            }
        )
        self.plots[6].append({"packets/s": packet_rate})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Qt viewer for robot feedback multicast packets")
    parser.add_argument("--machine-no", type=int, default=DEFAULT_MACHINE_NO)
    parser.add_argument("--interface-ip", default=None, help="local interface IP for multicast join")
    parser.add_argument("--history-size", type=int, default=DEFAULT_HISTORY_SIZE)
    parser.add_argument("--exit-after", type=float, default=0.0, help="close automatically after this many seconds")
    return parser


def main() -> None:
    parser = build_parser()
    args, qt_args = parser.parse_known_args()
    interface_ip = args.interface_ip or infer_interface_ip(args.machine_no)

    app = QApplication([sys.argv[0], *qt_args])
    window = FeedbackWindow(args.machine_no, interface_ip, args.history_size, args.exit_after)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
