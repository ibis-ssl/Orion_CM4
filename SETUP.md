# CM4 セットアップ

この手順は Raspberry Pi CM4 上で実行します。作業ディレクトリは `/home/ibis/Orion_CM4` を前提にしています。

## 事前準備

次の項目は環境ごとに値や操作が変わるため、`cm4/setup.sh` には入れていません。

- Raspberry Pi OS 64bit の導入
- ユーザー `ibis` の作成
- `wlan0` の固定 IP 設定
- CM4 側の `~/.ssh/authorized_keys` への公開鍵配置
- 必要に応じた Wi-Fi ドライバ設定

## セットアップ実行

```bash
cd /home/ibis/Orion_CM4
chmod +x cm4/setup.sh
./cm4/setup.sh
```

`sudo ./cm4/setup.sh` では実行しないでください。Python 依存を実行ユーザーの環境へ入れるため、必要な `sudo` はスクリプト内で個別に実行します。

`cm4/setup.sh` は次を実行します。

- APT パッケージ導入
- Python 依存導入
- `cm4/bridge/forward_robot_feedback.cpp` と `cm4/bridge/forward_ai_cmd_v2.cpp` のビルド
- `cm4/bin/robot_feedback.out` と `cm4/bin/ai_cmd_v2.out` の生成
- `cm4/camera/cam_server_v3.py` の PyInstaller ビルド
- `cm4/control_server.service` の配置、有効化、再起動

## オプション

APT upgrade を省略したい場合:

```bash
SKIP_APT_UPGRADE=1 ./cm4/setup.sh
```

カーネルヘッダ導入を省略したい場合:

```bash
SKIP_KERNEL_HEADERS=1 ./cm4/setup.sh
```

既存の `cm4/camera/dist/cam_server_v3` を使い、カメラサーバーの再ビルドを省略したい場合:

```bash
SKIP_CAMERA_BUILD=1 ./cm4/setup.sh
```

## systemd

サービス状態確認:

```bash
sudo systemctl status control_server.service
```

ログ確認:

```bash
journalctl -u control_server.service -f
```

`cm4/control_server.service` は `/home/ibis/Orion_CM4/cm4/lancher.py` を起動します。

## HSV 設定

カメラサーバーの HSV 設定は、`cm4/lancher.py` 経由で起動した場合に `cm4/runtime/cam_server_v3_hsv.json` に保存されます。

初回起動時は `cm4/camera/default_hsv_config.json` から作成されます。


## 手動ビルド
g++ cm4/bridge/forward_robot_feedback.cpp -pthread -o cm4/bin/robot_feedback.out
g++ cm4/bridge/forward_ai_cmd_v2.cpp -pthread -o cm4/bin/ai_cmd_v2.out

--debug バイナリ表示になる。マイコン側には送信されない。
-s オプションでボーレートを変更できる。デフォルト2Mbps
 ./cm4/bin/ai_cmd_v2.out --debug

 -n でIP指定(例:101)
 ./cm4/bin/robot_feedback.out -n 101
