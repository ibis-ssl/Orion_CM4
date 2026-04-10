# カメラ制御・デバッグ

このドキュメントは、CM4 側カメラサーバー、ホスト側カメラクライアント、デバッグ GUI の責務と通信仕様をまとめます。

## 対象ファイル

- `cm4_cam/cam_server_v3.py`
  - CM4 上で動作するカメラサーバーです。
- `cm4_cam/default_hsv_config.json`
  - 初回起動時に使うデフォルト HSV 設定です。
- `cm4_cam/cam_server_v3.spec`
  - `cam_server_v3.py` を PyInstaller で単体実行ファイルへ変換する設定です。
- `cm4_camera.py`
  - ホスト側から CM4 カメラサーバーを操作する共通 CLI / ライブラリです。
- `cam_viewer.py`
  - `cm4_camera.py` を利用する Qt ベースのカメラデバッグ GUI です。

## CM4 側カメラサーバー

`cm4_cam/cam_server_v3.py` は、カメラ画像を取得し、HSV マスクによるボール検出、HTTP API、multicast 座標配信を行います。

### HTTP API

- API ポート: `8001`
- `GET /frame/raw`
  - raw 画像を JPEG で返します。
- `GET /frame/mask`
  - HSV マスク画像を JPEG で返します。
- `GET /params`
  - 現在の HSV パラメータを JSON で返します。
- `POST /params`
  - `hsv_min` と `hsv_max` を更新します。
  - 更新後、CM4 側の設定ファイルへ保存します。

### HSV 設定

- 初回起動時のデフォルト値は `cm4_cam/default_hsv_config.json` に置きます。
- `lancher.py` 経由で起動した場合、保存先は `runtime/cam_server_v3_hsv.json` です。
- 保存時は一時ファイル `runtime/cam_server_v3_hsv.json.tmp` に書いてから、`runtime/cam_server_v3_hsv.json` へ置き換えます。
- `lancher.py` は保存先を環境変数 `ORION_CM4_HSV_CONFIG` で渡します。
- `runtime/*.json` と `runtime/*.tmp` は Git 管理対象外です。

### 座標計算

1. BGR フレームを HSV に変換します。
2. `hsv_min` / `hsv_max` で `cv2.inRange()` による 2 値マスクを作ります。
3. `cv2.morphologyEx(..., MORPH_OPEN, 5x5)` で小さいノイズを落とします。
4. `cv2.findContours()` で外部輪郭を抽出します。
5. 最大面積の輪郭を選びます。
6. `cv2.minEnclosingCircle()` の中心を `x, y` とします。
7. `cv2.minEnclosingCircle()` の半径を `radius` とします。
8. `cv2.contourArea()` を `area` とします。
9. 輪郭が無い場合は `x=0, y=0, area=0, radius=0` とします。

### multicast 座標配信

- `-n` に渡した値を `N` とすると、配信先は `224.5.10.N:5000+N` です。
- `lancher.py` 経由では CM4 の IP 末尾を渡します。
  - 例: 機体番号 10、IP `192.168.20.110` の場合は `-n 110`
  - 配信先は `224.5.10.110:5110`
- 送信ペイロードは文字列です。
  - 形式: `x,y,area,fps`
- multicast 送信は既定で `wlan0` の IP アドレスを `IP_MULTICAST_IF` に設定して行います。
- `--mcast-if` で送信インターフェイス名を指定できます。
- `--mcast-if-ip` で送信元 IPv4 アドレスを直接指定できます。

### STM32 feedback 用ローカルカメラ UDP

`forward_ai_cmd_v2.cpp` が STM32 へカメラ情報を渡せるよう、`cam_server_v3.py` は検出結果をローカル UDP にも送ります。

- 既定の送信先: `127.0.0.1:8890`
- 送信ペイロード: 7 バイト
  - `0..1`: x 座標、big-endian uint16
  - `2..3`: y 座標、big-endian uint16
  - `4..5`: radius、big-endian uint16
  - `6`: fps、uint8
- `--local-cam-host` と `--local-cam-port` で送信先を変更できます。
- `--disable-local-cam-udp` でこのローカル UDP 送信を無効化できます。

カメラが未接続、切断、またはボール未検出の場合は `radius=0, x=0, y=0` になるよう扱います。
カメラ更新が途絶えた場合は `forward_ai_cmd_v2.cpp` 側のタイムアウトで STM32 へ送る値が 0 に戻ります。

## ホスト側カメラクライアント

`cm4_camera.py` は、GUI と CLI から共通利用するカメラ通信処理です。

### 接続先規則

機体番号を `N` とすると:

- HTTP 接続先 IP: `192.168.20.(100 + N)`
- API ポート: `8001`
- multicast グループ: `224.5.10.(100 + N)`
- multicast ポート: `5100 + N`

例: 機体番号 10 の場合:

- HTTP: `http://192.168.20.110:8001`
- multicast: `224.5.10.110:5110`

### CLI 例

- 接続先確認
  - `uv run python cm4_camera.py config --machine-no 10`
- HSV パラメータ取得
  - `uv run python cm4_camera.py get-params --machine-no 10`
- 画像取得
  - `uv run python cm4_camera.py frame --machine-no 10 --image-name raw --output raw.jpg`
- HSV 更新
  - `uv run python cm4_camera.py params --machine-no 10 --hsv-min 0 100 100 --hsv-max 15 255 255`
- 座標受信
  - `uv run python cm4_camera.py coords --machine-no 10 --timeout 1.0`
- ROI 推定
  - `uv run python cm4_camera.py roi-calibrate --machine-no 10 --left 90 --top 180 --width 40 --height 40`

## カメラデバッグ GUI

`cam_viewer.py` は、CM4 側カメラサーバーの出力を見るためのデバッグ GUI です。

### 役割

- raw 画像と mask 画像を表示します。
- CM4 側が送信した multicast 座標を表示します。
- 受信した座標に基づいて十字線を描画します。
- HSV パラメータを表示・更新します。
- raw 画像上の ROI ドラッグから HSV を推定し、CM4 側へ送信します。
- 使用しているホスト側 IP とインターフェイス名を接続先表示に出します。

### 注意

- `cam_viewer.py` は座標計算を行いません。
- 座標は CM4 側から受信した `x,y,area,fps` のみを使います。
- mask 画像は表示だけに使います。

### 起動例

```powershell
uv run python cam_viewer.py --machine-no 10
```

表示例:

```text
機体10: http://192.168.20.110:8001 / 224.5.10.110:5110 / host 192.168.20.200 (AnkerUSBC-Eth)
```

## ビルド

`setup.sh` は `cm4_cam/cam_server_v3.py` を PyInstaller で `cm4_cam/dist/cam_server_v3` へビルドします。

`cm4_cam/dist/cam_server_v3` を使う場合は、`cam_server_v3.py` の変更後に CM4 上で再ビルドが必要です。
