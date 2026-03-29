# Orion_CM4

Orion 用の CM4 制御、カメラ配信、ホスト監視ツール一式です。

## 前提

- CM4 側 OS は Raspberry Pi OS 64bit
- 主な実行ユーザーは `ibis`
- CM4 側は `192.168.20.xxx` の固定 IP を利用
- ホスト側 GUI は Qt ベース
- Python 依存管理は `uv` を利用

## ディレクトリ内の主な役割

- `lancher.py`
  - CM4 側の制御 API サーバー
- `control_server.service`
  - `lancher.py` を自動起動する `systemd` ユニット
- `cm4_control.py`
  - 制御 API 用の共通 CLI / ライブラリ
- `host_lancher.py`
  - `cm4_control.py` を使うホスト側 Qt GUI
- `cm4_camera.py`
  - カメラ API / multicast 用の共通 CLI / ライブラリ
- `cam_viewer.py`
  - `cm4_camera.py` を使うホスト側 Qt GUI

## CM4 側セットアップ

### 1. Raspberry Pi 初期設定

- `sudo raspi-config`
- 必要に応じて Serial Port、VNC などを設定
- `wlan0` を有効化

### 2. SSH 設定

CM4 側の `~/.ssh/authorized_keys` にホスト側の公開鍵を配置します。

ホスト側の `~/.ssh/config` 例:

```sshconfig
Host CM4_1xx
    HostName 192.168.20.1xx
    User ibis
    ServerAliveInterval 10
    StrictHostKeyChecking no
    IdentityFile ~/.ssh/cm4_rsa
```

### 3. Wi-Fi 固定 IP 設定

`nmtui` で `wlan0` を固定 IP に設定します。

- `sudo nmtui`
- `Edit a connection`
- 対象 Wi-Fi を選択
- `IPv4 CONFIGURATION` を `Manual`
- 例: `192.168.20.103`

### 4. 必要パッケージ

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y git libboost-all-dev linux-headers-generic dkms pkg-config rsync gtkterm build-essential bc python3 python3-pip
sudo apt autoremove -y
```

### 5. リポジトリ取得

```bash
git clone git@github.com:ibis-ssl/Orion_CM4.git
cd Orion_CM4
```

HTTPS で clone 済みなら、必要に応じて SSH に切り替えます。

```bash
git remote set-url origin git@github.com:ibis-ssl/Orion_CM4.git
```

### 6. C++ バイナリのビルド

```bash
g++ forward_robot_feedback.cpp -pthread -o robot_feedback.out
g++ forward_ai_cmd_v2.cpp -pthread -o ai_cmd_v2.out
```

### 7. Python 依存の導入

CM4 側でも Python 依存は `uv` で管理します。

```bash
python3 -m pip install --user uv
python3 -m uv sync
```

### 8. systemd 設定

`control_server.service` を配置して有効化します。

```bash
sudo cp control_server.service /etc/systemd/system/control_server.service
sudo systemctl daemon-reload
sudo systemctl enable control_server.service
sudo systemctl start control_server.service
```

状態確認:

```bash
sudo systemctl status control_server.service
```

## ホスト PC 側セットアップ

### 1. Python と uv

Python 3.11 以上を用意し、`uv` を導入します。

```powershell
python -m pip install --user uv
```

### 2. 仮想環境と依存作成

```powershell
python -m uv sync
```

これで `.venv`、`uv.lock`、必要依存が揃います。

### 3. 主な依存

- `requests`
- `fastapi`
- `uvicorn`
- `flask`
- `numpy`
- `opencv-python`
- `pillow`
- `pyside6`

## 動作確認

### 制御 CLI

単体状態確認:

```powershell
python -m uv run cm4-control status --ip 192.168.20.103
```

複数台スキャン:

```powershell
python -m uv run cm4-control scan
```

起動:

```powershell
python -m uv run cm4-control start --ip 192.168.20.103
```

停止:

```powershell
python -m uv run cm4-control stop --ip 192.168.20.103
```

### カメラ CLI

接続先確認:

```powershell
python -m uv run cm4-camera config --machine-no 3
```

画像取得:

```powershell
python -m uv run cm4-camera frame --machine-no 3 --image-name raw --output raw.jpg
```

HSV 更新:

```powershell
python -m uv run cm4-camera params --machine-no 3 --hsv-min 0 100 100 --hsv-max 15 255 255
```

座標受信:

```powershell
python -m uv run cm4-camera coords --machine-no 3 --timeout 1.0
```

## GUI 起動

### ホスト制御 GUI

```powershell
python -m uv run host-launcher
```

### カメラ GUI

```powershell
python -m uv run cam-viewer
```

## 機体番号と接続先規則

機体番号を `N` とすると:

- 制御サーバー IP: `192.168.20.(100 + N)`
- 制御サーバーポート: `8000`
- カメラ API IP: `192.168.20.(100 + N)`
- カメラ API ポート: `8001`
- multicast グループ: `224.5.10.(100 + N)`
- multicast ポート: `5100 + N`

## 補足

- `host_lancher.py` と `cam_viewer.py` は `PySide6` が必要です。
- 通信処理は GUI に持たせず、CLI から単独確認できる共通モジュールへ分離しています。
- 詳細な役割分担は `doc/overview.md` を参照してください。
