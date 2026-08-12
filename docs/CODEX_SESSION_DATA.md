# Codexセッションデータ調査結果

## 調査日

2026-08-03

## 保存場所

`CODEX_HOME`環境変数は未設定であり、既定の保存場所が使用されている。

```text
%USERPROFILE%\.codex
```

セッション本体は次のディレクトリに保存される。

```text
%USERPROFILE%\.codex\sessions\YYYY\MM\DD\rollout-*.jsonl
```

アーカイブ済みセッションは次のディレクトリに保存される。

```text
%USERPROFILE%\.codex\archived_sessions
```

## 確認したファイル

```text
%USERPROFILE%\.codex\sessions\2026\08\03\rollout-YYYY-MM-DDThh-mm-ss-SESSION_ID.jsonl
```

調査時点でJSONLとして正常に解析でき、Codexの実行中に追記されていることを確認した。

## 仕様上の位置づけ

この`rollout-*.jsonl`はCodexがセッションを再開するための内部保存形式である。OpenAIの公開マニュアルは`CODEX_HOME`配下にローカル状態や履歴が保存されること、rollout logが存在することを説明しているが、`rollout-*.jsonl`の全フィールドを固定した公開スキーマは提示していない。

したがって、以下は2026年8月4日時点のVSCode Codexが生成した実ファイル3件、6,900行以上から観測した構造である。現在のファイルにあるキーは網羅しているが、将来のCodexでキーや列挙値が追加・変更される可能性がある。収集ツールはこの文書を固定スキーマとして盲信せず、未知フィールドを無視して既知フィールドだけを取り出す。

## JSONLの基本構造

JSONLは「1行につき1個のJSONオブジェクト」を並べた形式である。ファイル全体を囲む配列の`[`と`]`や、行間のカンマはない。

```json
{"timestamp":"...","type":"session_meta","payload":{...}}
{"timestamp":"...","type":"response_item","payload":{...}}
```

すべての行で確認したトップレベルキーは3個である。

| キー | 型 | 意味・取りうる値 |
|---|---|---|
| `timestamp` | string | レコード記録日時。ISO 8601形式 |
| `type` | string | `session_meta`、`turn_context`、`response_item`、`event_msg`、`world_state`、`compacted` |
| `payload` | object | `type`に応じた本体 |

トップレベルの`type`と、`payload.type`は別物である。たとえば`type: response_item`の中に`payload.type: message`が入る。

## `session_meta`

セッションの識別情報と開始・再開時の環境である。同じファイル内に複数回現れることがあり、最初の1件だけとは限らない。

| キー | 型 | 説明・観測値 |
|---|---|---|
| `id` | string | セッションUUID。観測範囲では`session_id`と同じ |
| `session_id` | string | セッションUUID |
| `timestamp` | string | セッションメタデータ側の日時 |
| `cwd` | string | その時点の作業ディレクトリ |
| `source` | string | 観測値は`vscode` |
| `originator` | string | 観測値は`codex_vscode` |
| `thread_source` | string | 観測値は`user` |
| `model_provider` | string | 観測値は`openai` |
| `cli_version` | string | Codex CLI／拡張機能側のバージョン文字列 |
| `base_instructions` | object | `text`に基礎指示全文を持つ |
| `git` | object | `branch`と`commit_hash`を持つ開始時Git情報 |
| `history_mode` | string | 観測値は`legacy`。古いレコードではキー自体がない |
| `memory_mode` | string | 観測値は`enabled`。古いレコードではキー自体がない |
| `context_window` | object | `window_id`を持つ。新しい一部レコードだけにある |

`base_instructions.text`は内部指示を含むため、ダッシュボードへ送信しない。

## `turn_context`

1回のユーザー指示に対するCodexターンの実行条件である。確認したキーは次のとおり。

| キー | 型 | 説明・観測値 |
|---|---|---|
| `turn_id` | string | ターンUUID |
| `cwd` | string | ターンの作業ディレクトリ |
| `current_date` | string | 実行日 |
| `timezone` | string | タイムゾーン名 |
| `model` | string | 使用モデル識別子。固定列挙ではない |
| `effort` | string | 観測値は`low`、`medium` |
| `personality` | string | 観測値は`friendly` |
| `approval_policy` | string | 観測値は`on-request` |
| `approvals_reviewer` | string | 観測値は`user`。古いレコードではない場合がある |
| `realtime_active` | boolean | リアルタイム機能の有効状態 |
| `workspace_roots` | array[string] | 許可されたワークスペースルート |
| `summary` | string | Codexへ渡された圧縮済みコンテキスト等。ユーザー向け要約とは限らない |
| `comp_hash` | string | コンテキスト／設定識別用ハッシュ。古いレコードではない場合がある |
| `multi_agent_mode` | string | 観測値は`explicitRequestOnly`。一部レコードだけにある |
| `multi_agent_version` | string | マルチエージェント設定のバージョン識別子 |
| `collaboration_mode` | object | `mode`と`settings`を持つ |
| `sandbox_policy` | object | sandboxの簡易設定 |
| `file_system_sandbox_policy` | object | ファイルアクセス規則 |
| `permission_profile` | object | ファイル・ネットワーク権限の詳細 |

`collaboration_mode`で確認した下位キー：

- `mode`：観測値は`default`
- `settings.model`：モデル識別子
- `settings.reasoning_effort`：推論強度
- `settings.developer_instructions`：追加指示文字列

`sandbox_policy`で確認した下位キー：

- `type`：観測値は`workspace-write`
- `network_access`：boolean
- `exclude_slash_tmp`：boolean
- `exclude_tmpdir_env_var`：boolean

`file_system_sandbox_policy`は`kind: restricted`と`entries`を持つ。`permission_profile`は次を持つ。

- `type`：観測値は`managed`
- `network`：観測値は`enabled`または`restricted`
- `file_system.type`：観測値は`restricted`
- `file_system.entries[]`
  - `access`：`read`または`write`
  - `missing_path_behavior`：観測値は`skip`。存在しないパス向けで、ない場合もある
  - `path.type`：`path`または`special`
  - `path.path`：`type: path`の場合の実パス
  - `path.value.kind`：`type: special`の場合に`root`、`slash_tmp`、`tmpdir`

## `world_state`

実行環境からCodexへ渡された能力・指示の有無を記録する。`payload.type`はない。

| キー | 型 | 説明 |
|---|---|---|
| `full` | boolean | 完全な状態通知かどうか |
| `state` | object | 状態本体 |

`state`内で観測した全キーは次のとおり。

- `agents_md`：AGENTS指示に関するobject
- `apps_instructions`：boolean
- `collaboration_mode`：観測値は`default`
- `environments_instructions`：boolean
- `git_attribution`：boolean
- `plugins_instructions`：boolean
- `permissions`：権限説明文字列
- `host_skills.body`：ホスト側スキル情報文字列
- `host_skills.includeInstructions`：boolean
- `skills.includeInstructions`：boolean
- `multi_agent_mode.mode`：観測値は`explicitRequestOnly`
- `realtime.active`：boolean
- `environments.current_date`：日付
- `environments.timezone`：タイムゾーン
- `environments.filesystem`：ファイルシステム説明文字列
- `environments.environments.local.cwd`：ローカル作業場所
- `environments.environments.local.shell`：シェル名
- `environments.environments.local.status`：観測値は`available`

これらはダッシュボード表示用データではなく、原則として送信対象外にする。

## `response_item`

モデルとの会話履歴、推論、ツール呼び出しと結果を保持する。`payload.type`ごとの構造は以下のとおり。

### `message`

| キー | 型 | 説明・取りうる値 |
|---|---|---|
| `type` | string | `message` |
| `id` | string | 通常は`msg_<UUID>`。古いuser/developerメッセージでは存在しない例がある |
| `role` | string | `user`、`assistant`、`developer` |
| `phase` | string | assistantで`commentary`または`final_answer`。user/developerでは通常キーがない |
| `content` | array | 本文要素 |
| `content[].type` | string | `input_text`または`output_text` |
| `content[].text` | string | メッセージ本文 |
| `internal_chat_message_metadata_passthrough` | object | `turn_id`を持つ内部関連情報 |

観測上、user/developerは`input_text`、assistantは`output_text`である。`phase: commentary`は途中経過、`phase: final_answer`はターン終了時の最終回答を表す。

### `reasoning`

| キー | 型 | 説明 |
|---|---|---|
| `type` | string | `reasoning` |
| `id` | string | 推論項目ID |
| `summary` | array | 推論要約。観測したファイルでは空配列 |
| `encrypted_content` | string | 暗号化された内部推論 |
| `internal_chat_message_metadata_passthrough.turn_id` | string | 関連ターンID |

`encrypted_content`は復号・解析・送信しない。

### `function_call`

| キー | 型 | 説明 |
|---|---|---|
| `type` | string | `function_call` |
| `id` | string | 呼び出し項目ID |
| `call_id` | string | 呼び出しと結果を結ぶID |
| `name` | string | 関数名。利用可能ツールに応じて変化するため固定列挙ではない |
| `namespace` | string | 名前空間。ごく一部だけに存在 |
| `arguments` | string | 引数をJSON文字列等として格納 |
| `internal_chat_message_metadata_passthrough.turn_id` | string | 関連ターンID |

### `function_call_output`

| キー | 型 | 説明 |
|---|---|---|
| `type` | string | `function_call_output` |
| `id` | string | 結果項目ID。一部だけに存在 |
| `call_id` | string | 対応する呼び出しID |
| `output` | stringまたはarray | 実行結果。配列の場合は`type: input_text`と`text`を持つ要素 |
| `internal_chat_message_metadata_passthrough.turn_id` | string | 関連ターンID |

### `custom_tool_call`

| キー | 型 | 説明 |
|---|---|---|
| `type` | string | `custom_tool_call` |
| `id` | string | 項目ID |
| `call_id` | string | 呼び出しID |
| `name` | string | ツール名。固定列挙ではない |
| `input` | string | ツール入力。形式はツール依存 |
| `status` | string | 観測値は`completed` |
| `internal_chat_message_metadata_passthrough.turn_id` | string | 関連ターンID |

### `custom_tool_call_output`

| キー | 型 | 説明 |
|---|---|---|
| `type` | string | `custom_tool_call_output` |
| `id` | string | 結果項目ID。一部だけに存在 |
| `call_id` | string | 対応する呼び出しID |
| `output` | stringまたはarray | ツール結果 |
| `output[].type` | string | `input_text`または`input_image` |
| `output[].text` | string | テキスト結果で使用 |
| `output[].image_url` | string | 画像結果で使用 |
| `output[].detail` | string | 画像詳細度。観測値は`high` |
| `internal_chat_message_metadata_passthrough.turn_id` | string | 関連ターンID |

ツール入出力にはコマンド、パス、ファイル本文、エラー、秘密情報が含まれ得るため、そのまま送信しない。

## `event_msg`

UI表示やターン進行に使われるイベントである。`payload.type`ごとの全キーは以下のとおり。

### 会話イベント

| `payload.type` | キー | 説明・取りうる値 |
|---|---|---|
| `user_message` | `message` | ユーザー本文 |
|  | `client_id` | 送信元クライアントID |
|  | `images`、`local_images` | 画像情報の配列。観測データでは要素なし |
|  | `audio`、`local_audio` | 音声情報の配列。古いレコードではキー自体がない例がある |
|  | `text_elements` | 構造化テキスト要素配列。観測データでは要素なし |
| `agent_message` | `message` | Codex本文 |
|  | `phase` | `commentary`または`final_answer` |
|  | `memory_citation` | 観測値は`null` |

同じ会話本文が`response_item.message`とこれらのイベントの両方に現れる。ダッシュボードではネイティブIDを持つ`response_item.message`を正とし、イベント側は進行状態の補助に使う。

### ターンイベント

| `payload.type` | キー | 説明・取りうる値 |
|---|---|---|
| `task_started` | `turn_id` | ターンID |
|  | `started_at` | Unix時刻のミリ秒整数 |
|  | `model_context_window` | コンテキスト上限の整数 |
|  | `collaboration_mode_kind` | 観測値は`default` |
| `task_complete` | `turn_id` | ターンID |
|  | `started_at` | Unixミリ秒。一部の古いレコードではない |
|  | `completed_at` | Unixミリ秒 |
|  | `duration_ms` | 所要時間ミリ秒 |
|  | `time_to_first_token_ms` | 最初の出力までのミリ秒 |
|  | `last_agent_message` | 最後のCodex本文。`string`または`null` |
| `turn_aborted` | `turn_id`、`started_at`、`completed_at`、`duration_ms` | 中断されたターン情報 |
|  | `reason` | 観測値は`interrupted` |
| `thread_rolled_back` | `num_turns` | ロールバックされたターン数 |

### 設定イベント

`thread_settings_applied`は`thread_settings`オブジェクトを持つ。確認した全キーは次のとおり。

- `cwd`
- `model`
- `model_provider_id`
- `reasoning_effort`：観測値`low`、`medium`
- `reasoning_summary`：観測値`none`
- `personality`：観測値`friendly`
- `service_tier`：観測値`default`
- `approval_policy`：観測値`on-request`
- `approvals_reviewer`：観測値`user`
- `active_permission_profile.id`
- `collaboration_mode.mode`：観測値`default`
- `collaboration_mode.settings.model`
- `collaboration_mode.settings.reasoning_effort`
- `collaboration_mode.settings.developer_instructions`：`string`または`null`
- `permission_profile`：`turn_context`と同じ`type`、`network`、`file_system`構造

### トークンイベント

`token_count`は`info`と`rate_limits`を持つ。

`info.last_token_usage`と`info.total_token_usage`の両方で確認したキー：

- `input_tokens`
- `cached_input_tokens`
- `cache_write_input_tokens`
- `output_tokens`
- `reasoning_output_tokens`
- `total_tokens`

すべて0以上の整数である。`info.model_context_window`も整数である。

`rate_limits`で確認した全キー：

- `limit_id`：string
- `limit_name`：観測値は`null`
- `plan_type`：string。契約等で変わるため固定列挙にしない
- `primary`、`secondary`：objectまたは`null`
  - `used_percent`：number
  - `window_minutes`：integer
  - `resets_at`：Unix時刻整数
- `credits`：objectまたは`null`
  - `has_credits`：boolean
  - `unlimited`：boolean
  - `balance`：stringまたは`null`
- `individual_limit`：観測値は`null`
- `rate_limit_reached_type`：観測値は`null`
- `spend_control_reached`：観測値は`null`

利用枠や契約情報はダッシュボードのMVP対象外とし、送信しない。

### ファイル変更イベント

`patch_apply_end`で確認したキー：

- `call_id`：対応するツール呼び出しID
- `turn_id`：ターンID
- `status`：string
- `success`：boolean
- `stdout`：string
- `stderr`：string
- `changes`：ファイルパスをキーにした動的object

`changes.<ファイルパス>`の形は次のいずれかである。

- 更新系：`type`、`unified_diff`、`move_path`
- 追加・内容保存系：`type`、`content`

`type`の観測値は`add`、`update`、`delete`、`move_path`は移動先パスまたは`null`である。ここにはファイル本文やdiff本文が含まれるため、ダッシュボードのファイル情報はGitから再取得したメタデータを正とする。

### Web検索イベント

`web_search_end`で確認したキー：

- `call_id`
- `query`
- `action.type`：`search`、`open_page`、`find_in_page`
- `action.queries[]`
- `action.url`
- `action.pattern`
- `results[]`
  - `type`
  - `ref_id`
  - `url`
  - `domain`
  - `title`
  - `snippet`
  - `thumbnail_url`

`action`の下位キーは動作種別に応じて使い分けられる。検索結果本文やURLは秘密情報検査なしに送信しない。

### MCP完了イベント

`mcp_tool_call_end`で確認したキー：

- `call_id`
- `invocation.server`
- `invocation.tool`
- `invocation.arguments`
- `duration.secs`
- `duration.nanos`
- `result.Ok`（観測値）。将来`result.Err`が現れた場合は失敗として扱う

2026年8月8日の追加調査で11件確認した。正規化時はサーバー名、ツール名、処理時間、成否だけを保持し、引数と結果本文は同じ内容を持つ`response_item`を優先するため複製しない。

### その他のイベント

- `context_compacted`：`payload.type`だけを持ち、コンテキスト圧縮の発生を示す。

## `compacted`

コンテキスト圧縮後の置換履歴であり、`payload.type`はない。

| キー | 型 | 説明 |
|---|---|---|
| `message` | string | 圧縮後コンテキストの本文 |
| `window_id` | string | 新しいウィンドウID |
| `window_number` | integer | ウィンドウ番号 |
| `first_window_id` | string | 最初のウィンドウID |
| `previous_window_id` | string | 直前のウィンドウID |
| `replacement_history` | array | 置換対象の履歴 |

`replacement_history[]`で観測したキー：

- `type`：`message`または`compaction`
- `id`
- `role`：`user`または`developer`
- `content[].type`：観測値は`input_text`
- `content[].text`
- `encrypted_content`
- `internal_chat_message_metadata_passthrough.turn_id`

圧縮レコードはセッション復元用の内部情報として扱い、通常のチャットメッセージとして重複登録しない。

## 観測した列挙値の一覧

| 場所 | 観測値 |
|---|---|
| トップレベル`type` | `session_meta`、`turn_context`、`response_item`、`event_msg`、`world_state`、`compacted` |
| `response_item.payload.type` | `message`、`reasoning`、`function_call`、`function_call_output`、`custom_tool_call`、`custom_tool_call_output` |
| `event_msg.payload.type` | `user_message`、`agent_message`、`task_started`、`task_complete`、`turn_aborted`、`thread_rolled_back`、`thread_settings_applied`、`token_count`、`patch_apply_end`、`web_search_end`、`mcp_tool_call_end`、`context_compacted` |
| `message.role` | `user`、`assistant`、`developer` |
| `message.phase` | `commentary`、`final_answer` |
| `message.content[].type` | `input_text`、`output_text` |
| ツール出力要素`type` | `input_text`、`input_image` |
| `custom_tool_call.status` | `completed` |
| `patch changes.type` | `add`、`update`、`delete` |
| `web action.type` | `search`、`open_page`、`find_in_page` |
| `turn_aborted.reason` | `interrupted` |
| 権限`access` | `read`、`write` |
| ネットワーク権限 | `enabled`、`restricted` |

これは「現在の3ファイルで観測した全値」であり、「Codexが将来もこの値しか生成しない」という保証ではない。文字列本文、UUID、モデル名、ツール名、ファイルパス、URL、バージョン番号などは自由値なので列挙型にしない。

## 公式資料との対応

- [Config basics](https://learn.chatgpt.com/docs/config-file/config-basic.md)：`CODEX_HOME`と履歴保存の公開説明
- [Codex App Server](https://learn.chatgpt.com/docs/app-server.md)：thread、turn、item、rollout logに関する公開説明

これらは周辺概念を説明する公式資料であり、本書の内部JSONLキー一覧そのものはローカル実ファイルの観測結果である。

## ダッシュボードデータとの対応

| 表示データ | 主な取得元 | 注意事項 |
| --- | --- | --- |
| チャット | `message`、`user_message`、`agent_message` | 同じ内容の重複を除外する |
| 実行状況 | `task_started`、`task_complete` | 未完了ターンを実行中として扱える |
| 最近の更新 | 各レコードの`timestamp` | 日時形式を正規化する |
| エラー | ツール出力、関数出力、`patch_apply_end` | 専用のエラーイベントは確認できない |
| 変更ファイル | `patch_apply_end` | 最終状態はGitを正とする |
| 変更内容の要約 | ユーザー指示、`agent_message`、安全化済みツール概要 | 最終回答を最優先し、Git差分本文は使用しない |
| セッション情報 | `session_meta`、`turn_context` | `cwd`で対象ワークスペースを判別する |
| 決定事項 | ユーザーの確定メッセージ、直前のアシスタント提案 | 専用イベントがないため抽出処理が必要 |
| ファイル参照 | ユーザー添付情報、ユーザー・アシスタントメッセージ | ファイル本体やツール出力は送信しない |
| 次の作業 | アシスタントメッセージ、`TASKS.md` | `TASKS.md`を優先情報として扱う |

`reasoning.encrypted_content`は解析対象にしない。

## 読み取り方法

- セッションファイルは読み取り専用で扱い、変更しない。
- 初回起動では`sessions`と`archived_sessions`にある対象ワークスペースの全セッションを索引化する。
- 同じワークスペースの複数セッションは個別に保持し、最終イベント日時が最も新しいセッションを現在のセッションとする。同時刻の場合はファイル更新日時、最後にセッションIDで順序を確定する。
- 最新の`rollout-*.jsonl`だけに依存せず、`session_meta.cwd`または`turn_context.cwd`で対象ワークスペースを判定する。
- 前回読み取り位置をバイトオフセットで保存し、追記分だけを処理する。
- 書き込み途中の最終行が不完全な場合は破棄せず、次回読み取り時に再処理する。
- 未知のレコード種別やフィールドはエラーにせず無視する。
- アーカイブ移動後も必要に応じて`archived_sessions`から追跡できるようにする。
- `--ephemeral`で開始されたセッションは保存されないため収集対象にできない。

実装は`tools/session_reader.py`に置く。完全な行だけをUTF-8として1行ずつ解析し、既知のトップレベル種別だけを後続処理へ渡す。読み取り結果にはファイルパス、物理行番号、開始・終了バイトオフセットを付ける。セッション索引ではファイル名または`session_meta`からセッションIDを取得し、同じIDのファイルが`sessions`から`archived_sessions`へ移動しても同一セッションとしてまとめる。

形式差の吸収は`tools/record_normalizer.py`で行う。正規化結果は中間ファイルへ保存せず、`tools/secret_redactor.py`で秘密情報を除外してからメモリ内だけで後続処理へ渡す。`response_item`と`event_msg`の出典、元ファイル位置、ネイティブIDを保持し、文字列・配列の本文形式や欠損フィールドを共通表現へ揃える。未知形式は推測せず警告し、画像本体、差分・ファイル本文、内部指示、暗号化推論を複製しない。

チャット抽出は`tools/chat_extractor.py`で行う。会話は`response_item.message`を優先し、イベント側は欠損時の代替とする。ユーザーとCodexの同文反復は保持し、開発者指示だけをセッション内の完全一致で重複排除する。ツールは引数・出力本文ではなく安全な概要だけを抽出する。Codexが自動付加した完全な先頭ブロックは除外し、除外位置と種類だけを記録する。

現在の作業状況は`tools/work_status_extractor.py`で抽出する。未完了の`task_started`を作業中、完了・中断後を待機中、ターンイベントなしを状態不明とする。現在作業は進行中turn IDに関連するユーザー指示から最大500文字のプレビューを生成する。ロールバック済みターンは削除せず明示的に区別する。イベント固有の開始・完了日時がない場合はJSONLレコード日時で補完し、取得元を`record_timestamp`として記録する。終了イベントなしで後続ターンに置き換わったターンは失敗と断定せず`incomplete`とする。

## セキュリティ

セッションにはユーザーメッセージ、コード、ファイルパス、コマンド、ツール引数、ツール出力、実行時指示が含まれる可能性がある。

- `auth.json`は読み取らない。
- `config.toml`全体を送信しない。
- `reasoning.encrypted_content`を送信しない。
- APIキー、トークン、パスワード、秘密鍵、環境変数をJSON生成前に除外する。
- Androidサーバー端末へは表示に必要な情報だけを送信する。

## 必要な環境と権限

- 通常はセッションを作成したWindowsユーザーの読み取り権限だけでよい。
- 収集ツールやタスクスケジューラは、セッションを作成したユーザーと同じアカウントで実行する。
- 管理者権限やCodex設定変更は不要。
- 作業用PCにはPython 3.10.6（64-bit）がインストールされており、標準ライブラリとpipが正常に利用できる。
- 初期実装では既存のPython 3.10.6を使用し、Python 3.12の追加導入は不要。
- VSCodeでは`C:\path\to\CodexMobileDashboard`をワークスペースとして開き直し、新しいセッションの`cwd`を正しいプロジェクトパスにする。

## 現時点で不要なファイル

- `history.jsonl`は存在しないが、セッションJSONLが取得できているためMVPには不要。
- `log`ディレクトリは存在しないが、診断ログ用でありMVPには不要。
- `auth.json`は認証情報を含むため使用禁止。
- SQLite状態ファイルは現時点では使用しない。
