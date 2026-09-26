# PC→CM4→G474 入出力タイミング実機調査（2026-09-20）

## 結論

今回のmode3の約102 ms周期の集中は、ai_cmd_v2やSTM32で初めて発生するものではない。
PCの有線インターフェースでは約15 ms間隔で送出されているが、CM4のカーネル到着時点で既に集中していた。
ブロードキャスト送信で再現し、同じ送信器を108宛てユニキャストにすると本試験では解消した。
CM4内部送信でも再現しなかった。CM4のWi-Fi省電力は全試験でoff。

原因範囲はPCのキャプチャ地点からCM4カーネル受信までのネットワーク配送経路。
約102.4 msの周期、beacon interval=100、DTIM=1、宛先による差から、APのグループ宛て配送待ちが有力。
ただしAPの無線送出とCM4の無線FW/driverの境界は直接観測していないため、AP内部の原因まで断定しない。
省電力端末の存在によるグループ配送の保留は[RFC 9119 §4.3](https://www.rfc-editor.org/rfc/rfc9119.html#section-4.3)とも整合する。

## 条件と測定方法

- PC: 192.168.20.11、有線ASIXアダプター。Wi-Fiアダプターは未接続。
- CM4_108: 192.168.20.108、wlan0、SSID SSL_ibis、BSSID 7a:27:f5:f2:c7:eb、5500 MHz、DTIM=1、beacon=100。
- PC送信: 715 byte、15 ms周期（66.6667 Hz）、対象ID8のみ。STOP・速度/キック/ドリブルゼロ。
- 危険フィールドをゼロにし、ユーザーから物理的安全状態の確認を受けて実施。STOPだけで全アクチュエータを無効化できるとは扱わない。
- Qt送信器候補のソースは15 ms、192.168.20.255宛て固定、毎回socket生成。実行中のQtプロセスは確認されなかったため、今回は新規CLIで送信を自動化した。Qt UI自体は操作・変更していない。
- 固定socketと毎回socket生成の両方でbroadcast集中/unicast改善を確認。socket再生成だけでは今回の周期的集中を説明できない。
- 通常カメラとrobot_feedbackを動かしたまま、ai_cmd_v2のみ診断ビルドへ一時置換して測定。
- PC: send呼出し前後のperf_counter_nsと、有線IFのdumpcapパケットキャプチャ。
- CM4: AF_PACKET受動キャプチャのkernel timestamp、本体recvmsgのSO_TIMESTAMPNS、読出し直後とwrite前後のmonotonic時刻。受動キャプチャは通常UDPを奪わない。
- G474: 10秒間のDWT記録（170 MHz）。72 byte正常フレーム完成と500 Hz制御採用を別配列へ保存。TPRB＋32bit連番で対応付け。
- STM32記録容量に合わせ本体同時計測は10秒単位。mode3 broadcastは3回以上、unicastは複数回、内部送信も比較。別途ネットワークのみ30秒試験も実施した。
- 装置間の時計は直接減算していない。CM4時計は8月29日を示しており、PCと同期していない。同一装置内の遅延と連番対応した間隔を評価した。

## 最終検証データ（mode3）

下表は `runtime/timing-20260920/verified-*` の代表値。単位ms。
STM32値は計測終了後にST-Link HOTPLUGで読み出した固定記録配列から算出した。

| 条件 | PC送信間隔 p50 | CM4カーネル到着間隔 p50 / p99 / max | STM32受信間隔 p50 / p99 / max |
| --- | ---: | ---: | ---: |
| broadcast | 15.010 | 1.136 / 96.808 / 198.247 | 1.157 / 97.263 / 198.403 |
| 108 unicast | 15.001 | 15.000 / 16.244 / 16.528 | 15.225 / 16.377 / 17.150 |
| CM4 localhost | 14.999 | 14.998 / 15.043 / 15.280 | 15.231 / 15.708 / 16.092 |

PCの有線キャプチャでもbroadcastは667件、送出間隔p50=15.013/p99=15.909/max=16.372 ms。
したがって「send呼出しだけ等間隔で、PCの送出段階で100 ms分まとめていた」という説明とも一致しない。

| 処理区間 | broadcast p99 | unicast p99 | localhost p99 |
| --- | ---: | ---: | ---: |
| CM4 kernel到着→アプリ読出し | 1.139 | 1.095 | 1.072 |
| アプリ読出し→UART write開始 | 0.027 | 0.040 | 0.014 |
| UART write呼出し所要 | 0.049 | 0.053 | 0.048 |
| STM32正常受信→制御採用 | 1.881 | 1.997 | 1.985 |

write呼出しの終了は線上送信完了ではない。STM32側の実受信記録も同時に比較して判断した。
unicastの読出し→writeは一過性max=1.560 msがあったが、約100 msの滞留は説明しない。

### 件数と間引き

- verified-broadcast: PC667→CM4 kernel/アプリ654→UART634。受信キューの最新値採用で20件を意図的に上書き。
- STM32記録窓内621受信→406制御採用。215件は次の制御tickより前に新しい指令で置き換わった。説明不能な制御採用欠落は0。
- verified-unicast: PC667→CM4/UART667。STM32記録窓内656受信→656採用。
- verified-local: 667送信→CM4/UART667。STM32記録窓内632受信→632採用。
- STM32窓はy受付から10秒、送信開始はその後である。末尾の13/11/35件など窓外の差をUART欠落と数えない。
- 最終5条件でchecksum/PE/FE/NE/ORE、診断配列overflow、apply_raceはいずれも0。CM4 socket dropとAF_PACKET capture dropも0。
- broadcastのPC→CM4件数差はキャプチャ／socket dropでは説明されず、配送経路での欠落と整合する。

## mode4の確認

STOP・ゼロ出力でmode4も比較した。入力は66.6667 Hz、CM4のUART出力設定は100 Hz。

| 条件 | CM4 kernel入力 p50 / p99 / max | STM32 UART受信 p50 / p99 / max |
| --- | ---: | ---: |
| broadcast | 1.143 / 97.919 / 198.141 | 9.803 / 10.968 / 11.259 |
| unicast | 15.008 / 16.190 / 17.265 | 9.841 / 10.954 / 10.975 |

mode4ではブリッジの周期送信が働くため、入力集中がそのままUART集中にはならなかった。
ただし新しい目標が届くタイミングはbroadcastの配送遅延の影響を受ける。
これは停止状態の経路試験であり、走行時の位置制御・目標追従・feedback変動の全問題を否定する試験ではない。
mode4は1入力→複数UART出力なので、mode3解析CLIの一対一連番照合は使用しない。

## ログの信頼性と除外試験

- COM56の2 Mbpsログに文字欠落があり、記録数・event連番・時計単調性の検査で検出した。UART制御指令の受信エラーとは別問題。
- 計測後dumpを1 ms間隔にしても少数の欠落が残ったため、最終5条件は測定終了後にST-LinkからSRAM配列を読み出して照合した。CPUのhalt/resetや測定中のSWD読出しは行っていない。
- 最終値の根拠は `stm32_sram.bin` と、それを復号した `stm32_sram.log`。COM原本 `stm32.log` は変更せず保存。
- 読出しアドレスはbuild 1789867492のELFから取得。0x2000174cから0x8d18 byte。別ビルドには流用不可。
- 全最終データでSRAMの件数・event連番・cycle単調性を検証済み。
- `actual-unicast-r3`、初回mode4などの破損COMデータはSTM32の時間評価に使用しない。
- `actual-unicast-r3-paced` はcapture件数0、`actual-local-r2` は完了ログなしで不採用。試験間の6秒無通信自己reset／初期化／診断状態との干渉が疑われるが、原因は断定しない。最終試験は試験間待機を追加し、非ゼロ件数と終了状態を確認した。
- 未使用ポートでの初期unicast10秒試験では一過性の遅延も観測した。その後の30秒試験および実UART本試験では継続的な102 ms集中は再現しなかった。長期の無線遅延上限を保証する結果ではない。

## 追加実装・再解析

- `host/apps/command_timing_probe.py`: mode3/4の安全停止指令をPCから周期送信。絶対deadline、遅延時の追い付き連打なし、固定socket/毎回socket比較、送信CSV。
- `cm4/bridge/forward_ai_cmd_v2.cpp`: `--timing-trace PATH --timing-seconds 30`で上限10万件をメモリ記録し、終了時に排他的作成CSVへ出力。通常時は計測無効。
- `host/apps/analyze_command_timing.py`: mode3の区間別統計、上書き・窓外分類、ログ整合性検査。`--stm-log-name`でSRAM由来ログを明示選択。
- G474 `Core/Src/main.c`: `y`で10秒capture、終了後にRX/APPLY/統計を出力。診断は安全インタロックではない。開始ACKがないため、y送信だけでcapture開始成功とは判断しない。

```powershell
python -m host.apps.analyze_command_timing runtime/timing-20260920/verified-broadcast runtime/timing-20260920/verified-unicast runtime/timing-20260920/verified-local --stm-log-name stm32_sram.log --json runtime/timing-20260920/reanalysis.json
```

## 最終状態と復元情報

- PC送信器・CM4内部送信器・診断ブリッジは停止済み。
- control_server.serviceはactive。HTTP stop/startを実行し、通常ai_cmd_v2・robot_feedback・カメラを再起動。最終statusはrunning=true。
- CM4本番のai_cmd_v2バイナリは元のまま。診断ビルドは `/tmp/orion-timing-stage/cm4/bin/ai_cmd_v2.out` でのみ実行した。
- G474 Slot Aには診断追加FWを配置済み。build ID 1789867492、image CRC32C 304C3047、metadata CRC32C C391703B。書込みverify成功。通常は計測無効。
- 変更前の全Flash 512 KiBは `runtime/timing-20260920/g474-before.bin`。SHA256: `094005F04BA60066F018ED027AAE36AC381FA3917A71A1A1BD6DB6A439610333`。復元時は対象とslot/metadataを確認し、無計画な全消去はしない。
- PC側文書のみ更新。CM4へドキュメントは配置していない。Qt送信先・AP設定・Wi-Fi省電力設定は変更していない。
- 検証: PC送信器6テスト、解析器4テスト、CM4実機上のbridge既存＋新規13テスト成功。STM32全再ビルド成功（既存RWX警告のみ）。

## 対策候補

まず制御UDPを各CM4宛てユニキャストにする方式を優先して検討する。今回の比較で実効性が確認できた。
複数機体へは送信先一覧を管理して個別配信する必要があるため、既存715 byte一括broadcastを単に108固定へ変えるだけでは全機体運用の解決にはならない。
AP側のDTIM・グループ配送設定の評価は別途行う。DTIM=1でも今回の周期は残るため、DTIMだけを下げれば解決するとはしない。
