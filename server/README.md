# Android・Termux側サーバー

このディレクトリには、Androidサーバー端末のTermux上で動作するHTTPSサーバーを配置します。

> [!IMPORTANT]
> Python 3.10以上で動作する最小サーバー基盤と`/health`を実装済みです。HTTPS、認証、Snapshot API、静的配信は後続タスクです。

## 実装・テスト結果

### 実装済み

- Python 3.10以上・標準ライブラリの`ThreadingHTTPServer`基盤
- UTF-8 INIの`--config`による実設定読込と、リポジトリ外Tokenファイルの読込
- `GET /health`
- `GET /data/{workspace_id}/{relative_json_path}`でcommit済みpublic配下のJSONだけを返す（ETagとIf-None-Matchによる304対応）
- `--static-dir`で固定する実在ディレクトリ
- `GET /`からstatic_dir直下の`index.html`配信
- HTML、CSS、JavaScriptのContent-Type判定
- URLデコード後のパストラバーサル、絶対パス、Windowsドライブ指定子、バックスラッシュ拒否
- ディレクトリURLと存在しない静的ファイルの404

### 未実装

HTTPS受信、認証、Snapshot POST・commit、JSON GET、キャッシュ、ログ、容量監視は後続タスクで追加する。

### 確認済みテスト

`server/tests/test_server.py`で、ヘルスチェック、ルートindex配信、Content-Type、危険な静的パス拒否、ディレクトリ・存在しないファイルの404を確認している。

## 役割

Androidサーバー端末は、作業用PCが生成した表示用JSONと固定のWeb画面を保存・配信します。

- 認証付きHTTPS POSTの受付
- リクエストサイズの確認
- JSON形式と送信先の検証
- 許可されたJSONの安全な保存
- 固定HTML、CSS、JavaScriptの配信
- JSON取得用HTTPS GETの提供
- ヘルスチェックの提供
- アクセスログとエラーログの保存
- 保存容量の監視

## 行わない処理

データの収集・解析・整形は作業用PC側で完了させます。Androidサーバー端末では次の処理を行いません。

- Codex JSONLの探索・読み取り・解析
- Gitコマンドの実行と変更内容の解析
- プロジェクトファイルの読み取り
- 会話、エラー、決定事項、TODOの抽出
- 変更内容のセマンティックな要約
- 表示用JSONの分類・再構成
- プロジェクトファイルやGit差分本文の保存

## 実装条件

- Python 3.10以上で動作すること
- Python標準ライブラリだけを使用すること
- Python 3.11以降で追加された機能に依存しないこと
- 基準端末の検証用Android端末とPython 3.14.6で実機確認すること
- 特定メーカーやCPUアーキテクチャに依存しないこと
- Termuxのアプリ専用領域に配置すること
- TCP 8765番ポートを初期値とし、INI設定で変更可能にすること
- `ssl.SSLContext`を使用し、Python 3.10以上でHTTPSを提供すること
- Python 3.14で追加された`HTTPSServer`には依存しないこと

## 静的配信ルート

サーバーは`--static-dir`で指定された実在ディレクトリを解決済みの固定ルートとして保持する。未指定時は起動ディレクトリを使用する。`/`はstatic_dir直下の`index.html`だけを配信する。存在しない場合は404を返す。HTMLは`text/html; charset=utf-8`、CSSは`text/css; charset=utf-8`、JavaScriptは`text/javascript; charset=utf-8`として配信する。静的パスはURLデコード後に検証し、`..`、絶対パス、Windowsドライブ指定子、バックスラッシュを拒否する。ディレクトリURLと存在しない静的ファイルは一覧や詳細を返さず404で拒否する。static_dirの`index.html`配信、Content-Type、404、危険なパス拒否は単体テストで確認する。その他の固定ファイル配信規則は後続タスクで追加する。

## 予定構成

実装時点で必要性を再確認し、不要なファイルやクラスは作成しません。

```text
server/
├─ README.md           # 本書
├─ server.py           # サーバーの起動点
└─ tests/              # サーバー単体テスト
```

固定HTML、CSS、JavaScriptは`client/`、受信した表示用JSONは実行環境のデータ保存先に配置します。実際の設定ファイルと認証トークンはリポジトリ外に置きます。

## PC senderとの通信契約

PCは`POST /api/v1/snapshots/{workspace_id}/{snapshot_id}/{relative_json_path}`へ、Bearer Token、UUIDの`X-Delivery-Id`、UTF-8 JSON生バイト列を送信する。サーバーは永続化完了後に`status: stored`、同じDelivery ID、Snapshot IDをJSONで返す。

全ファイルの保存後にPCは`POST /api/v1/snapshots/{workspace_id}/{snapshot_id}/commit`を呼ぶ。サーバーはstaging内のファイルを検証し、commitを受理したSnapshotだけを公開する。`metadata.json`受信だけで公開してはならない。同じDelivery IDの再送は冪等に扱う。

URLデコード後にもworkspace ID、snapshot ID、相対JSONパスを検証し、絶対パス、`..`、`\\`、保存ルート外への解決を拒否する。詳細は[`../docs/TRANSPORT_API.md`](../docs/TRANSPORT_API.md)を正とする。

## 処理の流れ

### JSON更新

```text
作業用PC
  ↓ 認証付きHTTPS POST
リクエストサイズ確認
  ↓
認証確認
  ↓
許可された送信先とJSON形式を検証
  ↓
一時ファイルへ書き込み
  ↓
既存ファイルと原子的に置換
  ↓
成功応答
```

検証または保存に失敗した場合は既存の正常なJSONを維持し、適切なHTTPステータスを返します。

### ブラウザ表示

```text
閲覧スマートフォン
  ↓ HTTPS GET
固定HTML・CSS・JavaScript
  ↓ HTTPS GET
表示用JSON
```

内容が変わっていない場合は、ETagと`If-None-Match`により`304 Not Modified`を返します。

## セキュリティ

- POSTには認証トークンを必須とする。
- トークンはリポジトリ外の秘密ファイルから読み取る。
- トークン比較には`hmac.compare_digest()`を使用する。
- トークン、HTTP本文、チャット全文をログへ出力しない。
- 許可されたJSON以外への書き込みを拒否する。
- パストラバーサルを拒否する。
- 不正JSONとサイズ超過を保存前に拒否する。
- サーバー内部の詳細エラーを作業用PCへ逆送しない。
- MVPでは信頼できるLAN内だけで使用し、ルーターのポート転送を行わない。
- プライベートCAが発行したサーバー証明書と秘密鍵を使用する。
- 証明書のSANを接続先IPアドレスまたはホスト名と一致させ、PC senderの通常のTLSホスト名検証で確認する。
- CA秘密鍵とサーバー秘密鍵をGit、ログ、表示用JSONへ含めない。
- 平文HTTPは架空のサンプルデータを使う独立した疎通確認だけに限定する。

## 保存と復旧

- JSONはUTF-8で保存する。
- 一時ファイルへ完全に書き込んでから置換する。
- 同時読み書きでJSONが破損しないようにする。
- 同じ送信識別子の再送を重複保存しない。
- 容量不足時は過去データを自動削除せず、新規保存を停止する。
- 詳細なサーバーエラーは端末内の`logs/server.log`へ保存する。
- 運用ログは日単位でローテーションし、直近7日分を保持する。

## 関連資料

- [`../PROJECT.md`](../PROJECT.md)：全体設計
- [`../docs/CONFIGURATION.md`](../docs/CONFIGURATION.md)：設定と秘密情報
- [`../docs/DATA_LIMITS.md`](../docs/DATA_LIMITS.md)：送信対象とサイズ制限
- [`../docs/ERROR_HANDLING.md`](../docs/ERROR_HANDLING.md)：エラー処理
- [`../docs/LOGGING.md`](../docs/LOGGING.md)：ログ仕様
- [`../docs/NETWORK.md`](../docs/NETWORK.md)：LAN内通信
- [`../docs/TERMUX_ENVIRONMENT.md`](../docs/TERMUX_ENVIRONMENT.md)：Android端末要件と基準端末
- [`../docs/UPDATE_POLICY.md`](../docs/UPDATE_POLICY.md)：更新頻度と保存期間
- [`../docs/MVP_ACCEPTANCE.md`](../docs/MVP_ACCEPTANCE.md)：MVP受入条件

## 実設定とBearer Token

通常運用では、Git管理外のUTF-8 INIを指定して起動する。設定例は[`../config/server.example.ini`](../config/server.example.ini)であり、実ファイルにはコピー先を使用する。

```sh
mkdir -p ~/.config/codex-mobile-dashboard/tls
chmod 700 ~/.config/codex-mobile-dashboard ~/.config/codex-mobile-dashboard/tls
# server.iniとserver.token、TLS証明書・秘密鍵をリポジトリ外に配置する
chmod 600 ~/.config/codex-mobile-dashboard/server.ini ~/.config/codex-mobile-dashboard/server.token ~/.config/codex-mobile-dashboard/tls/server.key
python server.py --config ~/.config/codex-mobile-dashboard/server.ini
```

`[server]`には接続先、ポート、証明書・秘密鍵、静的画面、公開JSONの各パスを設定する。`[auth] token_file`にはTokenを1行だけ保存した別ファイルを指定する。Tokenの値をINI、コマンドライン、ログ、Gitへ置かない。読み込み時には前後の空白・改行を除去し、空ファイルは`token_file_invalid`として起動を中止する。`--config`起動はHTTPS証明書と秘密鍵を必須とし、平文HTTPへフォールバックしない。

この時点ではTokenの読込だけを実装しており、POST APIでのBearer Token照合は後続タスクで追加する。