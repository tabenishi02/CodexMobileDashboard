# Codex CLI推論の現行調査

本書は、増分推論化の実装前に確認したcollectorのCodex CLI呼び出し経路、最大回数、キャッシュ範囲を記録する。対象は`collect-once`の現行実装であり、将来の推論モード実装後は更新する。

## 呼び出し経路

`tools.collector`の`collect-once`は`tools.collector_runtime.run_once()`を呼び、検出した各ワークスペースについて`_build_workspace_snapshot()`を実行する。同関数から次の3経路で隔離した`codex exec`を起動し得る。

| 用途 | 呼び出し元 | CLI実行条件 | 単位 |
| --- | --- | --- | --- |
| 変更要約 | `generate_change_summaries()` → `ChangeSummaryCliRunner.generate()` | 完了ターンに明示的な最終要約がなく、マスク済み根拠メッセージがある | 完了ターンごと |
| 決定事項 | `extract_decisions()` → `DecisionCliRunner.extract()` | ユーザーメッセージが決定トリガーを含み、参照が曖昧である | 決定候補メッセージごと |
| 次タスク | `extract_next_task()` → `CodexCliRunner.infer()` | 明示的な次タスクを抽出できず、マスク済み入力である | ワークスペースごと最大1回 |

3経路はいずれも`codex exec --ephemeral --sandbox read-only --ignore-user-config --ignore-rules --skip-git-repo-check --color never --output-schema <一時スキーマ> -`を別プロセスで実行する。実行ファイルは`shutil.which("codex")`で検出し、モデル指定を渡さないためCodex CLIの既定モデルと利用枠を使用する。

## 呼び出し回数と入力制限

現行の1呼び出しのタイムアウトは120秒、最大入力は128 KiB（`131072` bytes）である。上限を超える入力は各Extractor内で切り詰める。

ただし、collector実行全体のCodex CLI呼び出し回数を制限する仕組みは現時点で存在しない。したがって、1ワークスペースでは最大で「要約対象の完了ターン数 + 曖昧な決定候補数 + 1回」が呼ばれ、複数ワークスペースではその合計になる。

`run_once()`は履歴を復元してからワークスペースのSnapshotを再構築し、変更要約には`work.turns`全体、決定事項には全会話メッセージを渡す。このため、通常の`collect-once`でも過去ターンが推論候補になり得る。処理位置とマスク済み会話履歴はcollector終了時に保存されるが、推論済みかどうかの状態には使用されない。

## 既存キャッシュの範囲

各Extractorには次の一時キャッシュがある。

| 対象 | キー | 保存期間 | collector再起動後 |
| --- | --- | --- | --- |
| 変更要約CLI結果 | プロンプトSHA-256 | `ChangeSummaryCliRunner`のプロセス存続中 | 再利用しない |
| 決定事項CLI結果 | プロンプトSHA-256 | `DecisionCliRunner`のプロセス存続中 | 再利用しない |
| 変更要約の結果エントリ | ターンと根拠SHA-256 | 呼び出し元が渡す間だけ。失敗は5分 | collectorから再投入されない |
| 次タスクの結果エントリ | 根拠SHA-256 | 呼び出し元が渡す間だけ。失敗は5分 | collectorから再投入されない |

つまり、成功した推論結果をファイルへ永続化する実装はない。`ChangeSummaryGenerationResult.cache_entries`と`NextTaskExtractionResult.cache_entry`は返されるが、`collector_runtime`は保存も次回実行時の復元も行わない。決定事項のキャッシュもRunnerのインスタンス内だけである。

このため、collectorの完走後であっても、同一入力を次の`collect-once`で再推論する可能性がある。途中でCtrl+Cなどにより`run_once()`が終わらない場合は、読み取り状態・履歴が末尾で保存されず、同じ範囲が次回の候補になる可能性もある。

## 実装上の改善対象

通常収集での反復・大量推論を防ぐため、以後のタスクでは次を実装する。

- 既定`incremental`では今回追加された完了ターンだけを候補にする。
- workspace、session、turn、入力SHA-256、結果、生成メタデータを結ぶ永続台帳を、成功ごとに原子的保存する。
- 全推論種別で共有する実行回数上限を導入する。
- 過去未処理分は通常収集から除外し、明示的なbackfillでのみ処理する。
- 同一ターンの複数推論を可能な範囲で統合する。

プロンプト本文、Token、セッション本文はログ・台帳の運用ログへ出力しない。
## 推論モード（導入済み）

[ai_inference] modeはoff、incremental、backfillを受け付け、未指定時はincrementalである。offでは3経路ともCodex CLIを起動しない。incrementalとbackfillではCLIを許可する。増分対象選別、永続台帳、backfill専用コマンドは後続タスクで追加するため、現時点では両モードの候補範囲は同じである。


## 永続台帳（導入済み）

変更要約の成功結果は[storage] inference_ledger_fileへ、workspace・session・turn・入力SHA-256・推論種別・結果メタデータ・生成日時を結んで1件ずつ原子的に保存する。既定パスは%LOCALAPPDATA%\CodexMobileDashboard\state\ai-inference-ledger.jsonである。


## 永続台帳の再設計

台帳レコードは `schema_version`、`workspace_id`、`session_id`、`turn_id`、`inference_kind`、`input_sha256`、`generated_at`、`payload` を持つ。`payload`は種別ごとに、表示JSONを再生成できる全フィールドを保存する。プロンプト本文、Token、未マスク本文は保存しない。

- `change_summary`: title、short_summary、details、highlights、verification、confidence、状態、根拠メッセージIDを保存する。
- `decision`: status、title、description、reason、topic、置換関係、根拠IDを保存する。
- `next_task`: text、status、origin、confidence、reason、根拠IDを保存する。

同一の workspace・session・turn・種別・入力SHA-256 は同一実質入力とみなし、完全なpayloadを復元してCLIを起動しない。入力ハッシュが変わった場合だけ再推論する。成功payloadは各件の直後に原子的置換で保存し、失敗結果は成功として保存しない。旧形式の`result`が完全payloadを持たないレコードは復元しない。

### 実装分割

1. 台帳形式を版管理・完全payload・索引検索へ置換し、変更要約の保存と復元を接続する。
2. 決定事項・次タスクの完全payload保存と復元を接続する。
3. 増分対象判定、共通呼び出し上限、backfillコマンドを台帳の未処理状態へ接続する。
4. 同一ターンの構造化推論統合、入力削減、不要推論スキップを行う。
