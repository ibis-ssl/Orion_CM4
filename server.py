import socket
import threading
from flask import Flask, jsonify, render_template
import time
import struct

class RobotFeedback:
    def __init__(self):
        self.counter = 0
        self.kick_state = 0
        self.temperature = [0] * 7
        self.error_id = 0
        self.error_info = 0
        self.error_value = 0.0
        self.motor_current = [0.0] * 4
        self.ball_detection = [0] * 4
        self.ball_sensor = False
        self.yaw_angle = 0.0
        self.diff_angle = 0.0
        self.odom = [0.0] * 2
        self.odom_speed = [0.0] * 2
        self.mouse_odom = [0.0] * 2
        self.mouse_vel = [0.0] * 2
        self.voltage = [0.0] * 2
        self.check_ver = 0
        self.values = []
        
    def to_dict(self):
        return {
            "counter": self.counter,
            "kick_state": self.kick_state,
            "temperature": self.temperature,
            "error_id": self.error_id,
            "error_info": self.error_info,
            "error_value": self.error_value,
            "motor_current": self.motor_current,
            "ball_detection": self.ball_detection,
            "ball_sensor": self.ball_sensor,
            "yaw_angle": self.yaw_angle,
            "diff_angle": self.diff_angle,
            "odom": self.odom,
            "odom_speed": self.odom_speed,
            "mouse_odom": self.mouse_odom,
            "mouse_vel": self.mouse_vel,
            "voltage": self.voltage,
            "check_ver": self.check_ver,
            "values": self.values,
        }
        
        
def parse_feedback(buffer):
    feedback = RobotFeedback()

    # データの読み込み
    feedback.counter = buffer[3]

    feedback.yaw_angle = struct.unpack('f', buffer[4:8])[0]
    feedback.voltage[0] = struct.unpack('f', buffer[8:12])[0]

    feedback.ball_detection[0:3] = buffer[12:15]
    feedback.kick_state = buffer[15] * 10

    feedback.error_id = struct.unpack('H', buffer[16:18])[0]
    feedback.error_info = struct.unpack('H', buffer[18:20])[0]
    feedback.error_value = struct.unpack('f', buffer[20:24])[0]

    feedback.motor_current[0:4] = [x / 10.0 for x in buffer[24:28]]
    feedback.ball_detection[3] = buffer[28]

    feedback.temperature[0:7] = buffer[29:36]

    feedback.diff_angle = struct.unpack('f', buffer[36:40])[0]
    feedback.voltage[1] = struct.unpack('f', buffer[40:44])[0]
    feedback.odom[0] = struct.unpack('f', buffer[44:48])[0]
    feedback.odom[1] = struct.unpack('f', buffer[48:52])[0]
    feedback.odom_speed[0] = struct.unpack('f', buffer[52:56])[0]
    feedback.odom_speed[1] = struct.unpack('f', buffer[56:60])[0]

    feedback.check_ver = buffer[60]

    feedback.mouse_odom[0] = struct.unpack('f', buffer[64:68])[0]
    feedback.mouse_odom[1] = struct.unpack('f', buffer[68:72])[0]
    feedback.mouse_vel[0] = struct.unpack('f', buffer[72:76])[0]
    feedback.mouse_vel[1] = struct.unpack('f', buffer[76:80])[0]

    # 追加のフロートデータの読み込み
    for i in range(64, 128, 4):
        if i + 4 <= len(buffer):
            feedback.values.append(struct.unpack('f', buffer[i:i + 4])[0])

    return feedback

app = Flask(__name__)

# ロボットの状態データを保存するリスト
latest_record = dict()
lock = threading.Lock()

# UDPでロボットのデータを受信する関数
def udp_listener():
    global latest_record
    udp_ip = "224.5.20.104"  # すべてのインターフェースから受信
    udp_port = 50104     # forward_robot_feedback.cpp の送信先ポートに合わせる
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('', udp_port))  # 空文字で全インターフェースから受信
    
    # マルチキャスト設定（ローカルのインターフェースを指定する）
    mreq = socket.inet_aton(udp_ip) + socket.inet_aton('192.168.20.104')  # 実際のローカルIPアドレスに置き換える
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

    while True:
        data, addr = sock.recvfrom(1024)  # 1024バイトまでのデータを受信
        print(f"Received data length: {len(data)} bytes")  # データの長さを確認
        feedback = parse_feedback(data)  # バイナリデータを解析
        timestamp = time.time()
        with lock:
            latest_record = {"time": timestamp, "status": feedback.to_dict()}


# Flaskのルートエンドポイント
@app.route('/')
def index():
    return render_template('index.html')

# ロボットの状態データをJSONで返すエンドポイント
@app.route('/status')
def get_status():
    try:
        with lock:
            return jsonify(latest_record)  # JSON形式でデータを返す
    except Exception as e:
        print(f"Error occurred while fetching status: {e}")  # サーバー側のエラーログ
        return jsonify({"error": str(e)}), 500  # 500エラーとともにエラー内容を返す

if __name__ == '__main__':
    # UDPリスナーを別スレッドで実行
    listener_thread = threading.Thread(target=udp_listener)
    listener_thread.daemon = True
    listener_thread.start()
    
    # Flaskサーバーを起動
    app.run(host='0.0.0.0', port=5000)
