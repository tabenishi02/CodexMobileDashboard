# 定期実行

現時点で定期実行する対象は、PC側に保持した未送信Snapshotの再送だけです。Codex JSONLの収集、JSON生成、新規Snapshotの送信を一括実行するcollector本体は`collect-once`として実装済みです。現在の定期タスクは再送専用であり、新規収集の定期実行への統合は別途行います。

Windows タスク スケジューラへ、ログオン中の同一Windowsユーザーとして再送コマンドを1分ごとに登録します。ログオンしていない状態では動作しません。Codexセッションとユーザー秘密情報を同じユーザー権限で安全に扱うためです。

## 登録

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

## 将来のcollector本体

将来は、ターン完了時の即時処理と、作業中30秒・停止中60秒のJSONL/Git確認をcollector本体へ実装する。その実装後に、この再送専用タスクを収集・変換・送信の定期実行へ置き換えるか統合する。収集本体がない現在は、定期再送だけを行うため、未送信ではない新規データを勝手に送信しない。