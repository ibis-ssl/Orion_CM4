# overview

## MCUファームウェア更新

- CM4→Main→CANノードの高速・同時更新仕様は `doc/firmware_update_protocol.md` にまとめる。
- 全CANノードを先に安全な更新状態へ移し、MainのFDCAN1/FDCAN2を並行使用する。Main自身はゲートウェイ処理完了後に最後に更新する。
- 更新データは約896 byte単位で扱い、欠落・重複・順序ずれ・FIFO overflow・再接続を検出してchunk単位で回復する。
- CM4→Main→Subのv2経路は実装・実機確認済み。65,168 byteを正常時約8～10秒で更新し、UART CRC破損、CAN欠落・重複・逆順・payload破損からの回復を確認した。
- BLDC・電源基板にも同じアプリケーションブートローダーを実装した。OTA node IDはSub=4、BLDC=16/17（Flashのboard ID 0/1に対応）、電源=100とする。BLDC 2台はCM4→Main→CAN1/CAN2の並列更新を実機確認済み。電源基板も安全停止確認を含むCM4→Main→CAN更新を実機確認済みである。
- `cm4/firmware/all_can_updater.py`は全ノードを先に更新状態へ移し、左右BLDCをCAN1/CAN2へ並列配信し、全image確定後に一括再起動する。
- BLDC実機試験では63,592 byteを通常13.965秒、UART CRC破損とCAN欠落・重複・逆順・payload破損の複合注入時14.047秒で更新した。両台のreadback SHA-256・CRC32C `0xC22DAE9C`・metadata一致、board ID 0/1保持、2 Mbps UARTとCAN受信復帰を確認した。
- 2026-08-27、左右BLDCの同時更新を10回連続実施し10/10成功した。各回14.885～19.591秒、全回CRC32C `0xC22DAE9C`一致。UART応答欠落によるchunk再送88回もすべて回復し、両基板のmetadata CONFIRMED、VTOR `0x08004000`、board ID 0/1保持をST-Linkで確認した。
- MainはCM4経由のA/B更新を実装・実機確認済み。最終往復ではB→Aが9.808秒、A→Bが9.796秒で、Slot A generation 8、Slot B generation 9がともにCONFIRMED、boot attempts 0となった。通常USART2受信はIRQでRX FIFOを全量drainし、FWUP入口の72-byte要求取りこぼしを防ぐ。
- F303系のSub・BLDC・Powerは、有効なmetadataとアプリCRC32CがあればCAN待受けをせず即時にアプリへ遷移する。OTA要求時はアプリが出力を安全化してmetadataを無効化してからresetし、bootloaderは無効時だけCAN更新を無期限に待つ。不完全imageは起動しない。
- 2026-08-27、Powerを昇圧動作中から通常コマンドで停止し、安全statusを3フレーム確認後、65,244 byteを13.848秒で更新した。UARTで更新前の`PW 0 / BV 0 / Ch 0`と更新直後のアプリ起動・自己診断復帰を確認した。
- 同日、Subとboard ID 0/1の両BLDCへ即時起動版bootloaderをST-Linkで書込み・verifyした。周期CAN通信中のresetから各board IDを保持して即時起動することを確認後、CM4→Main経由でSubをCAN1へ14.132秒、BLDC node 16をCAN1へ17.157秒、node 17をCAN2へ14.300秒で更新した。全基板がUART通常動作とCAN受信へ復帰し、build ID・CRC32Cが期待バイナリと`SAME`であることを確認した。

## FW更新時間と高速化検討（2026-08-27）

現行はCM4–Main UARTが1 Mbaud、CAN1/CAN2がClassical CAN 1 Mbit/s、896 byte block、blockごとのstop-and-waitである。実機再測定ではBLDC 2台のCAN1/CAN2並列更新が64,360 byteで14.388秒、Main A/B更新が83,452 byteで9.896～10.032秒だった。F303系のeraseは約1.22秒、BLDC転送開始から最終block確定までは9.67秒で、3回のUART timeout再送約1.5秒を含む。Mainはerase約0.9秒、UART転送約3.3秒、finalize・reset・起動確認約4.9秒である。

PowerはSub/BLDCへの給電状態を制御するため、Powerを最初に安全停止して全nodeをbootloaderへ入れる順序は使用できない。実機ではPower停止後、Subはコンデンサ保持中に応答したが、BLDC選択時に給電が落ちてtimeoutした。更新順序は次のとおりとする。

1. SubとBLDC 2台を先にerase・転送・CRC確定する。
2. 必要ならSub/BLDCを再起動してmetadata確定済みであることを確認する。
3. Powerを通常コマンドで安全停止し、最後にPowerを更新する。
4. Power復帰後、Sub/BLDCは有効アプリへ即時起動する。Main自身のA/B更新は最後に行う。

現行`all_can_updater.py`はPowerを最初に停止するため、この給電構成では全体更新に使用しない。途中失敗後に別プロセスから再開するとOFW2 sequence同期エラーになる点、対象切替時の遅延CAN応答を除去できない点も修正が必要である。

高速化は次の優先順とする。

- 最優先はUART timeoutの解消である。USART2を1 byte割込みからDMA/ring bufferへ変更し、応答sequenceを再同期可能にする。実測では再送だけで1.5～6秒程度を消費している。
- F303 bootloaderは全アプリ領域54～55 pageをeraseしている。image size分の32～33 pageだけをeraseすれば、約1.22秒から約0.7～0.75秒へ短縮でき、image phaseごとに約0.5秒削減できる。
- 128 CAN frameを無間隔送信して失敗時に即時全再送する方式を、16～32 frame burstとflow-control、またはTX完了基準のadaptive pacingへ変更する。固定1 ms/8 frameはPowerを復旧できたが、更新時間が19.139秒へ増えたため採用しない。
- Sub/BLDCのeraseを並列開始し、CAN1ではSub→BLDC node 16、CAN2ではBLDC node 17を並行処理する。異なるimageをbusごとに保持できるgateway queueが必要である。
- 896 byte stop-and-waitを2～4 KiB windowへ拡大し、UART受信とCAN送信・Flash programを二重buffer化する。UARTを2 Mbaudへ上げるだけの効果は1 imageあたり約0.4～0.9秒であり、先にDMA化が必要である。
- F303はbxCANのためCAN FDは使用できない。Classical CAN 1 Mbit/sは標準上限であり、非標準の2 Mbit/s化は配線余裕と全node互換性を損なうため推奨しない。
- zlib level 1の参考圧縮率はSub 57.3%、BLDC 68.1%、Power 56.0%、Main 69.3%である。stream展開をbootloaderへ追加すれば効果は大きいが、開発量と障害時検証量も大きいため後段候補とする。

安全な低リスク改善では、CAN node全体を現状約40～45秒から約30～35秒、Mainを含む全体を約50～55秒から約40秒前後へ短縮できる見込みである。bus別pipeline、larger window、起動確認のevent化まで行う場合は全体20～30秒が現実的な目標で、Flash erase/programとClassical CAN帯域から見た下限は概ね15～20秒である。

### CM4–Main UART割込み受信の安定性

2026-08-27、CAN処理とFlash処理を使用せず、実更新と同じOFW2最大長923 byte（payload 907 byte）をCM4からMainへ送って応答を照合した。再送なしでは5 frame成功後の6 frame目でtimeoutを再現した。最大5回再送する試験では1,000/1,000 frameが最終成功したが、初回成功884、1回再送102、2回再送12、3回再送1、4回再送1で、11.6%が初回timeoutだった。

payload長別300回試験の初回timeout率は16 byteで0%、128 byteで1.33%、512 byteで7.0%、907 byteで11.33%だった。当初は長さ依存性からUART byte取りこぼしを疑ったが、後日のcount/hash計測でCM4送信列、Main ISR、リング取り出し列が完全一致し、この仮説は棄却した。

当時の実装はFIFO無効、HAL 1-byte再arm、エラー未計測だったため、FIFO・直接RX IRQ・リング・診断カウンタへ段階的に変更した。最終的な直接原因はpartial frame timeout判定のunsigned underflow競合であり、2026-08-28に修正・3,000回試験を完了した。詳細は末尾の「CM4–Main UART受信の安定化」を参照する。

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
- 更新中は全アクチュエータを無効化し、MainのブザーPWMも停止します。同一imageの左右BLDCはCAN1/CAN2へ並列配信し、G474は最後に更新します。
- BLDC の CAN ID とキャリブレーションを保持する Flash 領域は、アプリ更新領域から分離して消去禁止にします。
- CM4更新ツールだけでなく、G474/F303の基板別ブートローダーと全通常アプリFWの変更も開発範囲に含めます。
- 初回導入時だけ、全アプリの再配置と常駐ブートローダ書込のため SWD 作業が必要です。

Flash 配置、OFW-UART/OFW-CAN、bundle、状態遷移、障害復旧、受入試験の詳細は [STM32 ファームウェア更新仕様案](firmware_update.md) を参照してください。

2026-08-24時点で、G474 MainのM1（安全IO、Slot A再配置、CRC検証・jump、初回導入スクリプト）を実機へ導入済みです。readback一致、10回連続reset、metadata無効時の安全待機、PC12 Low/TIM5停止、metadata復元後のSlot A再起動を確認しました。

初回実機試験でbootloaderの割り込み禁止状態がSlot Aへ残る問題を修正済みです。修正後はCM4向けUSART2の128-byte frameを約124 Hzで連続受信し、デバッグLPUART1でも起動・IMU・CAN初期化ログを確認しています。

2026-08-25にMainの導入後更新を10回連続実施し、全回成功しました。F303 subも接続先スワップ後のST-Link（`002D00373033510635393935`、Device ID `0x422`）へbootloaderを初回導入済みです。通常更新のprogram/verify/reset後、VTOR=`0x08004000`、例外mask全解除、USART1 2 Mbpsログ、CAN受信カウンタ更新を実機確認しました。

## 開発用FWバージョン確認

- 各アプリの先頭から`0x400`に、magic `FWVR`とUnix秒のbuild IDを8 byteで配置する。製品用の署名・SemVer・互換性判定は行わない。
- MainはCM4から72 byte UART要求`FWVR`を受け、Main A/B、Sub、CAN1 BLDC、CAN2 BLDC、Powerのbuild IDとimage CRC32Cを60 byteで返す。CAN照会IDは`0x611`。
- `cm4/firmware/fw_version_reader.py`は現在値を一覧表示し、任意の期待バイナリを渡した場合は`SAME`、`OLDER`、`NEWER`、`CRC_MISMATCH`を表示する。
- STM32のアプリおよびブートローダー用PowerShellビルドスクリプトは、`Script/Logs/Build/`へbuild ID、UTC時刻、Git hash、dirty状態をJSON保存する。
- 2026-08-27の実機確認ではMain A/B、Sub、BLDC 2台がすべて期待バイナリと`SAME`になった。Powerは実装・ビルドのみで、未接続のため`UNREACHABLE`を確認した。

実行例:

```bash
python3 cm4/firmware/fw_version_reader.py \
  --main-a main_a.bin --main-b main_b.bin --sub sub.bin \
  --bldc-can1 bldc.bin --bldc-can2 bldc.bin
```

## CM4–Main UART受信の安定化（2026-08-28）

- CM4のGPIO14/15は、従来`/dev/serial0 -> ttyS0`のmini UARTだった。`/boot/firmware/config.txt`へ`dtoverlay=disable-bt`を追加し、`/dev/serial0 -> ttyAMA0`のPL011（GPIO14=TXD0、GPIO15=RXD0）へ切り替えた。元設定はCM4上の`/boot/firmware/config.txt.before_pl011_20260828`へ保存している。
- bridge、FW更新、バージョン確認の既定portは、UART実体名に依存しない`/dev/serial0`へ統一した。
- Main USART2は1 Mbpsの割り込み受信を維持し、8-byte FIFOを有効化した。RXはHALの1-byte受信状態機械から切り離し、ISRでFIFOをdrainして2 KBリングへ格納する。ORE等が発生してもISR末尾でRX割り込みを再有効化する。
- FW更新ゲートウェイ中は通常テレメトリDMAを停止し、OFW2応答とのUSART2送信競合を防ぐ。ブザーPWM停止も従来どおり維持する。
- 不安定性の直接原因は、partial frameの250 ms timeout式における競合だった。`HAL_GetTick()`取得後にUART IRQが`uart_last_byte_tick`を更新するとunsigned減算がunderflowし、受信途中のparserを誤ってresetしていた。最終byte時刻を先にsnapshotし、現在時刻取得後に値が変化していないことを再確認してからtimeout判定する。
- CM4期待列、Main ISR、リング取り出し列のcountとrolling hashが一致する診断を追加し、UART線上とリングのbyte欠落・並び替わりがないことを確認した。parser timeout、header/CRC、queue overflow、UART ORE/FE/NE/PEもST-Linkから参照できる。
- 最大payload 907 byte（総frame 923 byte）の初回応答試験は、修正前に約8～12%失敗していた。修正後は3,000/3,000成功（60.028秒、median 14.891 ms、p95 16.937 ms、max 17.832 ms）、Main A/BをCM4経由更新した後も1,000/1,000成功した。
- Main A/Bは同一build ID `1787928269`へ更新済みで、A=`23F1D426`、B=`CF4FCD66`のCRC32C一致を確認した。現在のactive slotはA。

コミット後の最終実機確認（2026-08-29）:

- Mainコミット`d050c6a`をdirtyなしでbuild ID `1787929548`としてA/Bビルドした。A→Bは84,444 byteを9.807秒（プロセス全体11.120秒）、B→Aは9.956秒（全体11.311秒）で更新し、双方のCRC32C一致とactive slot Aを確認した。
- 更新後のMainで最大payload 907 byte（総frame 923 byte）を3,000回連続送信し、3,000/3,000成功した。所要60.000秒、median 14.904 ms、p95 16.959 ms、max 17.108 msである。
- CM4→Main→CAN1のSub更新は65,912 byteを9.800秒（全体10.206秒）で完了した。試験終了後、Main A/B、Sub、BLDC 2台、Powerの全基板からbuild IDとCRC32Cを読出せることを確認した。
