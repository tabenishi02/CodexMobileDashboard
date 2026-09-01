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

`max_calls_per_run`は1回の実行における共有上限で、0ならCLIを起動しない。実行数と上限到達は`logs/collector.log`を確認する。

`backfill-ai`は実行時だけ`backfill`モードを強制するため、INIの`mode = incremental`は変更しない。既定上限3回に達した場合は、`collector.log`の`inference_run_metrics`で`limit_reached`、`successes`、`failures`を確認し、`limit_reached`が0になるまで必要に応じて同じコマンドを再実行する。`failures`がある場合は先に原因を確認する。

`--no-send`で補完した後は、次を実行して台帳から結果を復元したSnapshotをAndroidサーバーへ送信する。

```powershell
python -m tools.collector --config "$env:LOCALAPPDATA\CodexMobileDashboard\config\collector.ini" collect-once
```

補完結果を直ちに送信する場合は、`backfill-ai`実行時の`--no-send`を外す。詳細な空条件、永続キャッシュ、pendingとの関係は[`AI_INFERENCE.md`](AI_INFERENCE.md)を参照する。
