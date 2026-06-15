#!/usr/bin/env python3
# このファイルはCM4上で動作するカメラサーバー v3 を担当し、画像処理、HTTP API、座標配信を行う。
import argparse
# このファイルは CM4 上のカメラサーバー v3 を担当する。
# 画像取得、HSV ボール検出、HTTP API、multicast 座標配信、ローカルカメラ UDP 送信を行う。
import json
import os
import sys
import threading
import queue
import time
import socket
import struct
import cv2
import numpy as np
import tkinter as tk
from tkinter import font
from flask import Flask, Response, jsonify, request, send_file
from picamera2 import Picamera2
import io

# --- 定数・設定 ---
API_PORT   = 8001
LOCAL_CAMERA_UDP_HOST = "127.0.0.1"
LOCAL_CAMERA_UDP_PORT = 8890
SENSOR_FRAME_SIZE = (640, 480)
PROCESS_FRAME_SIZE = (320, 240)
DEFAULT_CAMERA_FPS = 206.0

# HSV パラメータ初期値
hsv_min = np.array([0, 100, 100])
hsv_max = np.array([15, 255, 255])
hsv_lock = threading.Lock()
hsv_config_lock = threading.Lock()
hsv_config_path = None

# フレーム＆マスク共有
frame_queue = queue.Queue(maxsize=1)
detected = {'x': 0, 'y': 0, 'area': 0, 'radius': 0}
last_frame = None
last_mask  = None
frame_lock = threading.Lock()
mask_lock  = threading.Lock()

# FPS 計測用
fps = 0.0
capture_fps = 0.0
camera_target_fps = DEFAULT_CAMERA_FPS


def default_hsv_config_path():
    env_path = os.environ.get("ORION_CM4_HSV_CONFIG")
    if env_path:
        return env_path
    return os.path.join(os.getcwd(), "runtime", "cam_server_v3_hsv.json")


def default_hsv_template_path():
    if getattr(sys, "frozen", False):
        return os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)), "default_hsv_config.json")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "default_hsv_config.json")


def load_hsv_json(path):
    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)
    apply_hsv_values(data["hsv_min"], data["hsv_max"])


def validate_hsv_values(values, upper_limits):
    if not isinstance(values, list) or len(values) != 3:
        raise ValueError("HSV must be a list of 3 values")
    result = []
    for value, upper_limit in zip(values, upper_limits):
        value = int(value)
        if not 0 <= value <= upper_limit:
            raise ValueError("HSV value is out of range")
        result.append(value)
    return result


def apply_hsv_values(new_hsv_min, new_hsv_max):
    new_hsv_min = validate_hsv_values(new_hsv_min, [180, 255, 255])
    new_hsv_max = validate_hsv_values(new_hsv_max, [180, 255, 255])
    with hsv_lock:
        hsv_min[:] = new_hsv_min
        hsv_max[:] = new_hsv_max


def load_hsv_config(config_path):
    global hsv_config_path
    hsv_config_path = config_path
    if not os.path.exists(config_path):
        template_path = default_hsv_template_path()
        try:
            if os.path.exists(template_path):
                load_hsv_json(template_path)
                print(f"loaded default HSV config: {template_path}")
            save_hsv_config()
            print(f"created HSV config: {config_path}")
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            print(f"failed to create HSV config {config_path}: {exc}")
        return

    try:
        load_hsv_json(config_path)
        print(f"loaded HSV config: {config_path}")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"failed to load HSV config {config_path}: {exc}")


def save_hsv_config():
    if hsv_config_path is None:
        return

    with hsv_lock:
        data = {
            "hsv_min": hsv_min.tolist(),
            "hsv_max": hsv_max.tolist(),
        }

    config_dir = os.path.dirname(hsv_config_path)
    if config_dir:
        os.makedirs(config_dir, exist_ok=True)

    with hsv_config_lock:
        tmp_path = f"{hsv_config_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(tmp_path, hsv_config_path)


def detect_ball(frame):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    with hsv_lock:
        current_hsv_min = hsv_min.copy()
        current_hsv_max = hsv_max.copy()
    mask = cv2.inRange(hsv, current_hsv_min, current_hsv_max)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5,5),np.uint8))

    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if cnts:
        c = max(cnts, key=cv2.contourArea)
        (x_f, y_f), radius_f = cv2.minEnclosingCircle(c)
        area_f = cv2.contourArea(c)
        x, y, area, radius = int(x_f), int(y_f), int(area_f), int(radius_f)
    else:
        x = y = area = radius = 0

    return x, y, area, radius, mask


def clamp_uint16(value):
    return max(0, min(65535, int(value)))


def clamp_uint8(value):
    return max(0, min(255, int(value)))


def pack_local_camera_packet(x, y, radius, camera_fps):
    return struct.pack(
        ">HHHB",
        clamp_uint16(x),
        clamp_uint16(y),
        clamp_uint16(radius),
        clamp_uint8(round(camera_fps)),
    )


def get_interface_ip(interface_name):
    import fcntl

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        return socket.inet_ntoa(
            fcntl.ioctl(
                sock.fileno(),
                0x8915,  # SIOCGIFADDR
                struct.pack('256s', interface_name[:15].encode('utf-8'))
            )[20:24]
        )


def configure_multicast_interface(sock, interface_name, interface_ip):
    if interface_ip is None and interface_name:
        interface_ip = get_interface_ip(interface_name)
    if interface_ip is None:
        return None

    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface_ip))
    return interface_ip

# --- キャプチャスレッド ---
def capture_loop(camera_fps=DEFAULT_CAMERA_FPS):
    global capture_fps, last_frame
    frame_duration_us = round(1_000_000 / camera_fps)
    camera = Picamera2()
    camera.configure(
        camera.create_video_configuration(
            main={"size": PROCESS_FRAME_SIZE, "format": "RGB888"},
            raw={"size": SENSOR_FRAME_SIZE},
            controls={"FrameDurationLimits": (frame_duration_us, frame_duration_us)},
            buffer_count=6,
            queue=False,
        )
    )
    camera.start()
    print(
        f"camera started: sensor={SENSOR_FRAME_SIZE[0]}x{SENSOR_FRAME_SIZE[1]}, "
        f"process={PROCESS_FRAME_SIZE[0]}x{PROCESS_FRAME_SIZE[1]}, target_fps={camera_fps:.2f}"
    )

    last_report = time.monotonic()
    count = 0
    while True:
        frame = camera.capture_array("main")
        count += 1
        now = time.monotonic()
        if now - last_report >= 1.0:
            capture_fps = count / (now - last_report)
            count = 0
            last_report = now

        # 最新フレームのみキュー＆キャッシュ
        with frame_lock:
            last_frame = frame.copy()
        try:
            frame_queue.put(frame, block=False)
        except queue.Full:
            _ = frame_queue.get_nowait()
            frame_queue.put(frame, block=False)

# --- 検出＆UDP送信スレッド ---
def detect_loop(mcast_grp, mcast_port, mcast_interface_name=None, mcast_interface_ip=None, local_cam_addr=None):
    global fps, last_mask
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, struct.pack('b',1))
    local_cam_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) if local_cam_addr is not None else None
    try:
        active_mcast_ip = configure_multicast_interface(sock, mcast_interface_name, mcast_interface_ip)
        if active_mcast_ip:
            print(f"multicast interface: {active_mcast_ip}")
    except OSError as exc:
        print(f"failed to configure multicast interface: {exc}")

    last_report = time.time()
    count = 0

    while True:
        frame = frame_queue.get()  # 新フレーム来るまで待機

        x, y, area, radius, mask = detect_ball(frame)

        # mask キャッシュ
        with mask_lock:
            last_mask = mask.copy()

        detected['x'], detected['y'], detected['area'], detected['radius'] = x, y, area, radius

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
        if local_cam_sock is not None:
            try:
                local_cam_sock.sendto(pack_local_camera_packet(x, y, radius, fps), local_cam_addr)
            except OSError as exc:
                print(f"failed to send local camera packet: {exc}")
                local_cam_sock.close()
                local_cam_sock = None

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


def generate_mjpeg(image_name, stream_fps):
    interval = 1.0 / stream_fps
    while True:
        started_at = time.monotonic()
        if image_name == "raw":
            with frame_lock:
                image = last_frame.copy() if last_frame is not None else None
            encode_params = [cv2.IMWRITE_JPEG_QUALITY, 80]
        else:
            with mask_lock:
                image = last_mask.copy() if last_mask is not None else None
            encode_params = []

        if image is not None:
            encoded, buffer = cv2.imencode(".jpg", image, encode_params)
            if encoded:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(buffer)).encode("ascii") + b"\r\n\r\n"
                    + buffer.tobytes()
                    + b"\r\n"
                )

        remaining = interval - (time.monotonic() - started_at)
        if remaining > 0:
            time.sleep(remaining)


@app.route("/stream/<image_name>")
def stream_frames(image_name):
    if image_name not in {"raw", "mask"}:
        return ("Unknown image", 404)
    default_fps = 30.0 if image_name == "raw" else 15.0
    try:
        stream_fps = float(request.args.get("fps", default_fps))
    except ValueError:
        return ("fps must be a number", 400)
    if not 1.0 <= stream_fps <= 60.0:
        return ("fps must be between 1 and 60", 400)
    return Response(
        generate_mjpeg(image_name, stream_fps),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/params", methods=["GET"])
def get_params():
    with hsv_lock:
        return jsonify({
            "hsv_min": hsv_min.tolist(),
            "hsv_max": hsv_max.tolist()
        })


@app.route("/status")
def get_camera_status():
    return jsonify({
        "sensor_width": SENSOR_FRAME_SIZE[0],
        "sensor_height": SENSOR_FRAME_SIZE[1],
        "process_width": PROCESS_FRAME_SIZE[0],
        "process_height": PROCESS_FRAME_SIZE[1],
        "target_fps": camera_target_fps,
        "capture_fps": capture_fps,
        "detect_fps": fps,
    })


@app.route("/params", methods=["POST"])
def set_params():
    data = request.get_json(silent=True) or {}
    mn = data.get("hsv_min", [])
    mx = data.get("hsv_max", [])
    try:
        apply_hsv_values(mn, mx)
        save_hsv_config()
        return ("OK", 200)
    except (TypeError, ValueError) as exc:
        return (f"Bad Request: {exc}", 400)
    except OSError as exc:
        return (f"Failed to save HSV config: {exc}", 500)

def start_api():
    app.run(host="0.0.0.0", port=API_PORT, threaded=True)

# --- ヘッドレスレポート ---
def headless_report():
    while True:
        time.sleep(1)
        x,y,area,radius = detected['x'], detected['y'], detected['area'], detected['radius']
        print(
            f"x={x}, y={y}, area={area}, radius={radius}, "
            f"capture_fps={capture_fps:.1f}, detect_fps={fps:.1f}"
        )

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
        with hsv_lock:
            initial_hsv_min = hsv_min.copy()
            initial_hsv_max = hsv_max.copy()
        for name, r, arr, idx in [
            ("H min",(0,180), initial_hsv_min,0),
            ("H max",(0,180), initial_hsv_max,0),
            ("S min",(0,255), initial_hsv_min,1),
            ("S max",(0,255), initial_hsv_max,1),
            ("V min",(0,255), initial_hsv_min,2),
            ("V max",(0,255), initial_hsv_max,2),
        ]:
            var = tk.IntVar(value=int(arr[idx]))
            self.sliders[name] = var
            tk.Scale(self.root, label=name, from_=r[0], to=r[1],
                     orient='horizontal', variable=var,
                     command=self.on_hsv_change).pack(fill='x')

        self.update_gui()

    def on_hsv_change(self, _=None):
        try:
            apply_hsv_values(
                [
                    self.sliders["H min"].get(),
                    self.sliders["S min"].get(),
                    self.sliders["V min"].get(),
                ],
                [
                    self.sliders["H max"].get(),
                    self.sliders["S max"].get(),
                    self.sliders["V max"].get(),
                ],
            )
            save_hsv_config()
        except (OSError, TypeError, ValueError) as exc:
            print(f"failed to save HSV config: {exc}")

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
                f"a={detected['area']:03d},r={detected['radius']:03d},fps={fps:.1f}"
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
    parser.add_argument('--hsv-config', default=default_hsv_config_path(),
                        help='path to persistent HSV config JSON')
    parser.add_argument('--mcast-if', default='wlan0',
                        help='interface name used for multicast send')
    parser.add_argument('--mcast-if-ip', default=None,
                        help='interface IPv4 address used for multicast send')
    parser.add_argument('--local-cam-host', default=LOCAL_CAMERA_UDP_HOST,
                        help='local UDP host used by forward_ai_cmd_v2.cpp')
    parser.add_argument('--local-cam-port', type=int, default=LOCAL_CAMERA_UDP_PORT,
                        help='local UDP port used by forward_ai_cmd_v2.cpp')
    parser.add_argument('--disable-local-cam-udp', action='store_true',
                        help='disable 7-byte local camera UDP packet output')
    parser.add_argument('--camera-fps', type=float, default=DEFAULT_CAMERA_FPS,
                        help='IMX219 sensor target FPS for the 640x480 high-speed mode')
    args = parser.parse_args()
    if args.camera_fps <= 0:
        parser.error("--camera-fps must be greater than zero")
    load_hsv_config(args.hsv_config)
    camera_target_fps = args.camera_fps

    n = args.n % 256
    mcast_grp = f"224.5.10.{n}"
    mcast_port = 5000 + (n % 1000)
    local_cam_addr = None if args.disable_local_cam_udp else (args.local_cam_host, args.local_cam_port)

    # スレッド開始
    threading.Thread(target=capture_loop, args=(args.camera_fps,), daemon=True).start()
    threading.Thread(
        target=detect_loop,
        args=(mcast_grp, mcast_port, args.mcast_if, args.mcast_if_ip, local_cam_addr),
        daemon=True,
    ).start()
    threading.Thread(target=start_api, daemon=True).start()

    if args.gui:
        PiGUI().run()
    else:
        headless_report()
