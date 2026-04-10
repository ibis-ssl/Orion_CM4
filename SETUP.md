# CM4 側セットアップ

このドキュメントは Raspberry Pi CM4 上で `Orion_CM4` を動かすためのセットアップ手順です。

## 前提

- OS は Raspberry Pi OS 64bit
- 実行ユーザーは主に `ibis`
- リポジトリ配置は `/home/ibis/Orion_CM4`
- CM4 側 IP は `192.168.20.xxx` の固定 IP を使う
- Python 依存は `uv` を使わず、`pip` で導入する

## 手動で行う設定

次の項目は環境ごとに値や操作が変わるため、`setup.sh` には入れていません。

### Raspberry Pi 初期設定

- `sudo raspi-config`
- 必要に応じて Serial Port、VNC などを設定
- `wlan0` を有効化

### SSH 設定

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

### Wi-Fi 固定 IP 設定

`nmtui` で `wlan0` を固定 IP に設定します。

- `sudo nmtui`
- `Edit a connection`
- 対象 Wi-Fi を選択
- `IPv4 CONFIGURATION` を `Manual`
- 例: `192.168.20.103`

## リポジトリ取得

```bash
git clone https://github.com/ibis-ssl/Orion_CM4.git
cd Orion_CM4
```

## セットアップ実行

CM4 上で次を実行します。

```bash
chmod +x setup.sh
./setup.sh
```

`sudo ./setup.sh` では実行しないでください。Python 依存を実行ユーザーの環境へ入れるため、必要な `sudo` はスクリプト内で個別に実行します。

`setup.sh` は次を実行します。

- APT パッケージの更新と導入
- `pyproject.toml` に基づく Python 依存の導入
- `forward_robot_feedback.cpp` と `forward_ai_cmd_v2.cpp` のビルド
- `cm4_cam/cam_server_v3.py` の PyInstaller ビルド
- `control_server.service` の配置、有効化、再起動

APT upgrade を避けたい場合:

```bash
SKIP_APT_UPGRADE=1 ./setup.sh
```

既存の `cm4_cam/dist/cam_server_v3` を使い、カメラサーバーの再ビルドを省略したい場合:

```bash
SKIP_CAMERA_BUILD=1 ./setup.sh
```

## 状態確認

```bash
sudo systemctl status control_server.service
```

ログを見る場合:

```bash
journalctl -u control_server.service -f
```

## 補足

- `control_server.service` は `/home/ibis/Orion_CM4/lancher.py` を起動します。リポジトリ配置を変える場合は、サービスファイルも更新してください。
- `cam_server_v3.py` の HSV 設定は、初回起動時に `cm4_cam/default_hsv_config.json` から `runtime/cam_server_v3_hsv.json` へ作成されます。
- HSV 設定は `runtime/cam_server_v3_hsv.json` に保存され、Git 管理対象外です。
