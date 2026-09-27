# 制御パケット

このドキュメントは、CM4→Mainの共通制御指令`RobotCommandSerializedV2`を定義します。crane→CM4の入力形式、各control modeの配置と機体別対応、UART・SPIでの転送もここにまとめます。指令本体は両機種とも64バイトで、対応するcontrol modeだけが異なります。

## SSOT（この仕様の正本）

`RobotCommandSerializedV2`（64 バイト）のレイアウトの正本は **crane 側**の
`crane/crane_sender/include/crane_sender/robot_packet.h` です。
次の実装が一致していなければなりません。4WS Mainの実装でも同じ64バイト配置を使用します。

| リポジトリ | ファイル | 状態 |
|---|---|---|
| crane | `crane_sender/include/crane_sender/robot_packet.h` | **正本** |
| G474_Orion_main | `Core/Inc/robot_packet.h` | byte 0..31 一致（32..37 は OrionMain が使わないので未定義） |
| framework | `src/simulator/ibis_protocol.h` | 一致 |
| Orion_CM4 | `cm4/bridge/robot_packet.h` | 一致 |
| 4WS_MainFW | 未実装 | 実装時に同じ`RobotCommandSerializedV2`を受信する |

`cm4/bridge/robot_packet_layout_test.cpp` が
byte 0..37 の全オフセット・`ControlMode`・`FlagAddress` を `static_assert` で固定し、
さらにゴールデンベクタで量子化挙動（丸めずに切り捨てる）まで検査します。
`cm4/build.sh` と CI から実行されます。**`robot_packet.h` を編集したら必ず通すこと。**

## 対象ファイル

- `cm4/bridge/robot_packet.h`
  - 64 バイトの `RobotCommandSerializedV2` と、シリアライズ / デシリアライズ処理を定義します。
- `cm4/bridge/robot_packet_layout_test.cpp`
  - 上記のレイアウトが正本からドリフトしていないことを検査します。
- `cm4/bridge/forward_ai_cmd_v2.cpp`
  - AI から受け取った制御パケットをUARTでSTM32へ送ります。ローカルカメラ情報はCM4内で受信します。
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

## 機体別の制御パケット（65バイト）

送信側は機体ごとに **1データグラム65バイト**を、そのCM4の
`192.168.20.(100 + ロボットID):12345` へユニキャストで送る。

```text
byte 0    : CHECK_COUNTER（指令内byte 1と同じ値）
byte 1..64: RobotCommandSerializedV2（64バイト）
```

- 送信対象が複数機体なら、各機体のIPへ個別にデータグラムを送る。
- シミュレータは `127.0.0.1:(12400+ロボットID)` の受信ポートで機体を識別する。
  `cm4_sim.out` は担当機体のポートだけを開く（`--in-port-base` で基点を変更可能）。
- 受信側は65バイト以外、先頭CHECK_COUNTERと指令内byte 1が異なるパケット、コマンド64バイトが
  全ゼロのパケットを採用しない。
- **使用中コマンドのbyte 28..31と38..63はゼロとは限らない。**
  受信側は予約領域がゼロであることを前提にしない。

`cm4/bridge/forward_ai_cmd_v2.cpp` は先頭CHECK_COUNTERと指令内byte 1の一致を確認する。自機IDは
`wlan0` のIPv4最終オクテットから100を引いて求める。取得できない場合は起動を中止する。

GUI_Qtの送信実装も
この65バイト形式を使う。GUI_Qtはmode 3の速度指令を送る。mode 4の位置指令を送る側は
同じ外枠に加え、下記のmode 4フィールドを設定する。

## RobotCommandSerializedV2

`cm4/bridge/robot_packet.h` の `RobotCommandSerializedV2` は 64 バイト固定長です。
実装済みのmode 3・4ではbyte 0..37を使用します。mode 5～8では、以下のモード別配置を使用します。

### バイトオフセット

| offset | 名前 | 符号化 |
|---|---|---|
| `0` | `HEADER` | crane→CM4では`0x00`。CM4→Mainでは両機種とも`254` |
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
| `23` | `CONTROL_MODE` | 3～8。機体別の対応は下表 |
| `24..31` | `CONTROL_MODE_ARGS` | **union**（`CONTROL_MODE` により意味が変わる） |
| `32..37` | mode 4・5・8の追加フィールド | 制御モードごとの配置を参照 |
| `38..63` | モード別領域 | 現在定義したmode 3～8では予約 |

### FLAGS

- bit `0`: `IS_VISION_AVAILABLE`
- bit `1`: `ENABLE_CHIP`
- bit `2`: 未使用（常に 0）
- bit `3`: `STOP_EMERGENCY`
- bit `4..7`: 未使用（常に 0）

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

`CONTROL_MODE`は指令本体のbyte 23です。mode 3・5・6はMainへ直接送る指令です。mode 4・7・8はCM4で制御処理を行い、対応する直接指令へ変換します。

| mode | 指令の意味 | OrionMain | 4WS Main | CM4から下流への経路 |
| --- | --- | --- | --- | --- |
| `3` | 極座標の速度目標 | 対応 | 対応予定 | 両機種のMainへ共通の`RobotCommandSerializedV2`を直接送る |
| `4` | 位置目標と到達時速度 | 対応 | 対応予定 | CM4で位置制御し、mode 3・5・6のいずれかへ変換する |
| `5` | 各輪の駆動周速度と操舵角 | 非対応 | 対応予定 | 4WS Mainへ共通の`RobotCommandSerializedV2`を直接送る |
| `6` | 各輪の駆動周速度 | 対応予定 | 非対応 | OrionMainへ共通の`RobotCommandSerializedV2`を直接送る |
| `7` | ボール基準の相対速度 | 対応予定 | 対応予定 | CM4のローカルカメラを使い、mode 3・5・6へ変換する |
| `8` | ボール基準の相対位置 | 対応予定 | 対応予定 | CM4のローカルカメラと位置制御を使い、mode 3・5・6へ変換する |

Orionで現在動作するのはmode 3と、CM4がmode 3へ変換するmode 4である。mode 5～8と4WS向け通信は未実装。mode 4から選べる出力はOrionではmode 3または6、4WSではmode 3または5に限る。選択規則と座標・方位変換は実装前に決める。機体が対応しないmode 5・6は拒否して安全停止する。

> **`CONTROL_MODE_ARGS` は union です。`CONTROL_MODE` を見ずに復号してはいけません。**
> mode 4 のパケットを mode 3 として復号すると `terminal_velocity_x/y` が `r/theta` として
> 読まれ、無言で暴走します。

**OrionMainへ直接送れる実装済みモードはmode 3です。** mode 4をMainへ素通ししません。mode 6の受信にはOrionMain側の復号と車輪制御の拡張が必要です。

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

### FOUR_WHEEL_STEERING_TARGET_MODE (5)

4WS Mainへ直接送る4輪駆動・4輪操舵の目標である。指令本体byte 23を`5`とし、byte 0..23の共通フィールドを使用する。

| 指令本体のbyte | 内容 |
| --- | --- |
| 24..25 | モジュール0の駆動周速度 [m/s] |
| 26..27 | モジュール0の操舵角 [rad] |
| 28..31 | モジュール1の駆動周速度、操舵角 |
| 32..35 | モジュール2の駆動周速度、操舵角 |
| 36..39 | モジュール3の駆動周速度、操舵角 |
| 40..63 | 予約、0 |

各値は2バイトで上位バイトから格納する。`raw = uint16_t(32767 × (x / R) + 32767)`、`x = (raw - 32767) × R / 32767`とする。速度の`R`は`32.767 m/s`、操舵角の`R`は`10π rad`。`0x7FFF`は0、`0xFFFF`は未使用値として拒否する。範囲外の値は黙って飽和させず拒否する。車輪番号と配置、回転・操舵の正方向、機構上の許容範囲は実装前に定義する。`STOP_EMERGENCY`は輪の目標より優先する。

mode 5のbyte 32..37は輪の目標であり、mode 4の位置目標として復号しない。crane、CM4、4WS Mainにmode 5用のシリアライザ・デシリアライザが必要である。4WS Mainの通信・制御は未実装であり、対応するまでmode 5を走行指令として送らない。

### OMNI_WHEEL_SPEED_TARGET_MODE (6)

OrionMainへ直接送る4輪の駆動周速度目標である。指令本体byte 23を`6`とする。mode 5から操舵角を除いた配置を使う。

| 指令本体のbyte | 内容 |
| --- | --- |
| 24..25 | 車輪0の駆動周速度 [m/s] |
| 26..27 | 車輪1の駆動周速度 [m/s] |
| 28..29 | 車輪2の駆動周速度 [m/s] |
| 30..31 | 車輪3の駆動周速度 [m/s] |
| 32..63 | 予約、0 |

各速度はmode 5と同じ2バイト符号化を使用する。符号化範囲は±32.767 m/sで、上位バイト、下位バイトの順に格納する。`0x7FFF`は0、`0xFFFF`は無効値とする。機構上の速度上限、車輪番号と配置、正回転方向は実装前に定義する。`STOP_EMERGENCY`は車輪目標より優先する。

mode 6ではbyte 32..37をmode 4の位置目標として復号しない。送信側とCM4側にはmode 6専用のシリアライザとデシリアライザが必要である。OrionMain側の受信・制御も未実装である。

### BALL_RELATIVE_VELOCITY_MODE (7)・BALL_RELATIVE_POSITION_MODE (8)

mode 7・8はCM4内部の制御にローカルカメラ観測値を反映する。mode 7はボール基準の相対速度、mode 8はボール基準の相対位置を指示する。ボール基準の目標と、ボール未検出時に使う通常の目標は別フィールドに入れる。`STOP_EMERGENCY`は検出状態と目標値より優先する。

| 指令本体のbyte | mode 7：ボール基準の相対速度 | mode 8：ボール基準の相対位置 |
| --- | --- | --- |
| 24..25 | 未検出時のmode 3 `target_global_velocity_r` [m/s] | 未検出時のmode 4 `terminal_velocity_x` [m/s] |
| 26..27 | 未検出時のmode 3 `target_global_velocity_theta` [rad] | 未検出時のmode 4 `terminal_velocity_y` [m/s] |
| 28..29 | 検出時のボール基準相対速度 `relative_velocity_x` [m/s] | 検出時のボール基準相対目標位置 `relative_target_x` [m] |
| 30..31 | 検出時のボール基準相対速度 `relative_velocity_y` [m/s] | 検出時のボール基準相対目標位置 `relative_target_y` [m] |
| 32..33 | 予約、0 | 未検出時のmode 4 `TARGET_GLOBAL_POS_X` [m] |
| 34..35 | 予約、0 | 未検出時のmode 4 `TARGET_GLOBAL_POS_Y` [m] |
| 36..37 | 予約、0 | 未検出時のmode 4 `TERMINAL_VELOCITY` [m/s] |
| 38..63 | 予約、0 | 予約、0 |

各2バイト値は上位バイト先行の符号化を使う。位置と並進速度の符号化範囲は±32.767 mまたはm/s、mode 7の未検出時の方向角はmode 3と同じ±32.767 radとする。実際に許す速度・距離は別途制限する。共通フィールドの目標方位はグローバル方位とし、ボール基準にするのは並進2軸である。

CM4はローカルカメラUDPの最新7バイトから有効なボール観測の有無を判定する。候補条件は`radius > 0`かつ最終受信から100 ms以内である。カメラが未起動で受信が一度もない場合や、停止・通信断により最終受信から100 msを超えた場合は条件を満たさない。`x`・`y`は画像座標、`radius`は画像上の半径であり、そのままメートル単位の目標と比較しない。有効な観測がある場合、CM4内部の制御に位置・半径と観測の鮮度を反映し、ボール基準の位置・速度を求め、機体向けのmode 3・5・6指令へ変換する。mode 8の相対目標に到達したときの目標速度は0とする。

有効なボール観測がないときは、mode 7のbyte 24..27からmode 3相当の指令を生成し、mode 8のbyte 24..27と32..37からmode 4相当の指令を生成する。mode 8ではCM4の位置制御を通した後、Orionならmode 3または6、4WSならmode 3または5へ変換する。検出状態の切り替え時はボール追従側の制御状態をリセットし、保存したボール位置を有効な新規観測として使わない。crane指令や機体feedbackが失効した場合は、カメラが見えていても安全停止を優先する。

カメラが出すのは画像座標と半径であり、ボール基準のメートル座標や速度ではない。実装前にカメラ較正、距離推定、軸の向き、ボール速度の推定方法、検出の信頼度と制御周期を決める。変換が未定義のままmode 7・8を走行指令として採用しない。mode 7・8の処理は未実装である。

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
crane 側から変えられる必要があります。65バイトの指令パケットとは**別ポートの
28 バイトのデータグラム**で運びます。相乗りさせないのは、64 バイトのレイアウトが
crane / OrionMain / framework / CM4 の 4 者一致を不変条件にしており、しかも crane が
使用中のコマンドの byte 28..31 / 38..63 をゼロ初期化していないためです。別ポートなら
OrionMain と framework は一切変わりません。

正本は `cm4/bridge/config_packet.h` です。送信側は設定パケットも対象機体のIPへ
ユニキャストで送る。複数機体に同じ設定を適用する場合は各機体へ個別に送る。

| byte | 内容 |
|---|---|
| 0..3 | magic `'O' 'C' '4' 'C'` |
| 4 | version（`2`） |
| 5 | robot_id（`0xFF` = 全機に適用する値。UDPは各機体へ個別送信） |
| 6..7 | 予約（0） |
| 8..11 | `position_gain`（kp） float32 little endian |
| 12..15 | `deceleration` float32 little endian |
| 16..19 | `position_tolerance` float32 little endian |
| 20..23 | `integral_gain`（ki） float32 little endian |
| 24..27 | `derivative_gain`（kd） float32 little endian |

2バイト固定小数ではなく素のfloat32です。量子化を挟まないため、
craneの表示値とCM4の実効値を一致させられます。

### 受信時の検査

サイズは28バイト、versionは`2`を要求する。サイズ不一致は`WrongSize`、
version不一致は`UnsupportedVersion`として拒否し、理由をログに出す。

> [!IMPORTANT]
> **crane と CM4 は対応する版を同時に配ること。** 設定が拒否された場合、
> CM4は現在のゲインを保持する。
> `cm4_sim` を使う場合は Docker イメージ（`ghcr.io/ibis-ssl/orion-cm4-sim`）の
> タグ固定も合わせて更新してください（crane 側 `docker/dev/docker-compose.yaml` と
> `docker/scenario/docker-compose.yaml` の `CM4_SIM_TAG`）。

受信側は次の範囲を検査し、外れていれば**クランプせずデータグラムごと捨てて**
拒否理由をログに出します。黙ってクランプすると crane 側の表示と実機の実効値が
食い違ったまま気付けません。

| フィールド | 範囲 |
|---|---|
| `position_gain`（kp） | `0 <= v <= 20` [1/s] |
| `integral_gain`（ki） | `0 <= v <= 20` [1/s²] |
| `derivative_gain`（kd） | `0 <= v <= 5` [無次元] |
| `deceleration` | `0 <= v <= 20` [m/s²] |
| `position_tolerance` | `0 <= v <= 1.0` [m] |

検査は**データグラム単位**です。ki だけが範囲外でも kp を含めて 1 つも適用しません。
一部だけ適用すると crane の表示と実機の実効値が食い違います。

- 受信ポートは実機 `ai_cmd_v2.out` もシミュレータ `cm4_sim.out` も `--config-port`（既定 12350）。
  受信・検証・適用・ログは同じ `config_packet.h` を通るので、sim で確かめた値は実機でも同じ扱いになります。
- 設定が途絶しても最後の値を保持します。ゲインは安全信号ではないので、
  届かないことを理由に既定値へ戻すとかえって挙動が飛びます。
- crane は同じ値を定期送信して構いません。値が変わったときだけログに出ます。
- **変更できるのはこの 5 つだけです。** `command_timeout_ms` / `feedback_timeout_ms` は
  安全停止の閾値、`vision_age_limit_ms` は OrionMain の定数と一致させるための値なので、
  遠隔から動かせるようにしていません（`cm4/control/position_controller.h`）。
  `integral_velocity_limit`（I 項が単独で出せる速度の上限）も同じ理由で載せていません。
  あれは「効き」ではなくワインドアップの暴走幅の上限で、遠隔で緩められるようにすると
  ki を上げすぎたときの逃げ場が無くなります。追従を強めたいときは ki を上げてください。
- `cm4_sim` は 11 台を 1 プロセスで代行するので、設定はプロセス全体へ適用されます。
  台ごとに別ゲインを試すときは `--robot-ids` と `--config-port` を分けて起動します。

## cm4/bridge/forward_ai_cmd_v2.cpp

`cm4/bridge/forward_ai_cmd_v2.cpp` はAI側UDPの制御指令をSTM32へUART送信します。ローカルカメラUDPもCM4内で受信します。

### 入力

- AI 制御パケット
  - UDP port: `12345`（`--ai-cmd-port` で変更可。ホスト PC でのテスト用）
  - 65バイト固定。これ以外の長さは捨てます。`recv()`に`MSG_TRUNC`を付けて
    データグラムの実長を検査します。
  - 先頭CHECK_COUNTERと指令内byte 1が異なるパケットと、コマンド64バイトが全ゼロのパケットは採用しません。
- ローカルカメラパケット
  - UDP port: `8890`（`--local-cam-port` で変更可）
  - `CAM_BUF_SIZE` は `7` バイトです。
- 位置制御設定パケット
  - UDP port: `12350`（`--config-port` で変更可）
  - 28バイト固定。crane がゲインを稼働中に変更するために送ります。
    詳細は上の[位置制御設定パケット](#位置制御設定パケットudp-12350)を参照。
- OrionMain feedback（位置制御ループを閉じるため）
  - UDP port: `127.0.0.1:(50000 + 100 + ロボット ID)`（`--feedback-port` で変更可）
  - `robot_feedback.out` が UART から読んだ 128 バイトを loopback unicast で渡します。
  - 詳細は [フィードバックパケット](feedback_packet.md) を参照。

ロボット ID は `wlan0` の IPv4 最終オクテット `- 100` です。決定できないときは
**0 号機として動かず終了します**。位置制御では ID が feedback の bind ポートも決めるので、
黙って 0 に落ちると「自分の OrionMain へ送りながら 0 号機の feedback で位置ループを閉じる」
機体跨ぎの制御になります。テスト時は `--robot-id` で明示指定できます。

### UART 送信

- UART port: `/dev/serial0`（CM4_108ではPL011の`ttyAMA0`）。`--serial-port` で変更可。
- 既定 baudrate: `1000000`
- `-s` で baudrate を変更できます。
- 送信サイズは指令64バイト＋未定義領域7バイト＋チェックサム1バイトの`72`バイトです。
- UART 送信バッファの先頭は `254` に上書きします。
- byte 64..70はプロトコル上の未定義領域です。CM4は毎フレーム0で初期化します。受信側は値に意味を持たせません。
- 末尾 1 バイトはチェックサムです（byte 0..70 の総和 & 0xFF）。

72 バイト x 10 bit / 1 Mbps = **720 us/パケット**です。送信レートを上げると UART 占有率が
そのまま上がるので注意してください（500 Hz で 36%、1 kHz で 72%）。

### 2 つの経路

craneからCM4を経てOrionMainへ届く実機経路と、mode 7・8のカメラ利用案は[処理ブロック図](overview.md#実機の処理ブロック図)を参照してください。

受信パケットの `CONTROL_MODE` で経路が分かれます。**この 2 つは意図的に統合していません。**

| 受信 mode | 動作 | 送信ゲート |
|---|---|---|
| `3`、または `--passthrough` 指定時 | 64 バイトをそのまま転送 | crane の `CHECK_COUNTER` が変化したとき |
| `4` | 位置制御ループを閉じて mode 3 を生成 | `--tx-rate-hz`（既定 100 Hz）の時間ゲート |

素通し経路では `check_counter` が crane 由来なので、値が変化したときに送ります。
位置制御経路では CM4 が `check_counter` を採番し、時間ベースのレートで送ります。

`--passthrough` は mode 4 が来ても強制的に素通しします。OrionMain は mode 4 を処理しないため、
mode 4 の診断では `--debug` と併用し、実機 UART へ送らないでください。

### 送信レートとポーリング

メインループは `usleep(1000)` の **1 kHz ポーリング**のままです。UDP は非ブロッキングで読み、
受信が無ければ前回のバッファがそのまま残ります。

位置制御経路の UART 送信レートは `--tx-rate-hz`、**既定 100 Hz** です。

- crane からの指令が途絶しても停止指令を送れるよう、時間ゲートで送信します。
- 100 Hz での UART 占有率は 720 us x 100 = **7.2%** です。
- `--tx-rate-hz 500` では占有率が約36%になるため、使用前に OrionMain の
  `ORE`/`FE`/`NE`/`PE` カウンタを確認してください。

### CHECK_COUNTER

crane は `RobotCommands` メッセージ 1 通につき 1 回インクリメントし、その値を
**全ロボット共通**で入れます（`0 → 201` で折り返すので実質 1..200 の巡回）。

OrionMain の `checkConnect2AI()`（`Core/Src/ai_comm.c`）は
**`check_counter` が変化し続けること**を AI 接続生存の判定に使います。
`AI_CMD_TIMEOUT(0.5) * MAIN_LOOP_CYCLE(500)` = **250 ms** 変化が無いと `connected_ai = false` です。

1 バイトなので値は周期的に一巡します。**ロス検出用のシーケンス番号としては使えません。**

#### mode 4 の採番

mode 4 を受けて位置制御を回す経路では、**CM4 が `check_counter` を採番します**
（送信ごとに `++c; if (c > 200) c = 0;`）。

この経路では **OrionMain の `connected_ai` は crane の生存を意味しません**。
CM4 が生きていれば crane が死んでいても `check_counter` は変化し続けるからです。
crane 断の安全停止は CM4 側で明示的に行います（下記）。

素通し経路では crane 由来の値をそのまま流します。

### 位置制御と安全停止

mode 4 を受けると `cm4/control/position_controller.cpp` を通します。制御則は crane の
`sim_position_controller.cpp` の `calculateSimGlobalVelocity()` と同一で、既定ゲインは
`position_gain = 2.0` / `deceleration = 3.0`（`--kp` / `--decel` は起動時の初期値。
稼働中は crane からの設定パケットで上書きされます）です。

更新後の仕様では、ロボットの現在位置は **OrionMain feedback の byte 112..119（`vision_based_position_x/y`）** を
使います。crane のパケットに入っている `vision_global_pos` では閉じません。
それは今回ループの外へ出そうとしている無線経路そのものだからです。
このbyte配置の送信・受信実装への反映は別作業です。

`position_tolerance` は65バイトの指令パケットに載らないので CM4 側の設定値です
（既定 0.01 m、`--tolerance`）。

次のいずれかで速度指令をゼロにし、`STOP_EMERGENCY`(byte 22 bit3) を立てます。

| 条件 | `reason` | 既定 |
|---|---|---|
| crane が `STOP_EMERGENCY` を立てた | `StopEmergency` | — |
| crane からのパケットが途絶 | `CommandStale` | `--command-timeout-ms 100` |
| OrionMain feedback が途絶（起動直後の未受信を含む） | `FeedbackStale` | `--feedback-timeout-ms 100` |
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

実機 OrionMain の停止条件は `Core/Src/state_func.c:314` の 4 つです。

```c
sys->stop_flag || ai_cmd->stop_emergency || !ai_cmd->is_vision_available
  || ai_cmd->elapsed_time_ms_since_last_vision > 500
```

このうち **`is_vision_available`（byte 22 bit0）と
`elapsed_time_ms_since_last_vision`（byte 20..21）の 2 つ**を CM4 でも見ます。
実機 OrionMain も同条件で停止します。

`vision_age_limit_ms`（500 ms）に CLI オプションを生やしていないのは意図的です。
これは調整パラメータではなく実機ファームウェアの定数と一致させるための値で、
現地で食い違った値を設定できると「CM4 は走らせているのに OrionMain は止めている」
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

CM4 側で巻き戻りを補正することは**しません**。実機 OrionMain と同じ 2 バイトを同じ
`uint16_t` として読んでいるので、実機と同じ判定になることのほうが重要です。
ここだけ賢くすると、実機と CM4 で挙動が分かれます（simulator-cli 側も同じ方針）。

#### crane 断から車輪が止まるまでの時間

`--command-timeout-ms`（既定 100 ms）に、下記が加算されます。

| 要素 | 時間 |
|---|---|
| 1 kHz ポーリングの検出遅れ | 最大 1 ms |
| UART 72 バイト @ 1 Mbps | 0.72 ms |
| OrionMain のメインループ 500 Hz | 最大 2 ms |

**合計でおよそ 104 ms** です。停止指令は `--tx-rate-hz` のゲートを待ちません
（停止理由が変わった周期はレートに関わらず即送信します）。実機で測るときは
「100 ms ちょうど」ではなくこの予算と照合してください。

この「`--tx-rate-hz` で動かない」は
`test_forward_ai_cmd_v2.py::test_stop_latency_does_not_depend_on_tx_rate` が
検査しています。**期待される「動かなさ」を検査にしておかないと、レートを上げ下げ
したときに停止レイテンシが一緒に動いても誰も気づきません。**

##### 104 ms は「駆動力が切れるまで」で、「止まるまで」ではありません

**安全停止では実機も惰走します。** OrionMain の停止分岐（`Core/Src/state_func.c:314`）は
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

| CM4 が送るもの | OrionMain が通る経路 | 挙動 |
|---|---|---|
| mode 3 で `r = 0`、`STOP_EMERGENCY` **なし** | `speedControl` → `omniMoveIndiv` | 車輪 PID が効く（能動制動） |
| `STOP_EMERGENCY` **あり** | `omniStopAll` | 駆動力ゼロ（惰走） |

`applySafetyStop()` は `STOP_EMERGENCY` を立てるので、**crane 断・feedback 断・
vision 断の安全停止はすべて下段（惰走）**です。約 104 ms で駆動力が切れ、そこから
慣性で転がります。

シミュレータの `SimRobot` も最後の指令から一定時間で standby に入り、車輪 PID に
到達する手前で return するので、**この 2 経路の作り分けは実機とシミュレータで
一致しています**。惰走距離は能動制動の数倍になります。

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

ただしこれが効くのは **CM4 ごと落ちて OrionMain への送信が止まった場合だけ**である。
mode 4 の位置制御では CM4 が送り続けるので `connected_ai` は発火せず、crane 断・
feedback 断・vision 断はすべて `STOP_EMERGENCY` 経路（上表の 2 行目）に入る。

なお **mode 4 がシミュレータまで届いた場合の停止だけは能動制動のまま**残されています。
この分岐は実機に対応物が無く、CM4 が mode 変換に失敗しているという合図なので、
惰走させると発見が遅れるという判断です。`POSITION_TARGET` の警告と同じ「構成ミスの
第一手掛かり」の位置づけです。

実機で「crane を止めて車輪が止まるまで」を測るときは、**駆動力が切れる時刻
（約 104 ms、予算と照合する対象）と、機体が静止する時刻（惰走距離ぶん後ろ）**を
分けて記録してください。

##### CM4とOrionMainの停止判定

crane が沈黙しても、**CM4 は `check_counter` を進めながら送信を続けます**
（`forward_ai_cmd_v2.cpp` の位置制御パスは毎送信で `nextCheckCounter()` を呼び、
停止中も `--tx-rate-hz` で送り続ける）。したがって OrionMain の `connected_ai` は真のまま
であり、車輪が止まる理由は **CM4 が立てた `STOP_EMERGENCY`** です。250 ms の
`connected_ai` タイムアウトはこの経路には出てきません。

250 ms の判定は **CM4 側（`ai_cmd_v2.out` のプロセス死、UART 断）の通信途絶**
に適用されます。このとき `check_counter` が凍り、OrionMain が自力で
止めます。

| 何が落ちたか | 止めるのは誰か | 時間 |
|---|---|---|
| crane（無線断・プロセス死） | CM4 の `STOP_EMERGENCY` | 約 104 ms |
| CM4（`ai_cmd_v2.out` の死、UART 断） | OrionMain の `connected_ai` | 250 ms |

`--passthrough` では `check_counter` が crane 由来なので、crane 断は
`connected_ai` の 250 ms 判定に現れます。

出力パケットは受信した 64 バイトをコピーして `CHECK_COUNTER` / `CONTROL_MODE` /
`CONTROL_MODE_ARGS` だけを差し替えて作ります。ゼロから組み立てると
`target_global_theta` / `angular_velocity_limit` / `kick_power` / `dribble_power` / flags を
取りこぼします（OrionMain も simulator-cli もこれらをすべて使います）。

なお実機では `VISION_GLOBAL_X/Y`(2..5) は crane 由来のまま流します。OrionMain が vision 融合に
使うので、CM4 の推定値を書き戻すと自己帰還になります。`cm4_sim` だけは simulator-cli の
0.5 m 照合ゲートを通すために feedback 由来の実位置で上書きします。

### CM4内のローカルカメラ受信

`cm4/camera/cam_server_v3.py` は検出結果をローカルUDP `127.0.0.1:8890`へ7バイトで送ります。`cm4/bridge/forward_ai_cmd_v2.cpp`はCM4内でこれを受信し、100 ms以内の最新値を保持します。UARTのbyte 64..70には反映しません。カメラパケットの形式は[カメラ](camera.md)、mode 7・8の利用方法は上記の制御モード節を参照してください。

## 4WS MainへのSPI転送（仮仕様）

4WS MainとはSPIで通信し、CM4がSPIマスターになる。制御指令本体には上記の64バイト`RobotCommandSerializedV2`を使用する。4WS Mainはmode 3・5を直接受け、mode 4・7・8はCM4がmode 3または5へ変換してから送る。mode 6を4WS Mainへ送らない。

SPIのmode、クロック、CS配線、転送周期、全二重転送の手順は実機で決める。指令本体のbyte 0はOrionMain向けと同じ`254`とする。転送単位やダミーバイトが必要でも、指令本体に4WS固有のヘッダ、メッセージ種別、長さ、CRC、別の制御ペイロードを追加しない。4WS MainのSPI通信は未実装である。Main→CM4のSPI受信でも共通の[フィードバックパケット](feedback_packet.md)を使用する。

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
