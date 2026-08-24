# STM32 ファームウェア更新仕様案

## 1. 目的と対象

CM4 から、基板を分解したり SWD を接続したりせず、次の 5 MCU のアプリケーション FW を更新できるようにする。

| 論理ターゲット | MCU / プロジェクト | 接続 | 備考 |
| --- | --- | --- | --- |
| `main` | STM32G474RE / `G474_Orion_main` | CM4 と USART2 | 更新ゲートウェイを兼ねる |
| `motor-can1` | STM32F303RB / `Orion_F303_BLDC` | G474 の CAN1 | 2 モータを制御 |
| `motor-can2` | STM32F303RB / `Orion_F303_BLDC` | G474 の CAN2 | 2 モータを制御 |
| `sub` | STM32F303CB / `Orion_F303_sub` | CAN | ドリブラ、サーボ、ボールセンサ等 |
| `power` | STM32F303CB / `F303_boost` | CAN | 電源、昇圧、キッカー等 |

`sub` と `power` が CAN1/CAN2 のどちらに実装されるかは、量産構成を確認してマニフェストへ固定する。CAN 上のアドレスは単独の CAN ID ではなく、`bus + node_id + board_type + STM32 UID` で識別する。

## 2. 現状調査

- CM4 と G474 は USART2、1,000,000 bps、8N1 で接続されている。
- G474 の FDCAN1/FDCAN2 と F303 の bxCAN は Classic CAN、標準 11 bit ID、約 1 Mbps である。
- 通常制御ではおおむね `0x000`～`0x500` 番台を使用している。更新用には `0x600`～`0x63F` を予約する。
- G474 は 512 KB Flash。現在の Debug map 上のロードイメージは約 73 KB である。
- 3 種類の F303 はすべて 128 KB Flash。現在の Debug map 上のロードイメージは約 61～63 KB である。
- BLDC 基板は `0x0801F000` に CAN board ID とキャリブレーション値を保存している。更新時にこの領域を消去してはならない。
- 電源基板は IWDG を使用している。更新モードでも IWDG の扱いと全出力の無効化を明示的に実装する必要がある。
- 現状は全 MCU ともアプリケーションが `0x08000000` 始まりであり、常駐ブートローダはない。最初の一度だけは SWD でブートローダと再配置済みアプリを書き込む必要がある。

## 3. 採用アーキテクチャ

```text
CM4: orion-fwupd / orion-fwctl
          |
          | USART2 1 Mbps、OFW-UART
          v
G474 基板専用アプリケーションブートローダー
  - マニフェスト・CRC・書込範囲検証
  - G474 A/B スロット更新
  - UART/CAN 更新ゲートウェイ
          |
          +-- CAN1 -- F303 基板専用アプリケーションブートローダー群
          |
          +-- CAN2 -- F303 基板専用アプリケーションブートローダー群
```

全 MCU はリセット後に必ず User Flash 先頭の自作アプリケーションブートローダーから起動する。STM32 の System Memory に内蔵された ST 製ブートローダー、BOOT0 pin、ROM の UART/CAN protocol は使用しない。ブートローダー領域は通常の更新では更新せず、Option Bytes の write protection を設定する。アプリケーションが壊れていても、ブートローダーと通信できれば CM4 から復旧できる構成とする。

G474 のブートローダーは、アプリ更新機能だけでなく F303 への CAN ゲートウェイも持つ。この機能を G474 の通常アプリだけに置くと、通常アプリ破損時に F303 も更新できなくなるためである。

### 3.1 アプリケーションブートローダーの構成

FW 開発範囲には CM4、G474、3 種類の F303 通常アプリと、各 MCU のブートローダーを含める。ブートローダーは完全共通バイナリにせず、通信・Flash・protocol の共通 core と基板固有 BSP を組み合わせてビルドする。

```text
bootloader/
  common/                 protocol、CRC32C、Flash journal、image validation
  g474_main/              USART2、FDCAN1/2、G474 A/B、main 基板 IO
  f303_bldc/              bxCAN、motor driver/PWM 安全化、設定保持
  f303_sub/               bxCAN、dribbler/servo 安全化、基板 IO
  f303_power/             bxCAN、boost/kick/power 出力安全化、IWDG
```

共通 core は MCU family 依存 HAL を薄い interface 越しに呼び、各 BSP は `board_clock_init()`, `board_io_init_safe()`, `board_transport_init()`, `board_watchdog_service()` を実装する。C 言語のみを使用し、各既存 STM32CubeIDE project から個別にビルドできる構成とする。

### 3.2 IO 初期化方針

ブートローダー開始直後、HAL、タイマ、DMA、CAN/UART の初期化より先に `board_io_init_safe()` を呼ぶ。この関数はHALへ依存せず、必要なGPIO clock enable、出力データレジスタ、安全mode/pull設定をレジスタ直接操作で完了させる。GPIO の出力データレジスタへ安全値を書いてから mode を output/alternate に切り替え、短い反転 pulse を発生させない。

- 全 package pin を「bootloader 使用」「安全出力」「入力＋pull」「analog」「SWD/clock」のいずれかに分類し、未分類 pin を build error にする pin ownership 表を基板ごとに持つ。
- bootloader で使わない未接続 pin は analog/no-pull とし、floating digital input のまま残さない。
- SWDIO/SWCLK、発振子、電源 pin は本来の用途を維持する。
- CAN/UART pin は安全出力設定の完了後に alternate function へ切り替える。
- 通常アプリへ jump するまで PWM timer、gate driver、昇圧、キック、servo、motor/dribbler 出力を開始しない。
- 通常アプリも起動直後は同じ安全値から初期化し、全 node の自己診断と G474 の走行許可が揃うまで出力 enable しない。

基板別の最低限の安全動作は次の通りとする。正確な active level は回路図と実機で確定し、BSP の単体試験に含める。

| 基板 | ブートローダー中の必須状態 |
| --- | --- |
| G474 main | buzzer PWM停止、通常制御CAN送信停止、未使用出力を既定安全値、USART2とFDCAN1/2のみ動作 |
| F303 BLDC | TIM1/TIM8等のPWMとMOE停止、motor driverをdisable/free-wheel、PB6/PB7等の制御出力を安全値、CANのみ動作 |
| F303 sub | dribbler出力停止、servo pulse停止、PHOTO/拡張出力を安全値、CANのみ動作 |
| F303 power | `POWER_SW_EN=Low`、boost PWM停止、`KICK_1/KICK_2=Low`、gate-driver PWM停止、CANと必要なIWDGのみ動作 |

ソフトウェアリセットおよび電源投入直後は、CPU が命令を実行するまで GPIO が MCU の reset state（通常は入力/Hi-Z）になる時間を完全には除去できない。active なままでは危険な信号には外付け pull-down/pull-up、gate enable、driver 側 disable を設けることが必要である。アプリケーションブートローダーは「コード実行開始後の未定義状態」をなくすものであり、reset直後も含む絶対的な安全性はハードウェアで保証する。

## 4. Flash 配置

### 4.1 STM32F303CB/RB、128 KB

F303 は A/B の 2 イメージを置く余裕が小さいため、単一アプリスロットとする。更新開始時にアプリを無効化し、電源断後もブートローダに留まって再送を待つ。直前バージョンへの自動ロールバックは行わない。

| 領域 | アドレス | サイズ | 用途 |
| --- | --- | ---: | --- |
| Bootloader | `0x08000000`～`0x08003FFF` | 16 KB | 基板別安全IO、CAN、Flash、CRC32C、起動判定 |
| Application | `0x08004000`～`0x0801DFFF` | 104 KB | 各基板アプリ |
| Update journal | `0x0801E000`～`0x0801E7FF` | 2 KB | イメージ状態、長さ、CRC32C、version |
| Board config A | `0x0801E800`～`0x0801EFFF` | 2 KB | node ID、board type、キャリブレーション |
| Board config B | `0x0801F000`～`0x0801F7FF` | 2 KB | Board config の冗長コピー |
| Reserved | `0x0801F800`～`0x0801FFFF` | 2 KB | 将来用 |

F303 の Flash page は 2 KB 単位として扱う。Application のリンカ開始アドレスと `SCB->VTOR` は `0x08004000` に変更する。現在の F303 イメージは再配置後も 104 KB の枠内に収まる。

BLDC の既存データは `0x0801F000` から読み取れる。初回移行時は旧形式を認識して Board config A/B の新形式へ移行する。設定レコードは `magic, schema_version, generation, payload_length, payload, CRC32C` を持たせ、新しい generation の正常なコピーを採用する。FW 更新処理は Board config A/B を一切消去しない。

### 4.2 STM32G474RE、512 KB

G474 は停止不能な中断や新 FW の起動失敗から戻せるよう A/B とする。

| 領域 | アドレス | サイズ | 用途 |
| --- | --- | ---: | --- |
| Bootloader | `0x08000000`～`0x08007FFF` | 32 KB | 基板別安全IO、UART、CAN gateway、CRC32C、起動制御 |
| Slot A | `0x08008000`～`0x0803FFFF` | 224 KB | G474 アプリ A |
| Slot B | `0x08040000`～`0x08077FFF` | 224 KB | G474 アプリ B |
| Boot metadata | `0x08078000`～`0x0807FFFF` | 32 KB | 冗長 journal、試行回数、確定スロット |

ビルド時に Slot A 用と Slot B 用の 2 バイナリを生成し、同じ更新 bundle に格納する。ブートローダは非 active slot だけを消去・更新する。新スロットは `pending` として最大 3 回まで試行し、アプリが起動後 10 秒以内に self-test 合格を `CONFIRM` しなければ、直前の `confirmed` slot に戻す。

Boot metadata は最低 2 page のログ構造とし、レコードを追記してから旧レコードを失効させる。電源断により有効レコードがゼロにならない順序で更新する。

G474 の Option Bytes は dual-bank (`DBANK`) を有効にした状態を製造条件とし、起動時にも確認する。異なる bank 構成では erase page の解釈が変わるため、不一致時は更新を拒否する。Slot A と bootloader は同じ bank にあるため、Slot A の erase/program は UART の request/response 間で行い、処理中に CM4 が次データを送らないフロー制御を徹底する。

## 5. 起動条件と状態

イメージ状態は `EMPTY`, `RECEIVING`, `VERIFIED`, `PENDING`, `CONFIRMED`, `INVALID` とする。

ブートローダは次の場合にアプリへ jump せず更新待機する。

- 有効なアプリがない。
- Update journal が `RECEIVING` または `INVALID` である。
- アプリから backup register / `.noinit` RAM の boot request magic を渡された。
- G474 で pending slot の起動試行回数が上限に達した。

アプリへ jump する前に、初期 MSP が対象 SRAM 内、Reset_Handler が Application/slot 内、イメージ長が領域内、全体 CRC32C が journal と一致することを確認する。割り込みを無効化し、使用した周辺機能と SysTick を停止し、`SCB->VTOR`、MSP を切り替えてから jump する。安全出力の GPIO latch は保持し、通常アプリが周辺機能を初期化するまで出力を有効化しない。

有効な旧アプリがある状態で更新待機に入った場合は、通信が 60 秒間開始されなければ旧アプリへ戻ってよい。一度 `BEGIN_UPDATE` を受理して状態を `RECEIVING` にした後は自動復帰せず、安全出力のまま CM4 の再送を待つ。

## 6. 更新モードへの遷移

1. CM4 は制御ブリッジを停止し、`/dev/ttyS0` の排他 lock を取得する。
2. G474 アプリへ maintenance request を送る。要求には固定 magic だけでなく、乱数 challenge に対する応答と連番を含め、通常の 72 byte 制御パケットで偶然成立しない形式にする。
3. G474 アプリは走行、ドリブラ、サーボ、昇圧、キッカーを停止し、停止状態を telemetry で確認する。
4. G474 アプリは両 CAN バスへ `ENTER_BOOT` を送る。対象 F303 アプリは基板固有の `board_io_init_safe()` 相当の安全停止処理を実行して boot request を設定し、system reset する。
5. 全 F303 の bootloader HELLO を確認した後、G474 自身も boot request を設定して reset する。
6. CM4 は G474 bootloader と OFW-UART session を開始する。

各 F303 アプリには共通の `ENTER_BOOT` 処理を追加する。BLDC は PWM/MOE を無効化、sub はドリブラとサーボ出力を無効化、power は昇圧、充電、ソレノイド、電源出力を無効化してから reset する。ブートローダ自身もクロック初期化より前に安全側 GPIO 状態を設定し、通常制御出力を決して有効にしない。

アプリが完全に応答不能で、個別 reset/power-cycle 線も G474/CM4 から操作できない場合、F303 をリモートでブートローダへ入れることは保証できない。このケースまで保証するには、F303 の NRST または各基板電源を G474 から制御できるハードウェア経路が必要である。少なくとも IWDG reset が連続した場合はブートローダが一定時間待機する実装にする。

## 7. FW bundle

配布単位は `.ofw` ファイルとし、中身は manifest と非圧縮 `.bin` の tar 形式を基本とする。圧縮する場合も CM4 で展開し、MCU へは生バイナリを送る。

manifest の必須項目:

- bundle format version、製品名、リリース version、作成日時
- 各 image の `target`, `board_type`, `hw_revision_min/max`, `mcu`, `flash_base`, `size`
- image 全体の `crc32c`、アプリ version、要求 boot protocol version
- G474 の場合は slot A/B の対応関係
- 更新順序と必須/任意ターゲット

製品向けの secure boot、暗号署名、証明書、秘密鍵管理は行わない。誤ファイルと転送破損の防止を目的として、CM4、G474、更新対象 MCU の各段で target、board type、MCU、HW revision、size、Flash address、CRC32C を検査する。G474 は manifest で許可された Application/slot 以外を消去・書込しない。

通常更新では version downgrade を拒否する。復旧時は、ローカル管理者が物理的に機体へアクセスして実行する `--allow-downgrade` を用意し、監査ログへ残す。

## 8. OFW-UART プロトコル

既存の制御パケットと明確に分離するため、更新モードでは COBS framing と末尾 `0x00` delimiter を使う。複数プロセスで UART を同時に開かない。

COBS decode 後の共通 header は little endian とする。

| Field | Size | 内容 |
| --- | ---: | --- |
| magic | 4 | ASCII `OFW1` |
| protocol_version | 1 | 初期値 1 |
| message_type | 1 | 下表 |
| flags | 1 | response/error 等 |
| target_bus | 1 | 0=G474、1=CAN1、2=CAN2 |
| target_node | 1 | bus 内 node ID、broadcast は `0xFF` |
| reserved | 1 | 0 |
| sequence | 2 | request/response 対応番号 |
| payload_length | 2 | 最大 1024 byte |
| payload | variable | message 固有 |
| CRC32C | 4 | header と payload 全体 |

必須 message:

| Type | 名前 | 主な payload / 動作 |
| ---: | --- | --- |
| `0x01` | `HELLO` | protocol、bootloader version、session nonce |
| `0x02` | `INVENTORY` | bus、node、board type、UID、app/boot version、状態 |
| `0x03` | `MANIFEST` | 対象、領域、version、CRC32Cを登録・検証 |
| `0x10` | `BEGIN_UPDATE` | size、CRC32C、version。journal を先に `RECEIVING` にする |
| `0x11` | `DATA` | 32 bit offset + 最大 1016 byte。期待 offset を応答する |
| `0x12` | `END_UPDATE` | 全体 CRC32C を検証し `VERIFIED` にする |
| `0x13` | `ACTIVATE` | F303 は有効化、G474 は slot を `PENDING` にする |
| `0x14` | `STATUS` | state、受領済み offset、直近 error、image CRC32C |
| `0x15` | `ABORT` | 未完了 image を `INVALID` にする。旧 image は消さない |
| `0x16` | `REBOOT` | 指定 node または全 node を reset |
| `0x17` | `CONFIRM` | G474 アプリが pending slot の自己診断成功を通知 |

全 request に同じ sequence の ACK/NACK を返す。CM4 は 1 秒で再送、同一 sequence の再受信は副作用なく前回応答を返す。`DATA` は offset を持つため再送可能である。Flash page erase/program 中は先に BUSY を返し、完了後に期待 offset を返す。UART の window は初期実装では 1 とし、正しさを優先する。

## 9. OFW-CAN プロトコル

更新時は通常制御送信を完全停止し、次の標準 ID を専用使用する。

| CAN ID | 用途 |
| --- | --- |
| `0x600` | broadcast discovery / enter / reboot |
| `0x610 + node_id` | G474 から node への command |
| `0x620 + node_id` | G474 から node への data |
| `0x630 + node_id` | node から G474 への response/event |

同じ node ID が別バスに存在してよい。G474 は `bus + node_id` で区別する。同一バス上では node ID を重複させない。各 response は `board_type` と STM32 の 96 bit UID を分割応答でき、CM4 は manifest の期待 inventory と照合する。

通常は G474 が node ID ごとに polling し、broadcast に対する一斉応答を要求しない。未設定 node の discovery が必要な場合だけ、UID から算出した応答 slot と衝突時 backoff を使用する。

Classic CAN の 8 byte 制約に合わせ、1 block を 252 byte 以下とする。

1. `BLOCK_BEGIN`: session、32 bit offset、block length を通知する。
2. `DATA`: session、8 bit frame sequence、最大 6 byte のデータを連続送信する。
3. `BLOCK_END`: block の CRC32C を通知する。
4. node は CRC と連続 sequence を検証し、`ACK(next_offset)` または `NACK(error, expected_offset)` を返す。
5. NACK/100 ms timeout 時は block 全体を再送する。最大 5 回失敗で session を失敗扱いにする。

F303 は 1 block 全体を RAM に受け、`BLOCK_END` 後に CRC を検証してから Flash を program する。erase/program 中は G474 が次 frame を送らず、処理完了後の ACK を待つ。単一 bank Flash の書込停止時間中に CAN frame が流れて受信 FIFO があふれることを防ぐ。

CAN controller の hardware CRC と自動再送に加え、block CRC32C と image 全体の CRC32C を必須にする。1 bus につき同時に更新する node は 1 台とし、初期実装では CAN1/CAN2 も並列化せず順次更新する。

## 10. CM4 ソフトウェア

`orion-fwupd` を更新処理の単一所有者とし、CLI は daemon/API を呼ぶ。

- `orion-fwctl inventory`
- `orion-fwctl verify release.ofw`
- `orion-fwctl update release.ofw [--target ...]`
- `orion-fwctl status`
- `orion-fwctl recover release.ofw`

状態と監査ログは `/var/lib/orion-fw/` に fsync して保存する。処理中は UART lock、bundle CRC32C、各 target の状態/offset、開始/終了時刻、失敗理由を記録する。再起動後は MCU の `STATUS` と CM4 journal を照合し、同じ bundle なら続きから再開する。

Web 管理から使う場合は既存 FastAPI に inventory、upload、start、progress、cancel の endpoint を追加してよい。ただし更新開始は認証済み管理操作とし、走行制御 API から直接開始しない。FW ファイル名を shell command に連結しない。

## 11. 更新手順

1. bundle の形式、image CRC32C、対象 MCU、board type、HW revision、Flash範囲を CM4 で検証する。
2. バッテリ/電源状態、コンデンサ電圧、機体停止、通信品質を preflight する。閾値は電源担当と合意して設定ファイルへ固定する。
3. 制御プロセスを止め、全アクチュエータを安全状態にして全 MCU を bootloader へ移す。
4. inventory を取得し、期待する 5 MCU、bus、node、board type、UID を照合する。
5. `sub` → `motor-can1` → `motor-can2` → `power` の順に 1 台ずつ更新・全体 CRC32C 検証する。電源基板は F303 の最後にする。
6. G474 の非 active slot を更新・検証し、pending にする。
7. F303 を activate/reboot し、最後に G474 を reboot する。
8. G474 アプリは安全出力のまま各 node の version と heartbeat を確認し、自己診断合格後に G474 slot を confirm する。
9. CM4 は全 target の version と診断結果を保存してから制御プロセスを再開する。必須 node が 1 台でも不一致なら走行許可しない。

## 12. エラー時の動作

| 事象 | 動作 |
| --- | --- |
| UART/CAN timeout | 同一 sequence/block を再送。上限後は安全停止のまま中断 |
| CM4 電源断/再起動 | journal と target offset を照合して resume |
| F303 更新中の電源断 | アプリ無効のため bootloader 待機。最初または報告 offset から再送 |
| G474 更新中の電源断 | active slot は未変更。旧 confirmed slot を起動 |
| G474 新 FW が起動不能 | 3 回失敗後に旧 confirmed slot へ rollback |
| image CRC32C 不一致 | activate 禁止、対象を `INVALID` にして再転送 |
| target/HW 不一致 | erase 前に拒否 |
| CAN bus-off | 再初期化を試行後、失敗を CM4 へ通知。自動的に別 bus へ送らない |
| 更新 cancel | 書込開始済み F303 は bootloader に残す。旧アプリへ見せかけて戻さない |

## 13. 初回導入

OTA 機能導入前の FW は再配置されていないため、初回だけ全基板への SWD 作業が必要である。

1. 共通 boot protocol と基板別 `board_io_init_safe()` を実装する。
2. G474/F303 の bootloader project とアプリ用 linker script を追加する。
3. vector table、割り込み、backup register、IWDG、Flash WRP を確認する。
   - G474 は Option Bytes の dual-bank (`DBANK`) が有効であることも確認する。
   - bootloader 起動時から動作する IWDG 構成では、erase、program、全体 CRC 計算中も安全に refresh する。
4. SWD で bootloader、再配置済みアプリ、初期 metadata/config を書く。
5. BLDC の既存 CAN ID/キャリブレーションを読み出して移行し、更新後も一致することを確認する。
6. bootloader version と STM32 UID を製造記録へ残す。

bootloader 自身の OTA 更新は初期スコープ外とする。bootloader に欠陥がある場合は SWD で更新する。

### 13.1 既存FWを含む開発対象

| 対象 | 主な変更 |
| --- | --- |
| Orion_CM4 | `orion-fwupd`、CLI/API、UART排他、bundle生成/検査、再開journal、既存制御bridgeの停止連携 |
| G474 bootloader | main基板の全IO安全初期化、OFW-UART、FDCAN1/2 gateway、A/B起動、CRC/範囲検査 |
| `G474_Orion_main` | maintenance遷移、全出力停止、F303のboot移行、A/B linker、VTOR、version/CONFIRM/診断通知 |
| BLDC bootloader | PWM/MOE/driver安全化、OFW-CAN、単一slot更新、CAN ID/校正領域保護 |
| `Orion_F303_BLDC` | `ENTER_BOOT`、安全停止、linker/VTOR変更、旧Flash設定移行、version応答 |
| sub bootloader | dribbler/servo/拡張IO安全化、OFW-CAN、単一slot更新 |
| `Orion_F303_sub` | `ENTER_BOOT`、安全停止、linker/VTOR変更、version応答 |
| power bootloader | boost/kick/power/gate出力安全化、OFW-CAN、IWDG、単一slot更新 |
| `F303_boost` | `ENTER_BOOT`、高電圧系安全停止、linker/VTOR変更、version応答 |
| build/CI | 全boot/app build、領域サイズ検査、Slot A/B生成、manifestと`.ofw`生成、protocol単体試験 |

各基板の `board_io_table.c` では port ごとに用途 mask を定義し、mask の重複がないことと対象GPIOがすべて分類済みであることを `_Static_assert` する。回路図に基づく安全levelと、現在の `.ioc`/`gpio.c` の初期値との差分をレビュー項目にする。

## 14. 受入試験

- 5 MCU の inventory、個別更新、一括更新、同一 version の再適用ができる。
- 各 Flash page の erase/program、CAN block 転送、全体CRC検証、activate、初回起動の各時点で電源を切り、復電後に復旧または rollback できる。
- 1 bit 改変した UART frame、CAN block、image、manifestをそれぞれCRC/整合検査で拒否する。
- 別 board type、別 HW revision、領域超過 image、無効な vector table を erase 前または activate 前に拒否する。
- CAN1/CAN2 に同じ node ID があっても誤更新せず、同一 bus の node ID 重複は preflight で拒否する。
- BLDC の CAN ID と全キャリブレーション値が更新前後で一致する。
- 更新モード中と異常中に PWM、昇圧、キッカー、モータ、ドリブラ、サーボが有効にならないことを実測する。
- G474 pending FW を意図的に HardFault/IWDG reset させ、3 回以内に旧 slot へ戻る。
- CM4 daemon kill、CM4 reboot、UART 抜線、CAN bus-off 後に再開できる。
- Debug/Release の最大 image size を CI で検査し、領域を 1 byte でも超えた build を失敗させる。

## 15. 実装前に確定する項目

- `sub` と `power` の実際の CAN bus 配置、および全 node ID
- CM4/G474 から F303 の reset または電源再投入が可能か
- 基板ごとの HW revision の取得方法
- update を許可する最低入力電圧と安全なコンデンサ電圧
- G474 bootloader 32 KB、F303 bootloader 16 KB に実装が収まることの試作 build 確認
