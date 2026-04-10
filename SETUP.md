# CM4 側セットアップ

このドキュメントは Raspberry Pi CM4 上で `Orion_CM4` を動かすためのセットアップ手順をまとめたものです。

## 前提

- OS は Raspberry Pi OS 64bit
- 実行ユーザーは主に `ibis`
- CM4 側 IP は `192.168.20.xxx` の固定 IP を使う
- Python 依存は `uv` を使わず、`pip` で導入する

## 1. Raspberry Pi 初期設定

- `sudo raspi-config`
- 必要に応じて Serial Port、VNC などを設定
- `wlan0` を有効化

## 2. SSH 設定

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

## 3. Wi-Fi 固定 IP 設定

`nmtui` で `wlan0` を固定 IP に設定します。

- `sudo nmtui`
- `Edit a connection`
- 対象 Wi-Fi を選択
- `IPv4 CONFIGURATION` を `Manual`
- 例: `192.168.20.103`

## 4. 必要パッケージ

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y git libboost-all-dev linux-headers-generic dkms pkg-config rsync gtkterm build-essential bc python3 python3-pip
sudo apt autoremove -y
```

## 5. リポジトリ取得

```bash
git clone https://github.com/ibis-ssl/Orion_CM4.git
cd Orion_CM4
```


## 6. C++ バイナリのビルド

```bash
g++ forward_robot_feedback.cpp -pthread -o robot_feedback.out
g++ forward_ai_cmd_v2.cpp -pthread -o ai_cmd_v2.out
```

## 7. Python 依存の導入

`pyproject.toml` に定義した依存を `pip` で導入します。

```bash
python3 -m pip install --user --break-system-packages -e .
```

## 8. systemd 設定

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
