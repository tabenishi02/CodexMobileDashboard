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