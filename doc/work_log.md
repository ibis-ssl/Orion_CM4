# 作業ログメモ

実機作業、調査、試験、変更経緯の記録を時点ごとに残す。現在の構成と使い方は [概要](overview.md) を参照。

## ログ管理の変更（2026-09-26）

- `.gitignore`で`*.log`とルートの`runtime/`全体を除外した。既存の追跡対象もファイル本体を残してindexから除外した。過去のGit履歴は書き換えていない。

## CM4_101・103～110のMain更新結果（2026-09-20実施）

- 対象9台すべてでMainのbuild ID=`1789881653`、CONFIRMED状態、期待BINとのCRC32C一致を確認した。103は導入済みのため再書込みせず、他8台を更新した。
- 105はSlot A（CRC32C=`78EAC00D`）、101・103・104・106～110はSlot B（CRC32C=`736AA93F`）から起動。各機の旧active slotは保持し、CAN基板のFWは書き換えていない。
- 最終確認では全9台の制御APIが`running:true`、`ai_cmd_v2.out`と`robot_feedback`の稼働を確認した。一部の更新直後にはBLDCのFW情報照会が一時的に応答しなかったため、全機のCAN状態まで保証する結果ではない。
- 103・104・105・106・109・110では制御再開時、`cm4/camera/dist/cam_server_v3`欠落によるHTTP 500を確認した。制御ブリッジは起動しているが、カメラ起動は別途修正が必要である。

## CM4_107のデフォルトゲートウェイ調査（2026-09-20）

- SSHで確認したNetworkManager 1.52.1の接続`SSL_ibis`（wlan0）は、`ipv4.method=manual`、`192.168.20.107/24`、DNS=`8.8.8.8`。保存設定は`ipv4.never-default=yes`、`ipv4.gateway`と`ipv4.routes`は空だった。デフォルト経路を禁止する設定のため、ゲートウェイ入力だけでは解決しない。
- 実行時には`default via 192.168.20.1 dev wlan0`（metric 0）が存在し、ユーザー申告の手動追加と整合する。`https://example.com`へのHEADはHTTP 200で成功。保存プロファイルと実行時の経路は区別する。
- nmtuiでは`SSL_ibis`のIPv4設定で「デフォルト経路として使用しない」に相当するチェックを外し、ゲートウェイ`192.168.20.1`を保存する。CLIでの同等操作は`sudo nmcli connection modify SSL_ibis ipv4.never-default no ipv4.gateway 192.168.20.1`、適用は`sudo nmcli device reapply wlan0`。調査時には機体設定の変更・再適用は実施していない。
- `doc/fleet.md`の「ロボット用LANはインターネットに到達できない」は従来の環境前提であり、今回のAP環境には一律に当てはまらない。

## CM4_103のMain単独更新完了（2026-09-20）

- ユーザー指示によりMainのみ更新した。Mainリポジトリのコミット`7cf72de6e07db6908fc7986888de542e6482ed6f`（dirtyなし）から`Script/build_slot_b.ps1 -Rebuild`でA/Bを再ビルドし、build ID=`1789881653`を生成した。
- CM4_103の`/home/ibis/main-fw-update-oqubcbwn/`へ更新CLI・確認CLI・A/B BINを配置し、転送前後のSHA-256一致を確認。`main_ab_updater.py`で非active Slot Bへ84,864 byteを書込み、generation=2、CRC32C=`736AA93F`、9.672秒で成功した。ログは同ディレクトリの`update.log`に保存した。
- 更新後のFWVR応答は`active_slot=B`、Main Bのbuild ID=`1789881653`・CRC32C=`736AA93F`で期待BINと`SAME`。Mainソースではvalid maskをCONFIRMED metadata（state=4）の場合にだけ立てるため、確定済み状態も確認できた。
- 旧Main Aはbuild ID=`1789875047`・CRC32C=`D79444C7`のまま保持。Sub=`1789483368/12E9586C`、BLDC CAN1=`1789881156/4E0D4BEB`、BLDC CAN2=`1789870533/F542F566`、Power=`1789874792/F4F0A5A2`も更新前後で一致し、CAN基板には書込みを行っていない。
- 通常制御APIは更新前後とも`running:false`。走行試験は行っていない。

## CM4_103のMain更新可否確認（2026-09-20）

- `CM4_103`（`192.168.20.103`）へSSH接続し、確認前後とも同IPの8000番APIで`running:false`、UART使用プロセスなしを確認した。APIはlocalhostにはbindしていない。
- `/dev/serial0`は`ttyS0`（mini UART）。1 MbaudでFWVR照会とMain bootloaderのOFW1通信に成功した。長時間転送の安定性は今回未検証。
- MainはSlot Aで稼働し、build ID=`1789875047`（2026-09-20 03:30:47 UTC）、CRC32C=`D79444C7`。Slot BはFWVRで`UNREACHABLE`（descriptor未検出）。
- FWUP command 4でMainを一時的に更新モードへ移し、書込みを伴わないINFOを実行。`status=0, target_slot=1(B), valid_mask=1(Aのみ有効), generation=2`を取得し、Aを保持してBへ更新する入口が実機で動作することを確認した。BEGIN/CHUNK/FINALIZE/CONFIRMは送信していない。
- REBOOT後、Slot Aの同一build ID・CRC32Cと全CAN基板のFW情報応答を再確認した。FW書込みは未実施で、更新成功そのものを検証した結果ではない。
- CM4側checkoutは`28c3ca0`。`/home/ibis`配下を深さ5まで探索した範囲にはMain更新CLIやBINがなかった。今回はローカルの既存PythonツールをSSH標準入力から実行した。実更新時は更新対象版のSlot A/B用BINと更新ツールを用意する。

## PC→CM4→G474 実機タイミング調査結果（2026-09-20）

詳細は [timing_investigation_20260920.md](history/timing_investigation_20260920.md)。PC有線キャプチャ、CM4 kernel/recv/write、COM56、STM32記録SRAMを同時評価した。
mode3の集中はCM4 kernel到着時点で既に存在し、broadcastで再現、108 unicastとlocalhostでは本試験で解消。
最終代表値のSTM32受信間隔p50/p99/maxはbroadcast=1.157/97.263/198.403 ms、unicast=15.225/16.377/17.150 ms。
CM4 kernel→読出しp99約1.1 ms、読出し→write p99 0.014～0.040 ms、STM32受信→採用p99約2 msで、約100 msの原因はブリッジ内ではない。
mode4のSTOP試験では入力集中があってもUART約100 Hzを維持した。走行時制御まで保証する結果ではない。
COMデバッグログに文字欠落があったため最終評価には計測終了後のST-Link HOTPLUG SRAM読出しも併用。制御UARTエラーは最終全条件0。
試験送信停止、control_server.service active、通常アプリ再開、HTTP running=trueを確認済み。
CM4本番バイナリは未変更、G474は診断追加build1789867492をSlot Aに配置。変更前Flashバックアップと全ログはPCのruntime/timing-20260920に保存した。

## PC→CM4→G474 同時計測の当初計画（2026-09-20、実施結果は上記）

- 目的は約102.4 ms周期のバーストが最初に現れる処理境界を特定すること。journal表示時刻やPCのCOM受信時刻は物理到着時刻とは区別する。
- PCの送信器候補は `Documents/GUI_Qt/Qt Communication Tester/Qt_Communication_Tester`。ソースは15 msタイマー、宛先192.168.20.255:12345固定、送信ごとにQUdpSocketを生成・bind・closeする。稼働exeとの一致は未確認。bind/sendの戻り値、GUIイベント配送の遅れも測る。既存挙動の測定前にソケット寿命やタイマーを修正しない。
- G474 repoは `C:/Users/hiroyuki/STM32CubeIDE/workspace_1.17.0/G474_Orion_main`。通常USART2受信はIRQ→cm4_uart_rx_byte→HAL_UART_RxCpltCallbackで72 byte組立・チェックサム・cmd_v2_buf更新まで実行する。2 KBリングからmain loopで解析する経路はFW gateway用であり、通常指令とは区別する。
- G474 USART2は1 Mbps、COM56/LPUART1は2 Mbps・8N1、TIM7制御は500 Hz。通常デバッグ表示は設定60 Hzの状態snapshot。Dtはcounter変化からの経過時間、Ltcyは指令値、AI_CMDのHz欄は未実装とのコメントがあり、いずれもフレームごとの実受信間隔として扱わない。UART RAWのbyte/rxirq/frame/valid/PE/FE/NE/OREを補助指標にする。
- 同一run内でPCのdeadline/send前後/戻り値、CM4のkernel到着/recv直後/UART write前後・実書込byte数、G474のフレーム完了/checksum/制御採用時刻を採取する。mode3予約領域のTPRB＋32bit連番で対応付ける（既存probeの38..45 byte）。既存送信器の初回測定はcounter・payloadで暫定照合し、連番導入は別条件とする。
- CM4は本体受信ソケットにrecvmsgとkernel timestampを追加する案を優先し、受信全件と最新値採用・間引きを分けて記録する。udp_rx_monitor.pyは単独受信専用で、通常ai_cmd_v2と同じポートへ並行bindしない。ソケットの競合やパケット分配を避ける。
- G474に追加する場合はフレーム完了時の単調増加時刻・連番を固定長リングに保存し、main側からCOM56へ排出する。IRQ内printfは禁止。診断リングのoverflow、ログ欠落、時計wrap、計測有効/無効による負荷差を記録する。必要ならRX byte/IRQ時刻の短時間traceを追加し、IRQサービス時刻を線上到着時刻と断定しない。
- 試験は各30秒×3回を基本に、(A)既存送信器・元の宛先、(B)宛先のみ108ユニキャスト、(C)計測用PC送信器・同一周期ユニキャスト、(D)CM4 localhost・同一周期、の順。power_save off、カメラ/feedback/ログ条件を揃える。通常負荷の有無や省電力on/offは別の一変数比較とし、mode4はmode3の境界特定後に評価する。
- 周期分布p50/p95/p99/max、短間隔(<3ms)と長間隔(>30ms)の組合せ、連番欠落・重複・間引き、約102.4ms周期の持続を比較する。PCとCM4とSTM32の時計を直接減算しない。同一装置内遅延と連番対応した間隔を主指標にし、装置間絶対遅延には別途時計同期・誤差評価が必要。
- 全試験は対象ID8のみ、STOP設定・速度/キック/ドリブルゼロ、送信器1個を前提とする。物理的な安全状態を確認する。監視開始→送信→送信停止→残ログ回収の順にし、試験終了後は診断プロセスを停止、元のサービス状態を復元してhost-launcherのstatus/start/stopを確認する。
- ST-Linkは必要時だけ使用し、対象シリアル・搭載build・active slot・復元用イメージを確認してからアプリ領域を更新する。bootloader/metadataを無計画に上書きしない。計測中のhalt/resetは禁止。既存flash.ps1のConnectOnlyもresetするため非侵襲確認には使わない。
- この計画と結果資料はPC側に保存する。CM4へドキュメントは配置しない。計画作成時点では送信試験・FW変更は未実施だった。実施後の結果・条件変更は上記の調査報告を参照。

## CM4_108入出力遅延評価・制御API復帰（2026-09-20）

- host-launcherからOfflineになった原因は内部診断で停止したcontrol_server.serviceが
  inactiveのままだったこと。サービスを起動し、PCのhost.lib.cm4_control_clientで
  Stopped→POST start(200)→Running→POST stop(200)→Stoppedを確認。終了時はAPI active、アプリ停止。
- localhostから停止状態mode 3を50 Hz送信。通常カメラ・feedback転送は停止した条件。
  疑似UART試験750件: UDP send直前→PTY readはp50=0.650、p95=1.159、p99=1.221、
  max=2.021 ms。送信器・受信器のスケジューリングも含み、純粋なブリッジ時間ではない。
- 実UART試験500件: strace -ttt -Tでrecvfrom(715 byte)終了→write(72 byte)開始を
  診断連番で対応付け、p50=0.544、p99=0.625、max=0.661 ms。
  write呼出し所要時間p50=0.059、p99=0.068、max=0.073 ms。
  write開始間隔18.846〜21.110 ms、p99=20.939 ms。追いつきバーストは観測していない。
  straceによる計測負荷を含み、recvfrom以前のソケット待ち時間とUART線上完了は含まない。
  UART線上時間は72 byte・1 Mbps・8N1で0.720 msだが、本試験では実測していない。
- 生データは108の `/tmp/orion-latency-pty-20260920/` と
  `/tmp/orion-latency-uart-20260920/` に保存。後者はtrace.log/latency.csv/sender/を含む。
  実機時計がずれているため暦時刻は参照しない。PTY評価は同一機monotonic時刻を使用。
  WindowsからのWi-Fi入力や通常アプリ負荷における遅延の保証ではない。

## UDP受信タイミングの単独監視（2026-09-20）

`cm4/bridge/udp_rx_monitor.py`をCM4_108に配置済み。Windows側の既存送信器から
108のUDP 12345へ送信し、CM4のSSH端末で次を実行する。

```bash
cd /home/ibis/Orion_CM4
python3 cm4/bridge/udp_rx_monitor.py --port 12345 --robot-id 8
# 60秒測定してCSV保存。保存先は未作成のファイルを指定。
python3 cm4/bridge/udp_rx_monitor.py --port 12345 --robot-id 8 --duration 60 --csv /tmp/udp-rx-108.csv
```

- UARTは開かず、STM32へ転送しない。同じポートのai_cmd_v2は事前に停止する。
  SO_REUSEPORTは使わず、競合時は終了。Ctrl+CまたはSIGTERMで最終集計とCSVを保存。
- SO_TIMESTAMPNS_NEW + recvmsgでカーネル受信時刻、直後にアプリ時刻を取得。
  kernel/appの間隔は有効な自機指令について送信元IP別に計算し、分布は合算する。
  送信元portが毎回変わるWindows送信器を考慮し、既定は `--stream-key ip`。
  同じIPの複数送信器を分離したい場合は `--stream-key peer` でIP/port別にする。
  rx pkt/sは空スロット・不正パケットを含む全データグラム数。
  batch_maxは一度の読み切りで受信した全件数。読み切りは100ms/4096件で打ち切る。
- 通常は1秒集計。kernel gap>=30msまたはread delay>=5msの詳細は1秒に3件まで。
  `--gap-ms` / `--delay-ms`で変更可能。duplicate_counterは同一送信元でのcounter重複で、
  欠落数ではない。SO_RXQ_OVFLはソケットドロップのみで、無線損失は含まない。
- kernel時刻とread delayにはrealtime、app間隔にはmonotonicを使用する。
  realtime-monotonicの差が1ms超変化したサンプルと直前比較を除外する。
  時計変更時に既にキューへ溜まっていたパケットの区間も分析対象から除外すること。
  ソフトウェアタイムスタンプなので、無線の物理到着時刻ではない。
- CSVは終了時に保存し、既定10万件を超えると古い記録から破棄。
  `--max-records`で調整でき、csv_evictedに破棄数を表示する。
  分布も各表示区間の最新max-records件に制限し、cumulativeカウンタは全期間。
- 単独CLIの読み出し遅延はai_cmd_v2の読み出し遅延そのものではない。
  カーネル到着が正常なら、次にブリッジ内部の受信計測へ進む。
- 108で実ソケットの受信・timestamp・CSV・ポート競合・異常分類テストを通過。
  文書はPC側にのみ保存する。

## CM4_108内部mode 3連続送信の稼働（2026-09-20）

- `feature/cm4_debug_tools` の診断ツールと `cm4/run_mode3_debug.sh` を108へ配置。
  `control_server.service`を停止し、一時unit `orion-mode3-debug.service` で稼働する。
  localhost:12445へ50 Hzのmode 3停止指令を生成し、既存mainのブリッジが
  `/dev/serial0`・1 MbpsでSTM32へ送る。通常のcrane入力12345は使わない。
- 速度・キック・ドリブルはゼロ、STOP_EMERGENCY付き。カメラ・feedback転送は起動しない。
  通常運用の負荷条件とは異なる。1時間ごとに送信器を更新してCSVを保存するため、
  その境界には短い送信中断がある。CSVは `cm4/runtime/mode3-debug/run-*/capture/`。
  SIGTERMでも保存し、ブリッジ異常時には送信器も終了させる。
- `sudo systemctl stop orion-mode3-debug.service`で診断停止。
  続いて `sudo systemctl start control_server.service` で通常APIを復帰できる。
  通常アプリの起動は別途Runまたは/startが必要。一時unitは再起動後に自動起動しない。
- 108上で停止・CSV保存・再起動を確認。694入力の間隔は18.704〜21.305 ms、
  p99=20.050 ms。これは送信器のUDP入力時刻でありUART線上時刻ではない。
  実機時計は2026-08-29を表示していた。周期計測はmonotonic clockを使用している。
  本文書はPC側のみに保存し、実機には転送しない。

## CM4_108のmain反映（2026-09-20）

- main `d7a2e07c47cf09c6d359e391f1cf2828f4fe7f5a` の `cm4/` を108へ反映。
  ドキュメント・host側ファイル・PCの未コミット変更は転送していない。
- 隔離ディレクトリでbuild.shのビルド・テストを完了し、生成したバイナリを配置。
  配布ソースのcmpとai_cmd_v2.outのSHA-256一致、制御API応答を確認した。
- カメラソース・specはmainと一致しており、既存カメラバイナリを保持。
  runtime設定とGit管理情報は保持しているため、実機のgit HEADは配布版を示さない。
  部分更新の記録は `/home/ibis/.orion_deploy/cm4_main_version.json` を参照。
- 更新前cm4一式は `/home/ibis/.orion_deploy/main-d7a2e07-stage/before-cm4.tar.gz`、
  サービス定義は同ディレクトリの `before-control_server.service` に退避した。

## CM4内部mode 3周期診断（2026-09-20）

`cm4/bridge/mode3_timing_probe.py` はlocalhostへ715 byteのmode 3指令を
定期送信する。速度・キック・ドリブルはゼロ、STOP_EMERGENCYを常時設定する。
予約領域38..45に診断マーカーと連番を載せる。通常指令送信元とは同時使用しない。
時刻はmonotonic ns、絶対deadline方式とし、遅延時は期限を飛ばして連打を防ぐ。
送信時刻は終了後にsend.csvへ保存する。Python/Linuxのスケジューリング遅延は
残るため、入力の周期精度も必ずCSVで確認する。

```bash
# 独立したブリッジを疑似UARTで起動。本番ポート・実UARTは使わない。
python3 cm4/bridge/mode3_timing_probe.py --pty --robot-id 8 --rate-hz 50 --seconds 30 --output /tmp/mode3-pty-run1

# 実UARTの切り分け: 別端末で診断専用ポートのブリッジを起動。
# 通常ブリッジを停止済みであること。本コマンドはSTM32へ停止指令を送る。
./cm4/bin/ai_cmd_v2.out --robot-id 8 --ai-cmd-port 12445 --local-cam-port 12446 --feedback-port 12447 --config-port 12448
# もう一方の端末でlocalhost送信。終了後、上記ブリッジもCtrl+Cで停止する。
python3 cm4/bridge/mode3_timing_probe.py --robot-id 8 --port 12445 --rate-hz 50 --seconds 30 --output /tmp/mode3-uart-run1
```

`--pty`では通常のUART書き込み経路を通り、受信側の時刻をpty.csvへ記録する。
同じreadで受けた複数フレームには同じ時刻が付くため、これは線上送信時刻ではない。
疑似UARTで集中がなくても実UARTドライバ・STM32側は未検証である。
実UARTではロジックアナライザのフレーム開始間隔とSTM32のIRQ/parser時刻を
send.csvと照合する。`--debug`はUART送信を止めるため本診断には使わない。
出力先は毎回新規ディレクトリを指定する。PTY時のbridge.logは通常ログを保存する。

## CM4_107手動更新後の再確認（2026-09-16）

- ユーザーによる旧FWからの手動更新後、107のFWVR応答と全基板の識別情報取得が成功した。以下の前回記録の107更新不可は解消した。
- CM4経由でSub 9.868秒、BLDC CAN1 9.628秒/CAN2 9.420秒、Power 12.254秒で更新成功。各更新間は5秒待機。PowerでUART再送が1回発生したが自動回復した。
- Main Slot B用の既存成果物が古かったため、現在のソースからA/Bを再ビルドし、B 9.688秒、A 10.016秒で更新成功。最終active A。
- 最終照合は全6エントリ`SAME`。Main A/B build ID=`1789569865`、CRC32C A=`43EE803A`/B=`3626D013`、Sub=`12E9586C`、左右BLDC=`A0BDF386`、Power build ID=`1789569751`/CRC32C=`9172D70B`。制御サービスを復帰した。走行試験は未実施。

## CM4_105・107・108更新可否確認（2026-09-16）

- 3台ともネットワーク・SSH・制御API接続可能。107/108にはホスト公開鍵を追加した。UART排他のため制御サービスを止め、`/tmp/orion_fw_check`へ配置した更新ツールで確認した。
- 105はSub 10.373秒、BLDC CAN1 12.203秒/CAN2 9.350秒、Power 11.353秒、Main B 9.980秒で再更新成功。最終active B。
- 108は旧FWから更新成功。Power転送間隔修正版のMain Bを先行更新（9.900秒）、Sub 9.826秒、BLDC CAN1 9.859秒/CAN2 9.349秒、Power 11.304秒、Main A 9.920秒。最終active A。
- 105/108ともSub直後のBLDC ENTERでCAN timeoutが一度発生した。再実行し、その後の各更新間に5秒待機を入れると完了した。起動待ちとの関連は未確定。両機とも最終6エントリが`SAME`で、Main build ID `1789484648`、CRC32C A=`AEC55D28`/B=`DB0D0D01`、Sub=`12E9586C`、左右BLDC=`A0BDF386`、Power=`A07D08B0`を確認した。
- 107は`/dev/serial0 -> ttyS0`、1 Mbaudで3秒間に47,617 byteの通常データを受信したが、FWVR照会とMain bootloader INFO（5回試行）はtimeout。FWVRを1 byte/10 msで送っても応答なし。書込みBEGINには到達せずFlash未更新。搭載MainのOTA対応状況、bootloader導入状態、CM4→Main送信経路の追加確認が必要であり、原因は断定していない。
- 終了時は全3台の制御サービスを復帰。走行試験は実施していない。107/108のsudoにはhostname `ibis`の名前解決警告があるが、サービス操作は成功した。

## CM4_105更新実機記録（2026-09-16）

- `192.168.20.105`へ現行CM4コードを配布し、ブリッジ再ビルド・systemd更新・制御API復帰を確認した。カメラバイナリは通常deployの仕様どおり再ビルドしていない。
- Windowsの`core.autocrlf=true`で`git archive`にCRLFのシェルスクリプトが入って更新が失敗したため、配布時のみ`git -c core.autocrlf=false archive`とする修正を加えた。修正版の実機deployは成功（配布snapshot `ceb4ef073af5`）。
- Subは9.871秒、BLDC node 16は9.850秒、node 17は9.358秒で個別更新した。左右同時更新はENTERでCAN timeoutとなったため個別更新へ切り替えた。原因は未確定。
- Powerは安全停止確認後、3584 byte地点で`node=3`（欠落・順序異常）が繰り返され、通常再送では復旧しなかった。Mainの`Core/Src/fw_update_gateway.c`へPower対象時のみ8 CAN frameごとに1 ms待つ処理を追加し、Main Slot Bを先行更新後、Powerを13.244秒で復旧した（UART再送あり）。その後Main Slot Aも更新した。
- ゲートウェイが失敗後に残る場合、OFW2 sequenceを連続させた`MSG_REBOOT`でPowerとMainを再起動できる。未確定Powerはbootloaderに留まる。sequence error応答でもMainのlast_sequenceは受信sequenceへ進むため、別プロセスの同期回復では同一プロセスから次sequenceを送る必要がある。
- 最終照合は全6エントリが`SAME`。Main A/B build ID=`1789484648`、CRC32C A=`AEC55D28`/B=`DB0D0D01`、Sub=`12E9586C`、左右BLDC=`A0BDF386`、Power=`A07D08B0`。Main active slotはA。`control_server.service`をactiveへ復帰し、制御APIはStopped（走行停止）を確認した。走行試験は実施していない。

## FW更新時間と高速化検討（2026-08-27）

現行はCM4–Main UARTが1 Mbaud、CAN1/CAN2がClassical CAN 1 Mbit/s、896 byte block、blockごとのstop-and-waitである。実機再測定ではBLDC 2台のCAN1/CAN2並列更新が64,360 byteで14.388秒、Main A/B更新が83,452 byteで9.896～10.032秒だった。F303系のeraseは約1.22秒、BLDC転送開始から最終block確定までは9.67秒で、3回のUART timeout再送約1.5秒を含む。Mainはerase約0.9秒、UART転送約3.3秒、finalize・reset・起動確認約4.9秒である。

PowerはSub/BLDCへの給電状態を制御するため、Powerを最初に安全停止して全nodeをbootloaderへ入れる順序は使用できない。実機ではPower停止後、Subはコンデンサ保持中に応答したが、BLDC選択時に給電が落ちてtimeoutした。更新順序は次のとおりとする。

1. SubとBLDC 2台を先にerase・転送・CRC確定する。
2. 必要ならSub/BLDCを再起動してmetadata確定済みであることを確認する。
3. Powerを通常コマンドで安全停止し、最後にPowerを更新する。
4. Power復帰後、Sub/BLDCは有効アプリへ即時起動する。Main自身のA/B更新は最後に行う。

現行`all_can_updater.py`はPowerを最初に停止するため、この給電構成では全体更新に使用しない。途中失敗後に別プロセスから再開するとOFW2 sequence同期エラーになる点、対象切替時の遅延CAN応答を除去できない点も修正が必要である。

高速化は次の優先順とする。

- 最優先はUART timeoutの解消である。USART2を1 byte割込みからDMA/ring bufferへ変更し、応答sequenceを再同期可能にする。実測では再送だけで1.5～6秒程度を消費している。
- F303 bootloaderは全アプリ領域54～55 pageをeraseしている。image size分の32～33 pageだけをeraseすれば、約1.22秒から約0.7～0.75秒へ短縮でき、image phaseごとに約0.5秒削減できる。
- 128 CAN frameを無間隔送信して失敗時に即時全再送する方式を、16～32 frame burstとflow-control、またはTX完了基準のadaptive pacingへ変更する。固定1 ms/8 frameはPowerを復旧できたが、更新時間が19.139秒へ増えたため採用しない。
- Sub/BLDCのeraseを並列開始し、CAN1ではSub→BLDC node 16、CAN2ではBLDC node 17を並行処理する。異なるimageをbusごとに保持できるgateway queueが必要である。
- 896 byte stop-and-waitを2～4 KiB windowへ拡大し、UART受信とCAN送信・Flash programを二重buffer化する。UARTを2 Mbaudへ上げるだけの効果は1 imageあたり約0.4～0.9秒であり、先にDMA化が必要である。
- F303はbxCANのためCAN FDは使用できない。Classical CAN 1 Mbit/sは標準上限であり、非標準の2 Mbit/s化は配線余裕と全node互換性を損なうため推奨しない。
- zlib level 1の参考圧縮率はSub 57.3%、BLDC 68.1%、Power 56.0%、Main 69.3%である。stream展開をbootloaderへ追加すれば効果は大きいが、開発量と障害時検証量も大きいため後段候補とする。

安全な低リスク改善では、CAN node全体を現状約40～45秒から約30～35秒、Mainを含む全体を約50～55秒から約40秒前後へ短縮できる見込みである。bus別pipeline、larger window、起動確認のevent化まで行う場合は全体20～30秒が現実的な目標で、Flash erase/programとClassical CAN帯域から見た下限は概ね15～20秒である。

### CM4–Main UART割込み受信の安定性

2026-08-27、CAN処理とFlash処理を使用せず、実更新と同じOFW2最大長923 byte（payload 907 byte）をCM4からMainへ送って応答を照合した。再送なしでは5 frame成功後の6 frame目でtimeoutを再現した。最大5回再送する試験では1,000/1,000 frameが最終成功したが、初回成功884、1回再送102、2回再送12、3回再送1、4回再送1で、11.6%が初回timeoutだった。

payload長別300回試験の初回timeout率は16 byteで0%、128 byteで1.33%、512 byteで7.0%、907 byteで11.33%だった。当初は長さ依存性からUART byte取りこぼしを疑ったが、後日のcount/hash計測でCM4送信列、Main ISR、リング取り出し列が完全一致し、この仮説は棄却した。

当時の実装はFIFO無効、HAL 1-byte再arm、エラー未計測だったため、FIFO・直接RX IRQ・リング・診断カウンタへ段階的に変更した。最終的な直接原因はpartial frame timeout判定のunsigned underflow競合であり、2026-08-28に修正・3,000回試験を完了した。詳細は末尾の「CM4–Main UART受信の安定化」を参照する。

## MCUファームウェア更新の実機記録

- CM4→Main→Subのv2経路は実機確認済み。65,168 byteを正常時約8～10秒で更新し、UART CRC破損、CAN欠落・重複・逆順・payload破損からの回復を確認した。
- BLDC 2台のCM4→Main→CAN1/CAN2並列更新と、電源基板の安全停止確認を含むCM4→Main→CAN更新を実機確認した。
- BLDC実機試験では63,592 byteを通常13.965秒、UART CRC破損とCAN欠落・重複・逆順・payload破損の複合注入時14.047秒で更新した。両台のreadback SHA-256・CRC32C `0xC22DAE9C`・metadata一致、board ID 0/1保持、2 Mbps UARTとCAN受信復帰を確認した。
- MainのA/B更新はCM4経由で実機確認済み。最終往復ではB→Aが9.808秒、A→Bが9.796秒で、Slot A generation 8、Slot B generation 9がともにCONFIRMED、boot attempts 0となった。
- 2026-08-27、左右BLDCの同時更新を10回連続実施し10/10成功した。各回14.885～19.591秒、全回CRC32C `0xC22DAE9C`一致。UART応答欠落によるchunk再送88回もすべて回復し、両基板のmetadata CONFIRMED、VTOR `0x08004000`、board ID 0/1保持をST-Linkで確認した。
- 2026-08-27、Powerを昇圧動作中から通常コマンドで停止し、安全statusを3フレーム確認後、65,244 byteを13.848秒で更新した。UARTで更新前の`PW 0 / BV 0 / Ch 0`と更新直後のアプリ起動・自己診断復帰を確認した。
- 同日、Subとboard ID 0/1の両BLDCへ即時起動版bootloaderをST-Linkで書込み・verifyした。周期CAN通信中のresetから各board IDを保持して即時起動することを確認後、CM4→Main経由でSubをCAN1へ14.132秒、BLDC node 16をCAN1へ17.157秒、node 17をCAN2へ14.300秒で更新した。全基板がUART通常動作とCAN受信へ復帰し、build ID・CRC32Cが期待バイナリと`SAME`であることを確認した。

## STM32ファームウェア更新の初期実機記録

2026-08-24時点で、G474 MainのM1（安全IO、Slot A再配置、CRC検証・jump、初回導入スクリプト）を実機へ導入済みです。readback一致、10回連続reset、metadata無効時の安全待機、PC12 Low/TIM5停止、metadata復元後のSlot A再起動を確認しました。

初回実機試験でbootloaderの割り込み禁止状態がSlot Aへ残る問題を修正済みです。修正後はCM4向けUSART2の128-byte frameを約124 Hzで連続受信し、デバッグLPUART1でも起動・IMU・CAN初期化ログを確認しています。

2026-08-25にMainの導入後更新を10回連続実施し、全回成功しました。F303 subも接続先スワップ後のST-Link（`002D00373033510635393935`、Device ID `0x422`）へbootloaderを初回導入済みです。通常更新のprogram/verify/reset後、VTOR=`0x08004000`、例外mask全解除、USART1 2 Mbpsログ、CAN受信カウンタ更新を実機確認しました。

## 開発用FWバージョン確認の実機記録

- 2026-08-27の実機確認ではMain A/B、Sub、BLDC 2台がすべて期待バイナリと`SAME`になった。Powerは実装・ビルドのみで、未接続のため`UNREACHABLE`を確認した。

## CM4–Main UART受信の安定化（2026-08-28）

- CM4のGPIO14/15は、従来`/dev/serial0 -> ttyS0`のmini UARTだった。`/boot/firmware/config.txt`へ`dtoverlay=disable-bt`を追加し、`/dev/serial0 -> ttyAMA0`のPL011（GPIO14=TXD0、GPIO15=RXD0）へ切り替えた。元設定はCM4上の`/boot/firmware/config.txt.before_pl011_20260828`へ保存している。
- bridge、FW更新、バージョン確認の既定portは、UART実体名に依存しない`/dev/serial0`へ統一した。
- Main USART2は1 Mbpsの割り込み受信を維持し、8-byte FIFOを有効化した。RXはHALの1-byte受信状態機械から切り離し、ISRでFIFOをdrainして2 KBリングへ格納する。ORE等が発生してもISR末尾でRX割り込みを再有効化する。
- FW更新ゲートウェイ中は通常テレメトリDMAを停止し、OFW2応答とのUSART2送信競合を防ぐ。ブザーPWM停止も従来どおり維持する。
- 不安定性の直接原因は、partial frameの250 ms timeout式における競合だった。`HAL_GetTick()`取得後にUART IRQが`uart_last_byte_tick`を更新するとunsigned減算がunderflowし、受信途中のparserを誤ってresetしていた。最終byte時刻を先にsnapshotし、現在時刻取得後に値が変化していないことを再確認してからtimeout判定する。
- CM4期待列、Main ISR、リング取り出し列のcountとrolling hashが一致する診断を追加し、UART線上とリングのbyte欠落・並び替わりがないことを確認した。parser timeout、header/CRC、queue overflow、UART ORE/FE/NE/PEもST-Linkから参照できる。
- 最大payload 907 byte（総frame 923 byte）の初回応答試験は、修正前に約8～12%失敗していた。修正後は3,000/3,000成功（60.028秒、median 14.891 ms、p95 16.937 ms、max 17.832 ms）、Main A/BをCM4経由更新した後も1,000/1,000成功した。
- Main A/Bは同一build ID `1787928269`へ更新済みで、A=`23F1D426`、B=`CF4FCD66`のCRC32C一致を確認した。現在のactive slotはA。

コミット後の最終実機確認（2026-08-29）:

- Mainコミット`d050c6a`をdirtyなしでbuild ID `1787929548`としてA/Bビルドした。A→Bは84,444 byteを9.807秒（プロセス全体11.120秒）、B→Aは9.956秒（全体11.311秒）で更新し、双方のCRC32C一致とactive slot Aを確認した。
- 更新後のMainで最大payload 907 byte（総frame 923 byte）を3,000回連続送信し、3,000/3,000成功した。所要60.000秒、median 14.904 ms、p95 16.959 ms、max 17.108 msである。
- CM4→Main→CAN1のSub更新は65,912 byteを9.800秒（全体10.206秒）で完了した。試験終了後、Main A/B、Sub、BLDC 2台、Powerの全基板からbuild IDとCRC32Cを読出せることを確認した。

## 起動時と異常時に必ず言葉が残るようにした（2026-09-13）

`/simplify` のレビューで挙がった「気付けない失敗」を潰した。いずれも実機とホスト
の両方に効く。

**1. ログのブロックバッファリング**

`ai_cmd_v2.out` と `cm4_sim.out` は `main()` の先頭で stdout を行バッファへ固定する。
stdout が端末でないとき（docker のログ、systemd の journal、テストのパイプ）既定は
ブロックバッファリングで、SIGTERM で落とされると直前の数十行が消える。**現地で一番
読みたいログが一番消えやすい。** 呼び出し側の `stdbuf -oL` には頼らない。

**stderr に同じことをしてはいけない**（既定のバッファ無しの方が強い）。理由は
`main()` のコメントにある。

**2. `ai_cmd_v2.out` が不明なオプションで落ちるようになった**

従来は引数の書き間違いを黙って無視して既定値で走っていた。`--kp` / `--decel` /
`--tolerance` / `--command-timeout-ms` / `--feedback-timeout-ms` がこの経路に
載った以上、`--Kp` と打ち間違えたロボットが既定ゲインで黙って走るのは許容できない。

既知オプションの一覧は別表ではなく、**引数解析が問い合わせた名前そのもの**
（`g_value_options` / `g_flag_options`）である。別表にするとオプションを足した
ときに片方だけ更新して、正しい指定を弾く事故になる。

`cm4/lancher.py` が渡すのは `-s 1000000` だけなので、実機の起動経路には影響しない。

**3. `cm4_sim` の `--multicast-if` 失敗が致命的になった**

`setsockopt(IP_MULTICAST_IF)` に失敗したら起動を中止する。従来は `perror` して
送出を続けていたが、それでは閉じ込めたかった経路（Wi-Fi への multicast 漏れ）へ
そのまま流れる。気付けるのは AP が落ちたときで、原因が `cm4_sim` だとは結び付かない。
OS 任せで構わないときは `--multicast-if ''` と明示する。

**4. `cm4_sim` が停止理由を表示するようになった**

共有制御器が `reason` を返すのは、実機と sim のどちらでも「なぜ止まっているか」を
言えるようにするためである。`cm4_sim` はそれを一切出していなかった。実機側と同じく
**理由が変わった周期にだけ** 1 行出す。終了時のサマリには 2 バイト固定小数の
クランプ回数も出す。

**5. `cm4/update.sh` がデプロイ経路でテストを走らせなくなった**

`build.sh` への一本化で、`cm4-fleet deploy` のたびに CM4 実機上で Python 結合
テスト（約 17 秒）が走るようになっていた。テストは UDP を bind して
`cm4_sim.out` / `ai_cmd_v2.out` を spawn するので、`restart_service` の前に
稼働中の `control_server` と同居する。`lancher.py` の `/status` は
`pgrep -f ai_cmd_v2.out` で判定するため、テストが立てたプロセスを本番稼働と
誤認する。`update.sh` は `build.sh --no-tests` を呼ぶ。検証は CI と
`cm4/setup.sh`（初期セットアップ）が担う。

