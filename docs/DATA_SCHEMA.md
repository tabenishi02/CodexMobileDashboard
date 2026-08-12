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

### JSONの文字コードと直列化

- JSONファイルはBOMなしUTF-8で保存する。
- 日本語や絵文字を`\uXXXX`へ強制変換せず、UTF-8文字として保持する。
- 改行はLF、インデントは2空白、ファイル末尾はLFとする。
- ページ索引と`metadata.json`の`byte_size`および`sha256`は、実際に保存する同一バイト列から算出する。
- 各JSONは同じディレクトリの一時ファイルへ完全に書き込み、`flush()`と`os.fsync()`後に`os.replace()`で置換する。
- 一時ファイル名は対象名を接頭辞とする推測困難な名前とし、`.tmp`で終える。
- 置換失敗時は一時ファイルを削除し、既存ファイルを維持する。
- 複数JSON全体は単一の原子的操作ではないため、定義済み順序で置換し、`metadata.json`を最後にする。

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
| `latest.summary` | string | 必須 | 可 | 最新の有効な変更要約の`short_summary`。未生成なら`null` |
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

### Codex状態の判定

- 終了イベントのない最新`task_started`があれば`codex.status`を`working`とする。
- ターン履歴があり、進行中ターンがなければ`idle`とする。
- ターンイベントがなければ`unknown`とする。
- `stopped`はJSONLだけから推測せず、collectorまたはCodexプロセスを確認できる後続処理で設定する。
- `working`時の`codex.current_work`は、同じturn IDを持つユーザー指示から最大500文字のプレビューを生成する。全文は`messages`を正とする。
- `idle`、`stopped`、`unknown`では`codex.current_work`を`null`とする。

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
| `turn_id_source` | string | 必須 | 不可 | `jsonl`または`generated` |
| `status` | string | 必須 | 不可 | `in_progress`、`completed`、`failed`、`incomplete` |
| `started_at` | string | 必須 | 不可 | ユーザー指示日時 |
| `started_at_source` | string | 必須 | 不可 | 日時の取得元 |
| `completed_at` | string | 必須 | 可 | 完了日時 |
| `completed_at_source` | string | 必須 | 不可 | 日時の取得元 |
| `user_message_id` | string | 必須 | 不可 | ユーザーメッセージ参照 |
| `assistant_message_ids` | array | 必須 | 不可 | 応答メッセージ参照。分割応答に対応 |
| `user_preview` | string | 必須 | 不可 | 一覧用プレビュー |
| `assistant_preview` | string | 必須 | 可 | 応答中または失敗時は`null`可 |
| `rolled_back` | boolean | 必須 | 不可 | Codexでロールバックされたターンか |

プレビューは表示用の派生情報であり、全文の正は`messages`データとする。

応答中は`current_turn`へ現在のやりとりを置き、`turns`には直前までに完了した2件を残す。応答完了時に`current_turn`を`turns`の先頭へ移し、3件目になった古いターンを`recent.json`からだけ外す。元メッセージは`messages`に残すため削除されない。

`turn_aborted`は`failed`とする。新しいターン開始時に終了イベントのない古いターンが残っている場合は、明示的な失敗と断定せず`incomplete`として警告し、理由を`superseded_by_new_turn`とする。`thread_rolled_back`の対象ターンは履歴から削除せず`rolled_back: true`とし、通常の最新ターンと直近2件の候補からは除外する。

`started_at_source`と`completed_at_source`は、イベント固有のUnixミリ秒を使用した場合に`event_field`、JSONLレコード自体の日時で補完した場合に`record_timestamp`、日時を取得できない場合に`missing`とする。補完値を元イベント固有の値として扱わない。

`turn_id`はJSONLのネイティブ値を優先する。欠損時は`session_id`、固定文字列`turn`、ターンイベントのJSONL内開始バイト位置をNULで区切った値からSHA-256で決定的に生成し、`turn_id_source: generated`とする。同じ元レコードからは同じIDを再生成でき、一意性は`session_id`と`turn_id`の組で保証する。

## Codexセッション由来の変更要約

完了、失敗、記録不完全の各ターンについて、Git差分やプロジェクトファイルを使用せず、秘密情報除外済みのCodexセッション情報だけから変更要約を生成する。応答中ターンは要約せず`codex.current_work`を使用する。ロールバック済み要約は履歴に保持するが、`dashboard.latest.summary`の候補から除外する。

変更要約要素：

| フィールド | 型 | 必須 | `null` | 定義 |
|---|---|---|---|---|
| `summary_id` | string | 必須 | 不可 | セッションID、ターンID、根拠メッセージIDから生成するSHA-256 ID |
| `turn_id` | string | 必須 | 不可 | 対象ターンID |
| `turn_id_source` | string | 必須 | 不可 | `jsonl`または`generated` |
| `status` | string | 必須 | 不可 | `completed`、`failed`、`incomplete` |
| `rolled_back` | boolean | 必須 | 不可 | ロールバック済みか |
| `title` | string | 必須 | 不可 | 最大80文字の見出し |
| `short_summary` | string | 必須 | 不可 | 初期画面用の最大160文字の要約 |
| `details` | string | 必須 | 不可 | 詳細画面用の最大500文字の要約 |
| `highlights` | array | 必須 | 不可 | 実施内容の要点。最大5件 |
| `verification` | array | 必須 | 不可 | テストや検証結果。最大5件 |
| `origin` | string | 必須 | 不可 | `explicit`または`codex_generated` |
| `confidence` | string | 必須 | 不可 | `high`、`medium`、`low` |
| `source_session_ids` | array | 必須 | 不可 | 根拠セッションID |
| `source_message_ids` | array | 必須 | 不可 | 要約全体の根拠メッセージID |

`highlights`と`verification`の各要素は、最大200文字の`text`と1件以上の`source_message_ids`を必須・非`null`とする。CLIが入力にないメッセージIDを返した場合は要約全体を採用しない。コードブロックは生成しない。全変更要約は`messages/summaries/`のページへ保存し、`messages.json`の`summary_pages`から参照する。`dashboard.latest.summary`には最新の非ロールバック要約の`short_summary`だけを複製する。

根拠には次の優先順位と用途制限を設ける。

1. Codex最終回答：実施内容、結果、検証の最優先根拠。
2. ユーザー指示：作業目的だけの根拠。単独で完了や変更を断定しない。
3. Codex途中経過：作業過程の補助。単独で完了を断定しない。
4. 安全化済みツール概要：明示的な成功・失敗だけの補助根拠。
5. 安全化済みファイル参照：関連名の補助。変更した事実の根拠にはしない。

開発者指示、システム指示、ツール引数・出力本文、コードブロック本文、Git情報は要約入力に含めない。

最終回答に具体的な実施結果があれば規則で抽出し、`origin: explicit`とする。「対応しました」のように単独で内容を特定できない場合だけ、隔離したCodex CLIで`origin: codex_generated`を生成する。CLIは次タスク推定と同じく一時ディレクトリ、読み取り専用sandbox、ユーザー設定・ルール・MCP・プラグイン除外、入力最大128KiB、120秒タイムアウト、即時再試行なしとする。

入力上限超過時は最終回答、ユーザー指示、途中経過、ツール概要の順で推定用入力だけを制限し、元メッセージを変更しない。成功結果は同じ根拠で再利用し、失敗は5分間キャッシュする。CLI失敗時も具体的な最終回答から規則抽出できなければ要約を`null`として警告し、ユーザー指示や以前の要約から成果を推測しない。秘密情報除外済みと確認できない入力は、原文やそのハッシュをキャッシュせず、規則抽出にもCLIにも使用しない。

## `messages.json`とページ

`messages.json`はページ索引とし、会話本文の正はページおよび断片に置く。

索引：

| フィールド | 型 | 必須 | `null` | 定義 |
|---|---|---|---|---|
| `total_messages` | integer | 必須 | 不可 | 対象メッセージ総数 |
| `latest_sequence` | integer | 必須 | 可 | 0件なら`null` |
| `pages` | array | 必須 | 不可 | メッセージページ索引 |
| `total_change_summaries` | integer | 必須 | 不可 | 変更要約の総数 |
| `summary_pages` | array | 必須 | 不可 | 変更要約ページ索引 |

メッセージページ索引要素：`page`、`path`、`first_sequence`、`last_sequence`、`message_count`、`byte_size`、`sha256`をすべて必須・非`null`とする。ページファイルにも共通フィールドを持たせ、加えて`page`と`messages`を必須・非`null`とする。

変更要約ページ索引要素：`page`、`path`、`summary_count`、`byte_size`、`sha256`をすべて必須・非`null`とする。変更要約ページは`data_type: change_summaries_page`とし、共通フィールドに加えて`page`と`summaries`を必須・非`null`とする。メッセージページと同じく100件または512KiBの早い方で分割する。

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
| `turn_id` | string | 必須 | 可 | 関連するCodexターン。取得できなければ`null` |
| `content` | object | 必須 | 不可 | 本文または断片参照 |
| `redactions` | array | 必須 | 不可 | マスク情報。なければ空配列 |
| `display_mode` | string | 必須 | 不可 | `expanded`または`collapsed` |
| `duplicate_of` | string | 必須 | 可 | 開発者指示の重複参照先。通常は`null` |
| `occurrence_count` | integer | 必須 | 不可 | 初回の開発者指示に同一本文の総出現回数、それ以外は1 |
| `removed_automatic_contexts` | array[string] | 必須 | 不可 | 除外した自動付加情報の種類。なければ`[]` |
| `file_references` | array | 必須 | 不可 | このメッセージに関連する安全化済みファイル参照。なければ`[]` |

### ファイル参照

ファイル参照は会話で話題になったファイルを表し、Gitが示す実際の変更状態とは分離する。ユーザー添付、ユーザー・Codexの表示対象メッセージにあるローカルMarkdownリンク、インラインコード、明示的なファイル名・パスを対象とする。ツール引数・出力、Git差分、パッチ本文、JSONL保存場所、開発者指示、環境情報、URL、フェンス付きコードブロック内は対象外とする。

`file_references`の各要素：

| フィールド | 型 | 必須 | `null` | 定義 |
|---|---|---|---|---|
| `reference_id` | string | 必須 | 不可 | 決定的な参照ID |
| `scope` | string | 必須 | 不可 | `workspace`または`external` |
| `path` | string | 必須 | 可 | ワークスペース相対パス。外部なら`null` |
| `display_name` | string | 必須 | 不可 | 表示用ファイル・ディレクトリ名 |
| `extension` | string | 必須 | 不可 | 小文字の拡張子。なければ空文字 |
| `kind` | string | 必須 | 不可 | `file`、`directory`、`missing`、`unknown` |
| `mention_count` | integer | 必須 | 不可 | 同じワークスペース参照の出現回数 |
| `source_session_ids` | array | 必須 | 不可 | 参照元セッションID |
| `source_message_ids` | array | 必須 | 不可 | 参照元メッセージID |
| `mentions` | array | 必須 | 不可 | 各言及位置 |

`mentions`の各要素は`session_id`、`message_id`、`line`、`column`、`origin`を必須とする。`line`と`column`は取得不能なら`null`、`origin`は`attachment`、`markdown_link`、`inline_code`、`plain_text`のいずれかとする。

ワークスペース内の絶対パスは、シンボリックリンクを含む解決後の位置がワークスペース配下にあることを確認して相対パスへ変換する。`..`やシンボリックリンクで外へ出る参照は`external`とする。外部参照は`path: null`とし、ファイル名と拡張子だけを送信する。外部絶対パスやそのハッシュをIDへ使用しない。

存在確認では本文を開かず、ファイル・ディレクトリ・欠損だけを確認する。UNCパスはアクセスせず`unknown`とする。ファイルサイズ、更新日時、内容、行番号の実在性は確認しない。

同じワークスペース相対パスは1参照へ集約し、参照元と行・列を追加する。外部参照は同名でもメッセージごとに別IDとする。物理JSONでは新しいトップレベルファイルを増やさず、該当メッセージの`file_references`へ関連情報を置く。`files.json`側は後続処理で同じ相対パスの`source_message_ids`を持てるが、Git変更と会話上の言及を同一事実として推測しない。

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
| `kind` | string | 必須 | 不可 | 正規化したエラー種別 |
| `first_occurred_at` | string | 必須 | 可 | 初回日時。取得不能なら`null` |
| `last_occurred_at` | string | 必須 | 可 | 最終日時。取得不能なら`null` |
| `occurred_at_source` | string | 必須 | 不可 | `record_timestamp`、`file_mtime`、`missing` |
| `occurrence_count` | integer | 必須 | 不可 | 発生回数 |
| `severity` | string | 必須 | 不可 | 重要度 |
| `category` | string | 必須 | 不可 | エラー分類 |
| `summary` | string | 必須 | 不可 | 最大500文字の要約 |
| `details_preview` | string | 必須 | 可 | 最大4,000文字の表示用抜粋 |
| `details_complete` | boolean | 必須 | 不可 | 抽出した詳細がエラー部分の全文なら`true` |
| `source_message_ids` | array | 必須 | 不可 | 関連メッセージ参照。なければ空配列 |
| `source_line_number` | integer | 必須 | 不可 | 元JSONLの物理行番号 |
| `source_start_offset` | integer | 必須 | 不可 | 元JSONLの開始バイト位置 |
| `detail_storage` | string | 必須 | 不可 | `message_reference`、`inline`、`chunks`、`preview_only` |
| `details` | string | 必須 | 可 | `inline`時の欠落のない全文。それ以外は`null` |
| `detail_chunks` | array | 必須 | 不可 | `chunks`時の断片参照。それ以外は空配列 |
| `status` | string | 必須 | 不可 | `open`、`resolved`、`ignored` |
| `resolved_at` | string | 必須 | 可 | 未解決なら`null` |
| `resolution` | string | 必須 | 可 | 未解決なら`null` |
| `rolled_back` | boolean | 必須 | 不可 | ロールバック対象ターン由来か |

保存候補は次の3つである。

1. 概要と元メッセージ参照だけ：最小だが、一覧だけでは原因を判断しにくい。
2. 概要、最大4,000文字のプレビュー、元メッセージ参照：通信量と可読性のバランスがよく、推奨する。
3. エラー全文を独立して複製：単独で完結するが、通信量と保存量が増え、メッセージとの不整合も生じ得る。

採用方式は候補2である。全文がメッセージに存在しない場合だけ、`inline`または`chunks`で欠落なく保存する。`detail_storage`が`message_reference`なら全文を含む`source_message_ids`を1件以上、`inline`なら`details`を非`null`、`chunks`なら`detail_chunks`を1件以上にする。複合出力からエラー全文を安全に分離できない場合は`preview_only`とし、`details_complete: false`、`details: null`、空の`detail_chunks`にする。古い解決済みエラーは削除せずページ分割する。

エラー部分を安定して分離できない複合ツール出力は、正常出力やソースコードを混入させないため最大4,000文字のプレビューだけを抽出し、`details_complete: false`とする。元データはPC上のCodex JSONLに残す。エラー部分を分離できる`stderr`、Python Traceback、個別テスト失敗、Gitの`fatal`は、そのエラー部分の全文をメモリ内で後続の秘密情報除外処理へ渡す。マスク完了前に表示用JSONへ保存しない。

`turn_aborted`の`interrupted`は`warning`、`patch_apply_end.success: false`と明示された非ゼロ終了コードは`error`とする。終了コード0で標準エラーに文字があるだけなら登録せず、終了コード0でもTracebackまたはテスト失敗を明確に認識できる場合だけ`warning`とする。`incomplete`ターンは開発エラーへ重複登録しない。ロールバック済みエラーは保持するが、現在の未解決件数から除外する。

日時はJSONLレコード日時、セッションファイル更新日時の順で採用し、どちらもなければ`null`とする。過去エラーを初回収集時の発生に見せないため現在時刻では補完しない。

## `decisions.json`

決定事項は上書きせず、追加型の履歴として同じ論理データセットに保存する。

| フィールド | 型 | 必須 | `null` | 定義 |
|---|---|---|---|---|
| `decisions` | array | 必須 | 不可 | 決定履歴 |
| `decision_id` | string | 必須 | 不可 | 決定ID |
| `decided_at` | string | 必須 | 可 | 決定日時。取得不能なら`null` |
| `decided_at_source` | string | 必須 | 不可 | `message_timestamp`、`file_mtime`、`missing` |
| `status` | string | 必須 | 不可 | 決定状態 |
| `title` | string | 必須 | 不可 | 最大120文字の短い表題 |
| `description` | string | 必須 | 不可 | 決定内容 |
| `reason` | string | 必須 | 可 | 理由不明なら`null` |
| `topic_key` | string | 必須 | 不可 | 同じ論点の変更を関連付ける正規化キー |
| `source_session_ids` | array | 必須 | 不可 | 根拠が存在するセッションID |
| `source_message_ids` | array | 必須 | 不可 | 根拠メッセージ |
| `supersedes` | string | 必須 | 可 | 置き換え前の決定ID |
| `superseded_by` | string | 必須 | 可 | 置き換え後の決定ID |

旧決定は`superseded`へ変更して残し、新決定との相互参照を持たせる。

決定事項はワークスペース単位で全セッションを日時順に処理して統合する。トップレベルの`session_id`はスナップショット生成時の現在セッションを表し、各決定の実際の出典は`source_session_ids`で表す。ユーザーが採用、拒否、変更、撤回を明示した内容だけを決定とし、Codexが提案しただけの内容、質問、検討中の表現、Codexの一時的な実装判断は登録しない。1メッセージに複数の独立項目があれば、個別の決定へ分割する。

本文だけで確定内容を特定できる表現はPC側の規則で抽出する。「案Aを採用」「提案をそのまま採用」など直前の文脈が必要な表現は、秘密情報除外済みのユーザーメッセージと直前のCodexメッセージを隔離したCodex CLIへ渡し、構造化JSONとして抽出する。未マスク、CLI失敗、入力超過、参照先不足では推測して決定を作らず、抽出保留の警告を残す。

同じ内容の再確認は新しい決定にせず、`source_session_ids`と`source_message_ids`を追加する。理由が後から明示された場合は、従来の理由が`null`なら補完する。同じ論点の内容が変わった場合は新しい決定を追加し、旧決定を`superseded`にする。過去と同じ内容へ戻した場合も履歴上の新しい決定とし、直前の決定を置き換える。

理由は会話中に明記された内容だけを保存し、存在しない理由を生成しない。日時はメッセージ日時、セッションファイル更新日時の順で使用し、取得不能なら`null`とする。現在時刻では補完しない。

文脈解決用Codex CLIは次タスク推定と同じ隔離条件、UTF-8で最大128KiB、120秒タイムアウトを使用する。同じマスク済み入力の成功結果はcollector稼働中に再利用する。プロジェクトファイル、ユーザー設定、ルール、MCP、プラグインを読み込ませない。

## `files.json`

Git変更メタデータの正とする。プロジェクトファイルとGit差分本文は含めない。

| フィールド | 型 | 必須 | `null` | 定義 |
|---|---|---|---|---|
| `repository.collection_status` | string | 必須 | 不可 | Git取得状態 |
| `repository.root_name` | string | 必須 | 可 | 表示用ルート名 |
| `repository.branch` | string | 必須 | 可 | detachedまたは失敗時は`null`可 |
| `repository.head` | string | 必須 | 可 | 取得失敗時は`null` |
| `repository.session_start_commit` | string | 必須 | 可 | 未取得なら`null` |
| `repository.session_start_source` | string | 必須 | 可 | `jsonl`、`first_observed`。未取得なら`null` |
| `repository.clean` | boolean | 必須 | 不可 | 作業ツリー状態 |
| `repository.retry_required` | boolean | 必須 | 不可 | 一時的な取得失敗を次の定期確認で再試行するか |
| `files` | array | 必須 | 不可 | 変更ファイル一覧 |
| `change_id` | string | 必須 | 不可 | 変更レコードID |
| `path` | string | 必須 | 不可 | プロジェクト相対パス |
| `old_path` | string | 必須 | 可 | 名前変更以外は`null` |
| `status.index` | string | 必須 | 不可 | ステージ領域の状態 |
| `status.worktree` | string | 必須 | 不可 | 作業ツリーの状態 |
| `status.committed_in_session` | string | 必須 | 不可 | セッション中にコミットされた状態 |
| `numstat.staged` | object | 必須 | 不可 | ステージ済み変更の行数取得結果 |
| `numstat.unstaged` | object | 必須 | 不可 | 未ステージ変更の行数取得結果 |
| `numstat.committed_in_session` | object | 必須 | 不可 | セッション中にコミットされた変更の行数取得結果 |
| `binary` | boolean | 必須 | 可 | Gitで判定済みなら真偽、未調査・取得失敗なら`null` |
| `scopes` | array | 必須 | 不可 | 変更が存在する範囲 |

各`numstat`オブジェクトは`state`、`added`、`deleted`を必須とする。`state`は`measured`、`binary`、`not_inspected`、`not_applicable`、`failed`のいずれかとする。`measured`では`added`と`deleted`を0以上の整数とし、それ以外では両方を`null`とする。空のテキストファイルは`measured`かつ0行として、未調査と区別する。

未追跡ファイルは内容を読み取らず、該当scopeを`not_inspected`、`binary`を`null`とする。Gitが`numstat`を`-`で返したファイルだけを`binary: true`とする。Gitコマンド失敗は`failed`とし、取得済みの状態を保持したまま`repository.collection_status: warning`、`repository.retry_required: true`として次回の定期確認で再試行する。

`change_id`は`workspace_id`と大文字小文字を正規化したプロジェクト相対パスをNULで区切り、SHA-256で生成する。時系列情報は含めず、スナップショットIDと取得日時で管理する。名前変更後は新しいパスの別IDとし、変更前のパスを`old_path`へ保持する。

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
| ファイル | `copied` | コピー |
| ファイル | `type_changed` | 種類変更 |
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
| ターン | `incomplete` | 記録不完全 |
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
| エラー詳細 | `preview_only` | 安全に分離できたプレビューだけを保存 |
| スナップショット | `building` | 作成中 |
| スナップショット | `complete` | 完成 |
| スナップショット | `failed` | 作成失敗 |
| 変更範囲 | `committed_in_session` | セッション中にコミット済み |
| 変更範囲 | `staged` | コミット予定 |
| 変更範囲 | `worktree` | 未ステージ |
| 行数取得 | `measured` | 取得済み |
| 行数取得 | `binary` | バイナリ |
| 行数取得 | `not_inspected` | 未調査 |
| 行数取得 | `not_applicable` | 対象外 |
| 行数取得 | `failed` | 取得失敗 |
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
| `tool` | コマンド・ツールの安全な概要 | 折りたたみ |
| `developer` | 開発者・製品側指示 | 別枠で折りたたみ |
| `system` | システムイベント | 非表示または状態表示 |

| `message_type` | 具体例 |
|---|---|
| `chat` | user/assistantの通常メッセージ |
| `tool_call` | コマンド、関数、MCP呼び出し |
| `tool_output` | コマンド・ツールの出力 |
| `tool_summary` | ツール名、結果受信、成否、処理時間などの安全な概要 |
| `developer_instruction` | developerメッセージ |
| `developer_instruction_reference` | 完全一致する開発者指示の再出現位置 |
| `system_notice` | セッション開始、圧縮、完了など |

`role`は「誰が生成したか」、`message_type`は「何の種類か」を表す。JSONLの内部値をそのまま公開仕様にせず、PC側で正規化する。

画面上の具体的な表示名は、`user`＝「あなた」、`assistant`＝「Codex」、`tool`＝「ツール」、`developer`＝「開発者指示」、`system`＝「システム」とする。メッセージ種別は、`chat`＝「会話」、`tool_call`＝「ツール呼び出し」、`tool_output`＝「ツール結果」、`tool_summary`＝「ツール概要」、`developer_instruction`＝「開発者指示」、`developer_instruction_reference`＝「開発者指示を再適用」、`system_notice`＝「システム通知」とする。

### チャット抽出規則

- ユーザーとCodexの本文は欠落なく保持する。
- Codexの`commentary`と`final_answer`を両方保持し、通常表示では`commentary`を折りたたむ。
- ツール引数と結果本文は`messages`へ保存せず、ツール名、呼び出しID、結果受信、判定可能な成否・処理時間だけを`tool_summary`として保存する。
- 開発者指示は通常会話と分離して折りたたむ。同一セッション内で本文が完全一致する場合、最初の1件だけに本文を持たせ、再出現位置は`developer_instruction_reference`として`duplicate_of`で参照する。
- ユーザーまたはCodexが同じ本文を意図的に繰り返した場合は重複削除しない。
- `response_item.message`を会話の正とし、同一内容の`event_msg.user_message`または`agent_message`を二重登録しない。対応する`response_item`がない場合だけイベント側を代替利用する。

### 自動付加情報

ユーザーメッセージの絶対先頭にあり、開始・終了構造が完全な次のブロックだけを除外する。

- `<recommended_plugins>...</recommended_plugins>`
- `# AGENTS.md instructions...`に続く`<INSTRUCTIONS>...</INSTRUCTIONS>`
- `<environment_context>...</environment_context>`

複数の本文要素に分かれていても、先頭から連続する完全なブロックだけを対象にする。構造が不完全な場合、メッセージ途中にある場合、Markdownコードフェンス内に例示された場合は除外しない。除外後もユーザー本文が残る場合は`removed_automatic_contexts`へ種類を記録する。自動付加情報だけで構成されたメッセージは表示対象にせず、PC側で元位置と種類だけを監査用に保持し、除外本文は複製しない。

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
├─ summaries/
│  ├─ summary-page-000001.json
│  └─ summary-page-000002.json
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
  --ignore-user-config --ignore-rules --skip-git-repo-check \
  --color never --output-schema next-task.schema.json -
```

現在のCodex CLIログインを再利用し、入力には秘密情報を除外した現在状況、最新2ターン、決定事項、未完了タスクだけを標準入力から渡す。対象プロジェクトではなく一時ディレクトリを作業場所とし、ユーザー設定、ルール、MCP、プラグインを読み込ませない。結果には`task`、`reason`、`confidence`を必須とし、最終JSONだけを標準出力からメモリへ取得する。

推定用入力全体はUTF-8で最大128KiB、タイムアウトは120秒、即時再試行は行わない。同じ根拠の成功結果は再利用し、失敗後の`TASKS.md`補完結果は5分間キャッシュしてから再推定可能とする。上限超過時は未完了タスク、決定事項、現在状況、直近メッセージの優先順で推定用コンテキストだけを構成し、省略警告を記録する。元のJSONLと抽出済み履歴は削除・変更しない。秘密情報除外済みであることを確認できない入力ではCLIを起動しない。

非対話実行、`--ephemeral`、読み取り専用sandbox、`--output-schema`の仕様は[Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode.md)を根拠とする。

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
3. 変更要約ページ
4. `messages.json`
5. `errors.json`
6. `decisions.json`
7. `files.json`
8. `recent.json`
9. `dashboard.json`
10. `metadata.json`

ブラウザは`snapshot_id`の不一致を検出したら短時間後に再取得し、解消しなければ「更新途中または不整合」と表示する。

## サンプル

- `data/samples/normal/`：正常な一式。
- `data/samples/invalid/`：構文不正、必須欠損、未対応スキーマ、snapshot不一致、不正参照。
- `data/samples/large/`：大容量テストデータ生成方法。

サンプルはすべて架空データとし、実セッション、実パス、秘密情報を転用しない。
