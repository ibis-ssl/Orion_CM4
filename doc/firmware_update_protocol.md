# MCUファームウェア同時更新仕様

## 実装状況

2026-08-25時点で、CM4→Main→Subについて本仕様のv2転送を実装し、実機確認済みである。BLDC、電源基板と2バス同時スケジューラは次段階とする。

- CM4: `cm4/firmware/sub_can_updater_v2.py`
- Main: `Core/Src/fw_update_gateway.c`
- Sub bootloader: `Bootloader/Src/boot_can_update.c`
- UART payload最大907 byte、FW chunk最大896 byte
- CAN dataは128 IDをsequenceとして使用し、各frameで7 byteを運ぶ
- Subは896 byte buffer、128 bit bitmap、32 frame software FIFOを持つ

## 目的

CM4からMain（STM32G474）を経由し、2本のClassic CAN（各1 Mbps）に接続されたSub、2台のBLDC、電源基板を更新する。製品用途ではないため署名検証は行わないが、転送破損をCRC32Cで検出し、不完全なアプリケーションは起動しない。

「同時更新」は、全対象を先に安全な更新状態へ移行し、2本のCANを並行動作させることを指す。同じバス上で異なるイメージを物理的に同時送信することはできないため、その場合はMainが時分割する。同一イメージを使用する左右BLDCは、CAN1/CAN2へ同じフレームを同時投入する。Main自身はCANゲートウェイを担当するため、全CANノードの確定後に最後に更新する。

## 現行方式の問題

- 7 byteのFWデータを送るたびに72 byteのCM4コマンドを送っており、UART上で約10倍に膨張する。
- 112 byteごとにACKを待つため、65,168 byteのSub FWで約582往復が発生する。Linux UARTの読み出し待ち時間も毎回加算され、実測は約103秒だった。
- アプリケーションからブートローダーへの遷移を固定待ち時間だけで扱い、対象が準備完了したか確認していない。実測では1回目成功後、次の更新開始時のHELLOがタイムアウトした。
- MainはCAN送信FIFO満杯時に最大20フレームを保持するが、満杯時は破棄する。更新データでは破棄を許容せず、送信側へback pressureを掛ける必要がある。
- ACKを全ノードが同時送信するとCAN arbitration上の衝突や識別不能が起きるため、ブロードキャスト後の応答方法を規定する必要がある。

## 更新セットとノード識別

CM4は1回の更新を`update_id`（32 bit乱数）で識別し、次のmanifestをMainへ渡す。

- image ID、対象機種、サイズ、CRC32C
- 必須ノードの`bus + node_id`
- 同じimageを共有するノードのbus mask
- アプリケーション先頭、最大サイズ、metadata/config領域

ノード応答は`bus + node_id`で一意にする。BLDCの左右設定値やCAN IDはアプリ領域とは別のconfig pageに残し、更新対象に含めない。Subと電源は固定node ID、BLDCはconfig pageのboard IDからnode IDを決定する。電源基板が両バスへ物理接続されている場合も、manifestで指定したprimary busだけを更新経路にする。

## CM4－Main間

通常制御用72 byteパケットへCANフレームを1枚ずつ格納する方式は廃止し、更新中だけ可変長バイナリフレームへ切り替える。

フレームは`magic、protocol version、message type、sequence、payload length、header CRC、payload、CRC32C`で構成し、最大payloadを1024 byteとする。受信側はmagicを探索して再同期し、長さ上限とCRCを満たさないフレームを破棄する。

主なmessage typeは次のとおり。

- `DISCOVER`: Mainが両CANバスのノードを個別pollし、機種、node ID、bootloader version、アプリ状態を返す。
- `BEGIN_SET`: manifestを登録し、全対象を更新状態へ移行する。
- `IMAGE_CHUNK`: image ID、offset、最大896 byteのデータ、chunk CRC32CをMainへ渡す。
- `CHUNK_RESULT`: 全対象のcommit offsetと再送要否を返す。CM4はこの応答を受けるまで次chunkを破棄可能な状態にしない。
- `FINALIZE`: 全体CRC32Cを検証し、全対象をconfirmedにする。
- `STATUS/ABORT/REBOOT`: 再接続、明示中断、再起動に使用する。

sequenceが同じ正常フレームは同じ結果を返す。期待sequenceより先のフレームは受理せず、Mainが期待sequenceを返す。これによりUARTの欠落、重複、再送で処理が二重に進まない。

## Main－CANノード間

### 状態遷移

1. Mainは両バスへ更新移行要求を複数回送る。
2. 固定時間待ちにはせず、必須ノードを1台ずつpollして`BOOT_READY`を確認する。
3. `BEGIN`でmetadataを無効化する。各ノードは自身のFlashを並行消去し、Mainは`ERASING`から`READY`になるまでpollする。
4. Mainは受信したchunkをCAN1/CAN2へ並列送信する。
5. 全フレーム送信完了後、各ノードを順番にpollする。欠落があればchunk全体を再送する。
6. 全ノードがchunk CRC32Cを確認してFlashへ書き、commit offsetを返してから次chunkへ進む。
7. 最後に全体CRC32Cとvector tableを検証し、metadataをconfirmedへ変更する。
8. 全必須ノードのconfirmedを確認してから一斉再起動する。

### データchunk

- 1 chunkは最大896 byte（128 frame × 7 byte）。F303側は約1 KBの固定バッファと128 bit受信bitmapを持つ。
- CAN IDに0～127のsequenceを含め、payload byte 0にchunk token、byte 1～7にデータを格納する。
- 順不同到着はsequence位置へ格納し、重複はbitmapで無視する。
- chunk tokenが現在値と違うデータは破棄する。
- CANハードウェアFIFOは空になるまで一度にdrainし、32 frame以上のソフトウェアリングFIFOへ移す。ハードウェアoverrunまたはソフトウェアoverflow時はchunkを失敗扱いにする。
- Flash消去・書込み中は次chunkを送らない。これによりFlash busy中のCAN FIFO溢れを防ぐ。

### 応答衝突防止

ノードからの自発送信ACKは禁止する。Mainが`bus + node_id`を1台ずつpollし、指定されたノードだけが固有response IDで応答する。コマンド再送は同じ`update_id、image ID、offset`なら冪等に処理する。

## エラーパターンと回復

| パターン | 検出 | 回復 |
| --- | --- | --- |
| UART byte欠落・追加・化け | magic、length、header CRC、frame CRC32C | parserを再同期し、同じsequenceを再送 |
| UARTフレーム重複 | sequenceとupdate ID | 前回結果を再応答し、二重commitしない |
| UARTタイムアウト | CM4側deadline | STATUSで期待sequence/commit offsetを取得して再開 |
| CANデータ欠落 | chunk受信bitmap、CAN FIFO overrun flag | 当該chunkを全再送 |
| CANデータ重複・順序ずれ | chunk token、sequence bitmap | 正しい位置へ1回だけ格納 |
| CANビット化け | CAN controller CRC/error、chunk CRC32C | frame欠落またはchunk CRCエラーとして再送 |
| Main TX FIFO満杯 | HAL free levelと更新専用queue水位 | 破棄せず送信を停止し、空き通知後に再開 |
| 複数ノードACK競合 | poll対象と固有response ID | 1台ずつpollし、応答slotを重ねない |
| コマンド/ACK欠落 | timeoutと状態poll | 同一コマンドを冪等再送 |
| Main再起動 | CM4のUART再接続、update ID不一致 | DISCOVER/STATUSから未確定chunkを再送。必要なら更新セットを再開始 |
| CANノード再起動 | node state/commit offsetの不一致 | 当該ノードだけ先頭から再更新。他ノードのconfirmedは維持 |
| 古い遅延データ | update ID、image ID、chunk token | 現在のcontextと不一致なら破棄 |
| 全体CRC不一致 | FINALIZE時CRC32C | metadataを無効のまま保持し、対象ノードを先頭から再更新 |

ハードウェア故障、断線、Flash寿命、電源断そのものの保証は対象外とする。ただし通信が回復しない場合に成功扱いにはせず、必須ノード名と最後の状態を返して停止する。

## 速度目標

Subの65,168 byteを例にすると、1 Mbps Classic CANで7 byte/frameを送る線上時間は概算1.2～1.5秒、1 Mbps UARTで生データを送る時間は約0.7秒である。これにFlash消去、書込み、pollを加え、単一imageを共有するノード群は5～10秒以内を初期目標とする。

2本のCANは並行送信する。左右BLDCが同一imageなら転送時間は1台分相当である。Sub、BLDC、電源のimageが異なる場合、全ノードは同時に更新状態へ入るが、同じ物理バス上のデータはMainが時分割する。全体所要時間は最大image 1個分ではなく、各バスへ送る異なるimageサイズの合計で決まる。

## 試験方針

通常の連続更新試験より先に、CM4 updaterへ故障注入オプションを設ける。

- UARTフレームを一定確率で破棄、重複、CRC破損する。
- CAN data sequenceをMain側で1枚破棄、重複、入れ替えする。
- chunk境界、消去中、commit応答直後に通信を中断して再接続する。
- Subを含む各nodeについて、FIFO overrunを意図的に発生させてchunk再送を確認する。
- 成功時はimage全体CRC、metadata、VTOR、アプリUART/CAN出力を確認する。

これらが通った後に、CM4→Main→全CANノードの10回連続更新を実施する。

## Sub実機試験結果

対象imageは65,168 byte、CRC32C `0xF692FBA9`。旧112-byte方式の実測約103秒に対し、v2の正常更新は8.287～10.437秒だった。

- UART CRC破損: timeout後に同一sequenceを再送して成功
- CAN frame欠落: bitmap未完了を検出し、chunk全体を再配信して成功
- CAN frame重複: bitmapにより二重格納せず成功
- CAN frame逆順: CAN ID sequence位置へ格納して成功
- CAN payload破損: chunk CRC32C不一致を検出し、再配信して成功
- 複合注入（上記すべて）: 14.186秒で全体CRC一致
- 単独payload破損: 11.083秒で全体CRC一致
- 重複＋逆順: 8.287秒で全体CRC一致
- ST-Link readback: 65,168 byte、CRC32C `0xF692FBA9`
- 更新後VTOR: `0x08004000`
- COM167 USART1 2 Mbps: 2秒で2,646 byte、状態ログとCAN受信カウンタ更新を確認
