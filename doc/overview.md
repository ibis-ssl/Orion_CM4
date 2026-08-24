# overview

## MCUファームウェア更新

- CM4→Main→CANノードの高速・同時更新仕様は `doc/firmware_update_protocol.md` にまとめる。
- 全CANノードを先に安全な更新状態へ移し、MainのFDCAN1/FDCAN2を並行使用する。Main自身はゲートウェイ処理完了後に最後に更新する。
- 更新データは約896 byte単位で扱い、欠落・重複・順序ずれ・FIFO overflow・再接続を検出してchunk単位で回復する。
- CM4→Main→Subのv2経路は実装・実機確認済み。65,168 byteを正常時約8～10秒で更新し、UART CRC破損、CAN欠落・重複・逆順・payload破損からの回復を確認した。
- BLDC・電源基板にも同じアプリケーションブートローダーを実装した。OTA node IDはSub=4、BLDC=16/17（Flashのboard ID 0/1に対応）、電源=100とする。実機電源が使用できないため、BLDC・電源はビルドとホスト模擬試験まで完了し、実機試験は未実施である。
- `cm4/firmware/all_can_updater.py`は全ノードを先に更新状態へ移し、左右BLDCをCAN1/CAN2へ並列配信し、全image確定後に一括再起動する。

## 全体方針

このリポジトリは、実行場所で大きく分けています。

- `host/`
  - ホスト PC 上で実行する Python CLI / GUI ツールを置きます。
  - Windows/Linux の両方で動かすツールはここに集約します。
- `cm4/`
  - Raspberry Pi CM4 上で実行する制御 API、カメラサーバー、UART ブリッジ、セットアップ資材を置きます。
  - 生成される CM4 用実行ファイルは `cm4/bin/` に置きます。
- `host/robot-manager/`
  - 複数台の CM4 をブラウザから操作する管理 Web UI です。
- `host/lib/fleet/`
  - ホスト PC から複数の CM4 へ OTA アップデート・SSH 鍵配布・設定配布を行うライブラリです(`cm4-fleet` CLI から利用)。詳細は [フリート管理](fleet.md)。
- `host/templates/`
  - 旧 Web 表示用の静的 HTML テンプレートです。
- `doc/`
  - 通信仕様、セットアップ、運用メモを日本語で残します。

## 主要ディレクトリ

```text
Orion_CM4/
  host/
    apps/
      cm4_control_cli.py
      cm4_camera_cli.py
      host_lancher.py
      cam_viewer.py
      robot_feedback_receiver_cli.py
      robot_feedback_viewer.py
      robot_feedback_rerun.py
    lib/
      cm4_control_client.py
      cm4_camera_client.py
      feedback/
        packet.py
        receiver.py

  cm4/
    lancher.py
    setup.sh
    control_server.service
    bridge/
      forward_ai_cmd_v2.cpp
      forward_robot_feedback.cpp
      robot_packet.h
    camera/
      cam_server_v3.py
      cam_server_v3.spec
      default_hsv_config.json
    bin/
      ai_cmd_v2.out
      robot_feedback.out
    runtime/
      cam_server_v3_hsv.json

  host/
    robot-manager/
    templates/
```

## CM4 側

`cm4/lancher.py` は、各 CM4 上で動作する制御用 FastAPI サーバーです。

- `/start`
  - `cm4/bin/ai_cmd_v2.out` を起動します。
  - `cm4/bin/robot_feedback.out` を起動します。
  - `cm4/camera/dist/cam_server_v3` を起動します。
- `/stop`
  - 上記プロセスを停止します。
- `/status`
  - 制御ブリッジの起動状態を返します。

`cm4/setup.sh` は CM4 側の初期セットアップ用スクリプトです。

- APT パッケージを導入します。
- `pip install -e .` で Python 依存を導入します。
- `cm4/bridge/*.cpp` をビルドし、`cm4/bin/` に出力します。
- `cm4/camera/cam_server_v3.py` を PyInstaller で `cm4/camera/dist/cam_server_v3` にビルドします。
- `cm4/control_server.service` を `/etc/systemd/system/` に配置します。

主な実行コマンド:

```bash
cd /home/ibis/Orion_CM4
chmod +x cm4/setup.sh
./cm4/setup.sh
```

## ホスト側

ホスト PC では `uv sync` で依存を導入し、`pyproject.toml` の entry point から実行します。

```powershell
uv sync
uv run cm4-control scan
uv run host-launcher
uv run cam-viewer --machine-no 10
uv run robot-feedback-viewer --machine-no 10
```

ファイルを直接指定する場合は、パッケージとして実行します。

```powershell
uv run python -m host.apps.cm4_control_cli scan
uv run python -m host.apps.cm4_camera_cli config --machine-no 10
uv run python -m host.apps.robot_feedback_receiver_cli --machine-no 3
```

## Web 管理 UI

`host/robot-manager/` は Docker で動く管理 Web UI です。

```powershell
docker compose up --build
```

`docker-compose.yaml` の build context は `./host/robot-manager` です。

## 通信の基本

機体番号を `N` とすると、基本的な接続先は次の通りです。

- CM4 制御 API: `http://192.168.20.(100 + N):8000`
- カメラ API: `http://192.168.20.(100 + N):8001`
- カメラ座標 multicast: `224.5.10.(100 + N):5100 + N`
- robot feedback multicast: `224.5.20.(100 + N):50000 + (100 + N)`

## 関連ドキュメント

- [ホスト PC 側ツール](host_tools.md)
- [フリート管理(OTA・複数台一括設定)](fleet.md)
- [STM32 ファームウェア更新仕様案](firmware_update.md)
- [STM32 FW更新機能 開発・実機試験手順](firmware_update_development.md)
- [カメラ制御・デバッグ](camera.md)
- [制御パケット](control_packet.md)
- [フィードバックパケット](feedback_packet.md)

## STM32 ファームウェア更新

CM4 から UART 接続の STM32G474 と、その配下の 2 系統の CAN に接続された 4 台の STM32F303 を更新する方針は次の通りです。

- 全 MCU の Flash 先頭へ書込保護した常駐ブートローダを置きます。
- STM32内蔵System Memoryブートローダーは使わず、各基板の全GPIOを安全状態へ初期化する自作アプリケーションブートローダーを使います。
- G474 ブートローダーを UART/CAN 更新ゲートウェイとし、CRC・対象基板・書込範囲を検証します。暗号署名や証明書は使用しません。
- G474 は 512 KB Flash を利用した A/B 更新と自動 rollback、F303 は単一アプリ領域と中断後の再送復旧を採用します。
- 更新中は全アクチュエータを無効化し、MainのブザーPWMも停止します。F303 は 1 台ずつ、G474 は最後に更新します。
- BLDC の CAN ID とキャリブレーションを保持する Flash 領域は、アプリ更新領域から分離して消去禁止にします。
- CM4更新ツールだけでなく、G474/F303の基板別ブートローダーと全通常アプリFWの変更も開発範囲に含めます。
- 初回導入時だけ、全アプリの再配置と常駐ブートローダ書込のため SWD 作業が必要です。

Flash 配置、OFW-UART/OFW-CAN、bundle、状態遷移、障害復旧、受入試験の詳細は [STM32 ファームウェア更新仕様案](firmware_update.md) を参照してください。

2026-08-24時点で、G474 MainのM1（安全IO、Slot A再配置、CRC検証・jump、初回導入スクリプト）を実機へ導入済みです。readback一致、10回連続reset、metadata無効時の安全待機、PC12 Low/TIM5停止、metadata復元後のSlot A再起動を確認しました。

初回実機試験でbootloaderの割り込み禁止状態がSlot Aへ残る問題を修正済みです。修正後はCM4向けUSART2の128-byte frameを約124 Hzで連続受信し、デバッグLPUART1でも起動・IMU・CAN初期化ログを確認しています。

2026-08-25にMainの導入後更新を10回連続実施し、全回成功しました。F303 subも接続先スワップ後のST-Link（`002D00373033510635393935`、Device ID `0x422`）へbootloaderを初回導入済みです。通常更新のprogram/verify/reset後、VTOR=`0x08004000`、例外mask全解除、USART1 2 Mbpsログ、CAN受信カウンタ更新を実機確認しました。
