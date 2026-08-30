# Codex CLI推論の現状と増分化設計

## 現在の呼び出し経路

`collect-once`は検出した各workspaceのSnapshotを構築し、次の3経路で別々に`codex exec`を起動し得る。

| 用途 | 経路 | 現在の単位 |
| --- | --- | --- |
| 変更要約 | `generate_change_summaries()` → `ChangeSummaryCliRunner.generate()` | 明示要約がない完了ターンごと |
| 決定事項 | `extract_decisions()` → `DecisionCliRunner.extract()` | 曖昧参照を含む決定候補メッセージごと |
| 次タスク | `extract_next_task()` → `CodexCliRunner.infer()` | 明示的な次タスクがないworkspaceごとに最大1回 |

CLIは`--ephemeral`、`--sandbox read-only`、`--ignore-user-config`、`--ignore-rules`、`--output-schema`を指定する。モデル指定は渡さないため、Codex CLI側の既定モデルと利用枠を使う。1呼び出しのタイムアウトは120秒、入力上限は128 KiBである。collector全体の共有呼び出し上限は未実装である。

## 推論モード

`[ai_inference] mode`は`off`、`incremental`、`backfill`を受け付け、未指定時は`incremental`である。

- `off`: 3経路のCodex CLIを起動せず、規則ベース抽出とフォールバックを使う。
- `incremental`: 今回の増分収集で追加されたturnだけをCLI推論対象にする。
- `backfill`: 現時点ではCLIを許可するだけで、候補範囲は`incremental`と同じ。専用コマンドも未実装。

したがって、増分化が完了するまで通常収集でも過去ターンが候補になり、反復・大量推論が発生し得る。

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
