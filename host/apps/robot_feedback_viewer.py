# このファイルはOrionMainと4WS Mainの生フィードバックをQt GUIで表示する。
# 共通パケットの現在値・時系列と機体種別を示し、通信とデコードはhost.lib.feedbackに委譲する。
from __future__ import annotations

import argparse
from collections import deque
import math
import socket
import sys
import threading
import time

from host.lib.feedback.packet import PACKET_SIZE, RobotFeedbackPacket
from host.lib.feedback.receiver import (
    MACHINE_TYPES,
    RECEIVE_BUFFER_SIZE,
    decode_feedback_packet,
    multicast_endpoint,
    open_multicast_socket,
    resolve_feedback_interface_ip,
)

try:
    from PySide6.QtCore import QObject, QRect, QTimer, Qt, Signal
    from PySide6.QtGui import QColor, QFontDatabase, QPainter, QPen
    from PySide6.QtWidgets import (
        QApplication,
        QComboBox,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QScrollArea,
        QSpinBox,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:
    raise SystemExit(f"PySide6 is required for robot_feedback_viewer.py: {exc}")


DEFAULT_MACHINE_NO = 10
DEFAULT_HISTORY_SIZE = 300
DEFAULT_RECEIVE_TIMEOUT = 1.0
PLOT_BACKGROUND = QColor("#f7f7f2")
PLOT_AXIS = QColor("#4a4f54")
PLOT_GRID = QColor("#d4d6d0")
PLOT_COLORS = (
    QColor("#006b5f"),
    QColor("#c43c35"),
    QColor("#1f6fb2"),
    QColor("#8c6a00"),
)
PLOT_LEGEND_NAMES = {
    "mouse_global_vel_x100": "mouse x ×100",
    "mouse_global_vel_y100": "mouse y ×100",
    "local_odom_speed_mvf_x": "odom x",
    "local_odom_speed_mvf_y": "odom y",
}
FIXED_FONT = QFontDatabase.systemFont(QFontDatabase.FixedFont)


class PlotWidget(QWidget):
    def __init__(
        self,
        title: str,
        labels: tuple[str, ...],
        history_size: int,
        y_range: tuple[float, float] | None = None,
        secondary_labels: tuple[str, ...] = (),
    ):
        super().__init__()
        self.title = title
        self.labels = labels
        self.history_size = history_size
        self.y_range = y_range
        self.secondary_labels = secondary_labels
        self.series = {label: deque(maxlen=history_size) for label in labels}
        self.setMinimumHeight(170)

    def clear(self) -> None:
        for values in self.series.values():
            values.clear()
        self.update()

    def append(self, values: dict[str, float]) -> None:
        if not all(math.isfinite(float(values[label])) for label in self.labels):
            return
        for label in self.labels:
            self.series[label].append(float(values[label]))
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setFont(FIXED_FONT)
        painter.fillRect(self.rect(), PLOT_BACKGROUND)

        margin_left = 54
        margin_right = 56 if self.secondary_labels else 12
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

        primary_values = [value for label, values in self.series.items()
                          if label not in self.secondary_labels for value in values]
        min_value, max_value = self._range(primary_values, self.y_range)
        secondary_values = [value for label in self.secondary_labels for value in self.series[label]]
        secondary_range = self._range(secondary_values, None, zero_based=True) if secondary_values else None

        for i in range(1, 4):
            y = plot_rect.top() + int(plot_rect.height() * i / 4)
            painter.setPen(QPen(PLOT_GRID, 1))
            painter.drawLine(plot_rect.left(), y, plot_rect.right(), y)

        painter.setPen(QPen(PLOT_AXIS, 1))
        painter.drawText(4, plot_rect.top() + 8, f"{max_value:.2f}")
        painter.drawText(4, plot_rect.bottom(), f"{min_value:.2f}")
        if secondary_range is not None:
            secondary_min, secondary_max = secondary_range
            painter.setPen(QPen(PLOT_COLORS[1], 1))
            painter.drawText(plot_rect.right() + 5, plot_rect.top() + 8, f"{secondary_max:.1f}")
            painter.drawText(plot_rect.right() + 5, plot_rect.bottom(), f"{secondary_min:.1f}")

        for index, label in enumerate(self.labels):
            values = list(self.series[label])
            color = PLOT_COLORS[index % len(PLOT_COLORS)]
            painter.setPen(QPen(color, 2))
            axis_min, axis_max = secondary_range if label in self.secondary_labels else (min_value, max_value)

            if len(values) >= 2:
                last_x = plot_rect.left()
                last_y = self._map_y(values[0], axis_min, axis_max, plot_rect)
                for value_index, value in enumerate(values[1:], start=1):
                    x = plot_rect.left() + int(plot_rect.width() * value_index / max(1, self.history_size - 1))
                    y = self._map_y(value, axis_min, axis_max, plot_rect)
                    painter.drawLine(last_x, last_y, x, y)
                    last_x = x
                    last_y = y

            legend_width = plot_rect.width() // len(self.labels)
            legend_x = plot_rect.left() + 8 + index * legend_width
            legend = PLOT_LEGEND_NAMES.get(label, label)
            caption = f"{legend}={values[-1]:+9.2f}" if values else legend
            painter.drawText(QRect(legend_x, self.height() - 24, legend_width - 12, 20),
                             Qt.AlignLeft | Qt.AlignVCenter, caption)

        painter.end()

    @staticmethod
    def _range(values: list[float], fixed: tuple[float, float] | None, zero_based: bool = False) -> tuple[float, float]:
        if fixed is not None:
            return fixed
        if zero_based:
            return 0.0, max(20.0, max(values, default=0.0) * 1.1)
        min_value = min(values, default=0.0)
        max_value = max(values, default=0.0)
        if min_value == max_value:
            min_value -= 1.0
            max_value += 1.0
        padding = (max_value - min_value) * 0.08
        return min_value - padding, max_value + padding

    @staticmethod
    def _map_y(value: float, min_value: float, max_value: float, plot_rect) -> int:
        ratio = (value - min_value) / (max_value - min_value)
        return plot_rect.bottom() - int(plot_rect.height() * ratio)


class FeedbackSignals(QObject):
    packet_ready = Signal(int, object, float)
    status_ready = Signal(str)


class FeedbackWindow(QWidget):
    def __init__(self, machine_no: int, interface_ip: str | None, history_size: int, exit_after: float, machine_type: str):
        super().__init__()
        self.machine_no = machine_no
        self.interface_selection = interface_ip or "auto"
        self.interface_ip = ""
        self.history_size = history_size
        self.exit_after = exit_after
        self.machine_type = machine_type
        self.running = True
        self.connection_id = 0
        self.packet_count = 0
        self.packet_timestamps = deque()
        self.last_packet_time: float | None = None
        self.signals = FeedbackSignals()

        self._setup_ui()
        self.signals.packet_ready.connect(self.on_packet_ready)
        self.signals.status_ready.connect(self.status_label.setText)
        self.apply_connection(machine_no, self.interface_selection, machine_type)

        if exit_after > 0:
            QTimer.singleShot(int(exit_after * 1000), self.close)

    def _setup_ui(self) -> None:
        self.setWindowTitle("Robot Feedback Viewer")
        self.resize(1080, 900)

        window_layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        root_layout = QVBoxLayout(content)
        scroll.setWidget(content)
        window_layout.addWidget(scroll)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("機体番号"))
        self.machine_spin = QSpinBox()
        self.machine_spin.setRange(0, 155)
        self.machine_spin.setValue(self.machine_no)
        controls.addWidget(self.machine_spin)

        controls.addWidget(QLabel("interface IP"))
        self.interface_combo = QComboBox()
        self.interface_combo.setEditable(True)
        for value in ("auto", self.interface_selection):
            if value and self.interface_combo.findText(value) < 0:
                self.interface_combo.addItem(value)
        self.interface_combo.setCurrentText(self.interface_selection)
        controls.addWidget(self.interface_combo)

        controls.addWidget(QLabel("形式"))
        self.machine_type_combo = QComboBox()
        self.machine_type_combo.addItems(MACHINE_TYPES)
        self.machine_type_combo.setCurrentText(self.machine_type)
        controls.addWidget(self.machine_type_combo)

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
            "crc",
            "battery",
            "capacitor",
            "yaw",
            "diff_angle",
            "position",
            "error",
            "mouse_quality",
            "mouse_global_vel",
            "local_odom_speed_mvf",
            "packet_count",
            "packet_rate",
            "packet_interval",
        )
        for index, name in enumerate(value_names):
            value_layout.addWidget(QLabel(name), index // 2, (index % 2) * 2)
            label = QLabel("-")
            label.setFont(FIXED_FONT)
            label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            label.setFixedWidth(270)
            self.value_labels[name] = label
            value_layout.addWidget(label, index // 2, (index % 2) * 2 + 1)
        root_layout.addLayout(value_layout)

        motor_group = QGroupBox("モーター")
        motor_layout = QGridLayout(motor_group)
        headers = ("輪", "回転数 [rps]", "電流 [A]", "駆動温度 [°C]", "操舵角 [rad]", "操舵温度 [°C]")
        for column, title in enumerate(headers):
            header = QLabel(title)
            header.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            motor_layout.addWidget(header, 0, column)
        self.motor_labels: list[dict[str, QLabel]] = []
        motor_columns = ("speed", "current", "temperature", "steering_angle", "steering_temperature")
        for wheel in range(4):
            motor_layout.addWidget(QLabel(f"{wheel}"), wheel + 1, 0)
            labels = {}
            for column, name in enumerate(motor_columns, start=1):
                label = QLabel("-")
                label.setFont(FIXED_FONT)
                label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                label.setFixedWidth(115)
                motor_layout.addWidget(label, wheel + 1, column)
                labels[name] = label
            self.motor_labels.append(labels)
        root_layout.addWidget(motor_group)

        self.plots = (
            PlotWidget("Power", ("battery", "capacitor/10"), self.history_size, y_range=(0.0, 40.0)),
            PlotWidget("Angle", ("yaw", "diff_angle"), self.history_size, y_range=(-180.0, 180.0)),
            PlotWidget("Position", ("position_x", "position_y"), self.history_size),
            PlotWidget("Motor Current", ("motor_0", "motor_1", "motor_2", "motor_3"), self.history_size, y_range=(0.0, 3.0)),
            PlotWidget("Velocity X", ("mouse_global_vel_x100", "local_odom_speed_mvf_x"), self.history_size, y_range=(-3.0, 3.0)),
            PlotWidget("Velocity Y", ("mouse_global_vel_y100", "local_odom_speed_mvf_y"), self.history_size, y_range=(-3.0, 3.0)),
            PlotWidget("Receive (left: packets/s, right: ms)", ("packets/s", "interval ms"),
                       self.history_size, y_range=(0.0, 150.0), secondary_labels=("interval ms",)),
            PlotWidget("Motor Speed [rps]", ("speed_0", "speed_1", "speed_2", "speed_3"), self.history_size),
        )
        plot_layout = QGridLayout()
        plot_layout.addWidget(self.plots[0], 0, 0)
        plot_layout.addWidget(self.plots[6], 0, 1)
        plot_layout.addWidget(self.plots[1], 1, 0)
        plot_layout.addWidget(self.plots[2], 1, 1)
        plot_layout.addWidget(self.plots[7], 2, 0, 1, 2)
        plot_layout.addWidget(self.plots[3], 3, 0, 1, 2)
        plot_layout.addWidget(self.plots[4], 4, 0)
        plot_layout.addWidget(self.plots[5], 4, 1)
        root_layout.addLayout(plot_layout)

    def closeEvent(self, event) -> None:
        self.running = False
        super().closeEvent(event)

    def on_reconnect(self) -> None:
        self.apply_connection(
            self.machine_spin.value(),
            self.interface_combo.currentText().strip() or "auto",
            self.machine_type_combo.currentText(),
        )

    def apply_connection(self, machine_no: int, interface_selection: str, machine_type: str) -> None:
        self.connection_id += 1
        connection_id = self.connection_id
        self.machine_no = machine_no
        self.interface_selection = interface_selection
        self.interface_ip = resolve_feedback_interface_ip(machine_no, interface_selection)
        self.machine_type = machine_type
        self.packet_count = 0
        self.packet_timestamps.clear()
        self.last_packet_time = None
        for plot in self.plots:
            plot.clear()
        for label in self.value_labels.values():
            label.setText("-")
        for motor in self.motor_labels:
            for label in motor.values():
                label.setText("-")

        group, port = multicast_endpoint(machine_no)
        interface_desc = f"{self.interface_ip} (auto)" if interface_selection.lower() == "auto" else self.interface_ip
        self.connection_label.setText(f"機体{machine_no}: {group}:{port} / interface {interface_desc} / {machine_type}")
        self.signals.status_ready.emit("connecting")
        threading.Thread(target=self.receive_loop, args=(connection_id, machine_no, self.interface_ip, machine_type), daemon=True).start()

    def receive_loop(self, connection_id: int, machine_no: int, interface_ip: str, machine_type: str) -> None:
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
                try:
                    packet = decode_feedback_packet(payload, machine_type)
                except ValueError as exc:
                    self.signals.status_ready.emit(f"decode error: {exc}")
                    continue
                self.signals.packet_ready.emit(connection_id, packet, time.monotonic())
        except socket.timeout:
            if self.running and connection_id == self.connection_id:
                self.signals.status_ready.emit("receive timeout")
        except OSError as exc:
            if self.running and connection_id == self.connection_id:
                self.signals.status_ready.emit(f"receive error: {exc}")
        finally:
            if sock is not None:
                sock.close()

    def on_packet_ready(self, connection_id: int, packet: RobotFeedbackPacket, received_at: float) -> None:
        if connection_id != self.connection_id:
            return

        self.packet_count += 1
        now = received_at
        interval_ms = (now - self.last_packet_time) * 1000.0 if self.last_packet_time is not None else None
        self.last_packet_time = now
        self.packet_timestamps.append(now)
        while self.packet_timestamps and self.packet_timestamps[0] < now - 1.0:
            self.packet_timestamps.popleft()
        packet_rate = len(self.packet_timestamps)

        self.value_labels["counter"].setText(f"{packet.check_counter:3d}")
        self.value_labels["sync"].setText("OK" if packet.is_sync_valid else "NG")
        self.value_labels["crc"].setText("OK" if packet.is_crc_valid else "NG")
        self.value_labels["battery"].setText(f"{packet.battery_voltage:+9.3f} V")
        self.value_labels["capacitor"].setText(f"{packet.capacitor_boost_voltage:+9.3f} V")
        self.value_labels["yaw"].setText(f"{packet.imu_yaw_deg:+9.3f} deg")
        self.value_labels["diff_angle"].setText(f"{packet.diff_angle_deg:+9.3f} deg")
        self.value_labels["position"].setText(
            f"x={packet.vision_based_position_x:+9.3f} y={packet.vision_based_position_y:+9.3f}"
        )
        self.value_labels["error"].setText(
            f"id={packet.current_error_id:5d} info={packet.current_error_info:5d} value={packet.current_error_value:+10.3f}"
        )
        self.value_labels["mouse_quality"].setText(f"{packet.mouse_quality:+9.1f}")
        self.value_labels["mouse_global_vel"].setText(
            f"x={packet.mouse_global_vel_x * 100.0:+9.3f} y={packet.mouse_global_vel_y * 100.0:+9.3f}"
        )
        self.value_labels["local_odom_speed_mvf"].setText(
            f"x={packet.local_odom_speed_mvf_x:+9.3f} y={packet.local_odom_speed_mvf_y:+9.3f}"
        )
        self.value_labels["packet_count"].setText(f"{self.packet_count:8d}")
        self.value_labels["packet_rate"].setText(f"{packet_rate:6d} packets/s")
        interval_text = f"{interval_ms:8.2f} ms" if interval_ms is not None else "    --.-- ms"
        self.value_labels["packet_interval"].setText(interval_text)

        speeds = (packet.motor_feedback_0, packet.motor_feedback_1,
                  packet.motor_feedback_2, packet.motor_feedback_3)
        currents = packet.motor_current
        for wheel, labels in enumerate(self.motor_labels):
            labels["speed"].setText(f"{speeds[wheel]:+9.2f}")
            labels["current"].setText(f"{currents[wheel]:6.1f}")
            labels["temperature"].setText(f"{packet.temp_motor[wheel]:3d}")
            labels["steering_angle"].setText(f"{packet.steering_angle[wheel]:+9.3f}")
            labels["steering_temperature"].setText(f"{packet.temp_steering_motor[wheel]:3d}")
        self.status_label.setText(f"receiving {packet.machine_type}")

        self.plots[0].append(
            {"battery": packet.battery_voltage, "capacitor/10": packet.capacitor_boost_voltage / 10.0}
        )
        self.plots[1].append({"yaw": packet.imu_yaw_deg, "diff_angle": packet.diff_angle_deg})
        self.plots[2].append(
            {"position_x": packet.vision_based_position_x, "position_y": packet.vision_based_position_y}
        )
        self.plots[3].append({f"motor_{index}": value for index, value in enumerate(currents)})
        self.plots[7].append({f"speed_{index}": value for index, value in enumerate(speeds)})
        self.plots[4].append(
            {
                "mouse_global_vel_x100": packet.mouse_global_vel_x * 100.0,
                "local_odom_speed_mvf_x": packet.local_odom_speed_mvf_x,
            }
        )
        self.plots[5].append(
            {
                "mouse_global_vel_y100": packet.mouse_global_vel_y * 100.0,
                "local_odom_speed_mvf_y": packet.local_odom_speed_mvf_y,
            }
        )
        if interval_ms is not None:
            self.plots[6].append({"packets/s": packet_rate, "interval ms": interval_ms})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Qt viewer for robot feedback multicast packets")
    parser.add_argument("--machine-no", type=int, default=DEFAULT_MACHINE_NO)
    parser.add_argument("--interface-ip", default=None, help="local interface IP for multicast join")
    parser.add_argument("--history-size", type=int, default=DEFAULT_HISTORY_SIZE)
    parser.add_argument("--machine-type", choices=MACHINE_TYPES, default="auto", help="decoder (default: auto)")
    parser.add_argument("--exit-after", type=float, default=0.0, help="close automatically after this many seconds")
    return parser


def main() -> None:
    parser = build_parser()
    args, qt_args = parser.parse_known_args()

    app = QApplication([sys.argv[0], *qt_args])
    window = FeedbackWindow(args.machine_no, args.interface_ip, args.history_size, args.exit_after, args.machine_type)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
