# 概要

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
    build.sh
    setup.sh
    update.sh
    control_server.service
    bridge/
      forward_ai_cmd_v2.cpp
      forward_robot_feedback.cpp
      robot_packet.h
      robot_packet_layout_test.cpp
      cm4_sim.cpp
      test_cm4_sim.py
      test_forward_ai_cmd_v2.py
    control/
      position_controller.h
      position_controller.cpp
      test_position_controller.cpp
    camera/
      cam_server_v3.py
      cam_server_v3.spec
      default_hsv_config.json
    bin/
      ai_cmd_v2.out
      robot_feedback.out
      cm4_sim.out                  (ホスト PC 専用)
      robot_packet_layout_test.out
      test_position_controller.out
    runtime/
      cam_server_v3_hsv.json

  host/
    robot-manager/
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

`cm4/build.sh` は C++ バイナリの**唯一のビルド定義**です。`setup.sh` と `update.sh`
（`cm4-fleet deploy` から呼ばれる）の両方がこれを呼びます。sudo も apt も使わないので、
ホスト PC (x86_64) でもそのまま走ります。末尾でテスト一式を実行します
（`--no-tests` でスキップ）。

```bash
./cm4/build.sh            # ビルド + テスト
./cm4/build.sh --no-tests # ビルドのみ
```

`cm4/setup.sh` は CM4 側の初期セットアップ用スクリプトです。

- APT パッケージを導入します。
- `pip install -e .` で Python 依存を導入します。
- `cm4/build.sh` を呼び、`cm4/bin/` に出力します。
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
- 統合仕様の正本（framework 側）: `framework/docs/robot-side-position-control.md`
- [作業ログメモ](work_log.md)

## ログの管理方針

- ログはGitへ追加しない。`.gitignore`で`*.log`とルートの`runtime/`全体を除外し、実行ログ・計測CSV・packet capture・一時BINはローカルに保存する。

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

## MCUファームウェア更新

- CM4→Main→CANノードの高速・同時更新仕様は `doc/firmware_update_protocol.md` にまとめる。
- 全CANノードを先に安全な更新状態へ移し、MainのFDCAN1/FDCAN2を並行使用する。Main自身はゲートウェイ処理完了後に最後に更新する。
- 更新データは約896 byte単位で扱い、欠落・重複・順序ずれ・FIFO overflow・再接続を検出してchunk単位で回復する。
- CM4→Main→Subのv2経路を実装済み。UART CRC破損、CAN欠落・重複・逆順・payload破損からの回復に対応する。
- BLDC・電源基板にも同じアプリケーションブートローダーを実装済み。OTA node IDはSub=4、BLDC=16/17（Flashのboard ID 0/1に対応）、電源=100とする。
- `cm4/firmware/all_can_updater.py`は全ノードを先に更新状態へ移し、左右BLDCをCAN1/CAN2へ並列配信し、全image確定後に一括再起動する。
- MainはCM4経由のA/B更新を実装済み。通常USART2受信はIRQでRX FIFOを全量drainし、FWUP入口の72-byte要求取りこぼしを防ぐ。
- F303系のSub・BLDC・Powerは、有効なmetadataとアプリCRC32CがあればCAN待受けをせず即時にアプリへ遷移する。OTA要求時はアプリが出力を安全化してmetadataを無効化してからresetし、bootloaderは無効時だけCAN更新を無期限に待つ。不完全imageは起動しない。

## 開発用FWバージョン確認

- 各アプリの先頭から`0x400`に、magic `FWVR`とUnix秒のbuild IDを8 byteで配置する。製品用の署名・SemVer・互換性判定は行わない。
- MainはCM4から72 byte UART要求`FWVR`を受け、Main A/B、Sub、CAN1 BLDC、CAN2 BLDC、Powerのbuild IDとimage CRC32Cを60 byteで返す。CAN照会IDは`0x611`。
- `cm4/firmware/fw_version_reader.py`は現在値を一覧表示し、任意の期待バイナリを渡した場合は`SAME`、`OLDER`、`NEWER`、`CRC_MISMATCH`を表示する。
- STM32のアプリおよびブートローダー用PowerShellビルドスクリプトは、`Script/Logs/Build/`へbuild ID、UTC時刻、Git hash、dirty状態をJSON保存する。

実行例:

```bash
python3 cm4/firmware/fw_version_reader.py \
  --main-a main_a.bin --main-b main_b.bin --sub sub.bin \
  --bldc-can1 bldc.bin --bldc-can2 bldc.bin
```

## ロボット側位置制御（CM4 で位置ループを閉じる）（2026-09-13）

### 何を変えたか

位置制御ループを crane（AI）側から CM4 側へ移した。crane は **位置指令（mode 4）** を送り、
CM4 が位置制御ループを閉じて **速度指令（mode 3）** を G474 へ渡す。

これまでは crane がループを閉じて速度指令を無線で送っていたため、
**不安定で遅延の乗る無線経路が位置制御ループの内側**に入っていた。新構成では無線経路が
ループの外側（目標値の更新経路）へ移る。

```text
旧: crane [位置ループ] --UDP 速度指令--> CM4 --UART--> G474
新: crane --UDP 位置指令--> CM4 [位置ループ] --UART 速度指令--> G474
```

### 構成

```text
実機: crane --UDP:12345 mode4--> ai_cmd_v2.out --UART mode3--> G474
                                      ^
                                      | UDP 127.0.0.1:(50000+機体番号) 128B feedback
                                 robot_feedback.out <--UART-- G474

sim : crane --UDP:12345 mode4--> cm4_sim.out --UDP:12346 mode3--> simulator-cli
                                      ^                                 |
                                      +---- UDP 127.0.0.1:(50100+id) ---+
```

`simulator-cli`（framework）は **G474 とロボット物理**を担当し、位置制御は行わない。
CM4 の位置制御は `cm4_sim.out` が担当し、**実機と同一のソース**
（`cm4/control/position_controller.cpp`）をリンクする。コピーを作らないことが、
実機とシミュレータの挙動が一致することの唯一の保証である。

### 位置制御ゲインは crane から実行中に変えられる

ゲインの正本は CM4 の `position_controller` だが、現地で詰めるために crane が
**UDP 12350 へ 28 バイトの設定パケットを broadcast** して稼働中に上書きできる。
再起動は要らない。`ai_cmd_v2.out` と `cm4_sim.out` は同じ `config_packet.h` を
通るので、sim で確かめた値は実機でも同じ扱いになる。

変えられるのは `position_gain`(kp) / `integral_gain`(ki) / `derivative_gain`(kd) /
`deceleration` / `position_tolerance` の 5 つだけで、安全停止のタイムアウト・
`vision_age_limit_ms`・`integral_velocity_limit` は遠隔から動かせない。範囲外の値は
クランプせずデータグラムごと捨てる。形式と範囲は
[制御パケット](control_packet.md#位置制御設定パケットudp-12350)を参照。

**後方互換は無い。** 旧フォーマット（20 バイト・version 1）は `WrongSize` で拒否する。
crane と CM4 は同時に配ること。片方だけ古い機体は停止せず既定ゲイン（kp = 2.0）のまま
走り続け、現地では「なんとなく追従が悪い」としか見えない（CM4 のログには拒否理由が出る）。

## 位置制御の PID 化

`position_controller` の制御則を P から **PID** に拡張した。

- **既定値は `ki = kd = 0`**: 従来の P 制御へ縮退する。
- **設定パケット (UDP 12350) の v2 拡張**: 28 バイトに拡張され、稼働中に `kp`, `ki`, `kd`, `deceleration`, `position_tolerance` を変更可能。旧 v1 (20 バイト) は拒否される。
- **状態の所有**: 積分・微分の状態 (`PositionControllerState`) は呼び出し側がロボットごとに所有する（`cm4_sim` で全機の積分が混ざるのを防止）。
- **feedback 更新時のみ計算**: 微積分は零次ホールドの feedback 更新時のみ行い、微分先行形（`-kd * 実測速度`）で目標キックを防止する。
- **アンチワインドアップ**: 速度上限（制動エンベロープ含む）に飽和している間は積分を停止し、I 項の速度上限 (`integral_velocity_limit = 0.5`) でクランプする。
- **状態のリセット**: 安全停止時や非制御時（素通しモード等）は状態をリセットする（`AtTarget` 到達時は定常偏差解消のため積分を保持）。

### `check_counter` の採番者が CM4 に移った

mode 4 を受けて位置制御を回す経路では、**CM4 が `check_counter` を採番する**。

その結果 **G474 の `connected_ai` は crane の生存を意味しなくなる**。CM4 が生きていれば
crane が死んでいても `check_counter` は変化し続けるからである。
crane 断の安全停止は CM4 側で明示的に行う（`--command-timeout-ms`、既定 100 ms）。
判定は `position_controller` の中にあるので実機と `cm4_sim` が必ず同じ判定を通る。

mode 3 の素通し経路（`--passthrough` を含む）では従来どおり crane 由来の値を流すので、
`connected_ai` の意味も従来どおりである。

### UART 送信レート

位置制御経路は `--tx-rate-hz`、**既定 100 Hz**（UART 占有率 7.2%）で送る。

- crane レート追随（旧構成と同じ `check_counter` 変化ゲート）にはできない。crane 断で
  送信そのものが止まり、`connected_ai` タイムアウト（250 ms）まで停止指令が届かない。
- **500 Hz（G474 メインループ相当・占有率 36%）は既定にしていない。** 現行の約 9 倍の
  UART 負荷を ST-Link での `ORE`/`FE`/`NE`/`PE` カウンタ確認なしに投入しないため。
  `--tx-rate-hz 500` で opt-in できる。**実機で確認が取れたら既定を上げること。**

### ゼロ埋め ≠ ゼロ値

2 バイト固定小数（range 32.767）の未設定フィールドは `0.0` ではなく **`-32.767`** として
復号される（encode が `0.0` を `0x7FFF` へ写すため）。実チェーンで crane 役が mode 4 の
`terminal_velocity_x/y` を書き忘れただけで、フィードフォワードが `(-32.767, -32.767)` に
なりロボットが目標と無関係な方向へ場外まで走った。

`position_controller` は `|v| >= 32.0`（と NaN）を「未設定のシグネチャ」として扱う。
終端速度は 0 とみなして P 制御を続け、目標位置・現在位置は `InvalidCommand` で停止する。
テストを書くときは **使わないフィールドも明示的に `0.0` をエンコードして埋めること**。
`bytearray(64)` のままだと全フィールドが `-32.767` になる。

ただし **同じパケットに 2 種類の符号化が同居している**点に注意する。
byte 18..21（`latency_time_ms` / `elapsed_time_ms_since_last_vision`）は素の
`uint16` なので、ゼロ埋めは正しく `0` になる。±range の 2 バイト固定小数だけが
`-32.767` に化ける。

### vision の健全性を CM4 でも見る

crane が見失っている、または vision が古すぎるロボットの `target_global_pos` は
推測値なので、`position_controller` は次の 2 つで停止する。

| 条件 | バイト | `reason` |
|---|---|---|
| `is_vision_available` が 0 | byte 22 bit0 | `VisionUnavailable` |
| `elapsed_time_ms_since_last_vision > 500` | byte 20..21 | `VisionStale` |

実機 G474 は `state_func.c:314` でこの 2 つを含む 4 条件でホイールを止めるので
**実機の挙動は変わらない**。500 ms は調整パラメータではなく実機ファームウェアの
定数なので、CLI オプションを生やしていない。境界（500 は動く / 501 は止まる）まで
実機と揃えてある。

`elapsed_time_ms_since_last_vision` は**無線が劣化すると真っ先に発火する**条件である。
これを見ないと「実機なら停まる状況で CM4 だけが走らせ続ける」ことになる。

ただし `elapsed_time_ms_since_last_vision` には crane 側に fail-open が 2 箇所ある
（例外時に 0 を詰める / uint16 の範囲外）ので、**`VisionUnavailable` が主防壁で
`VisionStale` は補助**である。両方を見ているのはそのため。詳細と、巻き戻りを
あえて補正しない理由は [制御パケット](control_packet.md) を参照。

テストを書くときは **FLAGS に bit0 を立てること**。立て忘れるとロボットは動かない。

### cm4_sim の使い方（ホスト PC 専用）

`cm4_sim.out` は実機では動かさないので `cm4/lancher.py` の起動対象に入れていない。

```bash
# 端末1: simulator-cli（framework、ibis ブランチ）
/home/hans/workspace/framework/build/bin/simulator-cli \
  -g 2020B --realism None --localhost \
  --ibis-port 12396 --ibis-feedback-port-base 50100

# 端末2: cm4_sim
./cm4/bin/cm4_sim.out --robot-ids 0 \
  --in-port 12345 --out-port 12346 --feedback-port-base 50100

# 端末3: crane を sim:=true feedback_sim_mode:=false で起動
```

起動順は **simulator-cli → cm4_sim → crane**。

| 用途 | アドレス:ポート |
|---|---|
| crane からの mode 4 | bind `0.0.0.0:12345` |
| simulator-cli への mode 3 | `127.0.0.1:12346`（`--ibis-port` と揃える） |
| simulator-cli からの feedback | bind `127.0.0.1:50100+id` |
| crane からの設定パケット | bind `0.0.0.0:12350`（`--config-port`） |
| feedback 再配信 | `224.5.20.(100+id):50100+id`（実機と同じ） |

注意点:

- **crane には `feedback_sim_mode:=false` を渡すこと。** 素の `sim:=true` だと
  `crane_robot_receiver` が `127.0.0.1:50100+id` を `SO_REUSEPORT` 付きで bind し、
  cm4_sim と feedback を取り合う。`SO_REUSEPORT` は 4-tuple ハッシュで振り分けるため
  **単一送信元からの feedback は必ずどちらか一方が全量取る**（実測: 200 発中 0 対 200）。
  どちらが当たるかは実行ごとに変わり、再現性がない。
- **feedback 再配信の送出 IF は既定でループバック固定**（`--multicast-if`、既定 `127.0.0.1`）。
  省略して `IP_MULTICAST_IF` を設定しないと OS が既定ルートの IF（開発 PC では Wi-Fi に
  なりうる）を選ぶ。crane 側には multicast が Wi-Fi へ漏れて AP が過負荷になる問題があり、
  対策（PR #1425）の iptables DROP は `224.5.23.0/24`（vision/referee）だけで
  **`224.5.20.0/24`（feedback）は対象外**である。従来この帯域には何も流れていなかったが、
  `cm4_sim` の再配信で実際に流れるようになったので発生元のソケットで閉じ込める。
  実ネットワークへ出したいときだけ `--multicast-if <ip>` で明示する。
  `cm4_sim` はホスト専用なので、実機の `robot_feedback.out` には影響しない。
- 再配信ポートは `--feedback-relay-port-base`（既定 50100）で入力ポートと独立に指定する。
  crane の `crane_robot_receiver` は `robot_id = port - 50100` と直書きしているので、
  再配信先は **50100 固定**である。
- 出力パケットの `VISION_GLOBAL_X/Y` には feedback 由来の実位置を詰める
  （`--vision-echo feedback`、既定）。simulator-cli はコマンドの `vision_global_pos` を
  実位置と 0.5 m 以内（`IBIS_POSITION_MATCH_THRESHOLD`）で照合してチーム判定し、
  外れるとコマンドを**無言で捨てる**ため。パケットは届き続け `check_counter` も進むので、
  「ロボットだけが動かない」という分かりにくい症状になる。

  理由は 2 つある。**どちらか一方だけでも成立する。**
  1. 劣化注入で crane 由来の値が古くなる。`--rx-delay-ms 200` 程度で 3 m/s なら 0.6 m
     ずれ、全コマンドが捨てられる。
  2. 劣化注入が無くても、crane の world model 推定が 0.5 m ずれれば同じことが起きる。

  **実機では crane 由来の値をそのまま流す**（G474 が vision 融合に使うので、CM4 の
  推定値を書き戻すと自己帰還になる）。素通し経路（mode 3）でも同じエコーがかかる。

  framework PR #7 でこの破棄に警告が付いた（`command dropped` で grep できる)。
  **起動直後に数行出て以降止まるのは正常**で、feedback 未受信の間は crane 由来の値を
  そのまま流すブートストラップ期間だからである。**出続ける場合は feedback 経路が
  繋がっていないサイン**なので、`--vision-echo feedback` の動作確認に使える。
- `simulator-cli` のログに `POSITION_TARGET` の警告が出たら mode 変換の失敗である。
  デバッグの第一手掛かりにする。
- ワイヤ上のバイト列を直接見たいときは framework の
  `data/scripts/ibis-packet-tap.py` を経路に挟む。crane → cm4_sim → simulator-cli の
  どの区間で何が化けたかを、こちら側にデバッグ表示を足さずに切り分けられる。
- 劣化注入（`--rx-delay-ms` / `--rx-jitter-ms` / `--rx-loss-rate` / `--seed`）は
  **crane → CM4 の入力側にのみ**適用する。mode 3 素通しでも mode 4 位置制御でも同じように
  かかる。無線経路の劣化に対する振る舞いを見たいときに使う。

### Docker イメージ（`ghcr.io/ibis-ssl/orion-cm4-sim`）

crane の `docker/scenario/docker-compose.yaml` が `cm4-loop` プロファイルで参照する。
定義は `cm4/Dockerfile`、push は `.github/workflows/cm4-sim-docker.yml`（main への
push と `workflow_dispatch`。pull request ではビルドのみで push しない）。

compose 側との契約は 3 つで、これを崩すと一括起動が壊れる。

| 契約 | 実装 |
|---|---|
| `entrypoint: ["tini", "--"]` | runtime ステージで `tini` を入れている |
| `command: cm4_sim ...` | `bin/cm4_sim.out` を **`/usr/local/bin/cm4_sim`** として置く（名前が違う） |
| `network_mode: host` | 前提。独立した netns に置くと 127.0.0.1 上の simulator-cli と crane に届かない |

ビルド定義は `cm4/build.sh` 唯一のままである。Dockerfile は `./build.sh --no-tests` を
呼ぶだけで、g++ の行を持たない。ここに書き直すと実機用の `setup.sh` / `update.sh` と
食い違ったバイナリを配ることになる。

イメージの検証は `simulator-cli` と crane を実際に繋いで行う。ローカルビルドの
バイナリと同じ挙動になることを確認すること。

**初回だけ手作業が要る。** ghcr のパッケージは最初の push で private として作られる。
crane の compose はログイン無しの素の `image:` で pull するので、workflow が緑に
なっても public にするまで `unauthorized` で失敗する。GitHub の Packages 設定で
`orion-cm4-sim` を public にすること。同じ組織の `robot-manager` と
`framework-simulatorcli` は既に public（匿名 pull が通ることを確認済み）。

また `workflow_dispatch` は既定ブランチにファイルが無いと選べないので、
**`:latest` が出るのはこのブランチが main へマージされたあと**である。

#### public 化した直後に 1 回だけやること

public 化は一度きりの操作で、間違えても**誰かが compose で使おうとするまで誰も
気づかない**。そこで確認まで込みで 1 セットにする。

1. `docker logout ghcr.io` してから `docker pull` できること（public 化そのものの確認）
2. 既定 entrypoint のまま起動し、`docker stop` 後に `docker logs` へ行が残ること
3. crane の compose から起動できること

**1 を落としやすい。** 手元は `docker login` 済みなので private のままでも pull が
通り、「public にした」と思い込める。
### シミュレータと実機の差（ゲインを詰めるときの注意）

- シミュレータ側の G474 相当は **125 Hz** で、実機の G474（500 Hz）より粗い
- simulator-cli は `IS_VISION_AVAILABLE` を見ない。CM4 側で止めているので実害は
  無いが、素通し経路では**下流が止めないまま**であることに注意する
- `cm4_sim` の素通し経路は crane 断で `--command-timeout-ms`（既定 100 ms）で止まる。
  実機で同じ状況を止めるのは G474 の `connected_ai` タイムアウト
  （`AI_CMD_TIMEOUT(0.5) * MAIN_LOOP_CYCLE(500)` = **250 ms**）なので、素通し経路の
  停止は実機より速い

### テスト

`./cm4/build.sh` が次を実行する（CI も同じ）。実機 UART も STM32 も不要。

- `robot_packet_layout_test.out` — crane 版正本からのパケットレイアウトのドリフト検出
- `test_position_controller.out` — 位置制御則の単体テスト（crane 版テストからの移植を含む）
- `test_cm4_sim.py` — cm4_sim の結合スモークテスト
- `test_forward_ai_cmd_v2.py` — 実機ブリッジの結合スモークテスト（`--debug` + pty）。
  1 件だけ `--debug` なしの実運用モードで動かし、位置制御の状態表示が実際に出ることを検査する

### 未了（実機で確認すること）

- `--passthrough` で旧構成と同じ挙動になること（ホストでは 72 バイトのバイト一致を確認済み）
- crane を mode 4 送出に切り替えて `ai_cmd_v2.out` の表示に `mode 4` と `tarPos` が出ること
- `--tx-rate-hz 500` での UART 占有率。G474 の `uart ORE/FE/NE/PE` と parser timeout
  カウンタが増えないことを ST-Link で確認する
- **安全停止時の惰走距離**。G474 は `stop_emergency` で `omniStopAll()`（駆動力ゼロ）
  に入るので、実機も惰走する。CAN フレームにブレーキフラグが無く、duty 0 が空転か
  短絡制動かはモータボード側のファームウェア次第で、このリポジトリからは確定
  できない（`doc/control_packet.md` の「104 ms は『駆動力が切れるまで』」）。
  **測るときはロボットが壁や他機に当たらない向きで、開始速度が実際に出ている
  ことを確かめること。** 塞がれた向きでも「それらしい」値が出るので、読みからは
  異常と分からない
- crane を止めて車輪が止まるまでの時間。予算は `--command-timeout-ms`(100) +
  ポーリング 1 ms + UART 0.72 ms + G474 メインループ 2 ms = **約 104 ms**
  （`doc/control_packet.md` の「crane 断から車輪が止まるまでの時間」）

### ビルドの構成

- `position_controller.cpp` は `.o` を 1 つ作って 3 箇所でリンクする。同一ソースで
  あることが実機と sim の挙動一致の保証なので、オブジェクトも 1 つにするのが素直
- 5 本の `g++` は互いに独立なので並列に投げる
- `--targets=sim` は `cm4_sim.out` に必要なものだけをビルドする。`cm4/Dockerfile`
  がこれを使う（イメージに入るのは `cm4_sim.out` 1 本だけ）。boost も不要になった
- テストの起動待ちは固定 `sleep` ではなく、`cm4_sim` は最初の出力データグラムを、
  `ai_cmd_v2` は起動バナー最終行を待つ
