# overview

## 全体方針

- ホスト側 GUI は `tkinter` ではなく Qt を使います。
- GUI は表示と操作に限定し、通信や処理本体は独立した Python モジュールへ分離します。
- 各機能は CLI だけで動作確認できる構成にします。
- GUI からも CLI からも同じ共通モジュールを利用し、処理の二重実装を避けます。
- CM4 の初期構築手順はプロジェクト直下の `SETUP.md` に分離して管理します。

## Python 依存導入

このリポジトリの Python 依存は `pyproject.toml` を元に `pip install -e .` で導入します。

### 管理ファイル

- `pyproject.toml`
- 必要に応じて `.venv`

### 主なコマンド

- 依存を導入
  - Linux: `python3 -m pip install --user --break-system-packages -e .`
  - Windows: `python -m pip install --user -e .`
- 仮想環境を使う場合
  - `python -m venv .venv`
  - Windows: `.venv\Scripts\pip install -e .`
  - Linux: `.venv/bin/pip install -e .`
- 制御 CLI の実行
  - `python cm4_control.py scan`
- カメラ CLI の実行
  - `python cm4_camera.py config --machine-no 3`
- ホスト GUI の実行
  - `python host_lancher.py`
- カメラ GUI の実行
  - `python cam_viewer.py`

### 補足

- Qt GUI の起動には `PySide6` を依存として含めています。
- `pip install -e .` を使うことで依存導入と編集可能インストールを同時に行えます。
- `project.scripts` を使えるよう、`pyproject.toml` には `setuptools` ベースのビルド設定を入れています。

## control_server.service

`control_server.service` は、CM4 起動後にハードウェア制御用の FastAPI サーバーを自動起動するための `systemd` ユニットファイルです。

### 役割

- ネットワーク初期化後に制御サーバーを起動します。
- 作業ディレクトリを `/home/ibis/Orion_CM4` に固定して実行します。
- `lancher.py` を `/usr/bin/python3` で起動します。
- 異常終了時は 5 秒待って自動再起動します。

## lancher.py

`lancher.py` は、各 CM4 上で動作する制御用 Web API サーバーです。

### 役割

- `/start` で制御関連プロセスを起動します。
- `/stop` で制御関連プロセスを停止します。
- `/status` で制御プロセスの稼働状態を返します。
- `wlan0` の IP アドレスを取得し、その IP の `8000` 番ポートで待ち受けます。

### 起動するプロセス

- `ai_cmd_v2.out`
- `robot_feedback.out`
- `cm4_cam/dist/cam_server_v3`

## cm4_cam/

`cm4_cam/` は、CM4 上で実行するカメラサーバー関連ファイルをまとめたディレクトリです。

### 役割

- `cam_server_v3.py` を管理します。
- `default_hsv_config.json` に、初回起動時に使うデフォルト HSV 設定を置きます。
- `cam_server_v3.spec` で、`cam_server_v3.py` の PyInstaller ビルド設定を管理します。
- `dist/cam_server_v3` に、`lancher.py` から起動するカメラサーバー実行ファイルを配置します。

### 補足

- ホスト側から利用する `cm4_camera.py` と `cam_viewer.py` は、引き続きプロジェクト直下に置きます。
- `lancher.py` の `/start` は `cm4_cam/dist/cam_server_v3` を起動します。
- `cam_server_v3.py` の HSV 初期値は `runtime/cam_server_v3_hsv.json` から読み込みます。
- HSV を更新したときは同じディレクトリに一時ファイル `runtime/cam_server_v3_hsv.json.tmp` を書き、`runtime/cam_server_v3_hsv.json` へ置き換えて保存します。
- `lancher.py` はカメラサーバー起動時に、保存先を環境変数 `ORION_CM4_HSV_CONFIG` で渡します。
- `dist/cam_server_v3` を使う場合は、`cam_server_v3.py` の変更後に CM4 上で再ビルドが必要です。

## forward_robot_feedback.cpp

`forward_robot_feedback.cpp` は、STM32 から UART で受信した 128 バイトの状態パケットを、そのまま UDP multicast へ転送する中継プログラムです。

### 役割

- `/dev/ttyS0` からシリアル受信します。
- 先頭同期バイト `0xAB 0xEA` を検出して 128 バイト固定長パケットを組み立てます。
- 組み立てたパケットを `224.5.20.<機体番号>:50000+機体番号` へ送信します。

### パケット構造

- バイト `0`: 同期バイト `0xAB`
- バイト `1`: 同期バイト `0xEA`
- バイト `2`: チェックサム
- バイト `3`: `check_counter`
- バイト `4..63`: STM32 側 `sendRobotInfo()` が埋める固定項目
- バイト `64..119`: `tx_value_array[14]` の float 値
- バイト `120..127`: 現状未使用

### 補足

- 浮動小数点は STM32 側 `float_to_uchar4()` の生バイト列をそのまま送っているため、little-endian IEEE754 `float` 前提で解釈します。
- 送信元の実装定義は `C:\Users\hiroyuki\STM32CubeIDE\workspace_1.17.0\G474_Orion_main\Core\Src\ai_comm.c` の `sendRobotInfo()` にあります。

## robot_feedback_packet.py

`robot_feedback_packet.py` は、`forward_robot_feedback.cpp` が送る 128 バイトパケットを Python でデコードする共通モジュールです。

### 役割

- 同期バイト、チェックサム、固定長レイアウトの解釈
- little-endian IEEE754 `float` の復元
- `tx_value_array[14]` のラベル付け

## robot_feedback_rerun.py

`robot_feedback_rerun.py` は、robot feedback の UDP multicast を受信し、`rerun-sdk` で時系列プロットする CLI ツールです。

### 役割

- `224.5.20.<機体番号>:50000+機体番号` を受信
- `robot_feedback_packet.py` でデコード
- 電圧、姿勢、カメラ座標、`tx_value_array` を Rerun へ記録
- カメラ検出位置を 2D View に表示

### CLI 例

- 3番機体を表示
  - `python robot_feedback_rerun.py --machine-no 3`
- 10 パケット受信して終了
  - `python robot_feedback_rerun.py --machine-no 3 --max-packets 10`
- 5 秒だけ待って受信が無ければ終了
  - `python robot_feedback_rerun.py --machine-no 3 --max-packets 1 --receive-timeout 5`

## cm4_control.py

`cm4_control.py` は、CM4 制御サーバー向けの共通クライアントです。GUI と CLI の両方から使います。

### 役割

- 制御サーバーの状態取得
- `start` / `stop` コマンド送信
- 複数 IP の並列スキャン

### CLI 例

- 単体状態確認
  - `python cm4_control.py status --ip 192.168.20.103`
- 複数台状態確認
  - `python cm4_control.py scan`
- 起動
  - `python cm4_control.py start --ip 192.168.20.103`
- 停止
  - `python cm4_control.py stop --ip 192.168.20.103`

## host_lancher.py

`host_lancher.py` は、`cm4_control.py` を利用する Qt ベースのホスト GUI です。

### 役割

- `192.168.20.100` から `192.168.20.112` までの CM4 を監視します。
- 各ノードに `Run` と `Stop` の操作ボタンを表示します。
- 定期的に状態を更新します。

### 実装方針

- 通信処理は `cm4_control.py` に持たせます。
- GUI は Qt の画面更新とイベント処理のみを担当します。
- 状態取得とコマンド送信はバックグラウンドスレッドで処理し、GUI をブロックしません。

## cm4_camera.py

`cm4_camera.py` は、CM4 カメラサーバー向けの共通クライアントです。GUI と CLI の両方から使います。

### 役割

- 機体番号から接続先設定を計算
- `raw` / `mask` 画像の取得
- HSV パラメータ送信
- multicast 座標受信
- ROI 矩形からの HSV 自動推定

### 接続先規則

- 機体番号を `N` とすると HTTP 接続先 IP は `192.168.20.(100 + N)` です。
- API ポートは `8001` です。
- multicast グループは `224.5.10.(100 + N)` です。
- multicast ポートは `5100 + N` です。

### CLI 例

- 接続先確認
  - `python cm4_camera.py config --machine-no 3`
- 画像取得
  - `python cm4_camera.py frame --machine-no 3 --image-name raw --output raw.jpg`
- HSV 更新
  - `python cm4_camera.py params --machine-no 3 --hsv-min 0 100 100 --hsv-max 15 255 255`
- 座標受信
  - `python cm4_camera.py coords --machine-no 3 --timeout 1.0`
- ROI 推定
  - `python cm4_camera.py roi-calibrate --machine-no 3 --left 90 --top 180 --width 40 --height 40`

## cam_viewer.py

`cam_viewer.py` は、`cm4_camera.py` を利用する Qt ベースのカメラ GUI です。

### 役割

- `raw` と `mask` の 2 画面表示
- 座標の表示と十字線描画
- 機体番号切り替え
- HSV パラメータ調整
- raw 画像上の ROI ドラッグから HSV 推定

### 実装方針

- 通信処理は `cm4_camera.py` に持たせます。
- GUI は Qt による表示と入力だけを担当します。
- 画像取得、HSV 送信、座標受信はバックグラウンドスレッドで処理し、GUI をブロックしません。
- raw 画像上をドラッグすると、その矩形の色分布から HSV を推定してスライダーへ反映し、同時に CM4 側へ適用します。

## 補足

- `host_lancher.py` と `cam_viewer.py` の実行には `PySide6` が必要です。
- CLI モジュールは Qt 非依存なので、Qt 未導入環境でも単体動作確認できます。
