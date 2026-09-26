# フィードバックパケット

このドキュメントは、STM32 から CM4 を経由してホスト PC へ送るフィードバックパケットの責務とレイアウトをまとめます。

## 対象ファイル

- `cm4/bridge/forward_robot_feedback.cpp`
  - STM32 から UART で受信した 128 バイトの状態パケットを UDP multicast へ転送します。
    あわせて同一 CM4 上の `ai_cmd_v2.out` へ loopback unicast でも渡します。
- `cm4/bridge/forward_ai_cmd_v2.cpp`
  - loopback unicast で受けた位置（byte 44..51）で位置制御ループを閉じます。
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
（byte 44..51）を必要とします。しかし **`/dev/serial0` の読み手は増やしません**。
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
- `2`: **定数 `10`**（`ai_comm.c` に `// CRC, 10:dummy` とある通り、チェックサムは未実装）
- `3`: `check_counter`（AI から受けた指令の `check_counter` をそのまま返す）

> **byte 2 はチェックサムではありません。** 現行の G474 ファームウェア
> (`Core/Src/ai_comm.c` の `sendRobotInfo()`) は `buf[2] = 10;` を書きます。
> `host/lib/feedback/packet.py` の `is_checksum_valid()` は `data[3:]` の総和と比較するので
> **常に false になります**。受信側でこの判定を有効にしてはいけません。

byte 3 は指令の `check_counter` の反射です。mode 4 の位置制御経路では **CM4 が `check_counter` を採番する**
ので、ここを見れば「CM4 が出した指令がどこまで G474 に届いたか」が分かります
（詳細は [制御パケット](control_packet.md) の CHECK_COUNTER を参照）。
**これは実機だけの性質です。** シミュレータは自走カウンタを送ります
（下記「シミュレータとの一致」を参照）。

### ペイロード

- `4..7`: `imu_yaw_deg`、little-endian IEEE754 float
- `8..11`: `battery_voltage_bldc_right`、little-endian IEEE754 float
- `12..13`: `ball_detection`
- `14`: `tx_cycle_count`（feedback 送信ごとにインクリメントする 1 バイトのカウンタ）
- `15`: `kick_state_div10`
- `16..17`: `current_error_id`、little-endian `uint16_t`
- `18..19`: `current_error_info`、little-endian `uint16_t`
- `20..23`: `current_error_value`、little-endian IEEE754 float
- `24..27`: `motor_current_x10`
- `28`: `ball_detection_extra`
- `29..32`: `temp_motor`
- `33`: `temp_fet`
- `34..35`: `temp_coil`
- `36..39`: `diff_angle_deg`、little-endian IEEE754 float
- `40..43`: `capacitor_boost_voltage`、little-endian IEEE754 float
- `44..47`: `vision_based_position_x`、little-endian IEEE754 float
- `48..51`: `vision_based_position_y`、little-endian IEEE754 float
- `52..55`: `global_odom_speed_x`、little-endian IEEE754 float
- `56..59`: `global_odom_speed_y`、little-endian IEEE754 float
- `60`: `camera_pos_x_div2`
- `61`: `camera_pos_y`
- `62`: `camera_radius_div4`
- `63`: `camera_fps`
- `64..119`: `tx_value_array[14]`、little-endian IEEE754 float
- `120..127`: reserved

### カメラ値の復元

`host/lib/feedback/packet.py` では、通信量削減用に圧縮された値を次のように復元します。

- `camera_pos_x = camera_pos_x_div2 * 2`
- `camera_radius = camera_radius_div4 * 4`
- `camera_pos_y` と `camera_fps` はそのまま使います。

### CM4 カメラから feedback までの経路

`cm4/camera/cam_server_v3.py` は検出した `x, y, radius, fps` をローカル UDP `127.0.0.1:8890` へ 7 バイトで送ります。
`cm4/bridge/forward_ai_cmd_v2.cpp` はこの値を STM32 へ送る UART パケットへ挿入します。
STM32 は受け取ったカメラ値を feedback パケットの `camera_pos_x_div2`, `camera_pos_y`, `camera_radius_div4`, `camera_fps` に反映します。

カメラ更新レートは STM32 の feedback 受信周期 125Hz より低いため、`cm4/bridge/forward_ai_cmd_v2.cpp` は最後に受信したカメラ値を短時間保持して使います。
一定時間更新が無い場合やカメラが接続されていない場合は、`x=0, y=0, radius=0, fps=0` を STM32 へ送ります。

### tx_value_array

`tx_value_array[14]` のラベルは次です。
送信元は STM32 側 `Core/Src/ai_comm.c` の `sendRobotInfo()` で、`enqueueFloatArray()` に追加した順番のまま `buf[64..119]` に little-endian float として格納されます。

- `0`: `mouse_odom_x`
- `1`: `mouse_odom_y`
- `2`: `mouse_global_vel_x`
- `3`: `mouse_global_vel_y`
- `4`: `output_vel_x`
- `5`: `output_vel_y`
- `6`: `motor_feedback_0`
- `7`: `motor_feedback_1`
- `8`: `motor_feedback_2`
- `9`: `motor_feedback_3`
- `10`: `local_odom_speed_mvf_x`
- `11`: `local_odom_speed_mvf_y`
- `12`: `local_odom_speed_mvf_w`
- `13`: `mouse_quality`

## host/lib/feedback/packet.py

`host/lib/feedback/packet.py` は次を担当します。

- 同期バイトの確認
- チェックサム検証
- 128 バイト固定長レイアウトのデコード
- little-endian IEEE754 float の復元
- `tx_value_array[14]` のラベル付け

## host/lib/feedback/receiver.py

`host/lib/feedback/receiver.py` は robot feedback の UDP multicast を受信し、標準出力へデコード結果を出します。
GUI フロントエンドや Rerun には依存しないため、通信とパースだけを確認する用途で使います。

### 出力する主な値

- 同期バイトの検証結果
- チェックサムの検証結果
- 電圧
- 姿勢
- エラー情報
- モーター電流
- カメラ座標
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
- カメラ座標
- モーター電流
- `mouse->global_vel[0]`, `mouse->global_vel[1]`
- `omni->local_odom_speed_mvf[0]`, `omni->local_odom_speed_mvf[1]`
- 同期バイトとチェックサムの検証結果
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
- カメラ座標
- `tx_value_array`

### CLI 例

- 3番機体を表示
  - `uv run robot-feedback-rerun --machine-no 3`
- 10 パケット受信して終了
  - `uv run robot-feedback-rerun --machine-no 3 --max-packets 10`
- 5 秒だけ待って受信が無ければ終了
  - `uv run robot-feedback-rerun --machine-no 3 --max-packets 1 --receive-timeout 5`

## シミュレータとの一致

`framework` の `simulator-cli` も実機と同じ 128 バイト形式を出します。
`cm4_sim` と `ai_cmd_v2.out` は位置制御に byte 44..51 の位置を使用します。
byte 2 は定数 `10`、byte 4..7 は度単位の `imu_yaw_deg`、byte 14 は
`tx_cycle_count`、byte 60 は `camera_pos_x_div2` です。

シミュレータ出力は `host/lib/feedback/packet.py` で復号でき、
`robot-feedback-viewer` などのツールで表示できます。

### シミュレータのbyte 52..59（速度）

シミュレータの速度フィールドは `RadioResponse` 由来のキャッシュから作られ、
`RadioResponse` は**指令が届いたときにしか生成されません**。したがって指令が
落ちている間（経路劣化によるロス、`vision_global_pos` の 0.5 m 照合ゲートによる
破棄）は、**同じパケットの中で位置（byte 44/48、毎周期 vision から）は新鮮なのに、
速度（byte 52/56）は破棄直前の値で凍ります**。ロボットが実際に停止したあとも
停止前の速度を返し続けます。

`cm4_sim` も `ai_cmd_v2.out` も **byte 44..51 の位置しか使わない**ので制御には
影響しません。**騙されるのは速度を見る診断だけ**です。feedback の速度を見て
「動いていないのに速度が出ている」と読んだら、まず指令が届いているかを疑って
ください。

**速度フィールドを判定に使うツールは、値が更新されていることを確認してください。**

`estimated_speed()` は実機 G474 の
オドメトリ相当であり、vision 微分に置き換えると**通常時のほうが実機から遠ざかる**
ため、現在の速度生成に使用します。

なお `STOP_EMERGENCY` による安全停止では指令自体は届き続けるので、速度は
正しく 0 まで減衰します。凍るのは指令が落ちたときだけです。

### シミュレータのbyte 3

byte 3 は実機では指令の `check_counter` の反射ですが、シミュレータの
`IbisFeedbackAdaptor` は指令ストリームを見ないので**自走カウンタ**を送ります。
陳腐化の検出には使えますが、**特定の指令とは対応しません**。
「CM4 が出した指令がどこまで G474 に届いたか」を byte 3 で追う使い方は
実機でのみ成立します。

## 既知の不整合（host 側デコーダ）

`host/lib/feedback/packet.py` は次の 2 点が現行ファームウェアと食い違っています。
このドキュメントの記載（上記）が正です。

- `ball_detection=(data[12], data[13], data[14])` — `data[14]` は `tx_cycle_count` です。
- `is_checksum_valid()` — byte 2 は定数 `10` なので常に false になります。

## 補足

- 浮動小数点は STM32 側 `float_to_uchar4()` の生バイト列をそのまま送る前提です。
- 送信元の実装定義は `C:\Users\hiroyuki\STM32CubeIDE\workspace_1.17.0\G474_Orion_main\Core\Src\ai_comm.c` の `sendRobotInfo()` にあります。
