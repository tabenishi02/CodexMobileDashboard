# OSS利用者向けAI backfill運用ガイド

## 目的と安全方針

`backfill-ai`は、初回導入時などに通常収集から除外された過去の完了turnを、利用者が明示的に補完するコマンドである。通常の`collect-once`や定期実行から大量backfillを暗黙に開始しない。

最初は`--no-send`と小さい`--max-runs`を指定し、使用量と処理時間を確認する。無制限のシェルループは使用せず、有限の`--max-runs`を持つ組み込みの自動継続を使用する。

```text
python -m tools.collector --config <collector.ini> backfill-ai --no-send --until-complete --max-runs 5
```

`<collector.ini>`は実際の設定ファイルへ置き換える。通常運用の`[ai_inference] mode = incremental`を`backfill`へ変更する必要はない。

## 使用量の見積もり

1反復のCodex CLI呼び出し上限を`max_calls_per_run = M`、自動継続の上限を`--max-runs R`とする。

| 実行方法 | backfillコマンド全体の理論最大CLI呼び出し数 |
| --- | ---: |
| `--no-send`あり | `M × R` |
| `--no-send`なし | `M × R + M` |

送信ありでは、補完完了後に完全Snapshotを作る通常`incremental`が1回動作するため、その共有上限`M`も保守的な見積もりへ含める。既定の`M = 3`、`R = 100`では、`--no-send`が最大300回、送信ありが最大303回である。これは上限であり、実際の`executions`は次の理由で少なくなる。

- 入力SHA-256が一致する永続キャッシュはCLIを起動しない。
- 同一turnの変更要約・決定事項・次タスクを統合できる場合は1回のCLI呼び出しになる。
- 推論不要と保守的に判定できるturnはスキップする。
- `limit_reached = 0`になれば`max_runs`より前に終了する。

標準出力JSONの`executions`が実際のCLI呼び出し数、`successes`と`failures`が成功・失敗数、`progress`が新たに永続保存できた結果数である。キャッシュヒットとスキップは`progress`へ含めない。`limit_reached`と`pending_remaining`は最終反復の残件を示すが、統合可否やキャッシュによって1回のCLIで処理できる量が変わるため、残件数をそのまま必要反復数へ換算しない。

推論メトリクスはプロンプト本文、Token、入力SHA-256、各種IDを出力しない。Token数や料金も集計しないため、課金または利用枠はCodex CLIを提供する側の利用量画面で確認する。collectorはモデルを指定せず、Codex CLI側の既定モデルと利用枠を使用する。

## 処理時間の見積もり

1回のCodex CLI呼び出しには120秒の固定タイムアウトがある。推論待ちだけの保守的な上限は次のとおりである。

```text
--no-sendあり: 120秒 × M × R
--no-sendなし: 120秒 × (M × R + M)
```

既定値ではそれぞれ最大10時間、最大10時間6分に相当する。実時間はキャッシュヒット、統合、早期完了により短くなる一方、JSONL収集、Git確認、台帳とSnapshotの保存時間が別途加わる。この計算値は完了時間の保証ではない。

最初は`--max-runs 1`から`5`程度で計測し、標準出力の`executions`と実時間から自環境の値を見積もる。

PowerShell:

```powershell
$config = "C:\path\to\collector.ini"
$startedAt = Get-Date
python -m tools.collector --config $config backfill-ai --no-send --until-complete --max-runs 5
$exitCode = $LASTEXITCODE
$elapsed = (Get-Date) - $startedAt
$exitCode
$elapsed.TotalMinutes
```

Bash:

```bash
CONFIG=/path/to/collector.ini
time python -m tools.collector --config "$CONFIG" backfill-ai --no-send --until-complete --max-runs 5
```

## 停止条件と対処

自動継続は`limit_reached > 0`かつ`progress > 0`の場合だけ次の反復へ進む。

| 終了コード | `stop_reason`または状態 | 意味 | 次の操作 |
| ---: | --- | --- | --- |
| `0` | `completed = true` | `limit_reached = 0`で補完完了 | `--no-send`なら通常`collect-once`で送信する |
| `2` | `inference_failure` | CLI推論が失敗 | `collector.log`の安全な失敗分類とCodex CLI環境を確認して再実行する |
| `2` | `save_failure` | 台帳またはSnapshotの保存に失敗 | 空き容量と設定済み保存先の権限を確認して再実行する |
| `2` | `hard_limit_exceeded` | runtimeが1反復の共有上限超過を報告 | 再実行せず、不具合報告用に安全な結果JSONとバージョンを保存する |
| `3` | `no_progress` | 残件があるが新規成功が0件 | `max_calls_per_run = 0`、CLI利用可否、入力マスク拒否などを確認する |
| `3` | `max_runs_reached` | 有限の反復上限に到達 | 結果と利用量を確認し、同じコマンドを再実行する |
| `130` | `interrupted` | Ctrl+Cなど利用者による中断 | 必要なときに同じコマンドを再実行する |

設定読込など自動継続開始前の失敗では、終了コード2と`manual_command_failed kind=...`が出力される場合がある。例外本文や内部パスを表示しない仕様のため、設定例との差分と`collector.log`の安全な分類を確認する。

## 安全な再開手順

1. 標準出力JSONの終了コード、`stop_reason`、`executions`、`successes`、`failures`、`progress`、`limit_reached`、`pending_remaining`を記録する。
2. `inference_failure`、`save_failure`、`no_progress`では原因を解消する。`max_runs_reached`と意図した`interrupted`は、そのまま再開できる。
3. `inference_ledger_file`、collector state、履歴、CodexプロジェクトJSONLを削除せず、同じ設定と対象workspaceで同じコマンドを再実行する。
4. 保存済みの成功結果が入力SHA-256で再利用され、未保存または入力が変わったturnだけが推論対象になることを標準出力で確認する。
5. `completed = true`かつ`limit_reached = 0`まで有限回ずつ繰り返す。
6. `--no-send`で完了した場合だけ、通常の送信あり収集を1回実行する。

```text
python -m tools.collector --config <collector.ini> collect-once
```

成功結果はCLI成功直後に1件ずつ原子的に台帳へ保存される。途中失敗や中断によって、それ以前の成功結果を巻き戻さない。同一入力の保存済み結果は再起動後も再利用するが、入力が変わった場合は新しいSHA-256となり再推論する。失敗結果は成功キャッシュとして保存しない。

## OSS運用上の注意

- 大量backfillをサービス起動時や通常の定期`collect-once`へ組み込まない。
- 初回は`--no-send --max-runs 1`から`5`程度で測定する。
- `--max-runs`は1～1,000に限定される。大きな値を既定運用へ固定しない。
- `max_calls_per_run = 0`はCLI停止用であり、残件がある自動backfillでは`no_progress`になる。
- 停止後に台帳やstateを初期化して再開しない。初期化するとキャッシュ済み結果を失い、使用量が増える。
- 不具合報告にはバージョン、終了コード、秘密情報を含まない標準出力JSON、ログのイベント名と件数だけを添付する。Token、設定ファイル、プロンプト本文、JSONL本文、内部パスは添付しない。
