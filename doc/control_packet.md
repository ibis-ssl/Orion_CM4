# 制御パケット

このドキュメントは、AI(crane) から CM4 を経由して STM32(G474) へ送る制御パケットの責務とレイアウトをまとめます。

## SSOT（この仕様の正本）

`RobotCommandSerializedV2`（64 バイト）のレイアウトの正本は **crane 側**の
`crane/crane_sender/include/crane_sender/robot_packet.h` です。
次の 4 者が一致していなければなりません。

| リポジトリ | ファイル | 状態 |
|---|---|---|
| crane | `crane_sender/include/crane_sender/robot_packet.h` | **正本** |
| G474_Orion_main | `Core/Inc/robot_packet.h` | byte 0..31 一致（32..37 は G474 が使わないので未定義） |
| framework | `src/simulator/ibis_protocol.h` | 一致 |
| Orion_CM4 | `cm4/bridge/robot_packet.h` | 一致（2026-09 に旧レイアウトから統一） |

過去に 2 度ドリフトしているため、`cm4/bridge/robot_packet_layout_test.cpp` が
byte 0..37 の全オフセット・`ControlMode`・`FlagAddress` を `static_assert` で固定し、
さらにゴールデンベクタで量子化挙動（丸めずに切り捨てる）まで検査します。
`cm4/build.sh` と CI から実行されます。**`robot_packet.h` を編集したら必ず通すこと。**

## 対象ファイル

- `cm4/bridge/robot_packet.h`
  - 64 バイトの `RobotCommandSerializedV2` と、シリアライズ / デシリアライズ処理を定義します。
- `cm4/bridge/robot_packet_layout_test.cpp`
  - 上記のレイアウトが正本からドリフトしていないことを検査します。
- `cm4/bridge/forward_ai_cmd_v2.cpp`
  - AI から受け取った制御パケットとローカルカメラ情報をまとめ、UART で STM32 へ送ります。
    mode 4 を受けたときは位置制御ループを閉じて mode 3 へ変換します。
- `cm4/control/position_controller.h` / `.cpp`
  - 位置指令から速度指令を作る制御則と、通信途絶時の安全停止判定です。
    transport 非依存で、実機ブリッジと `cm4_sim` が**同一ソースとして**リンクします。
- `cm4/bridge/cm4_sim.cpp`
  - シミュレータ用の CM4 相当プロセスです。ホスト PC 専用で、実機では動かしません。
- `host/lib/cm4_control_client.py`
  - CM4 の制御 API サーバーへ `start` / `stop` / `status` を送るホスト側クライアントです。
- `host/apps/host_lancher.py`
  - `host/lib/cm4_control_client.py` を利用するホスト側 GUI です。
- `cm4/lancher.py`
  - CM4 側で制御関連プロセスを起動・停止する Web API サーバーです。

## 制御プロセス

`cm4/lancher.py` の `/start` は次のプロセスを起動します。

- `ai_cmd_v2.out`
- `robot_feedback.out`
- `cm4/camera/dist/cam_server_v3`

`/stop` は上記の関連プロセスを `pkill -f` で停止します。

`cm4_sim.out` は**ホスト PC 専用**です。実機では起動しないので `cm4/lancher.py` の
起動プロセス一覧と `/stop` の `pkill -f` パターンには入れていません。

## パケット全体（715 バイト）

crane は 11 台分を **1 データグラム 715 バイト**にまとめて UDP ポート `12345` へ送ります。

```text
1 スロット = robot_id 1 バイト + RobotCommandSerializedV2 64 バイト = 65 バイト
715 バイト = 65 バイト x 11 スロット（固定）
```

- スロット `i`（0..10）の先頭バイトは **スロット添字そのもの**が入ります。
- crane がコマンドを持たないロボットのスロットは **コマンド 64 バイトがすべてゼロ**になります。
  受信側は「64 バイトが全ゼロ」または「`robot_id` が範囲外」で明示的にスキップしてください。
  `control_mode = 0` は現在の `ControlMode` に存在しない値なので、無効スロットの印として使えます。
- **使用中スロットの byte 28..31 と 38..63 はゼロとは限りません。**
  crane の `ibis_sender_node.cpp` は `RobotCommandSerializedV2` を `{}` なしで宣言しており、
  シリアライズが書かない領域にはスタックの残骸が乗ります。
  受信側は「予約領域＝0」を前提にしないでください。

`cm4/bridge/forward_ai_cmd_v2.cpp` は自機の `robot_id` と一致するスロットだけを取り出します。
`robot_id` は `wlan0` の IPv4 最終オクテットから `-100` して求めます
（`get_machine_id()`。`wlan0` が無い環境では 0 になります）。

## RobotCommandSerializedV2

`cm4/bridge/robot_packet.h` の `RobotCommandSerializedV2` は 64 バイト固定長です。
実際に使用しているのは byte 0..37 で、38..63 は未使用です。

### バイトオフセット

| offset | 名前 | 符号化 |
|---|---|---|
| `0` | `HEADER` | 生値（crane は `0x00`。CM4 が UART へ出す直前に `254` で上書きする） |
| `1` | `CHECK_COUNTER` | 生値（後述） |
| `2..3` | `VISION_GLOBAL_X` | float, range `32.767` |
| `4..5` | `VISION_GLOBAL_Y` | float, range `32.767` |
| `6..7` | `VISION_GLOBAL_THETA` | float, range `M_PI` |
| `8..9` | `TARGET_GLOBAL_THETA` | float, range `M_PI` |
| `10` | `KICK_POWER` | `uint8 = value * 20` |
| `11` | `DRIBBLE_POWER` | `uint8 = value * 20` |
| `12..13` | `ACCELERATION_LIMIT` | float, range `32.767` |
| `14..15` | `LINEAR_VELOCITY_LIMIT` | float, range `32.767` |
| `16..17` | `ANGULAR_VELOCITY_LIMIT` | float, range `32.767` |
| `18..19` | `LATENCY_TIME_MS` | **uint16 生値**（float 変換なし） |
| `20..21` | `ELAPSED_TIME_MS_SINCE_LAST_VISION` | **uint16 生値** |
| `22` | `FLAGS` | 後述 |
| `23` | `CONTROL_MODE` | 3 または 4 |
| `24..31` | `CONTROL_MODE_ARGS` | **union**（`CONTROL_MODE` により意味が変わる） |
| `32..33` | `TARGET_GLOBAL_POS_X` | float, range `32.767` |
| `34..35` | `TARGET_GLOBAL_POS_Y` | float, range `32.767` |
| `36..37` | `TERMINAL_VELOCITY` | float, range `32.767` |

> **旧レイアウトとの違い**: 2026-09 以前の Orion_CM4 は `ACCELERATION_LIMIT` を持たず
> `SPEED_LIMIT`(12..13) / `OMEGA_LIMIT`(14..15) だったため、**byte 12 以降が 2 バイトずれて**
> いました。`FLAGS` は 20、`CONTROL_MODE` は 21 でした。
> `forward_ai_cmd_v2.cpp` が受信バイト列を `memcpy` で素通しするだけの
> バイト転送器だったため顕在化していませんでしたが、デバッグ表示は誤った値を出していました。

### FLAGS

- bit `0`: `IS_VISION_AVAILABLE`
- bit `1`: `ENABLE_CHIP`
- bit `2`: 未使用（旧 `LIFT_DRIBBLER` の跡地。常に 0）
- bit `3`: `STOP_EMERGENCY`
- bit `4..7`: 未使用（旧 `PRIORITIZE_MOVE` / `PRIORITIZE_ACCURATE_ACCELERATION` の跡地。常に 0）

### スケーリング

- 位置と速度の多くは `convertFloatToTwoByte(value, 32.767)` で 2 バイト化します。
  量子化幅は `2 * 32.767 / 65534` ≒ **1 mm / 1 mm/s** です。
- 角度は `convertFloatToTwoByte(value, M_PI)` で 2 バイト化します。量子化幅は約 `0.0001 rad`。
- `kick_power` と `dribble_power` は `value * 20` を 1 バイトに入れます。
- `latency_time_ms` と `elapsed_time_ms_since_last_vision` は `uint16_t` を上位 / 下位バイトに分けます。
- **丸めは行いません。** `(uint16_t)(32767.f * (val / range) + 32767.f)` の切り捨てです。
  `roundf()` を足すと crane と 1 LSB ずれます（レイアウト検査テストが検出します）。
- 範囲外の値はクランプされます。crane は `std::cout` へ警告を出しますが、CM4 は制御ループ内で
  毎周期呼ぶため出力が溢れます。CM4 はクランプ回数を `robotPacketClampCount()` で数え、
  デバッグ表示にまとめて出します。

## 制御モード

`CONTROL_MODE`(byte 23) は次の値です。

| 値 | 名前 | ARGS(24..31) の意味 | 送信元 → 受信先 |
|---|---|---|---|
| `3` | `POLAR_VELOCITY_TARGET_MODE` | `target_global_velocity_r`, `target_global_velocity_theta` | CM4 → G474 / cm4_sim → simulator-cli |
| `4` | `POSITION_TARGET_WITH_TERMINAL_VELOCITY_MODE` | `terminal_velocity_x`, `terminal_velocity_y` | crane → CM4 / crane → cm4_sim |

> **`CONTROL_MODE_ARGS` は union です。`CONTROL_MODE` を見ずに復号してはいけません。**
> mode 4 のパケットを mode 3 として復号すると `terminal_velocity_x/y` が `r/theta` として
> 読まれ、無言で暴走します。

**G474 は mode 3 しか実装していません。** mode 4 は CM4 が消費して mode 3 に変換するものであり、
G474 へ素通ししてはいけません。

### POLAR_VELOCITY_TARGET_MODE (3)

- `24..25`: `target_global_velocity_r`（range 32.767）
- `26..27`: `target_global_velocity_theta`（range 32.767、**グローバル方向のラジアン**）
- `28..31`: 未使用

### POSITION_TARGET_WITH_TERMINAL_VELOCITY_MODE (4)

- `24..25`: `terminal_velocity_x`（range 32.767）
- `26..27`: `terminal_velocity_y`（range 32.767）
- `28..31`: 未使用

目標位置そのものは mode_args ではなく **固定フィールド** `TARGET_GLOBAL_POS_X/Y`(32..35) に、
到達時の速度上限（スカラー）は `TERMINAL_VELOCITY`(36..37) に入ります。

### `LINEAR_VELOCITY_LIMIT = 0` の扱い

このフィールドの `0` は **上流と下流で意味が違います**。

- crane の位置制御則（と CM4 の `position_controller`）は `clampNorm` の仕様上
  `0` を **「停止」** として扱います。
- framework の `ibis_protocol.h` は同じフィールドを `0 means "no limit"` と定義し、
  simulator-cli は `> 0` のときだけ制限を適用します。

CM4 では**上流（位置制御器）の解釈が先に勝ちます**。`linear_velocity_limit = 0` なら
位置制御器が `r = 0` を出すので、そのバイトを下流へ素通ししても結果は変わりません。
これは事故ではなく決定であり、`position_controller` の単体テストで固定しています。

## 位置制御設定パケット（UDP 12350）

位置制御ゲインの正本は CM4 の `position_controller` ですが、現地で詰めるには
crane 側から変えられる必要があります。715 バイトの指令パケットとは**別ポートの
20 バイトのデータグラム**で運びます。相乗りさせないのは、64 バイトのレイアウトが
crane / G474 / framework / CM4 の 4 者一致を不変条件にしており、しかも crane が
使用スロットの byte 28..31 / 38..63 をゼロ初期化していないためです。別ポートなら
G474 と framework は一切変わりません。

正本は `cm4/bridge/config_packet.h` です。crane は指令と同じく **broadcast** で送ります。

| byte | 内容 |
|---|---|
| 0..3 | magic `'O' 'C' '4' 'C'` |
| 4 | version（現在 `1`） |
| 5 | robot_id（`0xFF` = 全機宛） |
| 6..7 | 予約（0） |
| 8..11 | `position_gain` float32 little endian |
| 12..15 | `deceleration` float32 little endian |
| 16..19 | `position_tolerance` float32 little endian |

2 バイト固定小数ではなく素の float32 です。毎周期 11 台ぶんを運ぶわけではないので
圧縮する理由が無く、量子化を挟むと crane の表示値と CM4 の実効値が食い違います。

受信側は次の範囲を検査し、外れていれば**クランプせずデータグラムごと捨てて**
拒否理由をログに出します。黙ってクランプすると crane 側の表示と実機の実効値が
食い違ったまま気付けません。

| フィールド | 範囲 |
|---|---|
| `position_gain` | `0 <= v <= 20` |
| `deceleration` | `0 <= v <= 20` [m/s²] |
| `position_tolerance` | `0 <= v <= 1.0` [m] |

- 受信ポートは実機 `ai_cmd_v2.out` もシミュレータ `cm4_sim.out` も `--config-port`（既定 12350）。
  受信・検証・適用・ログは同じ `config_packet.h` を通るので、sim で確かめた値は実機でも同じ扱いになります。
- 設定が途絶しても最後の値を保持します。ゲインは安全信号ではないので、
  届かないことを理由に既定値へ戻すとかえって挙動が飛びます。
- crane は同じ値を定期送信して構いません。値が変わったときだけログに出ます。
- **変更できるのはこの 3 つだけです。** `command_timeout_ms` / `feedback_timeout_ms` は
  安全停止の閾値、`vision_age_limit_ms` は G474 の定数と一致させるための値なので、
  遠隔から動かせるようにしていません（`cm4/control/position_controller.h`）。
- `cm4_sim` は 11 台を 1 プロセスで代行するので、設定はプロセス全体へ適用されます。
  台ごとに別ゲインを試すときは `--robot-ids` と `--config-port` を分けて起動します。

## cm4/bridge/forward_ai_cmd_v2.cpp

`cm4/bridge/forward_ai_cmd_v2.cpp` は AI 側 UDP とローカルカメラ UDP を受け、STM32 へ UART 送信します。

### 入力

- AI 制御パケット
  - UDP port: `12345`（`--ai-cmd-port` で変更可。ホスト PC でのテスト用）
  - 715 バイト固定（`(64 + 1) * 11`）。これ以外の長さは捨てます。
    `recv()` には `MSG_TRUNC` を付けてデータグラムの実長を得ます。付けないと
    716 バイトが 715 バイトに切り詰められ「正常な全ゼロパケット」に化けます。
  - コマンド 64 バイトが全ゼロのスロットは指令とみなしません
    （framework の `ibisSlotIsEmpty()` と同じ判定）。
- ローカルカメラパケット
  - UDP port: `8890`（`--local-cam-port` で変更可）
  - `CAM_BUF_SIZE` は `7` バイトです。
- 位置制御設定パケット
  - UDP port: `12350`（`--config-port` で変更可）
  - 20 バイト固定。crane がゲインを稼働中に変更するために送ります。
    詳細は上の[位置制御設定パケット](#位置制御設定パケットudp-12350)を参照。
- G474 feedback（位置制御ループを閉じるため）
  - UDP port: `127.0.0.1:(50000 + 100 + ロボット ID)`（`--feedback-port` で変更可）
  - `robot_feedback.out` が UART から読んだ 128 バイトを loopback unicast で渡します。
  - 詳細は [フィードバックパケット](feedback_packet.md) を参照。

ロボット ID は `wlan0` の IPv4 最終オクテット `- 100` です。決定できないときは
**0 号機として動かず終了します**。位置制御では ID が feedback の bind ポートも決めるので、
黙って 0 に落ちると「自分の G474 へ送りながら 0 号機の feedback で位置ループを閉じる」
機体跨ぎの制御になります。テスト時は `--robot-id` で明示指定できます。

### UART 送信

- UART port: `/dev/serial0`（CM4_108ではPL011の`ttyAMA0`）。`--serial-port` で変更可。
- 既定 baudrate: `1000000`
- `-s` で baudrate を変更できます。
- 送信サイズは `AI_CMD_V2_SIZE + CAM_BUF_SIZE + 1`、つまり `72` バイトです。
- UART 送信バッファの先頭は `254` に上書きします。
- 末尾 1 バイトはチェックサムです（byte 0..70 の総和 & 0xFF）。

72 バイト x 10 bit / 1 Mbps = **720 us/パケット**です。送信レートを上げると UART 占有率が
そのまま上がるので注意してください（500 Hz で 36%、1 kHz で 72%）。

### 2 つの経路

受信パケットの `CONTROL_MODE` で経路が分かれます。**この 2 つは意図的に統合していません。**

| 受信 mode | 動作 | 送信ゲート |
|---|---|---|
| `3`、または `--passthrough` 指定時 | 64 バイトをそのまま転送（旧構成） | crane の `CHECK_COUNTER` が変化したとき |
| `4` | 位置制御ループを閉じて mode 3 を生成 | `--tx-rate-hz`（既定 100 Hz）の時間ゲート |

素通し経路は `check_counter` が crane 由来なので、既存の「変化したときだけ送る」ゲートが
**そのまま正しい**です（crane 断で G474 の `connected_ai` が false になるのが旧構成の
期待挙動）。位置制御経路は CM4 が `check_counter` を採番するのでそのゲートが成立せず
（常に変化してしまう）、時間ベースのレートで送ります。

`--passthrough` は mode 4 が来ても強制的に素通しします。旧構成の挙動を再現したいとき
（位置制御を疑う前に切り分けたいとき）に使います。

### 送信レートとポーリング

メインループは `usleep(1000)` の **1 kHz ポーリング**のままです。UDP は非ブロッキングで読み、
受信が無ければ前回のバッファがそのまま残ります。

位置制御経路の UART 送信レートは `--tx-rate-hz`、**既定 100 Hz** です。

- crane レート追随（旧構成と同じゲート）にはできません。crane 断のときに送信そのものが
  止まり、G474 の `connected_ai` タイムアウト（250 ms）まで停止指令が届かず、
  「crane 断から 100 ms 以内に止まる」を満たせないためです。
- 500 Hz（G474 メインループ相当・UART 占有率 36%）も既定にしていません。現行の約 9 倍の
  UART 負荷を、ST-Link での `ORE`/`FE`/`NE`/`PE` カウンタ確認なしに投入しないためです。
- 100 Hz は 720 us x 100 = **7.2%** で現行（約 55 Hz = 約 4%）の約 2 倍にとどまり、
  crane 断から 10 ms 以内に停止指令を届けられます。
- 実機で ST-Link 確認が取れたら `--tx-rate-hz 500` を既定に上げてください。

### CHECK_COUNTER

crane は `RobotCommands` メッセージ 1 通につき 1 回インクリメントし、その値を
**全ロボット共通**で入れます（`0 → 201` で折り返すので実質 1..200 の巡回）。

G474 の `checkConnect2AI()`（`Core/Src/ai_comm.c`）は
**`check_counter` が変化し続けること**を AI 接続生存の判定に使います。
`AI_CMD_TIMEOUT(0.5) * MAIN_LOOP_CYCLE(500)` = **250 ms** 変化が無いと `connected_ai = false` です。

1 バイトなので値は周期的に一巡します。**ロス検出用のシーケンス番号としては使えません。**

#### 新構成では採番者が CM4 に移ります

mode 4 を受けて位置制御を回す経路では、**CM4 が `check_counter` を採番します**
（送信ごとに `++c; if (c > 200) c = 0;`）。

結果として **G474 の `connected_ai` は crane の生存を意味しなくなります**。
CM4 が生きていれば crane が死んでいても `check_counter` は変化し続けるからです。
crane 断の安全停止は CM4 側で明示的に行います（下記）。

素通し経路では従来どおり crane 由来の値をそのまま流すので、`connected_ai` の意味も
従来どおりです。

### 位置制御と安全停止

mode 4 を受けると `cm4/control/position_controller.cpp` を通します。制御則は crane の
`sim_position_controller.cpp` の `calculateSimGlobalVelocity()` と同一で、既定ゲインは
`position_gain = 2.0` / `deceleration = 3.0`（`--kp` / `--decel` は起動時の初期値。
稼働中は crane からの設定パケットで上書きされます）です。

ロボットの現在位置は **G474 feedback の byte 44..51（`vision_based_position_x/y`）** を
使います。crane のパケットに入っている `vision_global_pos` では閉じません。
それは今回ループの外へ出そうとしている無線経路そのものだからです。

`position_tolerance` は 715 バイトパケットに載らないので CM4 側の設定値です
（既定 0.01 m、`--tolerance`）。

次のいずれかで速度指令をゼロにし、`STOP_EMERGENCY`(byte 22 bit3) を立てます。

| 条件 | `reason` | 既定 |
|---|---|---|
| crane が `STOP_EMERGENCY` を立てた | `StopEmergency` | — |
| crane からのパケットが途絶 | `CommandStale` | `--command-timeout-ms 100` |
| G474 feedback が途絶（起動直後の未受信を含む） | `FeedbackStale` | `--feedback-timeout-ms 100` |
| crane が vision でこのロボットを見失っている | `VisionUnavailable` | — |
| crane の vision がこのロボットを捉えてから時間が経ちすぎた | `VisionStale` | 500 ms（実機 FW 固定） |
| 目標位置・現在位置が物理的にありえない値 | `InvalidCommand` | — |

判定はこの表の順で、先に成立したものが理由になります。

安全停止時は `KICK_POWER` / `DRIBBLE_POWER` / `ENABLE_CHIP` も落とします。
古いキック指令を撃ち続けないためです。

#### フィードフォワードの上限

mode 4 の `terminal_velocity_x/y` はフィードフォワードとして速度に直接足されます。
未設定シグネチャ（`|v| >= 32`）は 0 とみなして P 制御を続けますが、
「もっともらしいが間違っている」終端速度は検査では見分けられません。

その場合でも **出力の大きさは `linear_velocity_limit`（byte 14..15）で頭打ち**になります。
制御則の最後に `clampNorm(v, min(linear_velocity_limit, 制動エンベロープ))` が入っており、
`linear_velocity_limit` 自体が未設定なら `max(0, -32.767) = 0` となって停止側に倒れるためです。
`test_position_controller.cpp` の `testFeedforwardCannotExceedVelocityLimit` で固定しています。

#### vision の健全性 (byte 22 bit0 と byte 20..21) を CM4 でも見ます

crane が見失っている間の `target_global_pos` は「見えていないロボット」に対する
推測値なので、そこへ向かって走らせてはいけません。

実機 G474 の停止条件は `Core/Src/state_func.c:314` の 4 つです。

```c
sys->stop_flag || ai_cmd->stop_emergency || !ai_cmd->is_vision_available
  || ai_cmd->elapsed_time_ms_since_last_vision > 500
```

このうち **`is_vision_available`（byte 22 bit0）と
`elapsed_time_ms_since_last_vision`（byte 20..21）の 2 つ**を CM4 でも見ます。
どちらも実機 G474 が同条件で止めるので、**実機の挙動はこれまでと変わりません**。

`vision_age_limit_ms`（500 ms）に CLI オプションを生やしていないのは意図的です。
これは調整パラメータではなく実機ファームウェアの定数と一致させるための値で、
現地で食い違った値を設定できると「CM4 は走らせているのに G474 は止めている」
状態を作れてしまいます。境界（500 は動く / 501 は止まる）まで実機と揃えてあります。

判定は `position_controller` にあるので、実機バイナリと `cm4_sim` が同じ経路を通ります。
なお `elapsed_time_ms_since_last_vision` は**無線が劣化すると真っ先に発火する**条件です。
「位置制御が効いていない」ように見えたときは、まずここを疑ってください。

##### `VisionUnavailable` が主防壁、`VisionStale` は補助

`elapsed_time_ms_since_last_vision` は crane 側に **fail-open が 2 箇所**あるので、
単独では信用できません。

1. **例外時に 0 を詰める** — `crane_sender/src/sender_base.cpp` の
   `catch (...)` が `elapsed_time_ms_since_last_vision = 0`（完全に新鮮）にします。
   world model からロボットを引けない状況、つまり**まさに vision を見失っている
   状況**で「新鮮」と主張することになり、安全側と逆です。
2. **uint16 の範囲外** — `crane_msgs/msg/control/RobotCommand.msg:36` は `uint16` で、
   代入元は `elapsed.nanoseconds() / 1e6`（`double`）です。65535 ms を超えると
   **範囲外の浮動小数から符号なし整数への変換**になり、これは未定義動作です。
   整数同士の変換と違って剰余セマンティクスは保証されないので、「上位ビットが
   落ちて巻き戻る」とは限りません。

   実測（g++ 13.3.0 / x86_64 / -O2、framework セッション計測）:

   | 経過時間 | 結果 |
   |---|---|
   | 70000 ms | 定数畳み込み `65535` / 実行時変換 `4464`（**同一バイナリ内で不一致**） |
   | 65536 ms | `0` = **「たった今 vision を検出した」** |

   fail-open の中でも最悪の値になります。正しい直し方は crane 側で代入前に
   飽和させること（`std::clamp`、負値も UB なので下限も要る）で、**3 リポジトリの
   中で crane の送信側が唯一の修正箇所**です。

どちらも同じ状況で `is_vision_available` が false になるので実運用では救われます。
したがって **`VisionUnavailable` が主防壁で、`VisionStale` は補助**という位置づけです。
両方を見ているのはそのためで、片方だけでは足りません。

CM4 側で巻き戻りを補正することは**しません**。実機 G474 と同じ 2 バイトを同じ
`uint16_t` として読んでいるので、実機と同じ判定になることのほうが重要です。
ここだけ賢くすると、実機と CM4 で挙動が分かれます（simulator-cli 側も同じ方針）。

#### crane 断から車輪が止まるまでの時間

`--command-timeout-ms`（既定 100 ms）に、下記が加算されます。

| 要素 | 時間 |
|---|---|
| 1 kHz ポーリングの検出遅れ | 最大 1 ms |
| UART 72 バイト @ 1 Mbps | 0.72 ms |
| G474 のメインループ 500 Hz | 最大 2 ms |

**合計でおよそ 104 ms** です。停止指令は `--tx-rate-hz` のゲートを待ちません
（停止理由が変わった周期はレートに関わらず即送信します）。実機で測るときは
「100 ms ちょうど」ではなくこの予算と照合してください。

この「`--tx-rate-hz` で動かない」は
`test_forward_ai_cmd_v2.py::test_stop_latency_does_not_depend_on_tx_rate` が
検査しています。**期待される「動かなさ」を検査にしておかないと、レートを上げ下げ
したときに停止レイテンシが一緒に動いても誰も気づきません。**

##### 104 ms は「駆動力が切れるまで」で、「止まるまで」ではありません

**安全停止では実機も惰走します。** G474 の停止分岐（`Core/Src/state_func.c:314`）は
`omniStopAll()` を呼び、4 輪のモータ電圧を 0 にして CAN へ duty `0.0` を送るだけです。

```c
} else if (sys->stop_flag || ai_cmd->stop_emergency ||
           !ai_cmd->is_vision_available || ai_cmd->elapsed_time_ms_since_last_vision > 500) {
  omniStopAll(output);        // motor_voltage[0..3] = 0 → CAN へ duty 0.0
} else {
  omniMoveIndiv(output, OMNI_OUTPUT_VOLTAGE_LIMIT);   // 車輪 PID が効く
}
```

CAN フレーム（`Core/Src/actuator.c:12`）は 4 バイトの float duty だけで、
**ブレーキフラグを持ちません**。duty 0 が空転か短絡制動かはモータボード側の
ファームウェアが決めるので、このリポジトリからは確定できません（実機で測る項目）。

重要なのは**同じ「止まれ」でも 2 通りある**ことです。

| CM4 が送るもの | G474 が通る経路 | 挙動 |
|---|---|---|
| mode 3 で `r = 0`、`STOP_EMERGENCY` **なし** | `speedControl` → `omniMoveIndiv` | 車輪 PID が効く（能動制動） |
| `STOP_EMERGENCY` **あり** | `omniStopAll` | 駆動力ゼロ（惰走） |

`applySafetyStop()` は `STOP_EMERGENCY` を立てるので、**crane 断・feedback 断・
vision 断の安全停止はすべて下段（惰走）**です。約 104 ms で駆動力が切れ、そこから
慣性で転がります。

シミュレータの `SimRobot` も最後の指令から一定時間で standby に入り、車輪 PID に
到達する手前で return するので、**この 2 経路の作り分けは実機とシミュレータで
一致しています**（framework PR #10 以降）。惰走距離は能動制動の数倍になります。

実機の惰走距離は測っていません。duty 0 が空転か短絡制動かはモータボード側の
ファームウェアが決めるので、**シミュレータの値は比較対象であって実機の予測値では
ありません**。

> 停止距離をシミュレータで測るときは、ロボットが壁や他機に当たらない向きで、
> 開始速度が実際に出ていることを確かめること。塞がれた向きでも「それらしい」値が
> 出るので、読みからは異常と分からない。手順は framework の
> `data/scripts/ibis-stop-distance.py` にある。

##### 忠実度ギャップ: 指令途絶時はシミュレータの方が早く止まる

シミュレータが standby に入るまでの猶予は、実機の `connected_ai` タイムアウトより
短い。したがって**指令途絶時の惰走距離はシミュレータの方が実機より短く出る**。

ただしこれが効くのは **CM4 ごと落ちて G474 への送信が止まった場合だけ**である。
新構成の通常運用では CM4 が送り続けるので `connected_ai` は発火せず、crane 断・
feedback 断・vision 断はすべて `STOP_EMERGENCY` 経路（上表の 2 行目）に入る。

なお **mode 4 がシミュレータまで届いた場合の停止だけは能動制動のまま**残されています。
この分岐は実機に対応物が無く、CM4 が mode 変換に失敗しているという合図なので、
惰走させると発見が遅れるという判断です。`POSITION_TARGET` の警告と同じ「構成ミスの
第一手掛かり」の位置づけです。

実機で「crane を止めて車輪が止まるまで」を測るときは、**駆動力が切れる時刻
（約 104 ms、予算と照合する対象）と、機体が静止する時刻（惰走距離ぶん後ろ）**を
分けて記録してください。

判定は `position_controller` の中にあるので、**実機バイナリと `cm4_sim` が必ず同じ判定を
通ります**。

##### この 104 ms と G474 の 250 ms は「二段構え」ではありません

新構成で crane が沈黙しても、**CM4 は `check_counter` を進めながら送信を続けます**
（`forward_ai_cmd_v2.cpp` の位置制御パスは毎送信で `nextCheckCounter()` を呼び、
停止中も `--tx-rate-hz` で送り続ける）。したがって G474 の `connected_ai` は真のまま
であり、車輪が止まる理由は **CM4 が立てた `STOP_EMERGENCY`** です。250 ms の
`connected_ai` タイムアウトはこの経路には出てきません。

新構成での 250 ms の役割は変わり、**CM4 側（`ai_cmd_v2.out` のプロセス死、UART 断）
に対する最後の砦**になります。このときだけ `check_counter` が凍り、G474 が自力で
止めます。

| 何が落ちたか | 止めるのは誰か | 時間 |
|---|---|---|
| crane（無線断・プロセス死） | CM4 の `STOP_EMERGENCY` | 約 104 ms |
| CM4（`ai_cmd_v2.out` の死、UART 断） | G474 の `connected_ai` | 250 ms |

旧構成（`--passthrough`）では `check_counter` が crane 由来なので、crane 断が
そのまま `connected_ai` の 250 ms に出ます。**同じ 250 ms が構成によって別の障害を
見ている**ので、実機で測るときに取り違えないこと。

出力パケットは受信した 64 バイトをコピーして `CHECK_COUNTER` / `CONTROL_MODE` /
`CONTROL_MODE_ARGS` だけを差し替えて作ります。ゼロから組み立てると
`target_global_theta` / `angular_velocity_limit` / `kick_power` / `dribble_power` / flags を
取りこぼします（G474 も simulator-cli もこれらをすべて使います）。

なお実機では `VISION_GLOBAL_X/Y`(2..5) は crane 由来のまま流します。G474 が vision 融合に
使うので、CM4 の推定値を書き戻すと自己帰還になります。`cm4_sim` だけは simulator-cli の
0.5 m 照合ゲートを通すために feedback 由来の実位置で上書きします。

### G474 無音からの復帰

**症状**: G474 の**送信 (フィードバック) だけが止まる**。CM4 側の UART 受信カウンタ
(`/proc/tty/driver/serial` の `rx`) が正確に 0 になるのに、CM4 から G474 への送信 (`tx`) は
続いており、G474 は受信もデバッグ出力もできている (2026-09-19 の実機で 2 回観測、うち 1 回は上記の
UART カウンタで直接確認。どちらも直前のリセットから 5〜9 分後)。CM4 のプロセスの問題ではない。

**なぜ復帰しないか**: G474 は「CM4 からの通信が 500 ms 途絶えたまま、さらに 6 秒続くと、一度でも
AI 接続済みなら `NVIC_SystemReset()` で自己リセットする」
(`G474_Orion_main` の `ai_comm.c` `commStateCheck`、500 Hz タイマー割り込みで 3000 カウント)。
crane が指令を送り続ける試合中は CM4 も UART へ送り続けるので、この自己リセットが起きず、
TX が死んだ G474 はその試合中ずっと復帰しない (mode 4 では `FeedbackStale` で停止したまま)。

**緩和**: feedback が `--g474-silence-ms` (既定 2000) 無音なら、`ai_cmd_v2.out` が **UART 送信を止める**。
これで G474 の自己リセットに復帰を委ねる。G474 が再び話し出したら (起動直後の feedback が届いたら)
即再開し、`--g474-recovery-ms` (既定 10000) を過ぎても話さなければ一度再開して様子を見る
(再び無音なら繰り返す)。無音の検知 2 秒 + 500 ms + 6 秒 + 起動 1.7 秒 ≒ 10 秒、ロボットは動けなくなる代わりに戻ってくる。

| 項目 | 内容 |
|---|---|
| 対象 | mode 3 (素通し) と mode 4 (位置制御) の両方 |
| 無効化 | `--g474-silence-ms 0` (従来動作) |
| 即リセット指令 | `--g474-reset-cmd 1` (既定 0)。下記「FWUP リセット指令」 |
| ログ | `G474 feedback 無音 N ms: UART 送信を停止…` / `G474 feedback 復帰…` / `…復帰待ちが N ms で時間切れ…` |
| テスト | `test_forward_ai_cmd_v2.py` (`test_suspends_uart_tx_*`、`test_g474_*`) |

**FWUP リセット指令 (`--g474-reset-cmd 1`、既定は無効)**: 無音を検知した時点で、G474 へ
`0xFE 'F' 'W' 'U' 'P' 0x03 0…0 <checksum>` (72 バイト) を **1 回だけ**送る。G474 (`main.c`) は
checksum が合った FWUP フレームのコマンド 3 を受けると直ちに `NVIC_SystemReset()` する。
自己リセット待ち (約 6 秒) が要らなくなり、復帰は約 10 秒から「検知 + 起動 1.4 秒」になる見込み。

- 実機確認 (2026-09-19、6 番): 正常な G474 にこの 1 フレームを送ると、40 ms 後に feedback が止まり、1.35 秒後に復帰した。
- 追加であって置換ではない: 送信停止は従来どおり併用するので、指令が届かなくても従来の自己リセットで復帰する。
  FWUP フレームは G474 の CM4 指令タイムスタンプを更新しない (`main.c` の FWUP 分岐が通常指令の処理より前) ため、その保険の時計も遅らせない。
- 送るのはコマンド 3 だけ。コマンド 4/5 は G474 をブートローダーへ入れる書き換え動作なので、ソースにも作る経路がない。
- FWUP を知らない旧ファームは、この 72 バイトを通常指令として読む。`vision 無効・停止フラグなし・速度 0` になり、G474 の出力は停止側に倒れる。
- 1 回の無音につき 1 回だけ送る (連打すると起動直後の G474 を再びリセットしうる)。

**制約と副作用**:

- G474 が「一度も AI 接続していない」状態で TX が死んでいる場合は、送信を止めても自己リセットしない。
  待ち時間切れで再開したとき、指令を受けて AI 接続済みになるので、次の周期で復帰する (最大 2 周期)。
- 従来 mode 3 は feedback に依存しなかったが、この緩和のあいだは feedback 無音で送信が止まる。
  feedback が無いロボットは crane から使えない (unavailable 扱い) ので、実害はないと判断した。
- **根本原因は G474 側**: `HAL_UART_Transmit_DMA(&huart2, …)` (`ai_comm.c`) が戻り値も
  `huart2.gState` も見ておらず、一度 READY に戻らなくなると `HAL_BUSY` で黙って捨て続ける構造という仮説がある
  (未検証)。G474 側で直るなら、この緩和は不要になる。

### ローカルカメラ情報の挿入

`cm4/camera/cam_server_v3.py` は、検出したカメラ情報をローカル UDP `127.0.0.1:8890` へ 7 バイトで送ります。
`cm4/bridge/forward_ai_cmd_v2.cpp` はこの値を受け、UART パケット末尾手前に挿入します。

- `0..1`: x 座標
- `2..3`: y 座標
- `4..5`: radius
- `6`: fps

カメラ更新レートは STM32 への送信周期より低いため、`cm4/bridge/forward_ai_cmd_v2.cpp` は最後に受信したカメラ情報を短時間保持して使います。
一定時間更新が無い場合、またはカメラが接続されていない場合は、カメラ領域を 0 で埋め、`x=0, y=0, radius=0, fps=0` として扱います。

## ホスト側制御ツール

`host/lib/cm4_control_client.py` は CM4 の `cm4/lancher.py` に対する HTTP クライアントです。

### CLI 例

- 単体状態確認
  - `uv run cm4-control status --ip 192.168.20.103`
- 複数台状態確認
  - `uv run cm4-control scan`
- 起動
  - `uv run cm4-control start --ip 192.168.20.103`
- 停止
  - `uv run cm4-control stop --ip 192.168.20.103`

`host/apps/host_lancher.py` はこの通信処理を利用し、`192.168.20.100` から `192.168.20.112` までの CM4 を GUI で監視・操作します。
