# 定期実行

通常の新規収集には`scripts/install_collector_task.ps1`、未送信Snapshotの再送には既存の`scripts/install_pending_queue_retry_task.ps1`を使用する。どちらも既定1分間隔、ログオン中の同一Windowsユーザーで実行する。ログオンしていない状態では動作しない。

## 通常収集の登録・確認・解除

実設定を準備して次を実行する。登録すると、既存設定のHTTPS送信先へ表示用Snapshotを継続送信する。

```powershell
cd C:\path\to\CodexMobileDashboard
.\scripts\install_collector_task.ps1 -Preview
.\scripts\install_collector_task.ps1
Get-ScheduledTask -TaskName 'CodexMobileDashboard-Collector'
Get-ScheduledTaskInfo -TaskName 'CodexMobileDashboard-Collector'
```

既定の設定ファイルは`%LOCALAPPDATA%\CodexMobileDashboard\config\collector.ini`。`-ConfigPath`、`-TaskName`、`-IntervalMinutes`（1～60）で変更できる。同名タスクの登録は更新になる。登録時のPython絶対パスとリポジトリの作業ディレクトリを記録し、非表示のPowerShellで起動する。初回は登録の約1分後で、以降は指定間隔で繰り返す。

実行内容は次のとおり。

```text
python -m tools.collector --config <collector.ini> collect-once --incremental
```

`--incremental`は実設定を書き換えず、この実行だけ通常増分モードを使用する。既存pendingを新規完了turnより先に処理し、設定済みの`max_calls_per_run`（既定3回）を維持する。大量の過去履歴補完は起動しない。モード`off`の設定もこのオプションで上書きするため、AIを停止する場合はタスクを無効化するか、実設定の`max_calls_per_run = 0`を使用する。

収集と定期再送のrunnerは同じWindowsセッション内の名前付きmutexを共有する。実行中の重複起動は処理せず正常終了し、前の実行が異常終了してmutexが放棄された場合は次回実行が取得する。直接`python -m tools.collector`を起動する手動操作はこのロックの対象外なので、定期タスクと同時に実行しない。タスクは実行時間による強制終了を無効化し、収集コマンドの終了コードを返す。

確認は`LastRunTime`、`NextRunTime`、`LastTaskResult`と、設定先の`collector.log`・`converter.log`・`sender.log`を組み合わせる。`LastTaskResult = 0`だけではロックによるスキップと区別できないため、収集ログの`inference_run_metrics`と送信ログの`snapshot_committed`も確認する。失敗時はログを確認し、保持されたpending・未送信キューを次回収集・別の再送処理で扱う。この収集タスク自体は`retry-queued`を起動しない。

一時停止・再開・解除は次のとおり。

```powershell
Disable-ScheduledTask -TaskName 'CodexMobileDashboard-Collector'
Enable-ScheduledTask -TaskName 'CodexMobileDashboard-Collector'
.\scripts\uninstall_collector_task.ps1 -Preview
.\scripts\uninstall_collector_task.ps1
```

無効化・解除は実行中の収集の終了を保証しない。状態が`Running`なら自然終了を待ってから手動操作する。PC再起動後は同じWindowsユーザーでログオンし、上記の状態・ログ・Android側の公開Snapshot更新を確認する。再起動後の実機確認は別タスクとして残る。

2026年9月9日：利用者の継続収集・送信に対する明示承認後、`CodexMobileDashboard-Collector`を登録した。タスクはログオン中1分間隔（`PT1M`）で有効。関連テスト52件は実装時に成功済み。以下を実機で確認した（JST）。

- 19:19:05に定期起動し、複数workspaceのHTTPS送信・Snapshot commitに成功。19:19:47の集計は推論実行1回・成功1回・失敗0・pending残件0。
- 19:20:06に再度定期起動し、HTTPS送信・Snapshot commitに成功。19:20:40の集計は推論実行0回・キャッシュヒット5件・失敗0・pending残件0。
- 2回目の終了後、`LastTaskResult = 0`、状態`Ready`、次回実行予定を確認した。

実機では持ち越しpendingが0だったため、既存pending優先処理の根拠は関連テストとする。PC再起動後の確認、未送信キュー再送タスクの実登録・自動再送確認は今回の完了範囲に含めない。

## 再送専用タスクの登録

実設定とTokenファイルを準備したうえで、PowerShellから次を実行する。

```powershell
cd C:\path\to\CodexMobileDashboard
.\scripts\install_pending_queue_retry_task.ps1
```

既定のタスク名は`CodexMobileDashboard-PendingQueueRetry`、設定ファイルは`%LOCALAPPDATA%\CodexMobileDashboard\config\collector.ini`である。別名・別設定・間隔を指定する場合は次のとおり。

```powershell
.\scripts\install_pending_queue_retry_task.ps1 `
  -TaskName "CodexMobileDashboard-PendingQueueRetry" `
  -IntervalMinutes 5 `
  -ConfigPath "$env:LOCALAPPDATA\CodexMobileDashboard\config\collector.ini"
```

`-Preview`を付けると、タスクを変更せず登録内容だけを確認できる。登録済みの同名タスクがある場合、実行内容をこの再送タスクへ更新する。

## 実行内容と失敗時

タスクは`scripts/run_pending_queue_retry.ps1`を起動し、次と同じ処理を行う。

```text
python -m tools.collector --config <collector.ini> retry-queued --all
```

- queueが空なら成功して終了する。
- 失敗するとキュー項目は保持され、次回の定期実行で同じDelivery IDを使って再送する。
- 一度に長引く送信がある場合、重複した起動は行わない。
- Token、本文、HTTP応答本文はタスク引数・標準出力・ログに出力しない。

タスクの実行結果は、タスク スケジューラの履歴と`%LOCALAPPDATA%\CodexMobileDashboard\logs`で確認する。通信設定や証明書の恒久的な誤りは自動では直らないため、`queue-status`とログを確認して設定を修正する。

## 解除

次のコマンドで、このタスクだけを解除する。

```powershell
cd C:\path\to\CodexMobileDashboard
.\scripts\uninstall_pending_queue_retry_task.ps1
```

解除前に対象を確認するには`-Preview`を付ける。

## 新規収集の定期実行

通常`collect-once --incremental`の定期起動スクリプトは実装済み。実タスクの登録と定期実行結果の確認は上記のとおり完了した。ターン完了時の即時起動と、作業中30秒・停止中60秒の切替スケジュールは未実装で、今回は固定の分間隔で起動する。

大量の過去履歴を対象にする`backfill-ai`は通常定期収集から暗黙に起動しない。実装済みの`backfill-ai --until-complete --max-runs N`を利用者が明示実行し、1回ごとの共有ハード上限、全体の有限上限、失敗・進捗停止時の自動停止を維持する。これにより通常pendingの自動消化と、使用量が大きくなり得る過去補完を分離する。運用上の見積もりと再開方法は[`BACKFILL_OPERATION.md`](BACKFILL_OPERATION.md)を参照する。
