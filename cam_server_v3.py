#!/usr/bin/env python3
import argparse
import threading
import queue
import time
import socket
import struct
import cv2
import numpy as np
import tkinter as tk
from tkinter import font
from flask import Flask, jsonify, request, send_file
import io

# --- 定数・設定 ---
API_PORT   = 8001

# HSV パラメータ初期値
hsv_min = np.array([0, 100, 100])
hsv_max = np.array([15, 255, 255])

# フレーム＆マスク共有
frame_queue = queue.Queue(maxsize=1)
detected = {'x': 0, 'y': 0, 'area': 0}
last_frame = None
last_mask  = None
frame_lock = threading.Lock()
mask_lock  = threading.Lock()

# FPS 計測用
fps = 0.0

# --- キャプチャスレッド ---
def capture_loop(device=0):
    global last_frame
    cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
    cap.set(cv2.CAP_PROP_FPS, 120)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    while True:
        if not cap.grab():
            time.sleep(0.005); continue
        ret, frame = cap.retrieve()
        if not ret:
            time.sleep(0.005); continue

        # 最新フレームのみキュー＆キャッシュ
        with frame_lock:
            last_frame = frame.copy()
        try:
            frame_queue.put(frame, block=False)
        except queue.Full:
            _ = frame_queue.get_nowait()
            frame_queue.put(frame, block=False)

# --- 検出＆UDP送信スレッド ---
def detect_loop(mcast_grp, mcast_port):
    global fps, last_mask
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, struct.pack('b',1))

    last_report = time.time()
    count = 0

    while True:
        frame = frame_queue.get()  # 新フレーム来るまで待機

        # 二値化マスク
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, hsv_min, hsv_max)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5,5),np.uint8))

        # mask キャッシュ
        with mask_lock:
            last_mask = mask.copy()

        # 輪郭検出
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if cnts:
            c = max(cnts, key=cv2.contourArea)
            (x_f, y_f), _ = cv2.minEnclosingCircle(c)
            area_f = cv2.contourArea(c)
            x, y, area = int(x_f), int(y_f), int(area_f)
        else:
            x = y = area = 0

        detected['x'], detected['y'], detected['area'] = x, y, area

        # FPS 更新
        count += 1
        now = time.time()
        if now - last_report >= 1.0:
            fps = count / (now - last_report)
            count = 0
            last_report = now

        # UDP 送信: x,y,area,fps
        msg = f"{x},{y},{area},{fps:.1f}"
        sock.sendto(msg.encode(), (mcast_grp, mcast_port))

# --- HTTP API サーバー (Flask) ---
app = Flask(__name__)

@app.route("/frame/raw")
def get_raw_frame():
    with frame_lock:
        img = last_frame.copy() if last_frame is not None else None
    if img is None:
        return ("No frame", 503)
    _, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY,80])
    return send_file(io.BytesIO(buf.tobytes()), mimetype='image/jpeg')

@app.route("/frame/mask")
def get_mask_frame():
    with mask_lock:
        m = last_mask.copy() if last_mask is not None else None
    if m is None:
        return ("No mask", 503)
    _, buf = cv2.imencode('.jpg', m)
    return send_file(io.BytesIO(buf.tobytes()), mimetype='image/jpeg')

@app.route("/params", methods=["GET"])
def get_params():
    return jsonify({
        "hsv_min": hsv_min.tolist(),
        "hsv_max": hsv_max.tolist()
    })

@app.route("/params", methods=["POST"])
def set_params():
    data = request.get_json()
    mn = data.get("hsv_min", [])
    mx = data.get("hsv_max", [])
    if len(mn)==3 and len(mx)==3:
        hsv_min[:] = mn
        hsv_max[:] = mx
        return ("OK", 200)
    return ("Bad Request", 400)

def start_api():
    app.run(host="0.0.0.0", port=API_PORT, threaded=True)

# --- ヘッドレスレポート ---
def headless_report():
    while True:
        time.sleep(1)
        x,y,area = detected['x'], detected['y'], detected['area']
        print(f"x={x}, y={y}, area={area}, fps={fps:.1f}")

# --- GUI モード ---
class PiGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Ball Detector (Pi GUI)")

        mono = font.nametofont("TkFixedFont")
        mono.configure(size=12)

        self.frame_label = tk.Label(self.root)
        self.frame_label.pack()
        self.mask_label  = tk.Label(self.root)
        self.mask_label.pack()

        self.stats = tk.StringVar(value="x=000,y=000,a=000,f=0.0")
        tk.Label(self.root, textvariable=self.stats, font=mono).pack()

        # HSV スライダー
        self.sliders = {}
        for name, r, arr, idx in [
            ("H min",(0,180), hsv_min,0),
            ("H max",(0,180), hsv_max,0),
            ("S min",(0,255), hsv_min,1),
            ("S max",(0,255), hsv_max,1),
            ("V min",(0,255), hsv_min,2),
            ("V max",(0,255), hsv_max,2),
        ]:
            var = tk.IntVar(value=int(arr[idx]))
            self.sliders[name] = var
            tk.Scale(self.root, label=name, from_=r[0], to=r[1],
                     orient='horizontal', variable=var,
                     command=self.on_hsv_change).pack(fill='x')

        self.update_gui()

    def on_hsv_change(self, _=None):
        hsv_min[0] = self.sliders["H min"].get()
        hsv_max[0] = self.sliders["H max"].get()
        hsv_min[1] = self.sliders["S min"].get()
        hsv_max[1] = self.sliders["S max"].get()
        hsv_min[2] = self.sliders["V min"].get()
        hsv_max[2] = self.sliders["V max"].get()

    def update_gui(self):
        with frame_lock:
            frame = last_frame.copy() if last_frame is not None else None
        with mask_lock:
            m = last_mask.copy() if last_mask is not None else None

        if frame is not None and m is not None:
            # 十字線
            x,y = detected['x'], detected['y']
            cv2.line(frame,(x,0),(x,frame.shape[0]),(0,0,255),1)
            cv2.line(frame,(0,y),(frame.shape[1],y),(0,0,255),1)

            for img,label in ((frame,self.frame_label),(m,self.mask_label)):
                ppm = cv2.imencode('.ppm', img)[1].tobytes()
                img_tk = tk.PhotoImage(master=self.root, data=ppm, format='PPM')
                label.configure(image=img_tk)
                label.image = img_tk

            self.stats.set(
                f"x={detected['x']:03d},y={detected['y']:03d},"
                f"a={detected['area']:03d},fps={fps:.1f}"
            )
        self.root.after(30, self.update_gui)

    def run(self):
        self.root.mainloop()

# --- エントリポイント ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('-n', type=int, default=5,
                        help='lower 8 bits of multicast IP and lower 3 digits of port')
    parser.add_argument('--gui', action='store_true', help='Enable local GUI')
    args = parser.parse_args()

    n = args.n % 256
    mcast_grp = f"239.255.0.{n}"
    mcast_port = 5000 + (n % 1000)

    # スレッド開始
    threading.Thread(target=capture_loop, daemon=True).start()
    threading.Thread(target=detect_loop,args=(mcast_grp, mcast_port), daemon=True).start()
    threading.Thread(target=start_api, daemon=True).start()

    if args.gui:
        PiGUI().run()
    else:
        headless_report()
