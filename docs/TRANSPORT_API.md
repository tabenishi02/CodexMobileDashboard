# HTTPSスナップショット送信API

作業用PCは表示用JSONの一世代を`Snapshot`としてAndroidサーバー端末へ送る。通信はプライベートCAで検証するHTTPSだけを使用し、HTTPへのフォールバックや証明書検証の無効化を行わない。

## 概念

- **Delivery**：1回のHTTP POST。UUID形式の`X-Delivery-Id`で識別する。
- **Snapshot**：同じ`workspace_id`と`snapshot_id`を持つ表示用JSON一式。
- **Commit**：Android端末がstagingのSnapshot全体を公開対象へ切り替える操作。

Delivery IDは未送信キューに保存する値であり、通信切断後の再送でも必ず同じ値を使う。サーバーは同じDelivery IDを安全に再処理し、重複保存してはならない。

## ファイル送信

```text
POST /api/v1/snapshots/{workspace_id}/{snapshot_id}/{relative_json_path}
```

ヘッダー：

```text
Authorization: Bearer <token>
X-Delivery-Id: <UUID>
Content-Type: application/json
```

本文は表示用JSONを保存したBOMなしUTF-8の生バイト列であり、PC側senderは再整形・再エンコードしない。1リクエストは最大1MiBとし、上限超過は送信前に拒否する。

成功は実際にstagingへ原子的保存を終えてから、HTTP 2xxと次のJSONで返す。

```json
{"status":"stored","delivery_id":"...","snapshot_id":"..."}
```

PC側はHTTPステータスだけでなく、`delivery_id`と`snapshot_id`が要求値と一致し、`status`が`stored`であることを確認する。

## Commit

全ファイルが`stored`になった後だけ、次を呼び出す。

```text
POST /api/v1/snapshots/{workspace_id}/{snapshot_id}/commit
```

同じ認証・`X-Delivery-Id`・`Content-Type`を使用する。本文には、送信済みファイルの相対パス、UTF-8バイト数、SHA-256を含める。PC側では相対パス昇順にファイルを送る。1件でも未送信または失敗ならcommitしない。

成功応答：

```json
{"status":"committed","delivery_id":"...","snapshot_id":"..."}
```

サーバーはcommitを受理するまで新しいSnapshotを公開してはならない。受理時に、`staging/{workspace_id}/{snapshot_id}/`の全ファイルを検証し、current/public側を原子的に切り替える。

## 相対パスと識別子

PC側は`.json`で終わる相対パスだけを送る。Windowsの`\`はURL用の`/`へ変換し、絶対パス、ドライブ指定、空要素、`.`、`..`を拒否する。URLでは各パス要素を個別にエンコードする。

Androidサーバー側もURLデコード後に同じ検証を行う。`workspace_id`と`snapshot_id`は英数字、`.`、`_`、`-`だけを許可し、パス区切り文字・空値・`..`を拒否する。保存先は解決後もstagingルートの子孫であることを確認する。

## TLSと認証

PC側は`ca_file`を明示して`ssl.create_default_context()`を作り、通常のホスト名またはIPアドレスSAN検証を行う。Bearer Tokenはリポジトリ外の`token_file`から読み、ログ、例外、設定例、URLへ含めない。

## エラー分類

`SenderError`は`kind`、`retryable`、`operation`、HTTPステータス、相対パスを持つ。本文・Token・応答本文は持たない。

- 将来の再送対象：接続・DNS、接続/読み取りタイムアウト、HTTP 408、429、5xx。
- 設定またはデータ修正が必要：証明書検証、TLS設定、401/403、404、409、413、400、不正応答、Delivery ID不一致、Snapshot ID不一致。

指数バックオフと未送信キューの永続化は別タスクで実装する。これらは既存のDelivery IDを再利用して`retryable`な失敗を扱う。
