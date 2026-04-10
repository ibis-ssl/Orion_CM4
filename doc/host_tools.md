# ホスト PC 側ツール

このドキュメントは、ホスト PC 側で実行する Python ツールの導入、起動方法、役割をまとめます。

## 前提

- ホスト PC 側の Python 依存環境は `uv` で管理します。
- Python 3.11 以上を使います。
- GUI ツールには `PySide6` が必要です。
- 通信対象の CM4 は `192.168.20.xxx` の固定 IP を持つ前提です。

## 依存導入

プロジェクト直下で実行します。

```powershell
uv sync
```

以降のホスト PC 側コマンドは `uv run` 経由で実行します。

## ツール一覧

### cm4_control.py

`cm4_control.py` は、CM4 側の `lancher.py` に対する HTTP クライアントです。

主な用途:

- 単体 CM4 の状態確認
- 複数 CM4 のスキャン
- `/start` による制御関連プロセス起動
- `/stop` による制御関連プロセス停止

CLI 例:

```powershell
uv run python cm4_control.py status --ip 192.168.20.103
uv run python cm4_control.py scan
uv run python cm4_control.py start --ip 192.168.20.103
uv run python cm4_control.py stop --ip 192.168.20.103
```

詳細: [制御パケット](control_packet.md)

### host_lancher.py

`host_lancher.py` は、`cm4_control.py` を利用する Qt ベースのホスト制御 GUI です。

主な用途:

- `192.168.20.100` から `192.168.20.112` までの CM4 を監視
- 各ノードの `Run` / `Stop` 操作
- 定期的な状態更新

起動:

```powershell
uv run python host_lancher.py
```

### cm4_camera.py

`cm4_camera.py` は、CM4 側カメラサーバーを操作する CLI / 共通ライブラリです。

主な用途:

- 接続先確認
- raw / mask 画像取得
- HSV パラメータ取得・更新
- multicast 座標受信
- ROI からの HSV 推定

CLI 例:

```powershell
uv run python cm4_camera.py config --machine-no 10
uv run python cm4_camera.py get-params --machine-no 10
uv run python cm4_camera.py frame --machine-no 10 --image-name raw --output raw.jpg
uv run python cm4_camera.py params --machine-no 10 --hsv-min 0 100 100 --hsv-max 15 255 255
uv run python cm4_camera.py coords --machine-no 10 --timeout 1.0
uv run python cm4_camera.py roi-calibrate --machine-no 10 --left 90 --top 180 --width 40 --height 40
```

詳細: [カメラ制御・デバッグ](camera.md)

### cam_viewer.py

`cam_viewer.py` は、CM4 側カメラサーバーの出力を見る Qt ベースのデバッグ GUI です。

主な用途:

- raw 画像と mask 画像の表示
- CM4 側から受信した座標の表示
- 受信座標に基づく十字線描画
- HSV パラメータの表示・更新
- raw 画像上の ROI ドラッグによる HSV 推定
- 使用中のホスト側 IP とインターフェイス名の表示

起動:

```powershell
uv run python cam_viewer.py --machine-no 10
```

注意:

- `cam_viewer.py` は座標計算を行いません。
- 座標は CM4 側から受信した `x,y,area,fps` のみを使います。
- mask 画像は表示だけに使います。

詳細: [カメラ制御・デバッグ](camera.md)

### robot_feedback_receiver.py

`robot_feedback_receiver.py` は、CM4 から送信される robot feedback の UDP multicast を受信し、パケットをデコードして標準出力へ出す CLI ツールです。
GUI や Rerun などのフロントエンドには依存しません。

主な用途:

- 128 バイトの状態パケット受信
- `robot_feedback_packet.py` によるデコード
- 同期バイトとチェックサムの確認
- カメラ座標、電圧、姿勢、エラー情報などのテキスト表示
- JSON Lines 形式での出力

CLI 例:

```powershell
uv run python robot_feedback_receiver.py --machine-no 3
uv run python robot_feedback_receiver.py --machine-no 3 --max-packets 10
uv run python robot_feedback_receiver.py --machine-no 3 --max-packets 1 --receive-timeout 5
uv run python robot_feedback_receiver.py --machine-no 3 --json
```

詳細: [フィードバックパケット](feedback_packet.md)

### robot_feedback_rerun.py

`robot_feedback_rerun.py` は、CM4 から送信される robot feedback を Rerun へ記録する可視化用 CLI ツールです。
受信とパースだけを確認したい場合は、フロントエンド非依存の `robot_feedback_receiver.py` を使います。

主な用途:

- 電圧、姿勢、カメラ座標、`tx_value_array` の可視化

CLI 例:

```powershell
uv run python robot_feedback_rerun.py --machine-no 3
uv run python robot_feedback_rerun.py --machine-no 3 --max-packets 10
uv run python robot_feedback_rerun.py --machine-no 3 --max-packets 1 --receive-timeout 5
```

既存の Rerun Viewer に接続したい場合:

```powershell
uv run python robot_feedback_rerun.py --machine-no 3 --no-spawn
```

詳細: [フィードバックパケット](feedback_packet.md)

## 機体番号と接続先規則

機体番号を `N` とすると:

- 制御サーバー IP: `192.168.20.(100 + N)`
- 制御サーバーポート: `8000`
- カメラ API IP: `192.168.20.(100 + N)`
- カメラ API ポート: `8001`
- カメラ座標 multicast グループ: `224.5.10.(100 + N)`
- カメラ座標 multicast ポート: `5100 + N`
- フィードバック multicast グループ: `224.5.20.(100 + N)`
- フィードバック multicast ポート: `50100 + N`

例: 機体番号 10 の場合:

- 制御 API: `http://192.168.20.110:8000`
- カメラ API: `http://192.168.20.110:8001`
- カメラ座標 multicast: `224.5.10.110:5110`
- フィードバック multicast: `224.5.20.110:50110`
