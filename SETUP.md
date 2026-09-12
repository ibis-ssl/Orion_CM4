# CM4 セットアップ

この手順は Raspberry Pi CM4 上で実行します。作業ディレクトリは `/home/ibis/Orion_CM4` を前提にしています。

## 事前準備

次の項目は環境ごとに値や操作が変わるため、`cm4/setup.sh` には入れていません。

- Raspberry Pi OS 64bit の導入
- ユーザー `ibis` の作成
- `wlan0` の固定 IP 設定
- CM4 側の `~/.ssh/authorized_keys` への公開鍵配置
- 必要に応じた Wi-Fi ドライバ設定

## セットアップ実行

```bash
cd /home/ibis/Orion_CM4
chmod +x cm4/setup.sh
./cm4/setup.sh
```

`sudo ./cm4/setup.sh` では実行しないでください。Python 依存を実行ユーザーの環境へ入れるため、必要な `sudo` はスクリプト内で個別に実行します。

`cm4/setup.sh` は次を実行します。

- APT パッケージ導入
- Python 依存導入
- `cm4/bridge/forward_robot_feedback.cpp` と `cm4/bridge/forward_ai_cmd_v2.cpp` のビルド
- `cm4/bin/robot_feedback.out` と `cm4/bin/ai_cmd_v2.out` の生成
- `cm4/camera/cam_server_v3.py` の PyInstaller ビルド
- `cm4/control_server.service` の配置、有効化、再起動

## オプション

APT upgrade を省略したい場合:

```bash
SKIP_APT_UPGRADE=1 ./cm4/setup.sh
```

カーネルヘッダ導入を省略したい場合:

```bash
SKIP_KERNEL_HEADERS=1 ./cm4/setup.sh
```

既存の `cm4/camera/dist/cam_server_v3` を使い、カメラサーバーの再ビルドを省略したい場合:

```bash
SKIP_CAMERA_BUILD=1 ./cm4/setup.sh
```

## systemd

サービス状態確認:

```bash
sudo systemctl status control_server.service
```

ログ確認:

```bash
journalctl -u control_server.service -f
```

`cm4/control_server.service` は `/home/ibis/Orion_CM4/cm4/lancher.py` を起動します。

## 2台目以降・以後の更新

2台目以降のセットアップや、以後のコード更新は、この手順を1台ずつ手動で行う代わりに、ホスト PC から `cm4-fleet` で複数台へ一括デプロイできます。詳細は [フリート管理](doc/fleet.md) を参照してください。

```powershell
uv sync --extra fleet
uv run cm4-fleet bootstrap --all
uv run cm4-fleet deploy --all
```

## HSV 設定

カメラサーバーの HSV 設定は、`cm4/lancher.py` 経由で起動した場合に `cm4/runtime/cam_server_v3_hsv.json` に保存されます。

初回起動時は `cm4/camera/default_hsv_config.json` から作成されます。


## 手動ビルド

ビルド定義は `cm4/build.sh` に一本化されています（`cm4/setup.sh` と `cm4/update.sh` は
これを呼ぶだけです）。sudo も apt も使わないので、ホスト PC (x86_64) でもそのまま実行できます。

```bash
./cm4/build.sh              # ビルド + テスト一式
./cm4/build.sh --no-tests   # ビルドのみ
```

テスト一式は実機 UART も STM32 も使わないので、ホスト PC でそのまま走ります。

出力は `cm4/bin/` です。

| バイナリ | 用途 |
|---|---|
| `ai_cmd_v2.out` | AI 制御 UDP を受けて UART で STM32 へ送るブリッジ |
| `robot_feedback.out` | STM32 からの 128B feedback を multicast へ再配信 |
| `robot_packet_layout_test.out` | `robot_packet.h` が crane 側正本からドリフトしていないか検査 |
| `test_position_controller.out` | 位置制御則の単体テスト |
| `cm4_sim.out` | シミュレータ用の CM4 相当プロセス（**ホスト PC 専用**。実機では使わない） |

### ai_cmd_v2.out

- `--debug` バイナリ表示になる。マイコン側には送信されない。
- `-s` オプションでボーレートを変更できる。**デフォルト 1 Mbps**。
- `--serial-port` で UART デバイスを変更できる（既定 `/dev/serial0`）。
- `--ai-cmd-port` / `--local-cam-port` で待ち受けポートを変更できる
  （既定 `12345` / `8890`。ホスト PC でのテスト用）。
- `--robot-id` でロボット ID を明示指定できる。既定は `wlan0` の IPv4 最終オクテット
  `- 100` で、**決定できない場合は 0 号機として動かず終了します**。
- `--passthrough` で mode 4 を位置制御せず素通しする（旧構成との A/B 比較用）。
- `--tx-rate-hz` で位置制御パスの UART 送信レートを変更できる（既定 `100`）。
  500 Hz は G474 メインループ相当ですが UART 占有率 36% になるので、ST-Link で
  `ORE`/`FE`/`NE`/`PE` を確認してから使ってください。
- `--kp` / `--decel` / `--tolerance` / `--command-timeout-ms` / `--feedback-timeout-ms`
  で位置制御の定数を変更できる。
- `-h` で全オプションを表示します。

```bash
./cm4/bin/ai_cmd_v2.out --debug
```

位置制御の詳細は [制御パケット](doc/control_packet.md) と
[overview](doc/overview.md) を参照してください。

### robot_feedback.out

- `-n` でIP指定(例:101)
- `/dev/serial0` の**唯一の読み手**です。UART から読んだ 128B を multicast へ再配信すると
  同時に、`127.0.0.1:(50000 + 機体番号)` へ loopback unicast でも投げます。
  `ai_cmd_v2.out` はこれを受けて位置制御ループを閉じます。

```bash
./cm4/bin/robot_feedback.out -n 101
```

### cm4_sim.out（ホスト PC 専用）

シミュレータ環境で CM4 の役を演じるプロセスです。実機では使いません。
`ai_cmd_v2.out` と**同一の位置制御ソース**をリンクしています。
使い方は [overview](doc/overview.md) の「ロボット側位置制御」の節を参照してください。

```bash
./cm4/bin/cm4_sim.out --help
```
