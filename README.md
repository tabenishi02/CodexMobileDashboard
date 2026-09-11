# Codex Mobile Dashboard

VSCode Codexで進めている開発の状況を、スマートフォンから短時間で確認するためのダッシュボードです。

> [!IMPORTANT]
> Phase 5は完了しました。作業用PCのcollector、Android・Termux向けHTTPSサーバー、スマートフォン向け閲覧画面、Codex CLI推論の増分化は実装済みです。SC-51EでのAndroidブラウザ表示、CA信頼済みHTTPS正式受入、一般的なAndroidスマートフォン相当の画面幅確認も完了しています。アクセシビリティ確認は利用者判断で対応不要とし、ブラウザ用外部ライブラリはリポジトリへ同梱して継続利用します。Phase 6の再開地点は[`docs/CURRENT_HANDOFF.md`](docs/CURRENT_HANDOFF.md)を参照してください。

## 目的

本システムは、スマートフォンからリモート開発を行うものではありません。作業用PCでCodexによる開発を続けながら、別の端末から次の情報を閲覧・判断できるようにします。

- 現在の作業状況
- Codexとの会話と最近の更新
- エラーと決定事項
- Gitで確認した変更ファイル
- 次に行う作業
- PCとAndroidサーバー端末の稼働状態

## システム構成

```text
作業用PC
VSCode + Codex
  ├─ Codex JSONLを収集・解析
  ├─ Git変更メタデータを取得
  ├─ 表示用JSONを生成
  └─ HTTPS POST
          │
          ▼
Androidサーバー端末
Termux Web Server
  ├─ 認証とJSON検証
  ├─ JSON保存
  └─ HTML・JSON配信
          │
          ▼
閲覧スマートフォン
Androidブラウザ
```

作業用PCを処理の主体とし、Androidサーバー端末ではCodexログやGitの解析、表示データの整形を行いません。閲覧スマートフォンには専用アプリを導入せず、HTML・CSS・JavaScriptで構成した画面をブラウザで表示します。

## 情報源と役割

| 情報源 | 使用目的 |
|---|---|
| Codex JSONL | 会話、作業目的、決定事項、エラー、次の作業、変更内容の要約 |
| Git | 実際の変更ファイル、変更状態、変更行数、ステージ状態 |
| PC収集ツール | 収集、解析、分類、JSON生成、分割、Androidサーバー端末への送信 |
| Androidサーバー端末 | 認証、検証、保存、静的ファイルとJSONの配信 |
| 閲覧ブラウザ | JSONの取得とモバイル向け表示 |

Gitは「何が実際に変更されているか」、Codex JSONLは「なぜ、何のために変更したか」を示します。両者が一致しない場合は推測で統合せず、それぞれの情報を分けて表示します。

## 送信するデータ

GitからAndroidサーバー端末へ送信するのは、次のメタデータだけです。

- ファイルパス
- 追加、修正、削除、名前変更、コピー、種類変更、競合、未追跡
- ステージ状態と作業ツリー状態
- 追加行数と削除行数
- バイナリーファイル判定
- ブランチ、`HEAD`、コミット識別情報

次のデータは、プロジェクトから読み取ってAndroidサーバー端末へ送信しません。

- プロジェクトのファイル本体
- ソースコード本文
- Git差分本文
- 画像、動画、音声、PDFなどのファイル本体
- GitリポジトリやGitオブジェクト

Codex JSONL内にコードがテキストとして記録されている場合は、セッション履歴の一部として扱います。

## 動作環境

### 作業用PC

- Windows
- VSCode + Codex
- Python 3.10.6 64-bit
- Git

### Androidサーバー端末

対象は、TermuxとPython 3.10以上を利用でき、必要なネットワーク、保存容量、バックグラウンド動作条件を満たすAndroidスマートフォンです。特定メーカーの機能には依存しません。

動作確認済みの基準端末：

- 検証用Android端末
- Android 12
- Termux 0.118.3
- aarch64
- Python 3.14.6
- Git 2.55.0
- 保存先：`/data/data/com.termux/files/home/CodexMobileDashboard`

### 閲覧端末

- Androidスマートフォン
- JavaScriptを使用できるWebブラウザ

初期実装はLAN内のTCP 8765番ポートとプライベートCAによるHTTPSを使用します。HTTPは架空サンプルによる独立した疎通確認だけに限定します。Tailscaleによる外部アクセスはMVP完成後の検討対象です。

## 技術方針

- Python 3.10以上で動作する構文を使用する
- Pythonコンポーネントは標準ライブラリだけで実装する。ブラウザクライアントは`THIRD_PARTY_NOTICES.md`記載の同梱ライブラリを使用する
- HTML、CSS、JavaScriptを分離する
- SQLite、FastAPI、WebSocketはMVPで使用しない
- 通常設定はUTF-8のINIファイルで管理する
- 認証トークンなどの秘密情報はリポジトリ外へ保存する
- PC側の設定、秘密、状態、キュー、生成JSON、ログは`%LOCALAPPDATA%\CodexMobileDashboard`配下へ保存する
- JSONLから候補を抽出し、`C:\codex`配下のGitプロジェクトだけを自動登録する
- 実セッションの送受信はHTTPSとし、証明書検証を無効化しない
- JSONは役割ごとに分割する
- 対象テキストを件数や保存期間で自動削除しない
- 大容量テキストは省略せず分割して送信する
- PCとAndroidサーバー端末の運用ログは日単位でローテーションし、直近7日分を保存する

## 表示用データ

表示用JSONは次の単位へ分けて生成します。会話履歴と変更要約は索引からページ・断片を参照します。

```text
data/
├─ dashboard.json
├─ recent.json
├─ messages.json
├─ messages/
│  ├─ pages/
│  ├─ chunks/
│  └─ summaries/
├─ errors.json
├─ decisions.json
├─ files.json
└─ metadata.json
```

Codexターン完了時に即時更新し、作業中は30秒、停止中は60秒ごとに変化を確認します。通常データは内容が変わった場合だけ送信し、5分間正常な送信がない場合は小さなハートビートを送信します。

閲覧画面は表示中に15秒ごとに更新を確認し、非表示中は自動更新を停止します。

## リポジトリ構成

```text
CodexMobileDashboard/
├─ README.md
├─ PROJECT.md
├─ CODING_RULES.md
├─ TASKS.md
├─ CHANGELOG.md
├─ config/             # 秘密を含まない設定例
├─ server/             # Android・Termux側HTTPSサーバー
├─ client/             # スマートフォン向け閲覧画面
├─ tools/              # PC側の収集・変換・送信ツール
├─ data/               # テスト用サンプルとローカル動作確認用データ
└─ docs/               # 詳細設計
```

## ドキュメント

| ファイル | 内容 |
|---|---|
| [`PROJECT.md`](PROJECT.md) | プロジェクトの目的と全体設計 |
| [`CODING_RULES.md`](CODING_RULES.md) | 実装時に守るコーディング規約 |
| [`TASKS.md`](TASKS.md) | フェーズ別の実装タスクとMVP完了状況 |
| [`CHANGELOG.md`](CHANGELOG.md) | 重要な変更履歴 |
| [`docs/CODEX_SESSION_DATA.md`](docs/CODEX_SESSION_DATA.md) | Codexセッションデータの調査結果 |
| [`docs/GIT_TRACKING.md`](docs/GIT_TRACKING.md) | Git変更メタデータの取得仕様 |
| [`docs/DATA_LIMITS.md`](docs/DATA_LIMITS.md) | データ量、分割、送信対象 |
| [`docs/UPDATE_POLICY.md`](docs/UPDATE_POLICY.md) | 更新頻度、ハートビート、保存期間 |
| [docs/AI_INFERENCE.md](docs/AI_INFERENCE.md) | Codex CLI推論の現行経路、制限、キャッシュ範囲 |
| [docs/BACKFILL_OPERATION.md](docs/BACKFILL_OPERATION.md) | OSS利用者向けAI backfillの使用量、処理時間、停止・再開手順 |
| [docs/CURRENT_HANDOFF.md](docs/CURRENT_HANDOFF.md) | 現在の実装・実機確認・既知問題・再開手順 |
| [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) | 設定ファイルと秘密情報の管理 |
| [`docs/MANUAL_COMMAND.md`](docs/MANUAL_COMMAND.md) | 未送信Snapshotの手動確認・再送 |
| [`docs/TRANSPORT_API.md`](docs/TRANSPORT_API.md) | PC・Android間のHTTPS Snapshot送信契約 |
| [`docs/LOGGING.md`](docs/LOGGING.md) | ログ出力とローテーション |
| [`docs/ERROR_HANDLING.md`](docs/ERROR_HANDLING.md) | エラー分類、保存、再試行 |
| [`docs/TERMUX_ENVIRONMENT.md`](docs/TERMUX_ENVIRONMENT.md) | Androidサーバー端末の要件と検証用Android端末での確認結果 |
| [`docs/TERMUX_SETUP.md`](docs/TERMUX_SETUP.md) | Android実機への配置、起動、停止、自動起動 |
| [`docs/TLS_CERTIFICATES.md`](docs/TLS_CERTIFICATES.md) | 証明書の正規配置、更新、事故対応 |
| [`docs/LAN_ACCESS.md`](docs/LAN_ACCESS.md) | LAN内HTTPSアクセス・workspace選択・障害切り分け |
| [`docs/NETWORK.md`](docs/NETWORK.md) | LAN内通信の確認結果 |
| [`docs/MVP_ACCEPTANCE.md`](docs/MVP_ACCEPTANCE.md) | MVPの受入条件 |
| [`docs/PHASE3_ACCEPTANCE.md`](docs/PHASE3_ACCEPTANCE.md) | Phase 3の受入確認 |
| [`docs/PHASE4_ACCEPTANCE.md`](docs/PHASE4_ACCEPTANCE.md) | Phase 4のAndroid実機受入確認 |
| [`docs/PHASE5_ANDROID_BROWSER_CHECK.md`](docs/PHASE5_ANDROID_BROWSER_CHECK.md) | AndroidブラウザでのPhase 5実機表示確認手順 |
| [`docs/PHASE5_HANDOFF.md`](docs/PHASE5_HANDOFF.md) | Phase 5開始時の記録と完了結果 |
| [`docs/PHASE6_HANDOFF.md`](docs/PHASE6_HANDOFF.md) | Phase 6開始時の実装状況、残作業、再開手順 |
| [`docs/PHASE5_SCREEN_DESIGN.md`](docs/PHASE5_SCREEN_DESIGN.md) | Phase 5の画面構成、遷移、データ取得、表示規則 |

PC単体の復旧用ZIP作成は[PCバックアップ手順](docs/BACKUP_PC.md)を参照してください。Android単体は[worker手順](docs/BACKUP_ANDROID.md)を参照してください。PCからの統合実行は[両端バックアップ](docs/BACKUP_PAIR.md)を参照してください。

## セットアップ

新規環境は[初回セットアップ手順](docs/INITIAL_SETUP.md)に従い、PC・Android・閲覧端末の準備から初回送信と自動起動まで確認します。

PC側collectorとAndroidサーバーの設定・運用手順は`docs/`と各コンポーネントのREADMEを参照します。閲覧画面の配置・実機確認手順は[`docs/PHASE5_ANDROID_BROWSER_CHECK.md`](docs/PHASE5_ANDROID_BROWSER_CHECK.md)、次の作業は[`docs/PHASE6_HANDOFF.md`](docs/PHASE6_HANDOFF.md)を参照してください。

PC側collectorは`python -m tools.collector --config <collector.ini> collect-once`で1回実行できます。

## MVPの範囲外

- スマートフォンからのファイル編集やCodex操作
- プロジェクトファイル本体とGit差分本文の閲覧
- 複数ワークスペースを同時比較する画面や高度な切替UI
- インターネット経由の接続とTailscale
- PWA、WebSocket、SQLite
- 全文検索、Wiki自動生成、音声要約
- 高度なコードビュー、グラフ、利用統計

MVPの合否は[`docs/MVP_ACCEPTANCE.md`](docs/MVP_ACCEPTANCE.md)の10項目で判定します。

## 変更履歴

重要な設計・実装変更は[`CHANGELOG.md`](CHANGELOG.md)へ記録します。MVPの初回リリースまでは`Unreleased`へ追記します。
