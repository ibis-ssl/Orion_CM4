import threading
import socket
import struct
import requests
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk, ImageDraw
import io

# 設定
MCAST_GRP = '224.5.20.1'
MCAST_PORT = 5005
PI_SERVER = 'http://192.168.20.112:8001'  # Raspberry Pi の API サーバ
FRAME_SIZE = (320, 240)  # GUI 表示用にリサイズするサイズ

# GUI 変数
latest_coords = None  # 取得座標を格納する StringVar
h_vars = {}  # HSV パラメータ用 IntVar 辞書

# UDP リスナー
def udp_listener(coord_var):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('', MCAST_PORT))
    mreq = struct.pack('4s4s', socket.inet_aton(MCAST_GRP), socket.inet_aton('0.0.0.0'))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    print(f"UDP listener bound to port {MCAST_PORT}, joined group {MCAST_GRP}")
    while True:
        try:
            data, addr = sock.recvfrom(1024)
            text = data.decode()
            coord_var.set(text)
        except Exception as e:
            print(f"UDP listener error: {e}")

# 画像更新＋クロスライン描画
def update_frame(label, coord_var):
    try:
        resp = requests.get(f"{PI_SERVER}/frame", timeout=1)
        img = Image.open(io.BytesIO(resp.content))
        img = img.resize(FRAME_SIZE)
        # クロスライン描画
        coords = coord_var.get().split(',')
        if len(coords) == 2 and coords[0].isdigit() and coords[1].isdigit():
            x, y = int(coords[0]), int(coords[1])
            # 座標がリサイズ後の範囲内かチェック
            if 0 <= x < FRAME_SIZE[0] and 0 <= y < FRAME_SIZE[1]:
                draw = ImageDraw.Draw(img)
                # 垂直線
                draw.line([(x, 0), (x, FRAME_SIZE[1])], width=1)
                # 水平線
                draw.line([(0, y), (FRAME_SIZE[0], y)], width=1)
        label.imgtk = ImageTk.PhotoImage(img)
        label.config(image=label.imgtk)
    except Exception:
        pass
    label.after(50, update_frame, label, coord_var)

# パラメータ適用
def apply_params():
    body = {
        'hsv_min': [h_vars['h_min'].get(), h_vars['s_min'].get(), h_vars['v_min'].get()],
        'hsv_max': [h_vars['h_max'].get(), h_vars['s_max'].get(), h_vars['v_max'].get()]
    }
    try:
        requests.post(f"{PI_SERVER}/params", json=body, timeout=1)
    except Exception as e:
        print(f"Error applying params: {e}")

# GUI 構築
def build_gui():
    global latest_coords, h_vars
    root = tk.Tk()
    root.title('Ball Tracker')

    # 取得座標用 StringVar
    latest_coords = tk.StringVar(root, value='-1,-1')

    # 映像表示ラベル
    frame_label = ttk.Label(root)
    frame_label.grid(row=0, column=0, rowspan=6)
    frame_label.after(100, update_frame, frame_label, latest_coords)

    # HSV スライダー生成
    defaults = {'h_min':5,'h_max':15,'s_min':100,'s_max':255,'v_min':100,'v_max':255}
    limits = {'h_min':180,'h_max':180,'s_min':255,'s_max':255,'v_min':255,'v_max':255}
    for idx, key in enumerate(['h_min','h_max','s_min','s_max','v_min','v_max']):
        lbl = key.replace('_',' ').upper()
        var = tk.IntVar(root, value=defaults[key])
        h_vars[key] = var
        ttk.Label(root, text=lbl).grid(row=idx, column=1, sticky='w')
        ttk.Scale(root, from_=0, to=limits[key], variable=var, orient='horizontal').grid(row=idx, column=2)

    ttk.Button(root, text='Apply', command=apply_params).grid(row=6, column=2, sticky='e')

    # 座標表示ラベル
    ttk.Label(root, text='Coords:').grid(row=6, column=0, sticky='w')
    ttk.Label(root, textvariable=latest_coords).grid(row=6, column=1, sticky='w')

    return root

if __name__ == '__main__':
    # GUI 初期化
    gui = build_gui()
    # UDP リスナー開始
    threading.Thread(target=udp_listener, args=(latest_coords,), daemon=True).start()
    gui.mainloop()
