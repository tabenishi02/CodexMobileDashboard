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

証明書作成・Token配置・実際のサーバー起動は、それぞれPhase 4の後続タスクで実装・文書化する。
