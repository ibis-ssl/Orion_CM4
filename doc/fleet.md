# CM4 フリート管理 (`cm4-fleet`)

ホスト PC から複数の CM4 に対して、OTA アップデート・SSH 鍵配布・設定配布を一括で行うツールです。

## 背景・制約

- ロボット用 LAN (`192.168.20.0/24`) は**インターネットに到達できません**。そのため OTA はデバイス側 `git pull` ではなく、**ホスト PC から `git archive` で作った tarball を SFTP で直接転送する方式**です。
- `cm4/bin/*.out`（C++ ブリッジ）は毎回デバイス上で再ビルドします。`g++` のみで完結し数秒、ネットワーク不要です。
- `cm4/camera/dist/cam_server_v3`（PyInstaller ビルド）は**デフォルトで再ビルドしません**。`pip install pyinstaller` がネットワークを要求するためです。カメラコードを変更した場合のみ、デバイスに一時的なネット到達性を用意した上で `--rebuild-camera` を指定してください。
- `cm4/runtime/*.json`（機体固有の HSV キャリブレーション等）は tar 展開でも `push-config` でも意図せず上書きされません。転送は既存ファイルへの上書き・追加のみで、明示的に指定しない限り削除・全体同期は行いません。

## 前提条件

`cm4-fleet deploy` は**軽量な更新**であり、初回のフルセットアップ([CM4 セットアップ](../SETUP.md)の `cm4/setup.sh`)を代替しません。対象デバイスには事前に `cm4/setup.sh` が(インターネット到達可能な環境で)一度実行され、`fastapi`/`uvicorn` の pip install と `g++`(build-essential)が導入済みである必要があります。これらが無い状態で `deploy` すると `stage=build` で分かりにくいエラーになります。

## インストール

```powershell
uv sync --extra fleet
```

`paramiko` はホスト専用の依存です。`cm4/setup.sh` の `pip install -e .` はデバイス側にもインストールされる `dependencies` のみを見るため、通常の `uv sync` では入りません。

## インベントリ

デフォルトでは機体番号 `N`(0〜12) に対して `192.168.20.(100+N)` を対象とします(既存の接続規則と同じ)。

台数追加や IP を個別に固定したい場合は、環境変数 `ORION_FLEET_INVENTORY` で JSON ファイルを指定できます。

```json
{
  "ip_base": "192.168.20.",
  "ip_offset": 100,
  "hosts": [
    { "machine_no": 0 },
    { "machine_no": 3, "ip": "192.168.20.150" }
  ]
}
```

対象の選択は各コマンド共通で次のいずれかを指定します。

```powershell
--all                     # 全機体
--machines "0,1,5-8"      # 機体番号(範囲指定可)
--ips "192.168.20.101,192.168.20.105"  # IP直接指定(インベントリ外も可)
```

## 初回セットアップ: SSH 鍵のブートストラップ

対象デバイスがパスワード認証のみの場合、まずこの PC の公開鍵を配布します。

```powershell
$env:ORION_FLEET_PASSWORD = "ibis"
uv run cm4-fleet bootstrap --ips 192.168.20.101
```

- パスワードは環境変数 `ORION_FLEET_PASSWORD` または対話プロンプトでのみ受け付けます(CLI 引数では受け付けません)。
- 配布する公開鍵は `~/.ssh/id_ed25519.pub` / `~/.ssh/id_rsa.pub` を自動検出します。`--pubkey-file` で明示指定も可能です。
- 既存の `authorized_keys` の内容(他の開発者の鍵)は保持したまま追記します。
- 鍵追加後、鍵認証での再接続を検証してから成功を報告します。失敗した場合は他の対象機には影響しません。
- **初回はこのコマンドを先に実行してください。** `status` / `deploy` はデフォルトで未知のホスト鍵を拒否するため、事前にこの PC からその機体へ一度も SSH していない場合は失敗します(`bootstrap` はホスト鍵を自動受理し `~/.ssh/known_hosts` に保存します)。

## OTA デプロイ

```powershell
uv run cm4-fleet deploy --all
uv run cm4-fleet deploy --machines "0,1,5-8"
uv run cm4-fleet deploy --ips 192.168.20.101 --ref v1.2.3   # 特定タグ/コミットへ
uv run cm4-fleet deploy --ips 192.168.20.101 --force        # 稼働中でも上書き
uv run cm4-fleet deploy --ips 192.168.20.101 --rebuild-camera  # カメラも再ビルド(要ネット到達性)
```

処理内容:

1. ホスト側の未コミット差分を検出(デフォルトは拒否、`--allow-dirty` で許可)。
2. `git archive <ref>` で tarball 作成(`.gitignore` 対象は自然に除外されます)。
3. 対象デバイスごとに並列実行: 稼働中ならスキップ(`--force` で上書き可)→ tarball 転送 → 既存ツリーへ展開 → 削除されたファイルのみ個別削除 → `cm4/update.sh` 実行(ブリッジ再ビルド・サービス再起動)→ デプロイ済みバージョン記録 → 起動確認。
4. 1 台の失敗は他台に影響しません。成功/失敗をステージ付きでサマリ表示します。

### ロールバック

専用コマンドはありません。**古い ref を指定して `deploy` を再実行することがロールバックです。**

```powershell
uv run cm4-fleet deploy --ips 192.168.20.101 --ref <過去のコミットやタグ>
```

ブリッジは毎回再ビルドするため数秒〜十数秒で戻せます。カメラコードを含む変更を跨ぐ場合のみ `--rebuild-camera` が必要です。

## 設定配布

```powershell
uv run cm4-fleet push-config --all --target env --file .\fleet-env.txt
uv run cm4-fleet push-config --machines 3 --target hsv --file .\hsv_101.json
uv run cm4-fleet push-config --ips 192.168.20.101 --target authorized-keys --file .\other-dev-key.pub
uv run cm4-fleet push-config --all --target file --file .\local-note.txt --remote-path ".orion_deploy/local-note.txt"
```

`--target hsv` のような機体固有ファイルは、誤って全台へ同一内容を配布しないよう `--all` との併用を拒否します。

`--target env` は `/home/ibis/.orion_deploy/env` に配置され、`cm4/control_server.service` の `EnvironmentFile` から読み込まれます。反映には `sudo systemctl daemon-reload && sudo systemctl restart control_server.service` が必要です(次回 `cm4-fleet deploy` を実行した際にも `cm4/update.sh` が同じ再起動を行うため自動的に反映されます)。

`--target file` / `--remote-path` で git 管理下のパス(`Orion_CM4/` 配下でリポジトリに追跡されているファイル)を指定しないでください。次回 `cm4-fleet deploy` の展開で上書きされ、変更が消えます。機体固有の恒久的な設定は `.orion_deploy/` 配下など git 管理外の場所に配置してください。

## ステータス確認

```powershell
uv run cm4-fleet status --all
```

稼働状態(`Running`/`Stopped`/`Offline`)とデプロイ済み commit を 1 台 1 行で表示します。

## 既知の制約 / Phase 2 (未実装)

- `releases/<id>/` + `current` シンボリックリンクによる即時ロールバック(ビルド待ち無し)は未実装です。現状は毎回ブリッジを再ビルドします(数秒程度のため実用上大きな問題にはなりません)。
- カメラビルド成果物の世代間 carry-forward は未実装です。`--rebuild-camera` は毎回フルビルドです。
- dry-run/diff プレビューは未実装です。
- `host/robot-manager` Web UI からの in-process 統合(「OTA 実行」ボタン等)は未実装です。`host/lib/fleet` の各関数は CLI から薄く呼ばれる設計のため、統合コストは小さいはずです。
- 対象デバイスの `sudo` がパスワードを要求する設定になっている場合、`cm4-fleet deploy`(`cm4/update.sh` 内の `systemctl` 呼び出し)は失敗します。sudoers の自動生成は行わないため、その場合は手動で NOPASSWD 設定を行ってください。

## トラブルシューティング

- `stage=connect` で `Server not found in known_hosts` のようなエラー: その機体に対して `cm4-fleet bootstrap` を先に実行してください。
- `stage=build` で `そのようなファイルやディレクトリはありません`: `git archive` は git に追跡されているファイルのみを含みます。新規ファイルを追加した場合は `git add` してから(コミット前でも `--allow-dirty` で)デプロイしてください。
- `--rebuild-camera` 時に `pip install pyinstaller` が失敗する: デバイスがロボット用 LAN のみに接続されている場合、一時的にインターネット到達可能なネットワークへ切り替えてから実行してください。
