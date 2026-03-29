# このファイルはCM4制御 GUI の Qt エントリポイントを担当し、
# 共通制御処理 cm4_control.py を利用して状態監視と起動停止操作を表示する。
import sys
import threading

from cm4_control import DEFAULT_IP_LIST, fetch_statuses, send_command

try:
    from PySide6.QtCore import QObject, Qt, QTimer, Signal
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import (
        QApplication,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:
    raise SystemExit(f"PySide6 is required for host_lancher.py: {exc}")


class ControlSignals(QObject):
    statuses_ready = Signal(list)
    command_result = Signal(str)


class ControlWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.signals = ControlSignals()
        self.status_scan_running = False
        self.row_by_ip = {}
        self._setup_ui()

        self.signals.statuses_ready.connect(self.apply_statuses)
        self.signals.command_result.connect(self.set_message)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_statuses)
        self.timer.start(1000)
        self.refresh_statuses()

    def _setup_ui(self):
        self.setWindowTitle("Raspberry Pi Controller")
        self.resize(560, 420)

        layout = QVBoxLayout(self)

        self.message_label = QLabel("Ready")
        layout.addWidget(self.message_label)

        self.table = QTableWidget(len(DEFAULT_IP_LIST), 4)
        self.table.setHorizontalHeaderLabels(["IP", "Status", "Run", "Stop"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)

        for row, ip in enumerate(DEFAULT_IP_LIST):
            self.row_by_ip[ip] = row

            ip_item = QTableWidgetItem(ip)
            status_item = QTableWidgetItem("Unknown")
            status_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 0, ip_item)
            self.table.setItem(row, 1, status_item)

            run_button = QPushButton("Run")
            run_button.clicked.connect(lambda _checked=False, target_ip=ip: self.send_command_async(target_ip, "start"))
            self.table.setCellWidget(row, 2, self._wrap_button(run_button))

            stop_button = QPushButton("Stop")
            stop_button.clicked.connect(lambda _checked=False, target_ip=ip: self.send_command_async(target_ip, "stop"))
            self.table.setCellWidget(row, 3, self._wrap_button(stop_button))

        self.table.resizeColumnsToContents()
        layout.addWidget(self.table)

    def _wrap_button(self, button):
        wrapper = QWidget()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(button)
        return wrapper

    def set_message(self, text):
        self.message_label.setText(text)

    def refresh_statuses(self):
        if self.status_scan_running:
            return

        self.status_scan_running = True

        def worker():
            statuses = fetch_statuses(DEFAULT_IP_LIST)
            self.signals.statuses_ready.emit(statuses)

        threading.Thread(target=worker, daemon=True).start()

    def apply_statuses(self, statuses):
        color_map = {
            "Running": "#2e7d32",
            "Stopped": "#c62828",
            "Offline": "#616161",
            "Error": "#ef6c00",
        }

        for status in statuses:
            row = self.row_by_ip[status["ip"]]
            item = self.table.item(row, 1)
            item.setText(status["state"])
            item.setForeground(QColor(color_map.get(status["state"], "#000000")))

        self.status_scan_running = False

    def send_command_async(self, ip, command):
        self.set_message(f"{ip} に {command} を送信中")

        def worker():
            try:
                result = send_command(ip, command)
                self.signals.command_result.emit(
                    f"{result['ip']}: {result['command']} -> {result['status_code']}"
                )
            except Exception as exc:
                self.signals.command_result.emit(f"{ip}: {command} failed: {exc}")

        threading.Thread(target=worker, daemon=True).start()


def main():
    app = QApplication(sys.argv)
    window = ControlWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
