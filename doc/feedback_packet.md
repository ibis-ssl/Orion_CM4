# OrionMainフィードバックパケット

このドキュメントは、OrionMain（STM32G474）からCM4を経由してホストPCへ送るフィードバックパケットの責務とレイアウトをまとめます。
craneの制御・監視向けに送る機体共通の項目は、[CM4共通状態パケット案](cm4_status_packet_proposal.md)に分けて定義します。

## 対象ファイル

- `cm4/bridge/forward_robot_feedback.cpp`
  - STM32 から UART で受信した 128 バイトの状態パケットを UDP multicast へ転送します。
    あわせて同一 CM4 上の `ai_cmd_v2.out` へ loopback unicast でも渡します。
- `cm4/bridge/forward_ai_cmd_v2.cpp`
  - loopback unicast で受けた位置を使って位置制御ループを閉じます。更新後の配置はbyte 112..119です。
- `host/lib/feedback/packet.py`
  - 128 バイトのフィードバックパケットを Python でデコードします。
- `host/lib/feedback/receiver.py`
  - UDP multicast を受信し、デコード結果を標準出力へ出します。
- `host/apps/robot_feedback_viewer.py`
  - 受信・パース結果を Qt GUI で時系列グラフ表示します。
- `host/apps/robot_feedback_rerun.py`
  - デコード済みの robot feedback を `rerun-sdk` で時系列表示します。

## 通信経路

```text
STM32
  -> UART /dev/serial0
  -> cm4/bridge/forward_robot_feedback.cpp
       |-> UDP multicast -> host/lib/feedback/receiver.py -> host/lib/feedback/packet.py
       `-> UDP unicast 127.0.0.1:(50000 + 機体番号) -> cm4/bridge/forward_ai_cmd_v2.cpp
```

## loopback unicast（位置制御ループ用）

`ai_cmd_v2.out` は mode 4 を受けたとき位置制御ループを閉じるため、ロボットの現在位置
（更新後の配置はbyte 112..119）を必要とします。しかし **`/dev/serial0` の読み手は増やしません**。
2 プロセスで読むと取り合いになるためです。

`forward_robot_feedback.cpp` が multicast 再配信と同時に
`127.0.0.1:(50000 + 機体番号)` へ unicast でも投げ、`ai_cmd_v2.out` がそれを bind します。

- `ai_cmd_v2.out` の bind は **`127.0.0.1` で行います**（`INADDR_ANY` ではありません）。
  ポート番号が multicast 再配信と同じなので、`INADDR_ANY` だと構成によっては
  自分の再配信のコピーまで位置制御の入力に混ざります。
- unicast は unicast ソケットへ、multicast は multicast ソケットへしか配送されないので、
  同じポート番号でも取り違えは起きません。
- シミュレータ（`cm4_sim.out`）も同じポートを同じ方法で bind します。制御プロセスの
  feedback 受信コードとポート番号が実機と sim で完全に同一になります。

## UDP multicast

`cm4/bridge/forward_robot_feedback.cpp` は、`-n` で指定した値から送信先を作ります。
`cm4/lancher.py` 経由で起動する場合は CM4 の IP 末尾オクテットを渡すため、ホスト側の機体番号 `N` に対して `100 + N` が使われます。

- multicast グループ: `224.5.20.<100 + 機体番号>`
- port: `50000 + 100 + 機体番号`

例:

- 機体番号 `10`: `224.5.20.110:50110`

## パケット仕様

パケット長は 128 バイト固定です。

### ヘッダ

- `0`: 同期バイト `0xAB`
- `1`: 同期バイト `0xEA`
- `2`: CRC-8/ATM（byte 3..127 を対象）
- `3`: `check_counter`（AI から受けた指令の `check_counter` をそのまま返す）

CRC-8/ATM のパラメータは多項式 `0x07`、初期値 `0x00`、入力・出力とも反転なし、最終 XOR `0x00` です。
計算範囲は **byte 3..127 の125バイト**で、同期バイトとbyte 2は含めません。
標準検査値は `"123456789" → 0xF4` です。送信側は全ペイロードの確定後にbyte 2を書きます。
CM4は長さ・同期バイト・CRCを確認し、不正なパケットを位置制御にも再配信にも渡しません。

byte 3 は指令の `check_counter` の反射です。mode 4 の位置制御経路では **CM4 が `check_counter` を採番する**
ので、ここを見れば「CM4 が出した指令がどこまで OrionMain に届いたか」が分かります
（詳細は [制御パケット](control_packet.md) の CHECK_COUNTER を参照）。
**これは実機だけの性質です。** シミュレータは自走カウンタを送ります
（下記「シミュレータとの一致」を参照）。

### ペイロード
ヘッダの後、byte 4から下表の記述順に隙間なく配置します。`tx_value_array[n]`は項目の論理番号です。
各値は表の位置に配置し、14要素を連続した配列としては扱いません。

#### コア機能

| バイト | 項目 | 形式・意味 |
| --- | --- | --- |
| `4` | `tx_cycle_count` | 送信ごとに1増える`uint8_t` |
| `5..6` | `current_error_id` | little-endian `uint16_t` |
| `7..8` | `current_error_info` | little-endian `uint16_t` |
| `9..12` | `current_error_value` | little-endian IEEE754 float |

#### メイン基板

| バイト | 項目 | 形式 |
| --- | --- | --- |
| `13..16` | `imu_yaw_deg` | little-endian IEEE754 float |
| `17..18` | `ball_detection[2]` | 各1バイト |
| `19` | `ball_detection_extra` | 1バイト |
| `20..23` | `diff_angle_deg` | little-endian IEEE754 float |

#### 電源基板

| バイト | 項目 | 形式 |
| --- | --- | --- |
| `24..27` | `battery_voltage` | little-endian IEEE754 float |
| `28` | `kick_state_div10` | 1バイト |
| `29` | `temp_fet` | 1バイト |
| `30..31` | `temp_coil[2]` | 各1バイト |
| `32..35` | `capacitor_boost_voltage` | little-endian IEEE754 float |
| `36..39` | `tx_value_array[0]`: `mouse_odom_x` | little-endian IEEE754 float |
| `40..43` | `tx_value_array[1]`: `mouse_odom_y` | little-endian IEEE754 float |
| `44..47` | `tx_value_array[2]`: `mouse_global_vel_x` | little-endian IEEE754 float |
| `48..51` | `tx_value_array[3]`: `mouse_global_vel_y` | little-endian IEEE754 float |
| `52..55` | `tx_value_array[13]`: `mouse_quality` | little-endian IEEE754 float |

#### モーター基板

| バイト | 項目 | 形式 |
| --- | --- | --- |
| `56..59` | `motor_current_x10[4]` | 各1バイト、電流の10倍 |
| `60..63` | `temp_motor[4]` | 各1バイト |
| `64..67` | `tx_value_array[4]`: `output_vel_x` | little-endian IEEE754 float |
| `68..71` | `tx_value_array[5]`: `output_vel_y` | little-endian IEEE754 float |
| `72..75` | `tx_value_array[6]`: `motor_feedback_0` | little-endian IEEE754 float |
| `76..79` | `tx_value_array[7]`: `motor_feedback_1` | little-endian IEEE754 float |
| `80..83` | `tx_value_array[8]`: `motor_feedback_2` | little-endian IEEE754 float |
| `84..87` | `tx_value_array[9]`: `motor_feedback_3` | little-endian IEEE754 float |
| `88..91` | `tx_value_array[10]`: `local_odom_speed_mvf_x` | little-endian IEEE754 float |
| `92..95` | `tx_value_array[11]`: `local_odom_speed_mvf_y` | little-endian IEEE754 float |
| `96..99` | `tx_value_array[12]`: `local_odom_speed_mvf_w` | little-endian IEEE754 float |
| `100..107` | `steering_angle[4]` | 各2バイト、ステア現在角度 [rad] |
| `108..111` | `temp_steering_motor[4]` | 各1バイト、ステアモーター温度 |

`steering_angle[0..3]`は輪番号順に各2バイトを上位バイトから格納します。
符号化範囲は±10π radで、[4WS指令](4ws_spi_packet_proposal.md)の操舵角と同じ2バイト表現です。
`temp_steering_motor[0..3]`の輪番号も操舵角と対応させます。

#### 制御

| バイト | 項目 | 形式 |
| --- | --- | --- |
| `112..115` | `vision_based_position_x` | little-endian IEEE754 float |
| `116..119` | `vision_based_position_y` | little-endian IEEE754 float |
| `120..123` | `global_odom_speed_x` | little-endian IEEE754 float |
| `124..127` | `global_odom_speed_y` | little-endian IEEE754 float |

byte 4..127はすべて上表のフィールドに割り当てます。
FWゲートウェイ応答をbyte 112..126に載せる間は制御用の位置・速度フィールドと重なるため、受信側はその値を位置・速度として解釈しません。

CM4のローカルカメラ情報は、このfeedbackパケットとは別の経路で受けます。形式は[カメラ](camera.md)を参照してください。
この節は更新後のパケット仕様を示します。送信側とホスト側の実装変更は別作業です。

## host/lib/feedback/packet.py

`host/lib/feedback/packet.py` は次を担当します。

- 同期バイトの確認
- CRC-8/ATM検証
- 128 バイト固定長レイアウトのデコード
- little-endian IEEE754 float の復元
- 各`tx_value_array[n]`のラベル付け（配置は上表）

## host/lib/feedback/receiver.py

`host/lib/feedback/receiver.py` は robot feedback の UDP multicast を受信し、標準出力へデコード結果を出します。
GUI フロントエンドや Rerun には依存しないため、通信とパースだけを確認する用途で使います。

### 出力する主な値

- 同期バイトの検証結果
- CRCの検証結果
- 電圧
- 姿勢
- エラー情報
- モーター電流
- JSON Lines 形式の全フィールド

### CLI 例

- 3番機体のフィードバックをテキスト表示
  - `uv run robot-feedback-receiver --machine-no 3`
- 10 パケット受信して終了
  - `uv run robot-feedback-receiver --machine-no 3 --max-packets 10`
- 5 秒だけ待って受信が無ければ終了
  - `uv run robot-feedback-receiver --machine-no 3 --max-packets 1 --receive-timeout 5`
- JSON Lines 形式で出力
  - `uv run robot-feedback-receiver --machine-no 3 --json`

## host/apps/robot_feedback_viewer.py

`host/apps/robot_feedback_viewer.py` は robot feedback を Qt GUI で確認するためのフロントエンドです。
受信・パースの責務は `host/lib/feedback/receiver.py` と `host/lib/feedback/packet.py` に置き、GUI 側では現在値と時系列グラフの表示だけを行います。

### 表示する主な値

- 電圧
- 姿勢
- モーター電流
- `mouse->global_vel[0]`, `mouse->global_vel[1]`
- `omni->local_odom_speed_mvf[0]`, `omni->local_odom_speed_mvf[1]`
- 同期バイトとCRCの検証結果
- エラー情報
- mouse quality

### CLI 例

- 10番機体を表示
  - `uv run robot-feedback-viewer --machine-no 10`
- interface IP を明示して表示
  - `uv run robot-feedback-viewer --machine-no 10 --interface-ip 192.168.20.200`

## host/apps/robot_feedback_rerun.py

`host/apps/robot_feedback_rerun.py` は robot feedback を Rerun に記録します。
通信とパースだけを確認したい場合は `host/lib/feedback/receiver.py` を使います。

### 記録する主な値

- 電圧
- 姿勢
- エラー情報
- モーター電流
- 温度
- `tx_value_array`

### CLI 例

- 3番機体を表示
  - `uv run robot-feedback-rerun --machine-no 3`
- 10 パケット受信して終了
  - `uv run robot-feedback-rerun --machine-no 3 --max-packets 10`
- 5 秒だけ待って受信が無ければ終了
  - `uv run robot-feedback-rerun --machine-no 3 --max-packets 1 --receive-timeout 5`

## シミュレータとの一致

`framework` の `simulator-cli` も128バイト形式を使います。CM4のCRC検証を通すには、
シミュレータ側もbyte 3..127からCRC-8/ATMを計算してbyte 2に入れる必要があります。
このリポジトリ内のシミュレータ用テストフレームはCRCを生成します。
更新後の仕様では、位置制御にbyte 112..119の位置を使用します。
byte 13..16は度単位の`imu_yaw_deg`、byte 4は`tx_cycle_count`です。
`framework`・`cm4_sim`・`ai_cmd_v2.out`の送受信位置も、この配置への更新が必要です。

CRC付きのシミュレータ出力は `host/lib/feedback/packet.py` で復号でき、
`robot-feedback-viewer` などのツールで表示できます。

### シミュレータの速度フィールド（byte 120..127）

シミュレータの速度フィールドは `RadioResponse` 由来のキャッシュから作られ、
`RadioResponse` は**指令が届いたときにしか生成されません**。したがって指令が
落ちている間（経路劣化によるロス、`vision_global_pos` の 0.5 m 照合ゲートによる
破棄）は、**同じパケットの中で位置（byte 112/116、毎周期 vision から）は新鮮なのに、
速度（byte 120/124）は破棄直前の値で凍ります**。ロボットが実際に停止したあとも
停止前の速度を返し続けます。

位置制御が参照するのは **byte 112..119 の位置**なので制御には
影響しません。**騙されるのは速度を見る診断だけ**です。feedback の速度を見て
「動いていないのに速度が出ている」と読んだら、まず指令が届いているかを疑って
ください。

**速度フィールドを判定に使うツールは、値が更新されていることを確認してください。**

`estimated_speed()` は実機 OrionMain の
オドメトリ相当であり、vision 微分に置き換えると**通常時のほうが実機から遠ざかる**
ため、現在の速度生成に使用します。

なお `STOP_EMERGENCY` による安全停止では指令自体は届き続けるので、速度は
正しく 0 まで減衰します。凍るのは指令が落ちたときだけです。

### シミュレータのbyte 3

byte 3 は実機では指令の `check_counter` の反射ですが、シミュレータの
`IbisFeedbackAdaptor` は指令ストリームを見ないので**自走カウンタ**を送ります。
陳腐化の検出には使えますが、**特定の指令とは対応しません**。
「CM4 が出した指令がどこまで OrionMain に届いたか」を byte 3 で追う使い方は
実機でのみ成立します。

## 補足

- 浮動小数点は STM32 側 `float_to_uchar4()` の生バイト列をそのまま送る前提です。
- 送信元の実装定義は `C:\Users\hiroyuki\STM32CubeIDE\workspace_1.17.0\G474_Orion_main\Core\Src\ai_comm.c` の `sendRobotInfo()` にあります。
