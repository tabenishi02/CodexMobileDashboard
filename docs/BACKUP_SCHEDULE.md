# バックアップ週次実行（Phase E）

同じ`tools.backup_pair`を手動CLIと非表示の週次タスクから実行する。既存collector/retryタスクは変更しない。

## 登録前

[両端バックアップ](BACKUP_PAIR.md)の設定・SSH鍵認証・known_hosts・Android配置を完了し、手動実行で両ZIPとサーバー復旧を確認してから登録する。本番の復元受入はPhase Fで確認する。

```powershell
cd C:\path\to\CodexMobileDashboard
python -m tools.backup_pair --config "$env:LOCALAPPDATA\CodexMobileDashboard\config\backup.ini"
```

バックアップ中にAndroidサーバーが一時停止する。直接の手動collector/backfill/retryと重ねない。collectorと同じWindowsユーザー・ログオンセッションで使う。

## 週次登録・状態・手動起動・解除

```powershell
.\scripts\install_backup_task.ps1 -Preview
.\scripts\install_backup_task.ps1 -Day Sunday -At '03:00'
.\scripts\get_backup_task_status.ps1 | Format-List
Start-ScheduledTask -TaskName 'CodexMobileDashboard-Backup'
.\scripts\uninstall_backup_task.ps1 -Preview
.\scripts\uninstall_backup_task.ps1
```

上記は操作例であり、登録から解除までをまとめて実行するものではない。`Start-ScheduledTask`は今すぐ同じ非表示処理を実行したい場合に使用する。

既定は日曜03:00、1週間間隔。曜日・時刻・ConfigPathを変更できる。同名登録は更新する。TaskNameはCodexMobileDashboard-Backupまたはそのハイフン接尾辞付き名に限定し、collectorタスクの誤上書きを防ぐ。Previewは登録しない。

pythonw.exeを実行し、CREATE_NO_WINDOWでPython子プロセスを起動する。標準入出力は画面に表示しない。SSHとGitも既存のコンソール抑止を使用する。Pythonパスと作業ディレクトリは登録時の絶対パスとなるため、環境移動時は再登録する。

タスク設定はInteractive・Limited、StartWhenAvailable=True、MultipleInstances=IgnoreNew、強制実行時間制限なし。電池駆動中も許可する。ログオフ中には実行しない。スリープからの強制復帰は設定しない。予定時刻に動作できなければ、実行可能になってからの開始をTask Schedulerへ委ねる。PCが常時稼働しない場合は実際にログオンする時間帯を指定する。

解除は登録を削除する操作であり、実行中のバックアップの安全な停止操作ではない。Running中はworker復旧を含む自然終了を待ってから解除する。

## 確認

状態スクリプトは登録・有効状態、最終/次回実行時刻、終了結果、週の間隔・曜日ビット値・開始日時、ログオン方式等を表示する。

- LastTaskResult=0：両ZIP成功と通常状態への復帰。
- LastTaskResult=2：失敗。`ID.pair.json`と`backup-pair.log`を確認する。
- Running中の結果値は完了結果ではない。完了を待つ。
- 設定読込やプロセス起動などの早期失敗ではpair.jsonがない場合がある。同じ手動CLIを実行して安全なエラー分類を確認する。

pair_stateとrecovery_stateは別判定。[結果判定・通信断時の手順](BACKUP_PAIR.md)を参照する。PC再起動後は同じユーザーでログオンし、タスク有効・次回予定・最終結果を確認する。collectorの正常な定期送信も確認する。

## 検証

2026-09-13：非表示ランチャー・一時週次タスクの登録/手動起動/状態/解除・両端統合の関連10テストが成功。一時タスクは専用名と代替モジュールで実行し、SSHや本番バックアップを行わない。

```powershell
$env:CODEX_DASHBOARD_TASK_TEST = '1'
python -m unittest tools.tests.test_backup_launcher tools.tests.test_backup_task_lifecycle tools.tests.test_backup_pair
Remove-Item Env:CODEX_DASHBOARD_TASK_TEST
```

2026-09-28：本番週次タスクを日曜03:00で登録し、手動起動で両端recoveryを完走した。`LastTaskResult=0`、`pair_state=complete`、`recovery_state=restored`、両ZIPの検証、Androidサーバー復旧を確認した。PC再起動後も登録・有効状態・日曜03:00の次回予定を維持した。再起動後の手動起動でも新しい両端ZIPが`pair_state=complete`、`recovery_state=restored`、`LastTaskResult=0`となり、両ZIPの内部検証と記録SHA-256一致を確認した。collectorの定期実行、未送信0件、再起動後の実HTTPS受信も確認した。
