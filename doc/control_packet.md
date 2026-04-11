# 制御パケット

このドキュメントは、AI から CM4 を経由して STM32 へ送る制御パケットの責務とレイアウトをまとめます。

## 対象ファイル

- `robot_packet.h`
  - 64 バイトの `RobotCommandSerializedV2` と、C++ 側のシリアライズ / デシリアライズ処理を定義します。
- `forward_ai_cmd_v2.cpp`
  - AI から受け取った制御パケットとローカルカメラ情報をまとめ、UART で STM32 へ送ります。
- `cm4_control.py`
  - CM4 の制御 API サーバーへ `start` / `stop` / `status` を送るホスト側クライアントです。
- `host_lancher.py`
  - `cm4_control.py` を利用するホスト側 GUI です。
- `lancher.py`
  - CM4 側で制御関連プロセスを起動・停止する Web API サーバーです。

## 制御プロセス

`lancher.py` の `/start` は次のプロセスを起動します。

- `ai_cmd_v2.out`
- `robot_feedback.out`
- `cm4_cam/dist/cam_server_v3`

`/stop` は上記の関連プロセスを `pkill -f` で停止します。

## RobotCommandSerializedV2

`robot_packet.h` の `RobotCommandSerializedV2` は 64 バイト固定長です。

### 固定フィールド

- `0`: `HEADER`
- `1`: `CHECK_COUNTER`
- `2..3`: `VISION_GLOBAL_X`
- `4..5`: `VISION_GLOBAL_Y`
- `6..7`: `VISION_GLOBAL_THETA`
- `8..9`: `TARGET_GLOBAL_THETA`
- `10`: `KICK_POWER`
- `11`: `DRIBBLE_POWER`
- `12..13`: `SPEED_LIMIT`
- `14..15`: `OMEGA_LIMIT`
- `16..17`: `LATENCY_TIME_MS`
- `18..19`: `ELAPSED_TIME_MS_SINCE_LAST_VISION`
- `20`: `FLAGS`
- `21`: `CONTROL_MODE`
- `22..`: `CONTROL_MODE_ARGS`

### FLAGS

- bit `0`: `IS_VISION_AVAILABLE`
- bit `1`: `ENABLE_CHIP`
- bit `2`: `LIFT_DRIBBLER`
- bit `3`: `STOP_EMERGENCY`
- bit `4`: `PRIORITIZE_MOVE`
- bit `5`: `PRIORITIZE_ACCURATE_ACCELERATION`

### スケーリング

- 位置と速度の多くは `convertFloatToTwoByte(value, 32.767)` で 2 バイト化します。
- 角度は `convertFloatToTwoByte(value, M_PI)` で 2 バイト化します。
- `kick_power` と `dribble_power` は `value * 20` を 1 バイトに入れます。
- `latency_time_ms` と `elapsed_time_ms_since_last_vision` は `uint16_t` を上位 / 下位バイトに分けます。

## 制御モード

`CONTROL_MODE` は次の値です。

- `0`: `LOCAL_CAMERA_MODE`
- `1`: `POSITION_TARGET_MODE`
- `2`: `SIMPLE_VELOCITY_TARGET_MODE`
- `3`: `VELOCITY_TARGET_WITH_TRAJECTORY_MODE`

### LOCAL_CAMERA_MODE

`CONTROL_MODE_ARGS` には次を入れます。

- `0..1`: `ball_pos[0]`
- `2..3`: `ball_pos[1]`
- `4..5`: `ball_vel[0]`
- `6..7`: `ball_vel[1]`
- `8..9`: `target_global_vel[0]`
- `10..11`: `target_global_vel[1]`

### POSITION_TARGET_MODE

- `0..1`: `target_global_pos[0]`
- `2..3`: `target_global_pos[1]`
- `4..5`: `terminal_velocity`

### SIMPLE_VELOCITY_TARGET_MODE

- `0..1`: `target_global_vel[0]`
- `2..3`: `target_global_vel[1]`

### VELOCITY_TARGET_WITH_TRAJECTORY_MODE

- `0..1`: `target_global_vel[0]`
- `2..3`: `target_global_vel[1]`
- `4..5`: `trajectory_global_origin[0]`
- `6..7`: `trajectory_global_origin[1]`
- `8..9`: `trajectory_origin_angle`
- `10..11`: `trajectory_curvature`

## forward_ai_cmd_v2.cpp

`forward_ai_cmd_v2.cpp` は AI 側 UDP とローカルカメラ UDP を受け、STM32 へ UART 送信します。

### 入力

- AI 制御パケット
  - UDP port: `12345`
  - 1 台分は `RobotCommandSerializedV2` 64 バイト + 機体 index 1 バイトです。
  - `AI_CMD_V2_ROBOT_NUM` は `11` です。
- ローカルカメラパケット
  - UDP port: `8890`
  - `CAM_BUF_SIZE` は `7` バイトです。

### UART 送信

- UART port: `/dev/ttyS0`
- 既定 baudrate: `1000000`
- `-s` で baudrate を変更できます。
- 送信サイズは `AI_CMD_V2_SIZE + CAM_BUF_SIZE + 1`、つまり `72` バイトです。
- UART 送信バッファの先頭は `254` に上書きします。
- 末尾 1 バイトはチェックサムです。

### ローカルカメラ情報の挿入

`cm4_cam/cam_server_v3.py` は、検出したカメラ情報をローカル UDP `127.0.0.1:8890` へ 7 バイトで送ります。
`forward_ai_cmd_v2.cpp` はこの値を受け、UART パケット末尾手前に挿入します。

- `0..1`: x 座標
- `2..3`: y 座標
- `4..5`: radius
- `6`: fps

カメラ更新レートは STM32 への送信周期より低いため、`forward_ai_cmd_v2.cpp` は最後に受信したカメラ情報を短時間保持して使います。
一定時間更新が無い場合、またはカメラが接続されていない場合は、カメラ領域を 0 で埋め、`x=0, y=0, radius=0, fps=0` として扱います。

## ホスト側制御ツール

`cm4_control.py` は CM4 の `lancher.py` に対する HTTP クライアントです。

### CLI 例

- 単体状態確認
  - `uv run python cm4_control.py status --ip 192.168.20.103`
- 複数台状態確認
  - `uv run python cm4_control.py scan`
- 起動
  - `uv run python cm4_control.py start --ip 192.168.20.103`
- 停止
  - `uv run python cm4_control.py stop --ip 192.168.20.103`

`host_lancher.py` はこの通信処理を利用し、`192.168.20.100` から `192.168.20.112` までの CM4 を GUI で監視・操作します。
