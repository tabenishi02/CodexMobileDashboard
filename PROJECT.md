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

    検証用Android端末
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
├── server/             # 検証用Android端末側
├── client/             # 閲覧用HTML
├── tools/              # 作業PC側の変換・送信ツール
├── data/               # テスト用JSON
└── docs/               # 詳細設計

# 基本方針

## サーバーはできるだけ賢くしない

Android端末は

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

```
PC

↓

Android端末

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