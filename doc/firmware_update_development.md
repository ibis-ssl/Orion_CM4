# STM32 FW更新機能 開発・実機試験手順

## 1. 目的

FW更新仕様を一度に全基板へ実装せず、最初は次の2台だけでG474のアプリケーションブートローダー、A/B更新、CM4更新クライアントを成立させる。

- ST-Link接続済みのSTM32G474 Main基板
- SSH接続済みのCM4 `CM4_108`（`ibis@192.168.20.108`）

Mainでprotocol、Flash journal、A/B起動、UART排他、安全IOの方式を固めた後、同じ考え方をCAN配下のF303へ展開する。

## 2. 2026-08-24 実機確認結果

確認は読み取りとビルドだけを行った。Flash書込み、Option Bytes変更、reset、サービス停止、UART送信は実施していない。

### 2.1 Main / ST-Link

- `STLINK-V3MINIE`を認識
- ST-Link serial: `002D00373033510635393935`
- target voltage: 3.27 V
- device: STM32G47x/G48x/G414、Device ID `0x469`、Revision X
- NVM: 512 KB
- HOTPLUG接続に成功
- RDP Level 0
- `DBANK=1`、dual-bank mode有効
- `BFB2=0`、hardware bank swapは無効
- Bank 1/2ともWRP/PCROPは未設定
- IWDG/WWDGはsoftware mode
- 現行`G474_Orion_main` Debug build成功
  - text 73,772 byte
  - data 756 byte
  - bss 8,024 byte
  - `.bin` 74,536 byte

現状はA/B配置の前提を満たしている。`BFB2`によるbank swapは使用せず、ブートローダーが選択したslotへ`SCB->VTOR`とMSPを設定してjumpする。

### 2.2 CM4_108

- SSH alias `CM4_108`は`ibis@192.168.20.108:22`
- OS: Linux 6.12.75+rpt-rpi-v8、aarch64
- `/dev/serial0 -> /dev/ttyS0`
- `/dev/ttyS0`は`root:dialout`、`ibis`は`dialout` group所属
- `enable_uart=1`
- kernel cmdlineにserial consoleなし
- `serial-getty@ttyS0.service`はdisabled/inactive
- `control_server.service`はenabled/active
- `GET /status`は`{"running":false}`
- `ai_cmd_v2.out`と`robot_feedback.out`は停止中で、`/dev/ttyS0`のownerなし
- CM4上の`/home/ibis/Orion_CM4`には既存の未commit変更がある
  - `cm4/setup.sh`変更済み
  - `cm4_cam/`未追跡

CM4上の既存worktreeは上書き、reset、clean、pullしない。初期試験物は`/home/ibis/orion-fw-dev/`へ分離配置する。

## 3. 開発中の安全原則

- Main以外のCAN node、モータ、昇圧、キッカーが動作しない試験構成を確認してから書き込む。接続されたままなら電力段を無効化し、CAN通常指令を送らない。
- ST-LinkによるFlash書込み、target reset、Option Bytes変更、CM4サービス停止は状態変更操作として、実行直前に対象と復旧方法を確認する。
- 開発中はRDP Level 0、WRPなしを維持する。bootloader保護は更新経路とSWD復旧試験が完了した最後に設定する。
- 現行Flash 512 KBとOption Bytes表示結果を退避してから最初の書込みを行う。
- UART updaterは単一processだけが所有する。`control_server`自体は稼働してよいが、`/start`で起動する2つのbridgeとupdaterを同時に動かさない。
- `/run/lock/orion-uart.lock`を全UART利用processの共通lockとする。既存bridgeがlock対応するまでは、`/status == false`と`fuser /dev/ttyS0`の両方を確認する。
- timeout、CRC error、想定外resetではアクチュエータを有効化せず、ST-Linkで復旧できる状態を保つ。

## 4. ソースとbuild構成

Main検証段階では次の成果物を作る。

| 成果物 | 内容 |
| --- | --- |
| `G474_Orion_bootloader.elf/.hex` | `0x08000000`、最大32 KB、安全IO、OFW-UART、A/B起動 |
| `G474_Orion_main_slot_a.elf/.bin` | `0x08008000`、最大224 KB |
| `G474_Orion_main_slot_b.elf/.bin` | `0x08040000`、最大224 KB |
| `orion_fw_client.py` | CM4上の開発用単体CLI。HELLO、STATUS、DATA、ACTIVATE |
| Main用`.ofw` | manifest、slot A/B image、CRC32C |

bootloaderは通常アプリと別のSTM32CubeIDE projectにする。Main固有の`board_io_init_safe()`、USART2、FDCAN1/2初期化を持つ。通常アプリは共通ソースからSlot A/B用の2 linker設定を使って2回linkする。

各Cファイル冒頭に目的と責務を記述する。CubeMX生成ファイルを変更する場合は`USER CODE`領域を使い、linker script、startup、`system_stm32g4xx.c`の変更理由を文書化する。

## 5. 開発マイルストーン

### M0: 現行状態の固定と復旧準備

目的は、以後の試験で失敗してもST-Linkから現在状態へ戻せるようにすることである。

1. Mainの回路図と`.ioc`から全pin ownership表を作る。
2. 各出力のactive levelとboot中の安全levelを実機配線と照合する。
3. 現行Debug/Releaseをbuildし、ELF、BIN、MAPを保存する。
4. ST-LinkでFlash全体を`main_flash_before_bootloader.bin`へread backする。
5. Option Bytes表示をテキスト保存する。
6. backupのサイズが524,288 byteであることを確認し、CRC32Cを記録する。
7. 現行ELFを再書込みできる既存`Script/flash.ps1`とProgrammer CLIの場所を確認する。

読み取り例:

```powershell
$programmer = "C:\ST\STM32CubeCLT_1.21.0\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe"
& $programmer -l stlink
& $programmer -c "port=SWD mode=HOTPLUG" -ob displ
& $programmer -c "port=SWD mode=HOTPLUG" -r8 main_flash_before_bootloader.bin 0x08000000 0x80000
```

完了条件:

- Flash backup、Option Bytes、現行ELFから復旧できる手順を別の開発者が再現できる。
- Mainの全GPIOが分類され、安全levelの未確定項目が一覧化されている。

### M1: 安全IOとSlot A jumpをST-Linkだけで成立させる

最初のbootloaderはFlash更新機能を持たせず、次だけを実装する。

1. reset直後にレジスタ直接操作の`board_io_init_safe()`を実行する。
2. LEDでbootloader起動を識別できるようにする。
3. Slot Aのvector、size、CRC32Cを検証する。
4. 有効ならSlot Aへjumpし、無効なら安全状態のまま待機する。
5. USART2/CAN/PWMはまだ開始しない。

通常アプリを`0x08008000`へ再配置し、`SCB->VTOR`、linker symbols、vector内Reset_Handlerを確認する。bootloaderとSlot AアプリをST-Linkで書き込み、次を試験する。

- power-on reset、NRST、software resetから通常アプリが起動する。
- Slot Aを壊すと通常アプリへjumpせず、bootloaderで安全待機する。
- bootloader中にbuzzerや未使用GPIOがpulseを出さない。
- 通常アプリ起動までCAN通常指令を送信しない。
- ST-Linkから現行FWまたはbootloader＋Slot Aを書き戻せる。

最初の書込みではWRPを設定しない。bootloaderだけを書いてSlot Aが空の時間を作らないよう、同一作業でbootloaderとSlot Aの両方を書いてverifyする。

完了条件:

- UARTやCM4なしで100回以上のreset/jumpを再現できる。
- oscilloscope/logic analyzerで危険出力に起動pulseがないことを確認できる。

### M2: CM4との読み取り専用OFW-UART

bootloaderへUSART2 1 Mbps、COBS、CRC32Cを追加する。この段階ではFlash erase/program commandを実装しない。

実装するcommand:

- `HELLO`
- `INVENTORY`
- `STATUS`
- `REBOOT_TO_APP`

CM4にはservice化前の`orion_fw_client.py`を`/home/ibis/orion-fw-dev/`へcopyし、SSH terminalから実行する。

実行前確認:

```bash
curl -sS http://192.168.20.108:8000/status
fuser -v /dev/ttyS0
ls -l /dev/serial0 /dev/ttyS0
```

`running:false`かつownerなしの場合だけUARTをopenする。開発clientは終了時にtermiosをcloseし、例外時にもlockを解放する。

試験内容:

- bootloader version、STM32 UID、active slot、slot状態をCM4から取得する。
- CRC不正、途中frame、連続delimiter後に次の正常frameへ復帰する。
- client killとSSH切断後にUARTを再openできる。
- 既存72 byte制御packetで更新commandが成立しない。

完了条件:

- Flashを変更せず、CM4_108から1,000回のrequest/responseをerrorなく完了する。
- UARTを失った場合もbootloaderが安全状態を維持する。

### M3: 非active Slot Bへの転送

Slot Aをactiveのまま、CM4からSlot Bへだけ書き込む。

1. `MANIFEST`でMCU、board type、base address、size、CRC32Cを検証する。
2. `BEGIN_UPDATE`でSlot Bを`RECEIVING`にする。
3. page単位でeraseし、offset付き`DATA`をwriteする。
4. `END_UPDATE`でFlash上の全体CRC32Cを計算する。
5. `VERIFIED`にはするが、最初は`ACTIVATE`しない。
6. ST-LinkでSlot Bをread backし、送信BINとbyte比較する。

同じoffsetの再送、順序違い、CRC error、size超過、Slot A/bootloader/metadata範囲へのwriteを拒否する試験を行う。Slot Aが動作中であっても、更新は必ずbootloaderへresetしてから実行し、通常アプリからFlashを書かない。

完了条件:

- 10回連続でSlot Bの転送、CRC、ST-Link read back比較が一致する。
- 不正address指定でFlash内容が変化しない。

### M4: pending、confirm、rollback

1. verified Slot Bを`pending`にしてresetする。
2. Slot B通常アプリは全出力を安全状態にしたままself-testする。
3. USART2、FDCAN初期化、Main内部診断に合格したら10秒以内に`CONFIRM`する。
4. confirmしない試験FW、HardFault、IWDG resetを使い、3回以内にSlot Aへrollbackする。
5. Slot B confirmed後、同じ手順でSlot Aをinactive更新する。

Slot A更新時はbootloaderとSlot Aが同じFlash bankにある。1 packetをRAMへ受信して応答を止めてからerase/programし、完了後にACKする。Flash停止中にUART dataを連続送信しない。

完了条件:

- A→B、B→Aの両方向更新ができる。
- 起動不能FWから旧confirmed slotへ自動復帰する。
- metadata書換え中のresetでもconfirmed slot情報を失わない。

### M5: CM4 updater統合

単体clientで安定した後に`orion-fwupd`、CLI、既存launcher連携を実装する。

- 更新開始時に既存bridgeを停止し、停止完了とUART ownerなしを確認する。
- updaterと両bridgeを同じUART lockへ対応させる。
- `/var/lib/orion-fw/`へbundle CRC、offset、状態をfsyncする。
- CM4 reboot後にG474の`STATUS`とjournalを照合してresumeする。
- updater終了後、Mainのversion/self-testを確認してからbridgeを再開する。

CM4_108の既存worktreeに未commit変更があるため、開発中は次を守る。

- `git reset`, `git clean`, checkoutによる上書きをしない。
- 初期prototypeは`/home/ibis/orion-fw-dev/`へ配置する。
- repoへ統合する際は`cm4/setup.sh`の既存差分を先に確認し、必要箇所だけmergeする。

完了条件:

- SSH切断、client kill、CM4 rebootから更新を再開できる。
- updaterと通常bridgeが同時にUARTをopenできない。
- update失敗時に通常制御を自動再開せず、明確なerrorを返す。

### M6: bootloader保護とMain段階完了

全試験と復旧を完了してから、bootloader領域だけにWRPを設定する。RDP Level 0は維持する。WRP解除にはOption Bytes変更とmass eraseの可能性があるため、設定前に対象pageと復旧手順を再確認する。

Main段階の完了条件:

- Main単独のA/B更新、resume、rollback、安全IOが成立する。
- ST-Linkを使う初回導入と復旧の両方が手順化されている。
- CM4_108からCLI/APIでversionと進捗を確認できる。
- protocol trace、Flash layout、IO表、試験結果がdocumentに残っている。

### M7: F303/CAN展開

Mainで確定したjournal、CRC、状態遷移をF303共通coreへ移植する。展開順はBLDC 1台、sub、power、2本目のBLDCとする。各段階でMainのCAN gatewayと対象1台だけを接続して試験し、最後に5 MCU一括更新を行う。

F303は単一slotのため、G474より先に電源断resumeを重点試験する。power基板は高電圧部の安全確認後、F303の最後に着手する。

## 6. 毎回の実機試験チェックリスト

### 試験前

- 対象commit、bootloader/app version、書込対象addressを記録した。
- MainへのST-Link接続とtarget voltageが正常。
- Main以外の電力段が動作しない構成。
- CM4 `/status`が`running:false`。
- `/dev/ttyS0` ownerなし。
- Flash backupと復旧用ELFがある。
- erase範囲がbootloader、active slot、metadataと重ならない。

### 試験後

- 起動slot、version、CRC、confirm状態を記録した。
- UART/CAN error counterとreset causeを回収した。
- GPIO/PWM/電源出力が安全状態だった。
- CM4のUART lockと一時processが残っていない。
- 失敗時は制御bridgeを再開せず、原因とFlash状態を記録した。

## 7. 次に着手する実装単位

最初の実装PR/commitはM1だけに限定する。

1. G474 Mainの全pin ownership/safe-level表
2. G474 bootloader最小project
3. `board_io_init_safe()`
4. Slot A用linker/startup変更
5. vector/CRC検証とSlot A jump
6. build size checkとST-Link初回導入script

OFW-UART、Flash更新、CM4 clientはM1の安全IOとjumpが実機で合格した後の別変更とする。

