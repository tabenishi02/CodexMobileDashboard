# Termuxセットアップ手順

## 前提

TermuxはF-Droidまたは公式GitHub配布版を使用し、同じ配布元のTermux:Bootを使用する。Google Play版は使用しない。実サーバーは未実装のため、本手順はPhase 4の実装・起動前準備である。

## 初期準備

```sh
pkg update && pkg upgrade
pkg install python git openssl
mkdir -p "$HOME/CodexMobileDashboard"
cd "$HOME/CodexMobileDashboard"
python --version
git --version
```

Python 3.10以上であること、空き容量を`df -h "$HOME"`で確認する。サーバー用ファイルは共有ストレージではなく`$HOME/CodexMobileDashboard`へ配置する。

## リポジトリと実設定

実装後にリポジトリをこのディレクトリへ配置する。Token、CA秘密鍵、サーバー秘密鍵、実INIはGitへ追加しない。実行時データは次のように分離する。

```text
$HOME/CodexMobileDashboard/
├─ app/                 # リポジトリのserver・client
├─ config/              # 実INI（Git管理外）
├─ secrets/             # Bearer Token・秘密鍵（Git管理外）
├─ certificates/        # CA公開証明書・サーバー証明書
├─ data/                # staging・公開Snapshot
└─ logs/                # ローテーションログ
```

## Android設定

- Termuxのバッテリー最適化を解除する。
- LAN内の固定IPまたはDHCP予約を設定する。
- 端末の画面ロック・省電力時にも手動起動できることを確認する。
- 自動起動はサーバー手動起動とHTTPS疎通確認後、Termux:Bootで設定する。
- `termux-wake-lock`は必要な場合だけ使用し、常用の前に電池消費を確認する。

## 起動前確認

```sh
uname -m
pwd
df -h "$HOME"
python --version
openssl version
```

証明書作成・Token配置後は、HTTPSサーバーを次のように前面起動する。`server.ini`はリポジトリ外へ置き、Tokenを引数やシェル履歴に渡さない。

```sh
cd "$HOME/CodexMobileDashboard/app/server"
chmod 700 start_server.sh
./start_server.sh
# 実INIを別の場所へ置く場合だけ指定する
# ./start_server.sh "$HOME/CodexMobileDashboard/config/server.ini"
```

設定ファイルがない場合は`server_config_not_found`、読めない場合は`server_config_unreadable`だけを表示して停止する。`Ctrl+C`で前面のサーバーを停止できる。Termux起動時の自動起動は次の手順で登録する。
## Termux起動時の自動起動

手動起動とHTTPS疎通確認が済んだ後、Termux:BootをF-Droidまたは公式GitHub配布版から、Termux本体と同じ配布元で導入する。Termux:Bootを一度開いてから、次を実行する。

```sh
cd "$HOME/CodexMobileDashboard/app/server"
chmod 700 termux_boot_start_server.sh install_termux_boot.sh
./install_termux_boot.sh
```

インストーラーは`~/.termux/boot/codex-mobile-dashboard`へ起動ファイルを作成する。既に同名のファイルがある場合は上書きせず、`boot_entry_already_exists`で停止する。端末を再起動後、`ps -ef | grep '[s]erver.py'`と`https://<Android端末IP>:<port>/health`で起動を確認する。自動起動の失敗時はTermux:Bootを一度開き、`$HOME/.termux/boot/codex-mobile-dashboard`の実行権と、実設定・証明書の読取権を確認する。
