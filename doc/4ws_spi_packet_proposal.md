# 4WS MainとのSPI通信（仮仕様）

4WS MainとはSPIで通信する。CM4がSPIマスターになる。SPIのmode、クロック、CS配線、転送周期、全二重転送の手順は実機で決める。これらの転送手順のために、4WS専用の制御・フィードバックパケットは定義しない。

## 共通パケット

| 方向 | パケット | 4WSでの扱い |
| --- | --- | --- |
| CM4→Main | 64バイトの`RobotCommandSerializedV2` | [制御パケット](control_packet.md)と同じバイト配置・符号化を使う。対応する`CONTROL_MODE`だけを受理する |
| Main→CM4 | 128バイトのfeedback packet | [フィードバックパケット](feedback_packet.md)と同じ同期値`0xAB 0xEA`、CRC-8/ATM、バイト配置を使う |

SPIの転送単位やダミーバイトが必要になっても、上表のパケット本体に4WS固有のヘッダ、メッセージ種別、長さ、CRC、別の状態ペイロードを追加しない。CM4は長さ・同期・CRCを検証したfeedback packetを共通のデコーダへ渡す。機体別の値の取得・制御処理は機体設定と`CONTROL_MODE`で切り替える。

4WS Mainが受けるmode 3・5の指令本体は同じ`RobotCommandSerializedV2`である。mode 4・7・8はCM4内で処理し、mode 3または5へ変換してから送る。mode 6は4WS Mainへ送らない。対応状況は[制御モードの機体別互換性](control_mode_compatibility.md)を参照する。

## mode 5：4輪駆動・4輪操舵の目標

crane→CM4のUDPは先頭1バイトの`CHECK_COUNTER`と64バイトの指令本体で構成する。指令本体byte 23の`CONTROL_MODE`を`FOUR_WHEEL_STEERING_TARGET_MODE = 5`とし、CM4→4WS Mainでも同じ64バイトの指令本体を使う。

| 指令本体のbyte | 内容 |
| --- | --- |
| 0..23 | 共通フィールド。byte 22の`STOP_EMERGENCY`を適用し、byte 23を`5`とする |
| 24..25 | モジュール0の駆動周速度 [m/s] |
| 26..27 | モジュール0の操舵角 [rad] |
| 28..31 | モジュール1の駆動周速度、操舵角 |
| 32..35 | モジュール2の駆動周速度、操舵角 |
| 36..39 | モジュール3の駆動周速度、操舵角 |
| 40..63 | 予約、0 |

各値は2バイトで上位バイトから格納する。`raw = uint16_t(32767 × (x / R) + 32767)`、`x = (raw - 32767) × R / 32767`とする。速度の`R`は`32.767 m/s`、操舵角の`R`は`10π rad`。`0x7FFF`は0、`0xFFFF`は未使用値として拒否する。範囲外の値は黙って飽和させず拒否する。車輪番号と配置、回転・操舵の正方向、機構上の許容範囲は実装前に定義する。`STOP_EMERGENCY`は輪の目標より優先する。

mode 5ではbyte 32..37も輪の目標であり、mode 4の位置目標として復号しない。mode 5専用のシリアライザ・デシリアライザをcraneとCM4と4WS Mainに追加する。4WS Mainの通信・制御は未実装であり、対応するまでmode 5を走行指令として送らない。

## 生フィードバックのUDP配信

CM4は有効なfeedback packetを、機体番号`N`の`224.5.20.(100+N):50100+N`へ配信する。4WSの場合に限り、UDPへ出すコピーのbyte 1を`0xEA`から`0xEB`に置き換える。Main→CM4では両機種とも`0xAB 0xEA`であり、ペイロード配置は共通である。CRC-8/ATMの計算対象はbyte 3..127なので、byte 1を変更してもCRC値は変わらない。OrionのUDPコピーはbyte 1も含めて変更しない。

PCの両対応デバッグツールはUDP上のbyte 1から機体を判別し、同じfeedbackデコーダで内容を読む。craneとメインPCの共通監視ツールには、CM4が[共通状態パケット](cm4_status_packet_proposal.md)へ変換して送る。4WSのSPI通信とUDP配信は未実装である。
