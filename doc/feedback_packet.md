# フィードバックパケット

このドキュメントは、STM32 から CM4 を経由してホスト PC へ送るフィードバックパケットの責務とレイアウトをまとめます。

## 対象ファイル

- `forward_robot_feedback.cpp`
  - STM32 から UART で受信した 128 バイトの状態パケットを UDP multicast へ転送します。
- `robot_feedback_packet.py`
  - 128 バイトのフィードバックパケットを Python でデコードします。
- `robot_feedback_rerun.py`
  - UDP multicast を受信し、`rerun-sdk` で時系列表示します。

## 通信経路

```text
STM32
  -> UART /dev/ttyS0
  -> forward_robot_feedback.cpp
  -> UDP multicast
  -> robot_feedback_packet.py / robot_feedback_rerun.py
```

## UDP multicast

`forward_robot_feedback.cpp` は、`-n` で指定した値から送信先を作ります。

- multicast グループ: `224.5.20.<機体番号>`
- port: `50000 + 機体番号`

例:

- 機体番号 `3`: `224.5.20.3:50003`

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

### tx_value_array

`tx_value_array[14]` のラベルは次です。

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

## robot_feedback_rerun.py

`robot_feedback_rerun.py` は robot feedback の UDP multicast を受信し、Rerun に記録します。

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
