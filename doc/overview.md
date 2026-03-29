# overview

## control_server.service

`control_server.service` は、CM4 起動後にハードウェア制御用の FastAPI サーバーを自動起動するための `systemd` ユニットファイルです。

### 役割

- ネットワーク初期化後に制御サーバーを起動します。
- 作業ディレクトリを `/home/ibis/Orion_CM4` に固定して実行します。
- `lancher.py` を `/usr/bin/python3` で起動します。
- 異常終了時は 5 秒待って自動再起動します。

### 主な設定

- `Description=FastAPI Launcher for Hardware Control`
- `After=network.target`
- `User=ibis`
- `WorkingDirectory=/home/ibis/Orion_CM4`
- `ExecStart=/usr/bin/python3 /home/ibis/Orion_CM4/lancher.py`
- `Restart=always`
- `RestartSec=5`
- `WantedBy=multi-user.target`

### 補足

- CM4 の通常起動で自動的に有効化して使う構成を想定しています。
- 実行対象のファイル名は `launcher.py` ではなく `lancher.py` です。サービス定義と実ファイル名を合わせて管理してください。

## lancher.py

`lancher.py` は、各 CM4 上で動作する制御用 Web API サーバーです。FastAPI と Uvicorn を使い、外部からの開始、停止、状態確認の要求を受け付けます。

### 役割

- `/start` で制御関連プロセスを起動します。
- `/stop` で制御関連プロセスを停止します。
- `/status` で制御プロセスの稼働状態を返します。
- `wlan0` の IP アドレスを取得し、その IP の `8000` 番ポートで待ち受けます。

### 起動するプロセス

- `ai_cmd_v2.out`
- `robot_feedback.out`
- `dist/cam_server_v3`

### 実装上のポイント

- 二重起動防止のため、`/start` 実行前に `ai_cmd_v2.out` の稼働有無を確認します。
- `robot_feedback.out` と `cam_server_v3` には、自機 IP アドレスの最終オクテットを `-n` オプションとして渡します。
- 停止処理は `pkill -f` を使って対象プロセス名で終了させます。
- 状態確認は `pgrep -f ai_cmd_v2.out` の戻り値で判定しています。

### 補足

- 本ファイルは `control_server.service` から起動される前提です。
- バインド先インターフェースは `wlan0` 固定です。ネットワーク構成を変更する場合は `get_ip_address()` の対象も合わせて見直してください。

## host_lancher.py

`host_lancher.py` は、ホスト PC 側から複数の CM4 を一括監視、操作するための GUI ツールです。`tkinter` で画面を構成し、各 CM4 上の `lancher.py` に HTTP リクエストを送ります。

### 役割

- `192.168.20.100` から `192.168.20.112` までの CM4 を監視対象にします。
- 各ノードに対して `Run` と `Stop` ボタンを提供します。
- 定期的に `/status` を問い合わせ、稼働状態を画面表示します。

### 通信仕様

- 接続先ポートは `8000` 固定です。
- 開始操作は `POST /start` を送信します。
- 停止操作は `POST /stop` を送信します。
- 状態確認は `GET /status` を送信します。
- 通信タイムアウトは `0.5` 秒です。

### 実装上のポイント

- 監視更新は別スレッドで動かし、1 秒周期で各ノードの状態を再取得します。
- 複数ノードへの状態確認は `ThreadPoolExecutor` で並列化しています。
- 取得した状態は GUI スレッドに戻してラベルへ反映します。
- 表示上の状態は `Running`、`Stopped`、`Offline`、`Error` の 4 種類です。

### 補足

- `host_lancher.py` は Windows/Linux の両方で使う想定ですが、接続先 API の仕様は `lancher.py` に依存します。
- 監視対象 IP 範囲を変更する場合は `PI_IP_LIST` を修正してください。

## cam_viewer.py

`cam_viewer.py` は、カメラ画像と検出座標をホスト PC 上で確認し、色抽出パラメータを調整するための GUI ツールです。`tkinter` を使って表示画面を構成し、HTTP と UDP multicast の 2 系統で CM4 側と通信します。

### 役割

- CM4 側から取得した `raw` と `mask` の 2 種類の画像を表示します。
- UDP で受信した座標情報をもとに、画像上へ十字線を重ねて表示します。
- HSV のしきい値を GUI から変更し、CM4 側へ反映します。

### 通信仕様

- 画像取得先は `http://192.168.20.106:8001` 固定です。
- 画像は `GET /frame/raw` と `GET /frame/mask` で取得します。
- HSV パラメータは `POST /params` に JSON で送信します。
- 座標情報は multicast `224.5.10.106:5106` で受信します。

### 実装上のポイント

- 画像更新は `after(100, ...)` を使い、約 100 ms 周期で再取得します。
- 表示サイズは `320x240` に固定してリサイズします。
- 受信した座標文字列を分解し、範囲内であれば縦線と横線を描画します。
- UDP 受信は別スレッドで常時実行し、最新座標を `StringVar` に反映します。
- HSV パラメータは `h_min`、`h_max`、`s_min`、`s_max`、`v_min`、`v_max` の 6 項目です。

### 補足

- `cam_viewer.py` は Windows/Linux の両方で使う想定です。
- 接続先 CM4 を変更する場合は `PI_SERVER` を修正してください。
- multicast のアドレスやポートを変更する場合は、送信側と `MCAST_GRP`、`MCAST_PORT` を合わせて更新してください。
