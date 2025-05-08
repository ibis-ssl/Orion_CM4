import tkinter as tk
from tkinter import ttk
import requests
import threading
import time
from concurrent.futures import ThreadPoolExecutor

PI_IP_LIST = [f"192.168.20.{i}" for i in range(100, 113)]
PORT = 8000
TIMEOUT = 0.5  # タイムアウトを短く

class PiControllerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Raspberry Pi Controller")

        self.status_labels = {}

        for idx, ip in enumerate(PI_IP_LIST):
            ttk.Label(root, text=ip, width=15).grid(row=idx, column=0, padx=5, pady=3)

            status_label = ttk.Label(root, text="Unknown", foreground="gray", width=10, anchor="center")
            status_label.grid(row=idx, column=1, padx=5, pady=3)
            self.status_labels[ip] = status_label

            ttk.Button(root, text="Run", width=8, command=lambda ip=ip: self.send_command(ip, "start")).grid(row=idx, column=2, padx=5)
            ttk.Button(root, text="Stop", width=8, command=lambda ip=ip: self.send_command(ip, "stop")).grid(row=idx, column=3, padx=5)

        self.running = True
        threading.Thread(target=self.status_updater, daemon=True).start()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def send_command(self, ip, command):
        url = f"http://{ip}:{PORT}/{command}"
        try:
            res = requests.post(url, timeout=TIMEOUT)
            print(f"{ip}: {command.upper()} -> {res.status_code}")
        except requests.RequestException as e:
            print(f"{ip}: Failed to send {command}: {e}")

    def get_status(self, ip):
        url = f"http://{ip}:{PORT}/status"
        try:
            res = requests.get(url, timeout=TIMEOUT)
            if res.ok:
                running = res.json().get("running", False)
                return ip, "Running" if running else "Stopped"
            else:
                return ip, "Error"
        except requests.RequestException:
            return ip, "Offline"

    def update_status_label(self, ip, status_text):
        color_map = {
            "Running": "green",
            "Stopped": "red",
            "Offline": "gray",
            "Error": "orange",
        }
        label = self.status_labels[ip]
        label.config(text=status_text, foreground=color_map.get(status_text, "black"))

    def status_updater(self):
        with ThreadPoolExecutor(max_workers=10) as executor:
            while self.running:
                futures = [executor.submit(self.get_status, ip) for ip in PI_IP_LIST]
                for future in futures:
                    ip, status = future.result()
                    self.root.after(0, self.update_status_label, ip, status)
                time.sleep(1)

    def on_close(self):
        self.running = False
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = PiControllerApp(root)
    root.mainloop()
