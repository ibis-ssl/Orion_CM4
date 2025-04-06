from fastapi import FastAPI
import subprocess
import uvicorn
import socket
import fcntl
import struct
import os

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
    executable_path = os.path.join(base_dir, "ai_cmd_v2.out")
    subprocess.Popen([executable_path,"-s","1000000"])
    
    ip = get_ip_address()
    ip_last = ip.split(".")[-1]

    executable_path = os.path.join(base_dir, "robot_feedback.out")
    subprocess.Popen([executable_path,"-s","1000000","-n",ip_last])

    return {"status": "started"}

@app.post("/stop")
def stop_control():
    subprocess.run(["pkill", "-f", "ai_cmd_v2.out"])
    subprocess.run(["pkill", "-f", "robot_feedback.out"])
    return {"status": "stopped"}

@app.get("/status")
def get_status():
    result = subprocess.run(["pgrep", "-f", "ai_cmd_v2.out"], capture_output=True)
    return {"running": result.returncode  == 0}

if __name__ == "__main__":
    wlan0_ip = get_ip_address()
    print(wlan0_ip)
    uvicorn.run(app, host=wlan0_ip, port=8000)