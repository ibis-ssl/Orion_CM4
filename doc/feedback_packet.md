# フィードバックパケット

このドキュメントは、STM32 から CM4 を経由してホスト PC へ送るフィードバックパケットの責務とレイアウトをまとめます。

## 対象ファイル

- `forward_robot_feedback.cpp`
  - STM32 から UART で受信した 128 バイトの状態パケットを UDP multicast へ転送します。
- `robot_feedback_packet.py`
  - 128 バイトのフィードバックパケットを Python でデコードします。
- `robot_feedback_receiver.py`
  - UDP multicast を受信し、デコード結果を標準出力へ出します。
- `robot_feedback_viewer.py`
  - 受信・パース結果を Qt GUI で時系列グラフ表示します。
- `robot_feedback_rerun.py`
  - デコード済みの robot feedback を `rerun-sdk` で時系列表示します。

## 通信経路

```text
STM32
  -> UART /dev/ttyS0
  -> forward_robot_feedback.cpp
  -> UDP multicast
  -> robot_feedback_receiver.py
  -> robot_feedback_packet.py
```

## UDP multicast

`forward_robot_feedback.cpp` は、`-n` で指定した値から送信先を作ります。
`lancher.py` 経由で起動する場合は CM4 の IP 末尾オクテットを渡すため、ホスト側の機体番号 `N` に対して `100 + N` が使われます。

- multicast グループ: `224.5.20.<100 + 機体番号>`
- port: `50000 + 100 + 機体番号`

例:

- 機体番号 `10`: `224.5.20.110:50110`

## パケット仕様

パケット長は 128 バイト固定です。

### ヘッダ

- `0`: 同期バイト `0xAB`
- `1`: 同期バイト `0xEA`
- `2`: チェックサム
- `3`: `check_counter`

チェックサムは `data[3:]` の総和の下位 8 bit です。

### ペイロード

- `4..7`: `imu_yaw_deg`、little-endian IEEE754 float
- `8..11`: `battery_voltage_bldc_right`、little-endian IEEE754 float
- `12..14`: `ball_detection`
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

`robot_feedback_packet.py` では、通信量削減用に圧縮された値を次のように復元します。

- `camera_pos_x = camera_pos_x_div2 * 2`
- `camera_radius = camera_radius_div4 * 4`
- `camera_pos_y` と `camera_fps` はそのまま使います。

### CM4 カメラから feedback までの経路

`cm4_cam/cam_server_v3.py` は検出した `x, y, radius, fps` をローカル UDP `127.0.0.1:8890` へ 7 バイトで送ります。
`forward_ai_cmd_v2.cpp` はこの値を STM32 へ送る UART パケットへ挿入します。
STM32 は受け取ったカメラ値を feedback パケットの `camera_pos_x_div2`, `camera_pos_y`, `camera_radius_div4`, `camera_fps` に反映します。

カメラ更新レートは STM32 の feedback 受信周期 125Hz より低いため、`forward_ai_cmd_v2.cpp` は最後に受信したカメラ値を短時間保持して使います。
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

## robot_feedback_packet.py

`robot_feedback_packet.py` は次を担当します。

- 同期バイトの確認
- チェックサム検証
- 128 バイト固定長レイアウトのデコード
- little-endian IEEE754 float の復元
- `tx_value_array[14]` のラベル付け

## robot_feedback_receiver.py

`robot_feedback_receiver.py` は robot feedback の UDP multicast を受信し、標準出力へデコード結果を出します。
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
  - `uv run python robot_feedback_receiver.py --machine-no 3`
- 10 パケット受信して終了
  - `uv run python robot_feedback_receiver.py --machine-no 3 --max-packets 10`
- 5 秒だけ待って受信が無ければ終了
  - `uv run python robot_feedback_receiver.py --machine-no 3 --max-packets 1 --receive-timeout 5`
- JSON Lines 形式で出力
  - `uv run python robot_feedback_receiver.py --machine-no 3 --json`

## robot_feedback_viewer.py

`robot_feedback_viewer.py` は robot feedback を Qt GUI で確認するためのフロントエンドです。
受信・パースの責務は `robot_feedback_receiver.py` と `robot_feedback_packet.py` に置き、GUI 側では現在値と時系列グラフの表示だけを行います。

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
  - `uv run python robot_feedback_viewer.py --machine-no 10`
- interface IP を明示して表示
  - `uv run python robot_feedback_viewer.py --machine-no 10 --interface-ip 192.168.20.200`

## robot_feedback_rerun.py

`robot_feedback_rerun.py` は robot feedback を Rerun に記録します。
通信とパースだけを確認したい場合は `robot_feedback_receiver.py` を使います。

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
  - `uv run python robot_feedback_rerun.py --machine-no 3`
- 10 パケット受信して終了
  - `uv run python robot_feedback_rerun.py --machine-no 3 --max-packets 10`
- 5 秒だけ待って受信が無ければ終了
  - `uv run python robot_feedback_rerun.py --machine-no 3 --max-packets 1 --receive-timeout 5`

## 補足

- 浮動小数点は STM32 側 `float_to_uchar4()` の生バイト列をそのまま送る前提です。
- 送信元の実装定義は `C:\Users\hiroyuki\STM32CubeIDE\workspace_1.17.0\G474_Orion_main\Core\Src\ai_comm.c` の `sendRobotInfo()` にあります。
