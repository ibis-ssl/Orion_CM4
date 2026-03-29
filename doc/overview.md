# overview

## 全体方針

- ホスト側 GUI は `tkinter` ではなく Qt を使います。
- GUI は表示と操作に限定し、通信や処理本体は独立した Python モジュールへ分離します。
- 各機能は CLI だけで動作確認できる構成にします。
- GUI からも CLI からも同じ共通モジュールを利用し、処理の二重実装を避けます。

## uv

このリポジトリの Python 依存管理には `uv` を使います。

### 管理ファイル

- `pyproject.toml`
- `uv.lock`
- `.venv`

### 主なコマンド

- 仮想環境と依存を作成
  - `python -m uv sync`
- 制御 CLI の実行
  - `python -m uv run cm4-control scan`
- カメラ CLI の実行
  - `python -m uv run cm4-camera config --machine-no 3`
- ホスト GUI の実行
  - `python -m uv run host-launcher`
- カメラ GUI の実行
  - `python -m uv run cam-viewer`

### 補足

- Qt GUI の起動には `PySide6` を依存として含めています。
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
- `dist/cam_server_v3`

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

## cam_viewer.py

`cam_viewer.py` は、`cm4_camera.py` を利用する Qt ベースのカメラ GUI です。

### 役割

- `raw` と `mask` の 2 画面表示
- 座標の表示と十字線描画
- 機体番号切り替え
- HSV パラメータ調整

### 実装方針

- 通信処理は `cm4_camera.py` に持たせます。
- GUI は Qt による表示と入力だけを担当します。
- 画像取得、HSV 送信、座標受信はバックグラウンドスレッドで処理し、GUI をブロックしません。

## 補足

- `host_lancher.py` と `cam_viewer.py` の実行には `PySide6` が必要です。
- CLI モジュールは Qt 非依存なので、Qt 未導入環境でも単体動作確認できます。
