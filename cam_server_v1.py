import cv2
import io
import uvicorn
import socket
import struct
import threading
import numpy as np
import netifaces
from fastapi import FastAPI
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
# CORS 設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 必要に応じてオリジンを制限
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# --- グローバルリソース ---
cap: cv2.VideoCapture = None
raw_frame: np.ndarray = None
frame_lock = threading.Lock()
hsv_min = np.array([5, 100, 100])
hsv_max = np.array([15, 255, 255])

# マルチキャスト設定
MCAST_GRP = '224.5.20.1'
MCAST_PORT = 5005
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, struct.pack('b', 1))

# WLAN0 の IP を取得
wlan0_addr = netifaces.ifaddresses('wlan0')[netifaces.AF_INET][0]['addr']
API_BIND_IP = wlan0_addr  # Raspberry Pi の wlan0 IP
API_BIND_PORT = 8001     # 競合しないポート番号


def capture_loop():
    global cap, raw_frame
    while True:
        ret, frame = cap.read()
        if not ret:
            continue
        with frame_lock:
            raw_frame = frame.copy()


def detect_loop():
    global raw_frame, hsv_min, hsv_max
    while True:
        with frame_lock:
            if raw_frame is None:
                continue
            frame = raw_frame.copy()

        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, hsv_min, hsv_max)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5,5), np.uint8))
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if cnts:
            c = max(cnts, key=cv2.contourArea)
            (x, y), _ = cv2.minEnclosingCircle(c)
            msg = f"{int(x)},{int(y)}".encode()
            sock.sendto(msg, (MCAST_GRP, MCAST_PORT))


@app.on_event("startup")
def startup():
    global cap
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 180)
    cap.set(cv2.CAP_PROP_FPS, 120)

    print(cap.get(cv2.CAP_PROP_FOURCC))
    print(cap.get(cv2.CAP_PROP_FPS))
    print(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    print(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    threading.Thread(target=capture_loop, daemon=True).start()
    threading.Thread(target=detect_loop, daemon=True).start()


@app.on_event("shutdown")
def shutdown():
    if cap:
        cap.release()


@app.get("/frame")
def get_frame():
    with frame_lock:
        if raw_frame is None:
            return JSONResponse({"error": "no frame yet"}, status_code=503)
        _, buf = cv2.imencode('.jpg', raw_frame)
    return StreamingResponse(io.BytesIO(buf.tobytes()), media_type="image/jpeg")


@app.get("/params")
def get_params():
    return {"hsv_min": hsv_min.tolist(), "hsv_max": hsv_max.tolist()}


@app.post("/params")
def set_params(p: dict):
    global hsv_min, hsv_max
    hsv_min = np.array(p["hsv_min"])
    hsv_max = np.array(p["hsv_max"])
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host=API_BIND_IP, port=API_BIND_PORT)
