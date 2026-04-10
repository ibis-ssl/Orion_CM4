# Orion_CM4

Orion 用の CM4 制御、カメラ配信、ホスト監視ツール一式です。

## 前提

- CM4 側 OS は Raspberry Pi OS 64bit
- 主な実行ユーザーは `ibis`
- CM4 側は `192.168.20.xxx` の固定 IP を利用
- ホスト側 GUI は Qt ベース
- CM4 側 Python 依存は `setup.sh` 経由で `pip` 導入
- ホスト PC 側 Python 依存は `uv` で導入・実行

## ディレクトリ内の主な役割

- `lancher.py`
  - CM4 側の制御 API サーバー
- `control_server.service`
  - `lancher.py` を自動起動する `systemd` ユニット
- `cm4_control.py`
  - 制御 API 用の共通 CLI / ライブラリ
- `host_lancher.py`
  - `cm4_control.py` を使うホスト側 Qt GUI
- `cm4_cam/`
  - CM4 側で動作するカメラサーバー本体、デフォルトHSV設定、PyInstaller 設定、実行ファイル
- `runtime/`
  - CM4 側で実行時に生成される設定ファイル置き場
- `cm4_camera.py`
  - カメラ API / multicast 用の共通 CLI / ライブラリ
- `cam_viewer.py`
  - `cm4_camera.py` を使うホスト側 Qt GUI
  - raw 画像の ROI ドラッグによる HSV 自動推定に対応
- `robot_feedback_packet.py`
  - `forward_robot_feedback.cpp` の 128 バイトパケットを Python でデコード
- `robot_feedback_rerun.py`
  - UDP multicast の robot feedback を受信して `rerun-sdk` で可視化

## CM4 側セットアップ

CM4 側のセットアップ手順は専用ドキュメントに分離しました。

- 詳細手順: [SETUP.md](SETUP.md)
- 内容: Raspberry Pi 初期設定、固定 IP、`setup.sh` による依存導入、C++ ビルド、カメラサーバービルド、`systemd` 設定

## 詳細ドキュメント

- [概要](doc/overview.md)
- [ホスト PC 側ツール](doc/host_tools.md)
- [カメラ制御・デバッグ](doc/camera.md)
- [制御パケット](doc/control_packet.md)
- [フィードバックパケット](doc/feedback_packet.md)

## ホスト PC 側セットアップ・ツール

ホスト PC 側は `uv` で依存環境を管理します。

```powershell
uv sync
```

各ツールの起動方法は [ホスト PC 側ツール](doc/host_tools.md) を参照してください。

## 機体番号と接続先規則

機体番号を `N` とすると:

- 制御サーバー IP: `192.168.20.(100 + N)`
- 制御サーバーポート: `8000`
- カメラ API IP: `192.168.20.(100 + N)`
- カメラ API ポート: `8001`
- multicast グループ: `224.5.10.(100 + N)`
- multicast ポート: `5100 + N`

## 補足

- `host_lancher.py` と `cam_viewer.py` は `PySide6` が必要です。
- 通信処理は GUI に持たせず、CLI から単独確認できる共通モジュールへ分離しています。
- `robot_feedback_rerun.py` は Windows/Linux の両方で動く Python 製の受信ツールです。
- 詳細な役割分担は `doc/overview.md` を参照してください。
- CM4 の初期構築手順は `SETUP.md` を参照してください。
