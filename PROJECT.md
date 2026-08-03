# Codex Mobile Dashboard

## 概要

VSCode Codexで行っている開発内容を、スマートフォンから快適に閲覧できるダッシュボードシステムを構築する。

本システムの目的はリモート開発ではなく、

- 現在の作業状況の確認
- チャット内容の閲覧
- コードやエラーの確認
- 次に行う作業の把握

をスマートフォンから容易に行えるようにすることである。

---

# システム構成

```
                HTTP POST

    作業用PC
(VSCode + Codex)

        │

        ▼

    Androidサーバー端末
(Termux Web Server)

        │

HTTP / HTTPS

        │

        ▼

閲覧用スマートフォン
(Android)
```

---

# ワークスペース フォルダ構成

CodexMobileDashboard/
│
├── PROJECT.md          # プロジェクトの目的・全体設計
├── CODING_RULES.md     # 実装ルール
├── TASKS.md            # 実装タスク管理
├── CHANGELOG.md        # 変更履歴
├── server/             # Android・Termux側
├── client/             # 閲覧用HTML
├── tools/              # 作業PC側の変換・送信ツール
├── data/               # テスト用JSON
└── docs/               # 詳細設計

# 基本方針

## Python実行環境

作業用PCでは、既にインストールされている次のPythonを使用する。

```text
Python 3.10.6 64-bit
%USERPROFILE%\AppData\Local\Programs\Python\Python310\python.exe
```

初期実装はPython 3.10以上で動作する構文と標準ライブラリを使用し、Python 3.11以降で追加された機能には依存しない。

サーバー対象は、Termuxを利用でき、Python 3.10以上、必要なネットワーク接続、保存容量、バックグラウンド動作条件を満たすAndroidスマートフォンとする。特定メーカーやCPUアーキテクチャには依存しない。動作確認済みの基準端末は検証用Android端末で、Termux 0.118.3、Android 12、aarch64、Python 3.14.6、Git 2.55.0を使用する。PCのPython 3.10.6と基準端末のPython 3.14.6の両方で動作確認する。詳細は`docs/TERMUX_ENVIRONMENT.md`に定める。

Pythonコードは標準ライブラリのみで実装する。外部ライブラリが必要または有効と判断した場合は、追加前に用途、利点、標準ライブラリだけで実装する場合との差を提示し、採用可否を検討する。

ログはコンソールとテキストファイルへ出力し、日単位でローテーションする。直近7日分をUTF-8で保存する。詳細は`docs/LOGGING.md`に定める。

通常設定はINI形式で管理し、秘密情報はリポジトリ外の専用ファイルに保存する。実設定と秘密ファイルはGitへ登録しない。詳細は`docs/CONFIGURATION.md`に定める。

ダッシュボードの対象は、Gitが利用でき、少なくとも1つのコミットを持つプロジェクトに限定する。変更ファイル、変更状態、変更行数はGitを正とし、変更内容の要約はCodex JSONLだけから生成する。プロジェクトのファイル本体とGit差分本文はAndroidサーバー端末へ送信しない。詳細は`docs/GIT_TRACKING.md`に定める。

Codex JSONLから抽出対象としたテキストは削除または省略せず、通信と表示のために分割する。データの分割基準、送信対象、PCとAndroidサーバー端末の責務は`docs/DATA_LIMITS.md`に定める。

Codexのターン完了時は即時更新し、作業中は30秒、停止中は60秒ごとに定期確認する。通常データは変更時だけ送信し、5分間正常な送信がない場合はハートビートを送信する。スマートフォンは表示中に15秒ごとに更新確認し、非表示中は停止する。保存期間を含む詳細は`docs/UPDATE_POLICY.md`に定める。

MVPは、PCからAndroidサーバー端末、閲覧スマートフォンまでの一連動作、データ完全性、Git変更メタデータ、送信対象、大容量処理、モバイル表示、障害復旧、秘密情報保護、運用手順、未解決不具合の10項目で合否判定する。実機受入確認には基準端末の検証用Android端末を使用する。詳細は`docs/MVP_ACCEPTANCE.md`に定める。

開発エラーは`errors.json`、システム運用エラーは各端末のローテーションログへ分離して保存する。Androidサーバー端末側の詳細エラーはPCへ逆送せず、スマートフォンにはシステム状態の要約だけを表示する。詳細は`docs/ERROR_HANDLING.md`に定める。

---

## サーバーはできるだけ賢くしない

Androidサーバー端末は

- 静的ファイル配信
- HTTP API
- JSON保存

のみを担当する。

解析処理は一切行わない。

---

## 作業用PCを頭脳とする

Codexログの解析

↓

JSON生成

↓

HTTP POST

までを担当する。

---

## 表示はブラウザ

閲覧用スマホは

HTML + JavaScript

のみで表示する。

専用アプリは作成しない。

PWA化は将来検討する。

---

# データ設計

JSONを役割ごとに分離する。

```
data/

dashboard.json

recent.json

messages.json

errors.json

decisions.json

files.json

metadata.json
```

巨大なJSONは作らない。

---

# HTML設計

HTMLは固定ファイルとする。

更新対象はJSONのみ。

JavaScriptがJSONを取得して画面を書き換える。

---

# サーバー設計

初期実装では

SQLiteは使用しない。

JSONをそのまま配信する。

FastAPIも導入しない。

静的HTTPサーバーで十分。

---

# ネットワーク

## Phase1

LAN内のみ

TCP 8765番ポートを使用し、作業用PCからAndroidサーバー端末へHTTP POST、閲覧スマートフォンからAndroidサーバー端末へHTTP GETする。検証用Android端末を基準端末としたLAN内の基本接続確認結果は`docs/NETWORK.md`に定める。

```
PC

↓

Androidサーバー端末

↓

閲覧スマホ
```

---

## Phase2

外部アクセス対応

Tailscaleを第一候補とする。

LAN内と同一URL・同一APIを利用できる構成を目指す。

---

# 非採用

現時点では採用しないもの

- SQLite
- RDB設計
- FastAPI
- WebSocket
- GitHub Pages
- Cloudflare Pages
- クラウド同期
- OneDrive同期

必要になった時点で再検討する。

---

# 拡張予定

将来的に追加可能

- 全文検索
- エラー検索
- TODO管理
- コードビュー
- Markdown生成
- Wiki生成
- PWA
- SQLite
- REST API
- WebSocket

初期実装では考慮のみ行い、
設計を複雑化させない。

---

# 設計方針

重要なのは

「作ること」

ではなく

「保守しやすいこと」

である。

そのため

- シンプル
- 責務分離
- JSON中心
- HTML固定

を最優先とする。

必要になった時だけ機能を追加する。
