# このファイルはCM4上の制御用Web APIを担当し、制御プロセスとカメラサーバーを起動・停止する。
# このファイルは CM4 上の制御用 Web API を担当する。
# 制御ブリッジ、feedback 転送、カメラサーバーの起動停止をまとめて行う。
from fastapi import FastAPI
import subprocess
import uvicorn
import socket
import fcntl
import struct
import os

from ai_cmd_options import load_ai_cmd_options

app = FastAPI()

def get_ip_address(ifname='wlan0'):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    return socket.inet_ntoa(
        fcntl.ioctl(
            s.fileno(),
            0x8915,  # SIOCGIFADDR
            struct.pack('256s', ifname[:15].encode('utf-8'))
        )[20:24]
    )

@app.post("/start")
def start_control():
    status = get_status()
    if status["running"]:
        print("already_running")
        return {"status": "already_running"}
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    bin_dir = os.path.join(base_dir, "bin")
    executable_path = os.path.join(bin_dir, "ai_cmd_v2.out")
    # 安全停止のタイムアウト等は runtime/ai_cmd_v2_options.json で機体ごとに上書きできる (無ければ既定値)。
    extra_args = load_ai_cmd_options(os.path.join(base_dir, "runtime"))
    subprocess.Popen([executable_path,"-s","1000000"] + extra_args)
    
    ip = get_ip_address()
    ip_last = ip.split(".")[-1]

    executable_path = os.path.join(bin_dir, "robot_feedback.out")
    subprocess.Popen([executable_path,"-s","1000000","-n",ip_last])

    executable_path = os.path.join(base_dir, "camera", "dist", "cam_server_v3")
    hsv_config_path = os.path.join(base_dir, "runtime", "cam_server_v3_hsv.json")
    env = os.environ.copy()
    env["ORION_CM4_HSV_CONFIG"] = hsv_config_path
    subprocess.Popen([executable_path,"-n",ip_last], env=env)

    return {"status": "started"}

@app.post("/stop")
def stop_control():
    subprocess.run(["pkill", "-f", "ai_cmd_v2.out"])
    subprocess.run(["pkill", "-f", "robot_feedback.out"])
    subprocess.run(["pkill", "-f", "cam_server_v3"])
    return {"status": "stopped"}

@app.get("/status")
def get_status():
    result = subprocess.run(["pgrep", "-f", "ai_cmd_v2.out"], capture_output=True)
    return {"running": result.returncode  == 0}

if __name__ == "__main__":
    wlan0_ip = get_ip_address()
    print(wlan0_ip)
    uvicorn.run(app, host=wlan0_ip, port=8000)
