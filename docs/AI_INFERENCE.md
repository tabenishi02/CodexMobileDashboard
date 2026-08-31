# Codex CLI推論の現状と増分化設計

## 現在の呼び出し経路

`collect-once`は検出した各workspaceのSnapshotを構築し、次の3経路で別々に`codex exec`を起動し得る。

| 用途 | 経路 | 現在の単位 |
| --- | --- | --- |
| 変更要約 | `generate_change_summaries()` → `ChangeSummaryCliRunner.generate()` | 明示要約がない完了ターンごと |
| 決定事項 | `extract_decisions()` → `DecisionCliRunner.extract()` | 曖昧参照を含む決定候補メッセージごと |
| 次タスク | `extract_next_task()` → `CodexCliRunner.infer()` | 明示的な次タスクがないworkspaceごとに最大1回 |

CLIは`--ephemeral`、`--sandbox read-only`、`--ignore-user-config`、`--ignore-rules`、`--output-schema`を指定する。モデル指定は渡さないため、Codex CLI側の既定モデルと利用枠を使う。1呼び出しのタイムアウトは120秒である。個別推論の入力上限は128 KiB、統合推論promptの入力上限は64 KiBである。collector全体のCodex CLI呼び出しは`max_calls_per_run`で共有上限を設け、既定は3回である。

## 推論モード

`[ai_inference] mode`は`off`、`incremental`、`backfill`を受け付け、未指定時は`incremental`である。

- `off`: 3経路のCodex CLIを起動せず、規則ベース抽出とフォールバックを使う。
- `incremental`: 今回の増分収集で追加されたturnだけをCLI推論対象にする。初回導入時に取り込む既存履歴は対象外である。
- `backfill`: `python -m tools.collector --config <config> backfill-ai`で明示実行する過去未処理turnの補完モード。共有上限を適用する。

通常収集は初回導入時の過去履歴を推論対象にせず、増分収集で追加された完了turnだけを対象にする。

## 既存キャッシュと台帳

各Runnerはプロセス内のプロンプトSHA-256キャッシュを持つが、collector再起動後は再利用できない。変更要約と次タスクのExtractorにはキャッシュエントリ型があるが、従来はcollectorが永続化していなかった。決定事項Runnerのキャッシュもインスタンス内だけである。

`[storage] inference_ledger_file`として`%LOCALAPPDATA%\CodexMobileDashboard\state\ai-inference-ledger.json`を追加し、トップレベル`version: 1`とentries配列を原子的置換する実装がある。entryはworkspace、session、turn、推論種別、入力SHA-256、生成日時、resultを持つ。

変更要約・決定事項・次タスクは完全payloadを台帳へ保存し、collector再起動後は入力SHA-256が一致する結果を復元してCodex CLIを再実行しない。

- 変更要約はversioned payloadを保存・復元し、同一turn内の全履歴から入力SHA-256が一致するentryを再利用する。
- 決定事項は入力SHA-256を照合し、同一入力ではCodex CLIを再実行しない。
- entryごとの`schema_version: 1`を必須にし、旧形式・不完全payloadは安全に再推論候補へ戻す。
- 次タスクも同じversioned payloadで保存・復元する。

## 完成形の台帳設計

成功レコードは`schema_version`、`workspace_id`、`session_id`、`turn_id`、`inference_kind`、`input_sha256`、`generated_at`、完全な`payload`を持つ。プロンプト本文、Token、未マスク本文は保存しない。同一workspace・session・turn・種別・入力SHA-256だけをキャッシュヒットとし、入力が変われば再推論する。成功結果は1件ごとに原子的保存し、失敗・不完全payloadは成功キャッシュとして保存または復元しない。

変更要約payloadは表題、短文、詳細、highlights、verification、confidence、状態、根拠IDを持つ。決定事項payloadは状態、内容、理由、`topic_key`、`supersedes`、`superseded_by`、時系列と根拠IDを持つ。次タスクpayloadは本文、状態、origin、confidence、理由と根拠IDを持つ。

## 実装順序

1. 変更要約保存経路の引数不一致を修正し、台帳専用テストを追加する。
2. 変更要約の完全payload保存・再起動後復元・ハッシュ照合を受入可能にする。
3. 決定事項の置換関係を含む保存・復元・ハッシュ照合を実装する。
4. 次タスクの保存・復元・ハッシュ照合を実装する。
5. 新規完了ターン判定、共有呼び出し上限、backfill専用コマンドを接続する。
6. 同一ターンの推論統合、入力削減、不要推論スキップを行う。

ログには実行数、キャッシュヒット、スキップ、上限到達、入力サイズ、成功・失敗だけを記録し、プロンプト全文や秘密情報を出力しない。

collector終了時には、実行したCLI回数と上限到達で次回へ持ち越した推論件数をログへ記録する。

過去未処理turnの補完はpython -m tools.collector --config <config> backfill-aiで明示実行する。max_calls_per_runの共有上限を適用する。

## 統合推論の対象条件

統合推論は、完了済みで今回のincremental対象となる同一turnに限る。入力はすべてマスク済みで、次タスク候補があり、turn外の文脈を必要としないことを必須とする。条件外、または統合推論の失敗時は既存の個別推論経路へfallbackする。
## 統合JSON schemaとCLI runner

`CombinedTurnCliRunner`は、変更要約（`summary`）、決定事項配列（`decisions`）、次タスク（`next_task`）を必須とする単一のJSON schemaを`codex exec`へ渡す。各値は既存の個別推論に変換できる最小の構造（表題・内容・理由・確信度など）を検証する。

runner自体は`--ephemeral`、`--sandbox read-only`、`--ignore-user-config`、`--ignore-rules`、`--output-schema`を指定して1回だけCLIを起動する。CLI失敗、JSON構文不正、schemaと異なる応答は成功結果として扱わない。実行オーケストレータは適格な同一turnで統合runnerを1回だけ呼び、成功時は個別経路を起動しない。非適格または統合runner失敗時は追加の統合CLIを起動せず、既存の個別経路へ委譲する。collectorへの接続は後続タスクで行う。
## 統合結果の保存形式

統合結果は既存の`ChangeSummary`、決定事項推論proposal、`NextTask`へ変換する。`combined_turn`台帳entryの`payload`には、既存の変更要約・決定事項・次タスクと同じversioned payloadをそれぞれ`change_summary`、`decision`、`next_task`として格納する。プロンプト本文や未マスク入力は保存しない。

統合結果は`combined_turn` 1 entryとして既存の原子的`append`で保存する。置換に失敗した場合は、直前の台帳を保持し一時ファイルを残さない。復元時はworkspace・session・turn・入力SHA-256がすべて一致する完全な`combined_turn`だけを採用する。不完全な統合entry、またはSHA-256不一致は復元せず、従来の個別台帳entryを安全に利用する。
## 統合推論の設定・制約・運用

統合推論専用の設定キーはまだない。`[ai_inference] mode`と`max_calls_per_run`、`[storage] inference_ledger_file`は既存の推論と共通であり、設定例では`incremental`、`3`、`%LOCALAPPDATA%\CodexMobileDashboard\state\ai-inference-ledger.json`を使用する。`max_calls_per_run`は0以上の整数で、統合呼び出しもcollectorへ接続した後はこの共有上限に含める。

適格なのは、今回のincremental対象で完了済みの同一turnだけである。すべての入力がマスク済みで、次タスク候補があり、turn外の文脈を必要としないことが必要である。適格なら統合runnerは1回だけ実行し、成功時は個別経路を起動しない。非適格、CLI失敗、JSON/schema不正、台帳payload不完全、または入力SHA-256不一致なら、個別経路へfallbackする。

運用時に推論本文・Token・未マスク本文を台帳やログへ保存してはならない。`combined_turn`は3種の個別versioned payloadを1 entryとして原子的保存し、復元時はworkspace・session・turn・入力SHA-256の完全一致を要求する。現時点では統合runner・台帳・fallbackオーケストレータは実装済みだが、`collector_runtime`への接続は未実装である。そのため通常のcollector運用は既存の個別推論経路を継続する。
## 統合promptの入力範囲と上限

統合promptには対象turnのマスク済みメッセージだけを入れる。Gitについては収集状態、branch、clean状態、変更ファイルのパス・旧パス・状態だけを含め、diff本文、ファイル本文、コミットメッセージは含めない。ファイル参照は採用済みの対象メッセージを根拠にするものだけを含める。

統合promptの上限は64 KiBである。メッセージを優先し、Gitファイルメタデータとファイル参照は上限内に収まる分だけを追加する。超過時はメッセージ本文を安全に切り詰め、それ以上のメタデータは省略する。未マスクの対象メッセージが含まれる場合はpromptを生成せず個別fallbackを選ぶ。
