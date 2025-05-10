import argparse
import threading
import socket
import struct
import cv2
import numpy as np
import io
import time
import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk, ImageDraw

# --- 設定 ---
MCAST_GRP = '239.255.0.1'
MCAST_PORT = 5005
API_PORT = 8001
FRAME_SIZE = (320, 180)  # GUIプレビュー用サイズ (幅x高)

# FastAPI アプリケーション設定
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# グローバルリソース
cap = None
latest_frame = None
hsv_min = np.array([5, 100, 100])
hsv_max = np.array([15, 255, 255])
frame_lock = threading.Lock()

# 検出結果保持
last_x = 0
last_y = 0
last_area = 0
detect_count = 0

# マルチキャスト UDP 送信ソケット
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, struct.pack('b', 1))

# キャプチャループ (高速化: grab/retrieve, バッファサイズ1)
def capture_loop():
    global cap, latest_frame
    while True:
        if not cap.grab():
            continue
        ret, frame = cap.retrieve()
        if not ret:
            continue
        with frame_lock:
            latest_frame = frame

# 検出ループ
def detect_loop():
    global latest_frame, hsv_min, hsv_max, last_x, last_y, last_area, detect_count
    while True:
        with frame_lock:
            frame = latest_frame.copy() if latest_frame is not None else None
        if frame is None:
            continue
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, hsv_min, hsv_max)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if cnts:
            c = max(cnts, key=cv2.contourArea)
            (x, y), _ = cv2.minEnclosingCircle(c)
            area = cv2.contourArea(c)
            # 更新
            last_x, last_y, last_area = int(x), int(y), area
            detect_count += 1
            # マルチキャスト送信
            msg = f"{last_x},{last_y},{int(last_area)}".encode()
            sock.sendto(msg, (MCAST_GRP, MCAST_PORT))

# API: フレーム配信 (JPEG品質落として高速化)
@app.get('/frame')
def get_frame():
    with frame_lock:
        if latest_frame is None:
            return JSONResponse({"error": "no frame"}, status_code=503)
        _, buf = cv2.imencode('.jpg', latest_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    return StreamingResponse(io.BytesIO(buf.tobytes()), media_type='image/jpeg')

# API: HSV パラメータ取得/設定
@app.get('/params')
def get_params():
    return {"hsv_min": hsv_min.tolist(), "hsv_max": hsv_max.tolist()}

@app.post('/params')
def set_params(p: dict):
    global hsv_min, hsv_max
    hsv_min = np.array(p['hsv_min'])
    hsv_max = np.array(p['hsv_max'])
    return {"status": "ok"}

# GUI プレビュー & 操作
class PiGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title('Pi Ball Detector')
        # 原画表示
        self.frame_label = ttk.Label(self.root)
        self.frame_label.grid(row=0, column=0)
        # 二値化表示
        self.mask_label = ttk.Label(self.root)
        self.mask_label.grid(row=0, column=1)
        # 情報表示 (fps, area)
        self.info_label = ttk.Label(self.root, text='FPS: --, Area: --')
        self.info_label.grid(row=1, column=0, columnspan=2)

        # HSV スライダー生成、変更時自動反映
        self.vars = {}
        defaults = {'h_min': 5, 'h_max': 15, 's_min': 100, 's_max': 255, 'v_min': 100, 'v_max': 255}
        limits = {'h_min': 180, 'h_max': 180, 's_min': 255, 's_max': 255, 'v_min': 255, 'v_max': 255}
        for i, key in enumerate(['h_min', 'h_max', 's_min', 's_max', 'v_min', 'v_max']):
            ttk.Label(self.root, text=key.upper()).grid(row=2+i, column=0, sticky='w')
            var = tk.IntVar(value=defaults[key])
            self.vars[key] = var
            scale = ttk.Scale(self.root, from_=0, to=limits[key], variable=var, orient='horizontal')
            scale.grid(row=2+i, column=1, sticky='we')
            var.trace_add('write', lambda *args: self.apply())

        self.last_time = time.time()
        self.fps = 0.0
        self.update()
        self.root.mainloop()

    def apply(self):
        import requests
        body = {
            'hsv_min': [self.vars['h_min'].get(), self.vars['s_min'].get(), self.vars['v_min'].get()],
            'hsv_max': [self.vars['h_max'].get(), self.vars['s_max'].get(), self.vars['v_max'].get()]
        }
        try:
            requests.post(f'http://127.0.0.1:{API_PORT}/params', json=body)
        except:
            pass

    def update(self):
        now = time.time()
        dt = now - self.last_time
        if dt > 0:
            self.fps = 0.9 * self.fps + 0.1 * (1.0 / dt)
        self.last_time = now

        with frame_lock:
            frame = latest_frame.copy() if latest_frame is not None else None
        if frame is not None:
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            mask = cv2.inRange(hsv, hsv_min, hsv_max)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
            cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            area = 0
            sx = sy = None
            if cnts:
                c = max(cnts, key=cv2.contourArea)
                (cx, cy), _ = cv2.minEnclosingCircle(c)
                area = cv2.contourArea(c)
                sx = int(cx * FRAME_SIZE[0] / frame.shape[1])
                sy = int(cy * FRAME_SIZE[1] / frame.shape[0])

            # 原画描画 & 十字ライン
            img = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(img).resize(FRAME_SIZE)
            draw = ImageDraw.Draw(img)
            if sx is not None:
                draw.line([(sx, 0), (sx, FRAME_SIZE[1])], fill='red')
                draw.line([(0, sy), (FRAME_SIZE[0], sy)], fill='red')
            imgtk = ImageTk.PhotoImage(img)
            self.frame_label.imgtk = imgtk
            self.frame_label.config(image=imgtk)

            # マスク表示
            mimg = Image.fromarray(mask).resize(FRAME_SIZE)
            masktk = ImageTk.PhotoImage(mimg)
            self.mask_label.imgtk = masktk
            self.mask_label.config(image=masktk)

            # 情報ラベル
            info = f"FPS: {self.fps:.1f}, Area: {area:.0f}"
            self.info_label.config(text=info)

        self.root.after(50, self.update)

# ヘッドレスモード時に定期出力
def headless_report():
    global detect_count, last_x, last_y, last_area
    prev_count = 0
    while True:
        time.sleep(1)
        # FPS as detections per second
        fps = detect_count - prev_count
        prev_count = detect_count
        print(f"x={last_x}, y={last_y}, area={last_area:.0f}, fps={fps}")

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--gui', action='store_true', help='Enable local GUI')
    args = p.parse_args()

    # カメラ初期化 (320x180, 120fps)
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_SIZE[0])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_SIZE[1])
    cap.set(cv2.CAP_PROP_FPS, 120)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    threading.Thread(target=capture_loop, daemon=True).start()
    threading.Thread(target=detect_loop, daemon=True).start()

    # API サーバ起動
    server = threading.Thread(target=lambda: uvicorn.run(app, host='0.0.0.0', port=API_PORT), daemon=True)
    server.start()

    if args.gui:
        from PIL import ImageDraw
        PiGUI()
    else:
        headless_report()
