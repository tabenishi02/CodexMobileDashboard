# Snapshot整理の適用

この手順は、確認済みdry-runと削除直前の再検証結果が一致した場合だけ、古いpublic・staging Snapshotを削除する。`current.json`、参照中Snapshot、PC未送信キュー、`.commits`、`.deliveries`は削除しない。

このスクリプトは確認済み候補を扱う緊急整理用で、[恒常運用の保持方針](SNAPSHOT_CLEANUP.md#恒常運用の保持方針)に定めた世代数・保持期間・受付履歴との同時整理はまだ実装していない。通常の自動整理は後続タスクが完了するまで有効にしない。

## 共通排他

Androidサーバー、Snapshot整理、recovery/fullバックアップは`~/.cache/codex-mobile-dashboard/backup.lock`を共有する。

- サーバーはファイル受信・公開処理中に共有lockを取得する。
- dry-runとapply、Androidバックアップは排他lockを取得する。
- 排他lock中のPOSTは`503`と`Retry-After: 1`を返す。GETとhealthは継続する。
- 受信・公開が進行中、またはrecovery/fullバックアップが実行中なら、整理は`maintenance_busy`で削除前に停止する。
- lockファイルの存在は実行中を意味しない。手動削除せず、OSのlock状態で判定する。

実機へ適用する前に、共通lock対応版の`server.py`と整理スクリプトが同時に配置され、`server.ini`の`[maintenance] lock_file`がバックアップと同じパスであることを確認する。

## dry-run

PCで最新の全未送信キューをUTF-8 JSONとして取得し、Androidへ配置する。dry-runは排他lockを保持して候補を再計算するが削除しない。

```sh
python "$HOME/CodexMobileDashboard/app/tools/snapshot_cleanup_apply.py" \
  --data "$HOME/CodexMobileDashboard/data" \
  --queue-status "$HOME/CodexMobileDashboard/app/tools/cleanup-queue-status.json" \
  --backup-lock "$HOME/.cache/codex-mobile-dashboard/backup.lock" \
  --dry-run > "$HOME/CodexMobileDashboard/app/tools/cleanup-dry-run.json"
echo "dry_run_exit=$?"
```

終了コード0を確認し、`candidates`、`protected`、`deferred`、`candidate_count`、`logical_bytes`を確認する。各候補にはファイル数・論理バイト数・`tree_sha256`が含まれる。dry-run結果は削除許可そのものではなく、内容確認後にapplyへ渡す承認済み入力である。

## apply

確認済みdry-runと同じ時点以後の最新キューJSONを使用する。applyは共通排他lockを取得後、候補集合と合計を再計算して承認済み結果と比較する。さらに各候補を削除する直前にcurrentと`tree_sha256`を再検証する。

```sh
python "$HOME/CodexMobileDashboard/app/tools/snapshot_cleanup_apply.py" \
  --data "$HOME/CodexMobileDashboard/data" \
  --queue-status "$HOME/CodexMobileDashboard/app/tools/cleanup-queue-status.json" \
  --approved-report "$HOME/CodexMobileDashboard/app/tools/cleanup-dry-run.json" \
  --backup-lock "$HOME/.cache/codex-mobile-dashboard/backup.lock" \
  --apply > "$HOME/CodexMobileDashboard/app/tools/cleanup-result.json"
echo "cleanup_exit=$?"
```

`maintenance_busy`、`candidate_set_changed`、`candidate_total_changed`、`candidate_content_changed`、`current_changed_during_cleanup`では安全側に停止する。削除開始後のストレージエラー等では一部候補だけが削除済みになり得るため、最新キューを取得してdry-runからやり直す。

## 実行後の確認

```sh
cat "$HOME/CodexMobileDashboard/app/tools/cleanup-result.json"
df -h "$HOME/CodexMobileDashboard/data"
du -h -d 1 "$HOME/CodexMobileDashboard/data"
```

各workspaceの`current.json`参照先が存在すること、HTTPSの`/health?workspace_id=...`、定期送信、スマートフォンの現在表示が正常なことを確認する。
