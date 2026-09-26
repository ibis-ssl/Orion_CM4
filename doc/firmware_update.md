# MCUファームウェア更新

CM4からUART接続のMain（STM32G474）を更新し、MainをゲートウェイとしてCAN上のSub、左右BLDC、Power（STM32F303）を更新する。MainはA/Bスロット、CANノードは単一アプリ領域を使用する。転送データと書込み結果はCRC32Cで確認する。

## 対象とツール

| 対象 | CM4側ツール | 接続 |
|---|---|---|
| Main | `cm4/firmware/main_ab_updater.py` | `/dev/serial0` |
| Sub | `cm4/firmware/sub_can_updater_v2.py` | Main → CAN1、node 4 |
| BLDC | `cm4/firmware/sub_can_updater_v2.py` | Main → CAN1 node 16 / CAN2 node 17 |
| Power | `cm4/firmware/sub_can_updater_v2.py` | Main → CAN1、node 100 |
| バージョン確認 | `cm4/firmware/fw_version_reader.py` | `/dev/serial0` |

CANノードの番号とバスは実機の配線に合わせて指定する。`sub_can_updater_v2.py` は1回の更新でCAN1とCAN2の各1ノードを選べる。BLDC左右には同じイメージを並列送信できる。

## 更新順序と確認

1. 対象、イメージ、給電状態を確認し、`control_server.service` を停止してUARTの競合を避ける。
2. SubとBLDCのイメージを更新・確定する。
3. Powerを安全停止したうえで更新する。PowerはSubとBLDCへの給電を制御するため、この順序を守る。
4. Mainの非稼働スロットを更新する。MainはCAN更新ゲートウェイなので最後に更新する。
5. `fw_version_reader.py` で全対象のbuild IDとCRC32Cを確認し、Mainの稼働スロットがCONFIRMEDであることを確認する。
6. `control_server.service` を復帰し、制御APIと必要な周辺機能の起動状態を確認する。

`all_can_updater.py` はPowerを先に停止する実装なので、この給電構成で全ノードの一括更新に使用しない。更新中の通信エラーや再起動後の不一致は成功として扱わず、対象と状態を確認してから再開する。

## 実行例

以下はツールの引数形式を示す。実行前に対象の機体番号、バス、node ID、イメージを照合する。

```bash
python3 cm4/firmware/sub_can_updater_v2.py sub.bin --port /dev/serial0 --node-can1 4
python3 cm4/firmware/sub_can_updater_v2.py bldc.bin --port /dev/serial0 --node-can1 16 --node-can2 17
python3 cm4/firmware/sub_can_updater_v2.py power.bin --port /dev/serial0 --node-can1 100
python3 cm4/firmware/main_ab_updater.py --slot-a main_a.bin --slot-b main_b.bin --port /dev/serial0
python3 cm4/firmware/fw_version_reader.py --port /dev/serial0 \
  --main-a main_a.bin --main-b main_b.bin --sub sub.bin \
  --bldc-can1 bldc.bin --bldc-can2 bldc.bin --power power.bin
```

各コマンドの詳細な引数は`--help`で確認する。Flash配置と通信処理の正本は各MCUリポジトリのbootloader、Mainの`fw_update_gateway.c`、およびCM4側ツールの実装とする。
