# overview

## 全体方針

- ホスト側 GUI は `tkinter` ではなく Qt を使います。
- GUI は表示と操作に限定し、通信や処理本体は独立した Python モジュールへ分離します。
- robot feedback の受信・パースは GUI や Rerun などのフロントエンドに依存させません。
- 各機能は CLI だけで動作確認できる構成にします。
- GUI からも CLI からも同じ共通モジュールを利用し、処理の二重実装を避けます。
- CM4 の初期構築手順はプロジェクト直下の `SETUP.md` に分離して管理します。

## 詳細ドキュメント

- [カメラ制御・デバッグ](camera.md)
  - `cm4_cam/cam_server_v3.py`
  - `cm4_camera.py`
  - `cam_viewer.py`
  - HSV 設定、HTTP API、multicast 座標、デバッグ GUI
- [ホスト PC 側ツール](host_tools.md)
  - `cm4_control.py`
  - `host_lancher.py`
  - `cm4_camera.py`
  - `cam_viewer.py`
  - `robot_feedback_receiver.py`
  - `robot_feedback_rerun.py`
  - `uv` による導入とホスト側実行コマンド
- [制御パケット](control_packet.md)
  - `robot_packet.h`
  - `forward_ai_cmd_v2.cpp`
  - `cm4_control.py`
  - `host_lancher.py`
  - AI 制御パケット、UART 送信、CM4 制御 API
- [フィードバックパケット](feedback_packet.md)
  - `forward_robot_feedback.cpp`
  - `robot_feedback_packet.py`
  - `robot_feedback_receiver.py`
  - `robot_feedback_rerun.py`
  - 128 バイト状態パケット、UDP multicast、受信パース、Rerun 表示

## Python 依存導入

このリポジトリの Python 依存は `pyproject.toml` を元に導入します。
CM4 側では `setup.sh` から `pip install -e .` を実行します。
ホスト PC 側では `uv sync` と `uv run` を使います。

### 管理ファイル

- `pyproject.toml`
- ホスト PC 側の `.venv`

### 主なコマンド

- CM4 側の依存導入・ビルド・systemd 登録
  - `./setup.sh`
- ホスト PC 側の依存導入
  - `uv sync`
- ホスト PC 側ツールの実行
  - [ホスト PC 側ツール](host_tools.md) を参照

### 補足

- Qt GUI の起動には `PySide6` を依存として含めています。
- ホスト PC 側では `uv sync` で依存導入と編集可能インストールを行います。
- `project.scripts` を使えるよう、`pyproject.toml` には `setuptools` ベースのビルド設定を入れています。

## control_server.service

`control_server.service` は、CM4 起動後にハードウェア制御用の FastAPI サーバーを自動起動するための `systemd` ユニットファイルです。

### 役割

- ネットワーク初期化後に制御サーバーを起動します。
- `wlan0` の IP アドレスを起動時に取得するため、`network-online.target` を待ってから起動します。
- 作業ディレクトリを `/home/ibis/Orion_CM4` に固定して実行します。
- `lancher.py` を `/usr/bin/python3` で起動します。
- 異常終了時は 5 秒待って自動再起動します。

## setup.sh

`setup.sh` は、CM4 側の自動セットアップ用スクリプトです。

### 役割

- APT パッケージを更新・導入します。
- `pyproject.toml` に基づいて Python 依存を導入します。
- `forward_robot_feedback.cpp` と `forward_ai_cmd_v2.cpp` をビルドします。
- `cm4_cam/cam_server_v3.py` を PyInstaller で `cm4_cam/dist/cam_server_v3` へビルドします。
- `control_server.service` を `/etc/systemd/system/` へ配置し、有効化・再起動します。

### 補足

- `SKIP_APT_UPGRADE=1 ./setup.sh` で APT upgrade を省略できます。
- `SKIP_KERNEL_HEADERS=1 ./setup.sh` でカーネルヘッダ導入を省略できます。
- `SKIP_CAMERA_BUILD=1 ./setup.sh` で既存の `cm4_cam/dist/cam_server_v3` を使い、カメラサーバーの再ビルドを省略できます。

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

## 補足

- `host_lancher.py` と `cam_viewer.py` の実行には `PySide6` が必要です。
- CLI モジュールは Qt 非依存なので、Qt 未導入環境でも単体動作確認できます。
