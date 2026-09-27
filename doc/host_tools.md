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

## 共通監視ツール（予定）

Orionと4WSを同じ画面で監視するメインPCツールは、CM4が送る[55バイトの共通状態パケット](cm4_status_packet_proposal.md)を使用します。マイコン側の生フィードバックは解釈しません。このツールと共通パケットの送信は未実装です。

## 生フィードバック用デバッグツール

以下のCLIとQtビューアはOrionMainと4WS Mainの128バイト生フィードバックを解析します。先頭バイトから形式を自動判別し、必要なら`--machine-type orion`または`--machine-type 4ws`で指定できます。マイコン側のフィールド変更に合わせて更新するツールであり、共通状態パケットの監視ツールとは別です。4WS Mainの生データ配信は未実装です。

### `robot-feedback-receiver`

CM4から送信されるMainの生フィードバックをUDP multicastで受信し、形式別にデコードして標準出力へ出します。

```powershell
uv run robot-feedback-receiver --machine-no 3
uv run robot-feedback-receiver --machine-no 3 --max-packets 10
uv run robot-feedback-receiver --machine-no 3 --max-packets 1 --receive-timeout 5
uv run robot-feedback-receiver --machine-no 3 --json
uv run robot-feedback-receiver --machine-no 3 --machine-type 4ws --json
```

### `robot-feedback-viewer`

OrionMainと4WS Mainの生フィードバックをQt GUIの形式別タブに表示します。

```powershell
uv run robot-feedback-viewer --machine-no 10
uv run robot-feedback-viewer --machine-no 10 --interface-ip 192.168.20.200
uv run robot-feedback-viewer --machine-no 10 --machine-type 4ws
```

4WS Mainのデコード対象は[SPI通信案](4ws_spi_packet_proposal.md)の種別`0x82`状態通知です。マイコン側で形式が確定したらデコーダと表示項目を更新します。

## フリート管理ツール

### `cm4-fleet`

複数台の CM4 へ OTA アップデート・SSH 鍵配布・設定配布を一括で行う CLI です。詳細は [フリート管理](fleet.md) を参照してください。

```powershell
uv sync --extra fleet
uv run cm4-fleet bootstrap --all
uv run cm4-fleet deploy --all
uv run cm4-fleet status --all
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
- `host/lib/feedback/packet_4ws.py`
- `host/apps/cm4_fleet_cli.py`
- `host/lib/fleet/`
