# HTTPSスナップショット送信API

作業用PCは表示用JSONの一世代を`Snapshot`としてAndroidサーバー端末へ送る。通信はプライベートCAで検証するHTTPSだけを使用し、HTTPへのフォールバックや証明書検証の無効化を行わない。

## 概念

- **Delivery**：1回のHTTP POST。UUID形式の`X-Delivery-Id`で識別する。
- **Snapshot**：同じ`workspace_id`と`snapshot_id`を持つ表示用JSON一式。
- **Commit**：Android端末がstagingのSnapshot全体を公開対象へ切り替える操作。

Delivery IDは未送信キューに保存する値であり、通信切断後の再送でも必ず同じ値を使う。サーバーはstaging直下の非公開な`.deliveries/`へ、Delivery ID、送信先、相対パス、本文SHA-256からなるreceiptを原子的に保存する。同じDelivery IDでこれらが一致する再送は、JSONを再保存せず同じ`stored`応答を返す。不一致の再利用は`409 Conflict`で拒否する。

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

本文は表示用JSONを保存したBOMなしUTF-8の生バイト列であり、PC側senderは再整形・再エンコードしない。1リクエストは最大1MiBとし、PC側は送信前に確認する。Androidサーバー側もAPI POSTの本文を読む前に単一の10進数`Content-Length`を検査し、1MiB超過は`413 Payload Too Large`、長さ欠落は`411 Length Required`、不正値・重複値・`Transfer-Encoding`は`400 Bad Request`で空本文として拒否する。


## 受信JSONの検証

サーバーは保存前に、本文をBOMなしUTF-8として厳密に復号し、JSONオブジェクトとして解析する。JSONの重複キー、構文不正、UTF-8不正を許可しない。共通必須フィールドである`schema_version`、`data_type`、`snapshot_id`、`generated_at`、`workspace_id`、`session_id`は空でない文字列、`warnings`は配列とする。対応する`schema_version`は`1.x`だけとし、互換性規則に従い未知の任意フィールドは保持対象とする。

本文の`workspace_id`と`snapshot_id`はURLの値と完全一致しなければならない。不正本文、未対応スキーマ、識別情報不一致は詳細や本文を返さず、保存前に空本文の`400 Bad Request`で拒否する。
`POST /api/v1/snapshots/{workspace_id}/{snapshot_id}/{relative_json_path}`は、認証、URL、サイズ、UTF-8 JSON、スキーマ、識別情報、単一の小文字ハイフン形式UUIDである`X-Delivery-Id`を検証後にstagingへ原子的保存する。Delivery ID欠落・不正は`400 Bad Request`、staging未設定時は`503 Service Unavailable`、保存失敗時は詳細を返さず`500 Internal Server Error`とする。保存成功時は`200 OK`とUTF-8 JSONを返し、`delivery_id`は要求ヘッダー値、`snapshot_id`はURL値と完全一致する。

```json
{"status":"stored","delivery_id":"...","snapshot_id":"..."}
```

PC側はHTTPステータスだけでなく、`delivery_id`と`snapshot_id`が要求値と一致し、`status`が`stored`であることを確認する。

## HTTPステータス

- `400`：JSON、Delivery ID、識別情報、manifestなどのリクエスト値が不正。
- `401`：Bearer Tokenが不正または未指定。
- `404`：API URL、workspace ID、snapshot ID、相対パスが不正。
- `409`：同一Delivery IDの内容不一致、またはcommit manifestとstaging Snapshotの不一致。
- `411`：Content-Length未指定。
- `413`：本文が1MiB超過。
- `500`：保存・公開・receipt読込などサーバー側障害。
- `503`：staging設定が利用できない。
- `507`：保存先の空き容量が予約値を下回るか、容量を取得できないため、新規保存・公開を停止した。

## 状態確認

```text
GET /health?workspace_id={workspace_id}
```

`workspace_id`は任意のクエリであり、英数字、`.`、`_`、`-`だけを許可する。指定時の応答は通常の状態項目に加え、`workspace_id`、`current_snapshot_id`、`last_received_at`を持つ。`current_snapshot_id`はcommit済みpublic Snapshotの識別子、`last_received_at`はそのSnapshotのcommitをサーバーが受理して公開ポインタを切り替えた時刻で、タイムゾーン付きISO 8601文字列とする。workspace IDなし、公開済みSnapshotなし、または旧形式の`current.json`では、これらの値は`null`とする。存在しないworkspaceと公開済みSnapshotなしは区別しない。

クエリの形式が不正、`workspace_id`以外の項目を含む、または同じ`workspace_id`を複数指定した場合は`400 Bad Request`を返す。状態確認は閲覧用の認証不要GETであり、Tokenや内部エラー詳細を返さない。

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

サーバーはcommitを受理するまで新しいSnapshotを公開してはならない。現時点のcommit APIは、`files`配列の相対パス昇順、SHA-256（小文字16進64文字）、非負のバイト数を検証し、Delivery ID・URL・本文SHA-256を非公開`.commits/`receiptとして保存する。同じ内容の再送は同じ`committed`応答を返し、不一致の同一Delivery IDは409で拒否する。commit時に`staging/{workspace_id}/{snapshot_id}/`の全JSONをmanifestと照合し、サイズ・SHA-256・UTF-8スキーマ・workspace/snapshot識別子を再検証する。成功時は`public/{workspace_id}/snapshots/{snapshot_id}/`へ世代として配置し、`current.json`を原子的に置換する。GETはcurrent参照の世代だけを配信するため、未commitのstagingデータは公開されない。

検証済みの各JSONはstaging_directory/{workspace_id}/{snapshot_id}/{relative_json_path}だけへ保存する。保存時は同じ親ディレクトリに一時ファイルを作成し、UTF-8生バイト列を書込み・flush・fsyncした後、os.replace()で対象ファイルを原子的に置換する。stagingは公開先ではなく、この保存だけではブラウザに新しいSnapshotを公開しない。

## 相対パスと識別子

PC側は`.json`で終わる相対パスだけを送る。Windowsの`\`はURL用の`/`へ変換し、絶対パス、ドライブ指定、空要素、`.`、`..`を拒否する。URLでは各パス要素を個別にエンコードする。

Androidサーバー側もURLデコード後に同じ検証を行う。`workspace_id`と`snapshot_id`は英数字、`.`、`_`、`-`だけを許可し、パス区切り文字・空値・`..`を拒否する。`relative_json_path`は同じ文字種の空でないパス要素を`/`で連結した`.json`終端の相対パスだけを許可し、`..`、絶対パス、Windowsドライブ指定子、バックスラッシュ、空要素、クエリ文字列を拒否する。保存先は解決後もstagingルートの子孫であることを確認する。

## TLSと認証

PC側は`ca_file`を明示して`ssl.create_default_context()`を作り、通常のホスト名またはIPアドレスSAN検証を行う。Bearer Tokenはリポジトリ外の`token_file`から読み、ログ、例外、設定例、URLへ含めない。

## タイムアウト

senderは`[sender].timeout_seconds`（既定10秒、1～120秒）をTLS接続作成時へ渡す。接続開始中の`socket.timeout`は`connect_timeout`、リクエスト送信後に応答を待つ段階の`socket.timeout`は`read_timeout`として区別する。どちらも`retryable: true`であり、今回の実装は待機・再試行を行わず呼び出し元へ返す。

## 再試行

`send_snapshot_with_retry()`は、`retryable: true`の`SenderError`だけを再試行する。`max_attempts`は初回送信を含み、既定値5回では待機が1、2、4、8秒となる。待機時間は`backoff_initial_seconds * 2^(失敗回数 - 1)`を`backoff_max_seconds`で上限化する。

HTTP 429で整数秒の`Retry-After`が返った場合は、その値を指数バックオフより優先する。401/403、404、409、413、証明書検証、不正応答などは待機せず失敗として返す。再試行の各回は、ファイルとcommitの同じDelivery IDを再利用する。指数バックオフ完了後の永続キュー処理は別タスクである。

## 未送信キュー

`PendingSnapshotQueue`は、再試行後に失敗したSnapshotを`queue/<workspace_id>/<sequence>-<snapshot_id>/`へ保存する。本文は`files/`にUTF-8生バイト列のまま複製し、`manifest.json`には相対パス、サイズ、SHA-256、各Delivery ID、commit Delivery ID、最後の安全なエラー分類だけを保存する。Token、HTTP本文、応答本文、例外詳細は保存しない。

キュー項目は全ワークスペースで単調なsequence順に処理する。再送ではmanifestのDelivery IDを使い、commit成功を確認した後だけ該当ディレクトリを削除する。再送の失敗、改ざん、途中データは削除せず、SHA-256不一致は送信せずエラーにする。

## エラー分類

`SenderError`は`kind`、`retryable`、`operation`、HTTPステータス、相対パスを持つ。本文・Token・応答本文は持たない。

- 将来の再送対象：接続・DNS、接続/読み取りタイムアウト、HTTP 408、429、5xx。
- 設定またはデータ修正が必要：証明書検証、TLS設定、401/403、404、409、413、400、不正応答、Delivery ID不一致、Snapshot ID不一致。

指数バックオフと未送信キューの永続化は別タスクで実装する。これらは既存のDelivery IDを再利用して`retryable`な失敗を扱う。
