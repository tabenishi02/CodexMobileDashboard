# Android・Termux側サーバー

このディレクトリには、Androidサーバー端末のTermux上で動作するHTTPSサーバーを配置します。

> [!IMPORTANT]
> Python 3.10以上で動作する最小サーバー基盤と`/health`を実装済みです。HTTPS、認証、Snapshot API、静的配信は後続タスクです。

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
- 証明書のSANを接続先IPアドレスまたはホスト名と一致させる。
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
