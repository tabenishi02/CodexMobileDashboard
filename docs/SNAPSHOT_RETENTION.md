# Snapshot保持整理

`tools/snapshot_retention.py`は、[保持方針](SNAPSHOT_CLEANUP.md#恒常運用の保持方針)をAndroid上で判定する。緊急整理用の`snapshot_cleanup_inventory.py`と`snapshot_cleanup_apply.py`とは分離し、通常運用では本CLIを使用する。

## 実装する保持条件

- publicはcurrent、current以外の新しい2世代、最終活動から1時間以内、PC未送信／再送キュー対象を保護する。
- 公開済みstagingはcurrent・キュー対象・最終活動から1時間以内を保護する。publicとファイル数・サイズ・SHA-256が一致しない場合は削除しない。
- 未完了stagingはcurrent・キュー対象・最終活動から7日以内を保護する。commit済みなのにpublicがない場合は削除しない。
- `.deliveries`は対応staging、`.commits`は対応publicと同じ整理で先に削除する。対応データがないreceiptは7日保持する。
- unknown workspace、不正receipt、リンク、特殊ファイル、未来時刻、欠損currentは安全側で停止または保留する。
- 受信・公開、recovery/fullバックアップとは共通`backup.lock`で排他する。

## 初回実機受入

1. 検証済みの最初の両端full ZIPを保持する。
2. PCの最新`queue-status`をUTF-8 JSONで保存する。
3. Androidへ同じGitコミットの`server.py`、`snapshot_cleanup_inventory.py`、`snapshot_cleanup_apply.py`、`snapshot_retention.py`を配置し、サーバーを再起動する。
4. `--dry-run`を実行し、終了コード0、`policy=retention-v1`、候補・保護・保留の件数と論理容量を確認する。
5. current、直前2世代、1時間／7日以内、PCキュー、異常・判定不能データが候補にないことを確認する。
6. PC定期送信を一時停止して最新キューを再取得し、再度dry-runする。承認した結果を同じキューと`--apply`へ渡す。
7. apply後にcurrent参照、HTTPS受信・commit、スマートフォン表示、未送信再送、recoveryバックアップを確認する。
8. 容量と候補推移を記録してから定期整理を有効化する。

## dry-run

```sh
python "$HOME/CodexMobileDashboard/app/tools/snapshot_retention.py" \
  --data "$HOME/CodexMobileDashboard/data" \
  --queue-status "$HOME/CodexMobileDashboard/app/tools/cleanup-queue-status.json" \
  --backup-lock "$HOME/.cache/codex-mobile-dashboard/backup.lock" \
  --dry-run > "$HOME/CodexMobileDashboard/app/tools/retention-dry-run.json"
```

標準エラーには進捗、標準出力にはJSONだけを出す。大容量環境では長時間かかるため、独立プロセスで実行し、短間隔でポーリングしない。

## apply

```sh
python "$HOME/CodexMobileDashboard/app/tools/snapshot_retention.py" \
  --data "$HOME/CodexMobileDashboard/data" \
  --queue-status "$HOME/CodexMobileDashboard/app/tools/cleanup-queue-status.json" \
  --approved-report "$HOME/CodexMobileDashboard/app/tools/retention-dry-run.json" \
  --backup-lock "$HOME/.cache/codex-mobile-dashboard/backup.lock" \
  --apply > "$HOME/CodexMobileDashboard/app/tools/retention-result.json"
```

applyはdry-runの評価時刻を再利用し、候補集合・容量・内容指紋・currentを再検証する。receipt、staging、publicの順で削除する。失敗時は古い承認結果を再利用せず、最新キューでdry-runからやり直す。

## 自動テスト

2026-09-29：保持世代、1時間／7日、current・pending、公開済み／未完了staging、receipt先行削除、孤立receipt、未来時刻、不正receipt、未知workspace、apply時current変更を含むretention関連7テストと、既存cleanup・サーバー・backup workerを合わせた92テストが成功した。
## 定期実行

Windowsの`CodexMobileDashboard-SnapshotRetention`タスクから、PC側の`tools.snapshot_retention_coordinator`を非表示で実行する。既定は月曜～土曜の03:00で、日曜03:00の週次recoveryバックアップとは重ねない。

```powershell
.\scripts\install_snapshot_retention_task.ps1 -Preview
.\scripts\install_snapshot_retention_task.ps1
.\scripts\get_snapshot_retention_task_status.ps1 | Format-List
Start-ScheduledTask -TaskName 'CodexMobileDashboard-SnapshotRetention'
.\scripts\uninstall_snapshot_retention_task.ps1 -Preview
.\scripts\uninstall_snapshot_retention_task.ps1
```

コーディネータは`backup.ini`の`backup.collector_config`、`android.ssh_host`、`android.repository`を再利用する。collector mutexを保持して最新キューを取得し、SSH host key確認を有効にしたままAndroidでdry-runと同じレポートを指定したapplyを順に実行する。recovery/fullバックアップも同じcollector mutexとAndroidの`backup.lock`を使用するため、同時実行しない。

タスクは`pythonw.exe`、Interactive、Limited、StartWhenAvailable、MultipleInstances=IgnoreNew、実行時間制限なしで登録する。処理中はcollectorが次回実行へ持ち越され、終了後の定期実行で再送される。Androidの一時queueと大容量レポートは成功・失敗時に削除し、最新の小さな結果とログだけを`$HOME/.cache/codex-mobile-dashboard/retention-latest-*`へ残す。PC側の最終結果はcollector stateファイルと同じディレクトリの`snapshot-retention-result.json`へatomic保存する。

`LastTaskResult=0`かつPC結果の`state=completed`を成功とする。失敗時はAndroidの`retention-latest.log`、PC結果の`error`、collectorキューを確認する。古いレポートを手動で再利用せず、次回タスクまたは手動コーディネータで最新キューからやり直す。

```powershell
python -m tools.snapshot_retention_coordinator `
  --config "$env:LOCALAPPDATA\CodexMobileDashboard\config\backup.ini"
```
