# 表示用JSONデータ仕様

## 状態

本書に記載した共通フィールド、ID、日時、メッセージ、エラー、ファイル変更、決定事項、次タスク、メタデータ、欠損値、列挙値、物理配置、スキーマ互換性をMVP仕様として採用する。

## 実データの規模

既存の3セッションを本文を出力せずに集計した。

| セッション | JSONL行数 | response item | message | user + assistant |
|---|---:|---:|---:|---:|
| `019f1ec9-...` | 4,469 | 2,743 | 638 | 602 |
| `019f82d4-...` | 717 | 321 | 106 | 97 |
| `019fc727-...` | 1,491以上 | 798以上 | 187以上 | 181以上 |

3件目は現在使用中のセッションであり、集計後も増加する可能性がある。

JSONLの`response_item`内のメッセージには、`msg_<UUID>`形式のネイティブIDがある。例：

```text
msg_019fc728-bdb2-7471-ac9a-370c26f24a2b
```

## IDとシーケンス

- `session_id`はJSONLファイル名または`session_meta`から得たUUIDをそのまま保持する。
- `message_id`はJSONLのネイティブ`payload.id`を優先する。
- ネイティブIDがない正規化メッセージは、セッションID、情報種別、元レコード位置からSHA-256で決定的な代替IDを生成する。
- `sequence`はセッション内の表示対象メッセージを1から数える整数とする。
- IDの一意性は`session_id`と各データIDの組で保証する。
- JSON内の`sequence`は整数のまま保存し、桁数を固定しない。
- ページ、断片、代替IDのファイル名では最小6桁でゼロ埋めする。100万を超えた場合は7桁以上へ自然に拡張し、上限を理由に削除しない。

実測最大は638 messageレコードである。ツール呼び出しなどを同じタイムラインに含めても2,743 response item、全JSONLレコードを連番にしても4,469であり、6桁表示には大きな余裕がある。

## 共通フィールド

すべてのトップレベルJSONに次を持たせる。

| フィールド | 型 | 必須 | `null` | 定義 |
|---|---|---|---|---|
| `schema_version` | string | 必須 | 不可 | JSON構造のバージョン。初期値は`1.0` |
| `data_type` | string | 必須 | 不可 | `dashboard`、`recent`などデータ種別 |
| `snapshot_id` | string | 必須 | 不可 | 同じ収集処理で生成したJSONの識別子 |
| `generated_at` | string | 必須 | 不可 | PC生成日時。タイムゾーン付きISO 8601 |
| `workspace_id` | string | 必須 | 不可 | 正規化した対象ワークスペースID |
| `session_id` | string | 必須 | 不可 | CodexセッションUUID |
| `warnings` | array | 必須 | 不可 | 警告がなければ空配列 |

主要フィールドは省略しない。未取得は`null`、0件は空配列、空文字自体に意味がある場合だけ`""`を使用する。

### 必須・任意・`null`の原則

- 本書の表に載せたMVPフィールドは、原則としてすべてキー自体を必須とする。受信側は「送信漏れ」と「値を取得できなかった状態」を区別できる。
- 表の`null`が「可」のフィールドは、キーを残したまま値を`null`にする。
- 配列は0件でも省略せず`[]`、オブジェクトはその種類を使用しない場合でも、個別仕様に従い`null`または必須キーを持つオブジェクトにする。
- 任意フィールドは、同一メジャーバージョンで後から追加する表示補助情報だけに限定する。クライアントは未知の任意フィールドを無視する。
- `content.kind`のような判別値によって必要になるフィールドは「条件付き必須」とする。別の種類ではそのフィールドを付けない。

`warnings`の各要素は、`warning_id`、`severity`、`code`、`summary`、`occurred_at`、`source_message_ids`を必須とする。`occurred_at`だけは元日時不明の場合に`null`を許可する。警告本文の全文は重複保存せず、必要なら元メッセージを参照する。

## 日時

- タイムゾーンを含むISO 8601文字列を使用する。
- 日本環境で生成する例は`2026-08-04T15:30:00+09:00`とする。
- 元データがUTCの場合も同一の瞬間を示す値として保持し、必要ならクライアントで表示タイムゾーンへ変換する。
- 日時不明は`null`とし、現在時刻で補完しない。

## `dashboard.json`

現在状態を短時間で把握するための要約であり、128KiB以内を目安とする。

| フィールド | 型 | 必須 | `null` | 定義 |
|---|---|---|---|---|
| `project.name` | string | 必須 | 不可 | 表示用プロジェクト名 |
| `project.phase` | string | 必須 | 不可 | 現在の開発段階 |
| `codex.status` | string | 必須 | 不可 | Codex状態 |
| `codex.current_work` | string | 必須 | 可 | 現在の作業。未取得なら`null` |
| `latest.user_message_id` | string | 必須 | 可 | 最新ユーザーメッセージ参照 |
| `latest.assistant_message_id` | string | 必須 | 可 | 最新Codexメッセージ参照 |
| `latest.summary` | string | 必須 | 可 | 最新やりとりの短い要約 |
| `next_actions` | array | 必須 | 不可 | 明示または推定した次タスク |
| `errors.open` | integer | 必須 | 不可 | 未解決エラー数 |
| `errors.critical` | integer | 必須 | 不可 | 未解決重大エラー数 |
| `errors.latest_error_id` | string | 必須 | 可 | 最新エラー参照 |
| `git.collection_status` | string | 必須 | 不可 | Git取得状態 |
| `git.branch` | string | 必須 | 可 | 現在ブランチ |
| `git.changed_files` | integer | 必須 | 不可 | 変更ファイル数 |
| `git.staged_files` | integer | 必須 | 不可 | ステージ済みファイル数 |
| `git.untracked_files` | integer | 必須 | 不可 | 未追跡ファイル数 |

`next_actions`の各要素：

| フィールド | 型 | 必須 | `null` | 定義 |
|---|---|---|---|---|
| `task_id` | string | 必須 | 不可 | 次タスクID |
| `text` | string | 必須 | 不可 | スマートフォンに表示する内容 |
| `status` | string | 必須 | 不可 | `pending`または`in_progress` |
| `origin` | string | 必須 | 不可 | `explicit`、`codex_inferred`、`fallback` |
| `confidence` | string | 必須 | 不可 | `high`、`medium`、`low` |
| `reason` | string | 必須 | 可 | 推定理由 |
| `source_message_ids` | array | 必須 | 不可 | 根拠メッセージ参照 |

## `recent.json`

直近2件の「ユーザー指示と、それに対するCodex応答」の組を保持する。一般イベントの横断タイムラインにはしない。

| フィールド | 型 | 必須 | `null` | 定義 |
|---|---|---|---|---|
| `current_turn` | object | 必須 | 可 | 応答中のやりとり。なければ`null` |
| `turns` | array | 必須 | 不可 | 完了済みの新しい順2件 |

turn要素：

| フィールド | 型 | 必須 | `null` | 定義 |
|---|---|---|---|---|
| `turn_id` | string | 必須 | 不可 | やりとりID |
| `status` | string | 必須 | 不可 | `in_progress`、`completed`、`failed` |
| `started_at` | string | 必須 | 不可 | ユーザー指示日時 |
| `completed_at` | string | 必須 | 可 | 完了日時 |
| `user_message_id` | string | 必須 | 不可 | ユーザーメッセージ参照 |
| `assistant_message_ids` | array | 必須 | 不可 | 応答メッセージ参照。分割応答に対応 |
| `user_preview` | string | 必須 | 不可 | 一覧用プレビュー |
| `assistant_preview` | string | 必須 | 可 | 応答中または失敗時は`null`可 |

プレビューは表示用の派生情報であり、全文の正は`messages`データとする。

応答中は`current_turn`へ現在のやりとりを置き、`turns`には直前までに完了した2件を残す。応答完了時に`current_turn`を`turns`の先頭へ移し、3件目になった古いターンを`recent.json`からだけ外す。元メッセージは`messages`に残すため削除されない。

## `messages.json`とページ

`messages.json`はページ索引とし、会話本文の正はページおよび断片に置く。

索引：

| フィールド | 型 | 必須 | `null` | 定義 |
|---|---|---|---|---|
| `total_messages` | integer | 必須 | 不可 | 対象メッセージ総数 |
| `latest_sequence` | integer | 必須 | 可 | 0件なら`null` |
| `pages` | array | 必須 | 不可 | ページ索引 |

ページ索引要素：`page`、`path`、`first_sequence`、`last_sequence`、`message_count`、`byte_size`、`sha256`をすべて必須・非`null`とする。ページファイルにも共通フィールドを持たせ、加えて`page`と`messages`を必須・非`null`とする。

メッセージ要素：

| フィールド | 型 | 必須 | `null` | 定義 |
|---|---|---|---|---|
| `message_id` | string | 必須 | 不可 | JSONLネイティブIDまたは決定的な代替ID |
| `source_message_id` | string | 必須 | 可 | JSONLネイティブID。代替ID使用時は`null` |
| `sequence` | integer | 必須 | 不可 | セッション内表示順。1開始 |
| `created_at` | string | 必須 | 可 | 元日時不明なら`null` |
| `role` | string | 必須 | 不可 | 発言主体 |
| `message_type` | string | 必須 | 不可 | 正規化したメッセージ種別 |
| `phase` | string | 必須 | 可 | JSONLのphaseがなければ`null` |
| `content` | object | 必須 | 不可 | 本文または断片参照 |
| `redactions` | array | 必須 | 不可 | マスク情報。なければ空配列 |

### 本文形式

作業用PC側で通常文とフェンス付きコードブロックを分離し、クライアントは整理済みブロックを安全に表示する方式を採用する。

案1は本文を単一テキストとして送り、コード判定と表示をクライアントJavaScriptが行う方式である。案2はPC側で通常文とコードを分離して送り、クライアントJavaScriptは整理済みブロックの表示だけを行う方式である。この理解でよい。MVPでは閲覧端末の処理を軽くし、複数クライアントで判定結果を統一できる案2を採用する。

```json
{
  "kind": "blocks",
  "blocks": [
    {"block_id": "block-0001", "type": "text", "text": "説明です。"},
    {"block_id": "block-0002", "type": "code", "language": "python", "text": "print('test')"}
  ]
}
```

`kind: blocks`では`blocks`が条件付き必須である。各ブロックの`block_id`、`type`、`text`は必須・非`null`、コードブロックの`language`は必須だが言語不明なら`null`とする。`type`は`text`または`code`である。

- ブロックの順序と全文を保持する。
- MVPではフェンス付きコードブロックだけを確実に分離する。
- 構文が曖昧または壊れている場合は、メッセージ全体を`plain_text`として保持し、推測で欠落させない。
- クライアントはコードを実行せず、テキスト要素として表示する。

長文は`kind: chunked_blocks`として、`original_byte_size`、`chunk_count`、`chunks`、`sha256`を条件付き必須とする。`chunks`の各要素は`part`、`path`、`byte_size`、`sha256`を必須・非`null`とする。断片ファイルには共通フィールドに加えて`message_id`、`part`、`total_parts`、`blocks`を必須・非`null`として持たせる。

## `errors.json`

開発エラーの索引・概要・解決状態の正とする。システム運用エラー全文は端末ごとのローテーションログに保存する。

| フィールド | 型 | 必須 | `null` | 定義 |
|---|---|---|---|---|
| `errors` | array | 必須 | 不可 | エラー一覧。物理ページは最大100件 |
| `error_id` | string | 必須 | 不可 | セッション内エラーID |
| `fingerprint` | string | 必須 | 不可 | 重複判定用SHA-256 |
| `first_occurred_at` | string | 必須 | 不可 | 初回日時 |
| `last_occurred_at` | string | 必須 | 不可 | 最終日時 |
| `occurrence_count` | integer | 必須 | 不可 | 発生回数 |
| `severity` | string | 必須 | 不可 | 重要度 |
| `category` | string | 必須 | 不可 | エラー分類 |
| `summary` | string | 必須 | 不可 | 最大500文字の要約 |
| `details_preview` | string | 必須 | 可 | 最大4,000文字の表示用抜粋 |
| `source_message_ids` | array | 必須 | 不可 | 全文を含むメッセージ参照 |
| `detail_storage` | string | 必須 | 不可 | `message_reference`、`inline`、`chunks` |
| `details` | string | 必須 | 可 | `inline`時の欠落のない全文。それ以外は`null` |
| `detail_chunks` | array | 必須 | 不可 | `chunks`時の断片参照。それ以外は空配列 |
| `status` | string | 必須 | 不可 | `open`、`resolved`、`ignored` |
| `resolved_at` | string | 必須 | 可 | 未解決なら`null` |
| `resolution` | string | 必須 | 可 | 未解決なら`null` |

保存候補は次の3つである。

1. 概要と元メッセージ参照だけ：最小だが、一覧だけでは原因を判断しにくい。
2. 概要、最大4,000文字のプレビュー、元メッセージ参照：通信量と可読性のバランスがよく、推奨する。
3. エラー全文を独立して複製：単独で完結するが、通信量と保存量が増え、メッセージとの不整合も生じ得る。

採用方式は候補2である。全文がメッセージに存在しない場合だけ、`inline`または`chunks`で欠落なく保存する。`detail_storage`が`message_reference`なら`source_message_ids`を1件以上、`inline`なら`details`を非`null`、`chunks`なら`detail_chunks`を1件以上にする。古い解決済みエラーは削除せずページ分割する。

## `decisions.json`

決定事項は上書きせず、追加型の履歴として同じ論理データセットに保存する。

| フィールド | 型 | 必須 | `null` | 定義 |
|---|---|---|---|---|
| `decisions` | array | 必須 | 不可 | 決定履歴 |
| `decision_id` | string | 必須 | 不可 | 決定ID |
| `decided_at` | string | 必須 | 不可 | 決定日時 |
| `status` | string | 必須 | 不可 | 決定状態 |
| `title` | string | 必須 | 不可 | 短い表題 |
| `description` | string | 必須 | 不可 | 決定内容 |
| `reason` | string | 必須 | 可 | 理由不明なら`null` |
| `source_message_ids` | array | 必須 | 不可 | 根拠メッセージ |
| `supersedes` | string | 必須 | 可 | 置き換え前の決定ID |
| `superseded_by` | string | 必須 | 可 | 置き換え後の決定ID |

旧決定は`superseded`へ変更して残し、新決定との相互参照を持たせる。

## `files.json`

Git変更メタデータの正とする。プロジェクトファイルとGit差分本文は含めない。

| フィールド | 型 | 必須 | `null` | 定義 |
|---|---|---|---|---|
| `repository.collection_status` | string | 必須 | 不可 | Git取得状態 |
| `repository.root_name` | string | 必須 | 可 | 表示用ルート名 |
| `repository.branch` | string | 必須 | 可 | detachedまたは失敗時は`null`可 |
| `repository.head` | string | 必須 | 可 | 取得失敗時は`null` |
| `repository.session_start_commit` | string | 必須 | 可 | 未取得なら`null` |
| `repository.clean` | boolean | 必須 | 不可 | 作業ツリー状態 |
| `files` | array | 必須 | 不可 | 変更ファイル一覧 |
| `change_id` | string | 必須 | 不可 | 変更レコードID |
| `path` | string | 必須 | 不可 | プロジェクト相対パス |
| `old_path` | string | 必須 | 可 | 名前変更以外は`null` |
| `status.index` | string | 必須 | 不可 | ステージ領域の状態 |
| `status.worktree` | string | 必須 | 不可 | 作業ツリーの状態 |
| `numstat.staged` | object | 必須 | 可 | 対象なしなら`null` |
| `numstat.unstaged` | object | 必須 | 可 | 対象なしなら`null` |
| `numstat.committed_in_session` | object | 必須 | 可 | 対象なしなら`null` |
| `binary` | boolean | 必須 | 不可 | バイナリ判定 |
| `scopes` | array | 必須 | 不可 | 変更が存在する範囲 |

各`numstat`オブジェクトの`added`と`deleted`はキーを必須とし、テキストファイルでは0以上の整数、Gitが行数を算出しないバイナリでは`null`とする。

コミット済み変更を別scopeで保持する理由は、コミット後に`git status`から消える変更をセッション履歴として失わないためである。同一ファイルがコミット後に再変更された場合も、コミット済みと現在の未コミット状態を混同しない。

## `metadata.json`

PC生成データ全体のスキーマ、収集状態、スナップショット構成を示す。

| フィールド | 型 | 必須 | `null` | 定義 |
|---|---|---|---|---|
| `collector.status` | string | 必須 | 不可 | PC収集ツール状態 |
| `collector.codex_status` | string | 必須 | 不可 | Codex状態 |
| `collector.last_checked_at` | string | 必須 | 不可 | 最終確認日時 |
| `collector.last_data_change_at` | string | 必須 | 可 | データ未生成なら`null` |
| `collector.last_acknowledged_snapshot_id` | string | 必須 | 可 | 前回受信成功スナップショット |
| `collector.last_send_succeeded_at` | string | 必須 | 可 | 前回送信成功日時 |
| `snapshot.state` | string | 必須 | 不可 | `building`、`complete`、`failed` |
| `snapshot.files` | array | 必須 | 不可 | ファイル名、サイズ、SHA-256 |

`snapshot.files`の各要素は`path`、`byte_size`、`sha256`を必須・非`null`とする。`metadata.json`自身は内容に自己参照するハッシュを持てないため、この一覧から除外する。

今回のスナップショットの受信日時は`metadata.json`へ自己記録しない。Androidサーバー端末が、最終受信日時と受信スナップショットIDを内部状態およびヘルスチェック応答で管理する。表示用JSONの解析・再構成は行わない。

## 列挙値と日本語表示

保存値は英小文字、画面表示は日本語とする。

| 分類 | 保存値 | 日本語表示 |
|---|---|---|
| ファイル | `none` | 変更なし |
| ファイル | `added` | 追加 |
| ファイル | `modified` | 修正 |
| ファイル | `deleted` | 削除 |
| ファイル | `renamed` | 名前変更 |
| ファイル | `conflicted` | 競合 |
| ファイル | `untracked` | 未追跡 |
| Codex | `working` | 作業中 |
| Codex | `idle` | 待機中 |
| Codex | `stopped` | 停止 |
| Codex | `unknown` | 状態不明 |
| 決定 | `adopted` | 採用 |
| 決定 | `rejected` | 非採用 |
| 決定 | `on_hold` | 保留 |
| 決定 | `superseded` | 後の決定により置換 |
| エラー | `open` | 未解決 |
| エラー | `resolved` | 解決済み |
| エラー | `ignored` | 対応不要 |
| 重要度 | `debug` | デバッグ |
| 重要度 | `info` | 情報 |
| 重要度 | `warning` | 注意 |
| 重要度 | `error` | エラー |
| 重要度 | `critical` | 重大 |
| ターン | `in_progress` | 応答中 |
| ターン | `completed` | 完了 |
| ターン | `failed` | 失敗 |
| 収集 | `ok` | 正常 |
| 収集 | `warning` | 注意あり |
| 収集 | `failed` | 取得失敗 |
| 次タスク | `pending` | 未着手 |
| 次タスク | `in_progress` | 実行中 |
| 生成元 | `explicit` | 会話で明示 |
| 生成元 | `codex_inferred` | Codexによる推定 |
| 生成元 | `fallback` | タスク一覧から補完 |
| 確信度 | `high` | 高 |
| 確信度 | `medium` | 中 |
| 確信度 | `low` | 低 |
| エラー詳細 | `message_reference` | 関連メッセージ参照 |
| エラー詳細 | `inline` | 本文内に保存 |
| エラー詳細 | `chunks` | 分割して保存 |
| スナップショット | `building` | 作成中 |
| スナップショット | `complete` | 完成 |
| スナップショット | `failed` | 作成失敗 |
| 変更範囲 | `committed_in_session` | セッション中にコミット済み |
| 変更範囲 | `staged` | コミット予定 |
| 変更範囲 | `worktree` | 未ステージ |
| エラー分類 | `session_collection` | セッション収集 |
| エラー分類 | `git_collection` | Git情報取得 |
| エラー分類 | `data_conversion` | データ変換 |
| エラー分類 | `http_send` | HTTP送信 |
| エラー分類 | `command` | コマンド実行 |

未知の列挙値は推測で翻訳せず、`未対応（値）`と表示する。

## `role`と`message_type`

JSONLの`response_item`で`payload.type`が`message`の場合、`payload.role`に`user`、`assistant`、`developer`がある。ツール呼び出しと出力はmessageとは別のpayload typeで記録される。

正規化後：

| `role` | 定義 | 既定表示 |
|---|---|---|
| `user` | ユーザーの指示 | 展開 |
| `assistant` | Codexの回答 | 展開 |
| `tool` | コマンド・ツールと結果 | 折りたたみ |
| `developer` | 開発者・製品側指示 | 非表示 |
| `system` | システムイベント | 非表示または状態表示 |

| `message_type` | 具体例 |
|---|---|
| `chat` | user/assistantの通常メッセージ |
| `tool_call` | コマンド、関数、MCP呼び出し |
| `tool_output` | コマンド・ツールの出力 |
| `developer_instruction` | developerメッセージ |
| `system_notice` | セッション開始、圧縮、完了など |

`role`は「誰が生成したか」、`message_type`は「何の種類か」を表す。JSONLの内部値をそのまま公開仕様にせず、PC側で正規化する。

画面上の具体的な表示名は、`user`＝「あなた」、`assistant`＝「Codex」、`tool`＝「ツール」、`developer`＝「開発者指示」、`system`＝「システム」とする。メッセージ種別は、`chat`＝「会話」、`tool_call`＝「ツール呼び出し」、`tool_output`＝「ツール結果」、`developer_instruction`＝「開発者指示」、`system_notice`＝「システム通知」とする。

## 物理配置

```text
dashboard.json
recent.json
errors.json
decisions.json
files.json
metadata.json
messages.json
messages/
├─ pages/
│  ├─ page-000001.json
│  └─ page-000002.json
└─ chunks/
   ├─ <message-id>-part-000001.json
   └─ <message-id>-part-000002.json
```

断片ファイル名の`<message-id>`には確定した`message_id`を使用する。ファイル名として不適切な文字が含まれる場合は、許可文字へ正規化し、JSON内部のIDは変更しない。

たとえば`message_id`が`msg_019fc728-bdb2-7471-ac9a-370c26f24a2b`なら、断片名は`msg_019fc728-bdb2-7471-ac9a-370c26f24a2b-part-000001.json`、`...-part-000002.json`となる。セッションIDとシーケンスを連結した独自IDへ置き換えず、JSONL由来のメッセージIDを保つ。

## 次タスク推定

1. 最新のユーザー・Codexメッセージに明示された次タスクを抽出する。
2. 明示されていない場合、PC側からCodex CLIの非対話モードを読み取り専用・一時セッションで呼び出し、構造化JSONとして推定する。
3. 同じ根拠メッセージでは再推定せずキャッシュする。
4. Codex実行が失敗した場合は`TASKS.md`の最初の未完了項目を`origin: fallback`として表示し、推定失敗の警告を付ける。

予定コマンドの形：

```text
codex exec --ephemeral --sandbox read-only \
  --output-schema next-task.schema.json \
  -o next-task.json \
  "与えられた開発コンテキストから、実行可能な次タスクを1件推定してください。"
```

入力には秘密情報を除外した現在状況、最新2ターン、決定事項、未完了タスクだけを渡す。結果には`task`、`reason`、`confidence`を必須とする。Codex CLIの認証、利用制限、タイムアウト、失敗時の処理を実装前に確認する。

非対話実行、`--ephemeral`、読み取り専用sandbox、`--output-schema`、`-o`の仕様は[Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode.md)を根拠とする。

## マスク済み文字列

マスク前データを新たに複製しない方針と、マスク後文字列の表現は別の問題として扱う。元のCodex JSONLは読み取り専用の情報源として残るが、本システムはマスク前の値を追加のファイルやログへ複製しない。

採用するマスク表現：

```text
[REDACTED:API_KEY]
[REDACTED:TOKEN]
[REDACTED:PASSWORD]
[REDACTED:PRIVATE_KEY]
[REDACTED:URL_CREDENTIAL]
```

- 秘密部分全体を固定マーカーへ置換し、先頭・末尾・長さを残さない。
- 前後の安全な説明文は保持する。
- JSONの秘密フィールドは値全体を置換する。
- URLはユーザー名・パスワード部分全体を除去する。
- `redactions`には種別、検出方法、対象ブロックIDだけを保存し、原文、ハッシュ、長さを保存しない。
- クライアントはマーカーを「秘密情報を除外しました」と表示する。
- 運用ログにはマスク件数と種別だけを記録する。

例：

```json
{
  "text": "Authorization: [REDACTED:TOKEN]",
  "redactions": [
    {
      "redaction_id": "redaction-000001",
      "type": "token",
      "block_id": "block-000001",
      "detector": "authorization_header"
    }
  ]
}
```

## スキーマ互換性

- `1.0`：初期MVP。
- 同じメジャーバージョンの任意フィールド追加は後方互換とする。
- `1.1`：後方互換を保ったフィールド・列挙値追加。
- `2.0`：既存フィールド削除、必須化、型変更など破壊的変更。
- ブラウザは同じメジャーバージョンの未知フィールドを無視する。
- 未対応の新しいメジャーバージョンでは詳細表示を停止し、「未対応データ形式」と表示する。
- 既存の履歴JSONは書き換えず、読み取り互換または移行手順を用意する。

## スナップショット更新順序

1. メッセージ断片
2. メッセージページ
3. `messages.json`
4. `errors.json`
5. `decisions.json`
6. `files.json`
7. `recent.json`
8. `dashboard.json`
9. `metadata.json`

ブラウザは`snapshot_id`の不一致を検出したら短時間後に再取得し、解消しなければ「更新途中または不整合」と表示する。

## サンプル

- `data/samples/normal/`：正常な一式。
- `data/samples/invalid/`：構文不正、必須欠損、未対応スキーマ、snapshot不一致、不正参照。
- `data/samples/large/`：大容量テストデータ生成方法。

サンプルはすべて架空データとし、実セッション、実パス、秘密情報を転用しない。
