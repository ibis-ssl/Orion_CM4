# ホスト PC 側ツール

ホスト側で実行する Python ツールは `host/` にまとめています。

## セットアップ

```powershell
uv sync
```

通常は `pyproject.toml` の entry point から実行します。

## 制御ツール

### `cm4-control`

CM4 側の `cm4/lancher.py` に対する HTTP クライアントです。

```powershell
uv run cm4-control status --ip 192.168.20.103
uv run cm4-control scan
uv run cm4-control start --ip 192.168.20.103
uv run cm4-control stop --ip 192.168.20.103
```

Python module として直接実行する場合:

```powershell
uv run python -m host.apps.cm4_control_cli scan
```

### `host-launcher`

`cm4-control` と同じ処理を使う Qt GUI です。

```powershell
uv run host-launcher
```

## カメラツール

### `cm4-camera`

CM4 側カメラサーバーの HTTP API と multicast 座標を扱う CLI / 共通ライブラリです。

```powershell
uv run cm4-camera config --machine-no 10
uv run cm4-camera get-params --machine-no 10
uv run cm4-camera frame --machine-no 10 --image-name raw --output raw.jpg
uv run cm4-camera params --machine-no 10 --hsv-min 0 100 100 --hsv-max 15 255 255
uv run cm4-camera coords --machine-no 10 --timeout 1.0
uv run cm4-camera roi-calibrate --machine-no 10 --left 90 --top 180 --width 40 --height 40
```

### `cam-viewer`

CM4 側カメラサーバーの raw/mask 画像、座標、HSV 設定を確認する Qt GUI です。

```powershell
uv run cam-viewer --machine-no 10
```

## robot feedback ツール

### `robot-feedback-receiver`

CM4 から送信される robot feedback の UDP multicast を受信し、128 バイトパケットをデコードして標準出力へ出します。

```powershell
uv run robot-feedback-receiver --machine-no 3
uv run robot-feedback-receiver --machine-no 3 --max-packets 10
uv run robot-feedback-receiver --machine-no 3 --max-packets 1 --receive-timeout 5
uv run robot-feedback-receiver --machine-no 3 --json
```

### `robot-feedback-viewer`

robot feedback を Qt GUI で時系列表示します。

```powershell
uv run robot-feedback-viewer --machine-no 10
uv run robot-feedback-viewer --machine-no 10 --interface-ip 192.168.20.200
```

### `robot-feedback-rerun`

robot feedback を Rerun に記録・表示します。

```powershell
uv run robot-feedback-rerun --machine-no 3
uv run robot-feedback-rerun --machine-no 3 --max-packets 10
uv run robot-feedback-rerun --machine-no 3 --max-packets 1 --receive-timeout 5
uv run robot-feedback-rerun --machine-no 3 --no-spawn
```

## ファイル配置

- `host/lib/cm4_control_client.py`
- `host/apps/host_lancher.py`
- `host/lib/cm4_camera_client.py`
- `host/apps/cam_viewer.py`
- `host/lib/feedback/packet.py`
- `host/lib/feedback/receiver.py`
- `host/apps/robot_feedback_receiver_cli.py`
- `host/apps/robot_feedback_viewer.py`
- `host/apps/robot_feedback_rerun.py`
