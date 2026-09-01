# 手動実行コマンド

`tools.collector`は、Codex JSONLの探索、抽出、表示用JSON生成、HTTPS送信をまとめて1回実行する`collect-once`と、永続未送信キューの確認・再送を提供する。

実設定を次へ配置する。

```text
%LOCALAPPDATA%\CodexMobileDashboard\config\collector.ini
```

設定例`config/collector.example.ini`をコピーして、AndroidサーバーのHTTPS URL、CA公開証明書、Tokenファイルを実値へ設定する。TokenはINIへ書かない。

## キュー状態の確認

```powershell
cd C:\path\to\CodexMobileDashboard
python -m tools.collector --config "$env:LOCALAPPDATA\CodexMobileDashboard\config\collector.ini" queue-status
```

標準出力へ、未送信件数、sequence、ワークスペースID、Snapshot ID、ファイル数、最後の安全なエラー分類だけをJSONで出力する。本文、Token、HTTP応答本文は出力しない。

## 最古の1件を再送

```powershell
python -m tools.collector --config "$env:LOCALAPPDATA\CodexMobileDashboard\config\collector.ini" retry-queued
```

最古のキュー項目を1件だけ再送する。commit成功後にだけその項目を削除し、失敗時は項目を保持したまま終了コード2を返す。

## 全件を順に再送

```powershell
python -m tools.collector --config "$env:LOCALAPPDATA\CodexMobileDashboard\config\collector.ini" retry-queued --all
```

先頭から順に再送する。いずれかの項目で失敗すると停止し、残りのキューは変更しない。再送時は保存済みDelivery IDを再利用する。

## 終了コード

| 値 | 意味 |
| --- | --- |
| `0` | 状態確認または指定再送が成功 |
| `2` | 設定、キュー、通信、認証、証明書のいずれかで失敗 |

未送信キューの定期再送はWindows タスク スケジューラで登録できる。手順は[`SCHEDULED_EXECUTION.md`](SCHEDULED_EXECUTION.md)を参照する。


## 収集・変換を1回実行

```powershell
python -m tools.collector --config "$env:LOCALAPPDATA\CodexMobileDashboard\config\collector.ini" collect-once
```

HTTPS送信を行わず、ローカルJSON、読取位置、マスク済み履歴だけを更新する確認には`--no-send`を付ける。

`[logging] directory`を設定している場合、手動`collect-once`と`backfill-ai`は`collector.log`、`converter.log`、`sender.log`を初期化する。収集・推論は`collector.log`、表示用JSON変換は`converter.log`、HTTPS送信は`sender.log`で確認する。`--no-send`では送信処理を行わないため、`sender.log`に送信イベントは記録されない。

各ログは成功時の完了イベントと失敗時の安全な分類を記録する。失敗調査ではイベント名、件数、`kind`、HTTPステータス等を確認し、例外本文、Token、チャット本文、ローカルパスをログへ追加しない。

## 推論モードの運用

通常は`[ai_inference] mode = incremental`のまま`collect-once`を実行する。初回導入時の過去履歴は通常推論しない。そのため、過去turnしかなく、完全一致する永続台帳も規則で抽出できる明示情報もない場合は、決定事項・変更要約が空でも収集失敗ではない。CLIを使わずに収集する場合は`mode = off`へ変更する。

過去の未処理turnを明示的に補完する場合だけ、共有上限を適用する次を実行する。送信を省略する確認には`--no-send`を付ける。

```powershell
python -m tools.collector --config "$env:LOCALAPPDATA\CodexMobileDashboard\config\collector.ini" backfill-ai --no-send
```

`max_calls_per_run`は1回の実行における共有上限で、0ならCLIを起動しない。コマンドの標準出力はJSONで、`executions`、`successes`、`failures`、`limit_reached`、`pending_remaining`、`progress`を返す。`progress`はその実行で新たに成功した推論結果数であり、キャッシュヒットとスキップは含まない。

`backfill-ai`は実行時だけ`backfill`モードを強制するため、INIの`mode = incremental`は変更しない。既定上限3回に達した場合は、標準出力JSONの`limit_reached`、`successes`、`failures`を確認し、`limit_reached`が0になるまで必要に応じて同じコマンドを再実行する。判定はログ文字列へ依存しない。`failures`がある場合は先に原因を確認する。

既定では従来どおり1回だけ実行する。`limit_reached=0`まで有限回だけ自動継続する場合は次を使用する。

```powershell
python -m tools.collector `
  --config "$env:LOCALAPPDATA\CodexMobileDashboard\config\collector.ini" `
  backfill-ai --no-send --until-complete --max-runs 100
```

`--max-runs`は`--until-complete`と同時にだけ指定でき、1～1,000を許可する。省略時は100回である。各反復の`max_calls_per_run`は変更しない。自動継続の標準出力JSONでは`executions`、`successes`、`failures`、`progress`が全反復の合計、`limit_reached`と`pending_remaining`が最終反復の値になる。`limit_reached=0`なら終了コード0、推論失敗なら2を返す。`limit_reached>0`かつ`progress>0`の場合だけ次の反復へ進み、`progress=0`または最大反復数到達なら終了コード3で停止する。進捗停止時の`stop_reason`は`no_progress`、最大反復数到達時は`max_runs_reached`である。`--no-send`では全反復をローカル処理だけにする。送信ありの場合も中間Snapshotは送信せず、補完完了後に通常`incremental`の完全Snapshotを1回だけ送信する。通常の定期`collect-once`からbackfillを暗黙起動しない。

`--no-send`で補完した後は、次を実行して台帳から結果を復元したSnapshotをAndroidサーバーへ送信する。

```powershell
python -m tools.collector --config "$env:LOCALAPPDATA\CodexMobileDashboard\config\collector.ini" collect-once
```

補完結果を直ちに送信する場合は、`backfill-ai`実行時の`--no-send`を外す。詳細な空条件、永続キャッシュ、pendingとの関係は[`AI_INFERENCE.md`](AI_INFERENCE.md)を参照する。
