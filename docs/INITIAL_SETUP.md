# 初回セットアップ

新規環境で、作業PC → Androidサーバー → 閲覧スマートフォンのLAN内HTTPS表示を開始する手順。PCはPowerShell、AndroidはTermuxで実行する。`<リポジトリURL>`、IP、ユーザー名等の例示値は自分の環境に置き換える。

既存環境では実INI・Token・証明書・状態・未送信キューを上書きせず、未実施の工程だけ確認する。PC Tokenの正規配置は現行設定例の`secrets/sender.token`とし、`certificates`にはCA公開証明書だけを置く。

## 1. 準備するもの

- Windows PC：Python 3.10以上（同じインストール先の`pythonw.exe`を含む）、Git、OpenSSL、PowerShell。各ツールを導入しPATHから呼び出せるようにする。Python部分は標準ライブラリだけを使うため、追加のpipパッケージは不要。
- Androidサーバー：Termuxと同じ配布元のTermux:Boot。導入・省電力設定は[Termuxセットアップ](TERMUX_SETUP.md)。サーバー既定の空き容量予約は1 GiBであり、保存データ分も別途必要。
- 閲覧スマートフォン：CA証明書を信頼登録できるブラウザ環境。
- 3台を相互通信可能な同一LANに接続し、Androidの固定IPまたはDHCP予約とHTTPSポート（既定8765）を決める。ルーターの外向けポート転送は不要。
- 収集対象：許可ルート内の初回commit済みGitプロジェクトと、そのプロジェクトを作業ディレクトリとするCodexセッション履歴。
- AI推論を使う場合は、定期実行するWindowsユーザーでCodex CLIを利用できる状態にしておく。利用しない場合の設定は工程6に示す。

PCで確認する。

```powershell
python --version
git --version
openssl version
python -c "import pathlib,sys; p=pathlib.Path(sys.executable).with_name('pythonw.exe'); print(p); assert p.is_file()"
```

Node.jsはクライアントの開発テスト用であり、通常運用には不要。同梱ブラウザライブラリのダウンロードも不要。

## 2. PCへ配置する

空の新規配置先へリポジトリを取得する。取得済みの場合は既存の作業ディレクトリへ移動する。

```powershell
git clone '<リポジトリURL>' C:\path\to\CodexMobileDashboard
cd C:\path\to\CodexMobileDashboard
$dashboardRoot = Join-Path $env:LOCALAPPDATA 'CodexMobileDashboard'
'config','secrets','certificates' | ForEach-Object {
    New-Item -ItemType Directory -Force -Path (Join-Path $dashboardRoot $_) | Out-Null
}
$collectorConfig = Join-Path $dashboardRoot 'config\collector.ini'
if (Test-Path -LiteralPath $collectorConfig) { throw 'collector.ini already exists' }
Copy-Item -LiteralPath '.\config\collector.example.ini' -Destination $collectorConfig
```

秘密ファイルの配置先は同じWindowsユーザーだけが読み書きできるアクセス権にする。共有フォルダーやGit管理下へ置かない。正式な構成は[設定管理](CONFIGURATION.md)。

## 3. TLS証明書とTokenを準備する

次のコマンドは新規発行用。既存の証明書がある場合は再発行せず、有効期限・SAN・対応する鍵を確認して再利用する。発行方針は[TLS証明書](TLS_CERTIFICATES.md)を参照する。

PCの同じPowerShellで実行する。IPは実際のAndroid固定IPに置き換える。CA鍵のパスフレーズはOpenSSLの対話入力で指定し、コマンドへ書かない。

```powershell
$androidAddress = '192.0.2.10' # 実際のAndroid固定IPへ変更
$certificateDirectory = Join-Path $dashboardRoot 'certificates'
if (@(Get-ChildItem -LiteralPath $certificateDirectory -Force).Count -gt 0) {
    throw 'Certificate directory is not empty; inspect existing certificates'
}
Push-Location $certificateDirectory
try {
    openssl req -x509 -newkey rsa:3072 -sha256 -days 3650 -subj '/CN=Codex Dashboard Local CA' -addext 'basicConstraints=critical,CA:TRUE' -addext 'keyUsage=critical,keyCertSign,cRLSign' -keyout ca.key -out ca.crt
    if ($LASTEXITCODE -ne 0) { throw 'CA generation failed' }
    openssl req -new -newkey rsa:2048 -nodes -subj '/CN=Codex Dashboard Server' -keyout server.key -out server.csr
    if ($LASTEXITCODE -ne 0) { throw 'CSR generation failed' }
    @("subjectAltName=IP:$androidAddress", 'basicConstraints=critical,CA:FALSE', 'keyUsage=critical,digitalSignature,keyEncipherment', 'extendedKeyUsage=serverAuth') | Set-Content -LiteralPath server-ext.cnf -Encoding ascii
    openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 365 -sha256 -extfile server-ext.cnf -out server.crt
    if ($LASTEXITCODE -ne 0) { throw 'Certificate signing failed' }
    openssl verify -CAfile ca.crt server.crt
    if ($LASTEXITCODE -ne 0) { throw 'Certificate verification failed' }
    openssl x509 -in server.crt -noout -dates -ext subjectAltName
} finally {
    Pop-Location
}
python -c "import os,pathlib,secrets; p=pathlib.Path(os.environ['LOCALAPPDATA'])/'CodexMobileDashboard'/'secrets'/'sender.token'; f=p.open('x',encoding='utf-8'); f.write(secrets.token_urlsafe(32)+'\n'); f.close()"
if ($LASTEXITCODE -ne 0) { throw 'Token generation failed; do not overwrite an existing token' }
```

Tokenは画面に出力しない。PCの`sender.token`とAndroidの`server.token`は同じ内容にする。CA秘密鍵`ca.key`はPCだけに保持し、暗号化して保管する。発行後はサーバー鍵を含むファイルのアクセス権を確認する。

## 4. Androidへ配置する

Termuxで準備する。Termux:Bootは一度開いておく。

```sh
pkg update
pkg install python git openssl
umask 077
mkdir -p "$HOME/CodexMobileDashboard"
cd "$HOME/CodexMobileDashboard"
git clone '<リポジトリURL>' app
mkdir -p "$HOME/.config/codex-mobile-dashboard/tls"
mkdir -p "$HOME/CodexMobileDashboard/data/public" "$HOME/CodexMobileDashboard/data/staging" "$HOME/CodexMobileDashboard/logs"
if [ -e "$HOME/.config/codex-mobile-dashboard/server.ini" ]; then
    echo 'server.ini already exists'
else
    cp app/config/server.example.ini "$HOME/.config/codex-mobile-dashboard/server.ini"
fi
```

Git cloneは新規配置時だけ実行する。既存の`app`へ重ねない。PCと同じ版の`server`・`client`・`config`が取得できていることを確認する。

信頼できるSSH/SFTP接続または直接接続で次の3ファイルを転送する。SSHを使う場合はTermuxで`pkg install openssh`、`passwd`、`sshd`を実行し、`whoami`でユーザー名を確認する。PC側はTermuxのSSHポート（通常8022）へ接続し、ホスト鍵を確認する。転送ツールのログにToken本文を出さない。

| PC側（`$dashboardRoot`配下） | Android側 |
| --- | --- |
| `certificates/server.crt` | `~/.config/codex-mobile-dashboard/tls/server.crt` |
| `certificates/server.key` | `~/.config/codex-mobile-dashboard/tls/server.key` |
| `secrets/sender.token` | `~/.config/codex-mobile-dashboard/server.token` |

転送後、Termuxで権限と設定を確認する。

```sh
chmod 700 "$HOME/.config/codex-mobile-dashboard" "$HOME/.config/codex-mobile-dashboard/tls"
chmod 600 "$HOME/.config/codex-mobile-dashboard/server.ini" "$HOME/.config/codex-mobile-dashboard/server.token" "$HOME/.config/codex-mobile-dashboard/tls/server.key"
python --version
df -h "$HOME"
```

`server.ini`のパスが上記配置に一致し、`port = 8765`、`static_directory`が`app/client`、`public_directory`と`staging_directory`が`data/`配下であることを確認する。

## 5. Androidを手動起動してHTTPSを確認する

Termuxで前面起動し、このセッションを開いたままPCで次工程を行う。

```sh
cd "$HOME/CodexMobileDashboard/app/server"
chmod 700 start_server.sh stop_server.sh restart_server.sh
./start_server.sh
```

PCの同じPowerShellで、生成したCAを使って確認する。

```powershell
$env:DASHBOARD_SETUP_URL = "https://${androidAddress}:8765/health"
python -c "import os,pathlib,ssl,urllib.request; ca=pathlib.Path(os.environ['LOCALAPPDATA'])/'CodexMobileDashboard'/'certificates'/'ca.crt'; r=urllib.request.urlopen(os.environ['DASHBOARD_SETUP_URL'],context=ssl.create_default_context(cafile=str(ca)),timeout=10); print(r.read().decode())"
```

HTTP 200と`status: ok`を確認する。TLS失敗時は日時・CA・SAN・IPを照合する。`/health`は認証不要のため、Tokenの検証は次工程で行う。

## 6. PC設定と初回送信

工程2の`collector.ini`を編集する。INIにTokenの値を直接書かない。

| 設定 | 確認・変更内容 |
| --- | --- |
| `discovery.allowed_roots` | 収集を許可するGitプロジェクトの親ディレクトリ。既定は`C:\codex` |
| `sessions_dir` / `archived_sessions_dir` | 実際のCodex履歴保存先。存在する通常sessionsディレクトリが必要 |
| `sender.base_url` | `https://<Android固定IP>:8765`。SANと同じIPを使う |
| `sender.token_file` | `%LOCALAPPDATA%\CodexMobileDashboard\secrets\sender.token` |
| `sender.ca_file` | `%LOCALAPPDATA%\CodexMobileDashboard\certificates\ca.crt` |
| `ai_inference.mode` | 通常は`incremental`。推論を使わない初回確認は`off` |
| `ai_inference.max_calls_per_run` | 通常3。推論を使わない場合は0（定期runnerはincrementalを指定するため上限0も必要） |

AI推論ありの場合はGitとCodex CLIを定期実行ユーザーのPATHから起動できる状態にする。初回履歴の扱いと過去補完は[AI推論](AI_INFERENCE.md)、[明示backfill](BACKFILL_OPERATION.md)を参照する。セットアップ中に大量backfillを暗黙に実行しない。

定期タスク未登録の状態で、リポジトリルートから実行する。実際の履歴からSnapshotを生成しAndroidへ送信する操作である。

```powershell
cd C:\path\to\CodexMobileDashboard
python -m tools.collector --config "$collectorConfig" collect-once
if ($LASTEXITCODE -ne 0) { throw 'Initial collection failed; inspect logs before continuing' }
python -m tools.collector --config "$collectorConfig" queue-status
```

終了コード0に加え、`$dashboardRoot/logs/sender.log`の`snapshot_committed`、未送信キューの解消を確認する。workspaceが0件なら、許可ルート・Git初回commit・Codexセッションの作業ディレクトリを確認する。起動成功だけでは初回送信完了と扱わない。エラーは[LANアクセスの切り分け](LAN_ACCESS.md)を参照し、キューや台帳を削除しない。

## 7. 閲覧スマートフォンで確認する

PCの`certificates/ca.crt`だけを閲覧端末へ転送し、利用者がCA証明書として信頼登録する。設定画面の名称はOSにより異なる。秘密鍵や送信用Tokenは転送しない。

閲覧端末でAndroidサーバーのトップページを開く。

```text
https://<Android固定IP>:8765/
```

証明書警告なしで開き、公開中ワークスペース一覧から対象プロジェクトを選択する。選択後は内部的に`/?workspace_id=<workspace_id>`へ遷移する。既存ブックマークや診断用途では直接URLも利用できるが、通常操作でworkspace IDを調べる必要はない。

プロジェクト名・主要画面・システム情報のSnapshot IDと最終受信日時が正しいことを確認する。TokenをURLへ入れない。手動更新と表示中15秒更新も確認する。

## 8. 自動起動へ移行する

手動送信・閲覧が成功した後に行う。

1. Androidの手動サーバーを`Ctrl+C`または別Termuxセッションの`stop_server.sh`で正常停止する。
2. [Termux自動起動手順](TERMUX_SETUP.md)の`install_termux_boot.sh`を実行する。既存エントリは上書きしない。bootエントリで`termux-wake-lock`を取得し、サーバー停止時には解放しない。
3. Androidを再起動し、HTTPS `/health`と閲覧を再確認する。SSHの自動起動はこのサーバー用インストーラーの対象外であり、必要な場合は別途設定する。
4. PCで[定期実行手順](SCHEDULED_EXECUTION.md)に従い`install_collector_task.ps1 -Preview`で確認後に登録する。登録した時点からログオン中の定期送信が始まる。
5. 複数回の実行で`LastRunTime`更新、完了時の結果0、`snapshot_committed`、閲覧側の受信日時更新を確認する。
6. PC再起動後も同じWindowsユーザーでログオンし、同じ確認を行う。画面消灯後のAndroid稼働継続も確認する。

登録後の手動collectorは定期実行と重ねない。停止・無効化・解除方法は[定期実行](SCHEDULED_EXECUTION.md)を参照する。

## 初回セットアップの完了条件

- 正しいCA・SANでHTTPSが成功し、認証付き送信がcommitされる。
- 意図したworkspaceを閲覧でき、更新が反映される。
- Android・PCの再起動後に通常運用へ復帰する。
- 設定・Token・秘密鍵・生成データ・ログがGit管理外にある。

この文書追加時点では、新規端末を初期化して通し実行する受入試験は行っていない。既存実装・設定例・これまでの実機確認と手順を照合し、コードブロックの構文とリンクを検証した。バックアップ方法は実装済みで、既存PCのToken配置も2026年9月28日に`secrets`へ統一した。
