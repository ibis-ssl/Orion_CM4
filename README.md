# Orion_CM4

Orion 用 CM4 制御、カメラ配信、ホスト側監視ツール一式です。

## ディレクトリ構成

実行場所で大きく分けています。

- `host/`
  - ホスト PC 上で実行する Python CLI / Qt GUI です。
  - 実行アプリケーションは `host/apps/`、共通ライブラリは `host/lib/` に置きます。
- `cm4/`
  - Raspberry Pi CM4 上で実行するコードとセットアップ資材です。
  - `lancher.py`, `setup.sh`, `control_server.service`, `bridge/`, `camera/`
- `host/robot-manager/`
  - 複数台の CM4 を操作する Web 管理 UI です。
- `doc/`
  - 仕様と運用メモです。日本語で記述します。

## CM4 側

CM4 側のセットアップ:

```bash
cd /home/ibis/Orion_CM4
chmod +x cm4/setup.sh
./cm4/setup.sh
```

`cm4/setup.sh` は C++ ブリッジを `cm4/bin/` にビルドし、カメラサーバーを `cm4/camera/dist/` にビルドし、`cm4/control_server.service` を systemd に登録します。

`cm4/lancher.py` は `:8000` の FastAPI サーバーとして動き、`/start`, `/stop`, `/status` を提供します。

## ホスト側

ホスト PC 側の依存導入:

```powershell
uv sync
```

主な実行コマンド:

```powershell
uv run cm4-control scan
uv run host-launcher
uv run cam-viewer --machine-no 10
uv run robot-feedback-viewer --machine-no 10
uv run robot-feedback-rerun --machine-no 10
```

直接 Python module として実行する場合:

```powershell
uv run python -m host.apps.cm4_control_cli scan
uv run python -m host.apps.cm4_camera_cli config --machine-no 10
```

## フリート管理 (OTA・複数台一括設定)

複数台の CM4 へ、ホスト PC から一括で OTA アップデート・SSH 鍵配布・設定配布を行うツールです。

```powershell
uv sync --extra fleet
uv run cm4-fleet bootstrap --all
uv run cm4-fleet deploy --all
uv run cm4-fleet status --all
```

詳細は [フリート管理](doc/fleet.md) を参照してください。

## Web 管理 UI

```powershell
docker compose up --build
```

Docker build context は `host/robot-manager/` です。

## 接続規則

機体番号を `N` とすると:

- 制御 API: `192.168.20.(100 + N):8000`
- カメラ API: `192.168.20.(100 + N):8001`
- カメラ multicast: `224.5.10.(100 + N):5100 + N`
- robot feedback multicast: `224.5.20.(100 + N):50000 + (100 + N)`

## 詳細ドキュメント

- [概要](doc/overview.md)
- [CM4 セットアップ](SETUP.md)
- [ホスト PC 側ツール](doc/host_tools.md)
- [フリート管理(OTA・複数台一括設定)](doc/fleet.md)
- [カメラ制御・デバッグ](doc/camera.md)
- [制御パケット](doc/control_packet.md)
- [フィードバックパケット](doc/feedback_packet.md)
