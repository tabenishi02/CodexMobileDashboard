# Termuxセットアップ手順

## 前提

TermuxはF-Droidまたは公式GitHub配布版を使用し、同じ配布元のTermux:Bootを使用する。Google Play版は使用しない。本手順は実装済みのPhase 4サーバーをAndroid端末へ配置・起動するための手順である。

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

リポジトリを`app/`へ配置する。Token、CA秘密鍵、サーバー秘密鍵、実INIはGitへ追加しない。実行時データとGit管理外設定は次のように分離する。

```text
$HOME/CodexMobileDashboard/
├─ app/                 # リポジトリのserver・client
├─ data/                # staging・公開Snapshot
└─ logs/                # ローテーションログ

$HOME/.config/codex-mobile-dashboard/
├─ server.ini           # 実INI（Git管理外）
├─ server.token         # Bearer Token（Git管理外）
└─ tls/
   ├─ server.crt        # サーバー証明書
   └─ server.key        # サーバー秘密鍵（chmod 600）
```

CA秘密鍵はAndroid端末へ置かず、作業用PCのリポジトリ外で保護する。証明書の正規配置と更新手順は[`TLS_CERTIFICATES.md`](TLS_CERTIFICATES.md)を参照する。

## Android設定

- Termuxのバッテリー最適化を解除する。
- LAN内の固定IPまたはDHCP予約を設定する。
- 端末の画面ロック・省電力時にも手動起動できることを確認する。
- 自動起動はサーバー手動起動とHTTPS疎通確認後、Termux:Bootで設定する。
- Termux:Boot起動時に`termux-wake-lock`を取得する。サーバー停止後も保持し、`stop_server.sh`からは解放しない。コマンドが失敗した場合は`set -eu`により起動を中止する。

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
## 停止・再起動

通常の停止は、PIDファイルと実行コマンドを照合してから`SIGTERM`を送るスクリプトを使う。別プロセスへの誤送信を避けるため、PID不正または`server.py --config`以外のプロセスだった場合は停止しない。20秒以内に終了しない場合も強制終了せず、`server_stop_timeout`で停止する。

```sh
cd "$HOME/CodexMobileDashboard/app/server"
chmod 700 stop_server.sh restart_server.sh
./stop_server.sh
./restart_server.sh
# 別の実INIで再起動する場合だけ指定する
# ./restart_server.sh "$HOME/CodexMobileDashboard/config/server.ini"
```

停止済みの場合は`server_not_running`を表示して正常終了する。再起動は停止処理が成功した後だけ起動する。PIDファイルは`~/.cache/codex-mobile-dashboard/server.pid`にあり、手作業で削除するのは、端末再起動後などにスクリプトが停止済みと確認できない場合だけにする。

2026年9月10日：自動起動サーバー停止時のSSH切断改善を検証するためwake lock取得を追加した。利用者が実機で問題の解決を確認した。提示ログでは`stop_exit=0`、`server_stopped_confirmed`、`pid_file_removed`により正常停止とPIDファイル削除を確認し、停止前後で同じsshd・SSHセッションのPIDが継続した。10秒後の`ssh_session_alive`と続く`pwd`も成功した。サーバー停止時にwake lockは解放しない。これは導入後の改善確認であり、Termux内部の原因を断定するものではない。既存の`~/.termux/boot/codex-mobile-dashboard`はインストーラーで上書きされないため、配備時は既存エントリのカスタマイズを確認し、同じ位置へ`termux-wake-lock`を反映する。
