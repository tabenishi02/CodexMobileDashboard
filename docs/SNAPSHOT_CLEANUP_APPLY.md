# Snapshot整理の適用

この手順は、確認済みdry-runと削除直前の再検証結果が完全一致した場合だけ、古いpublic・staging Snapshotを削除する。`current.json`、参照中Snapshot、PC未送信キュー、`.commits`、`.deliveries`は削除しない。

## 実行前

1. PCの`CodexMobileDashboard-Collector`が無効で、手動collector・再送・バックアップ処理もないことを確認する。
2. Androidで独立backup workerが動作していないことを確認する。
3. Androidの`stop_server.sh`を実行し、PIDファイルが削除され、`server.py`が動作していないことを確認する。
4. PCで最新の`queue-status`をUTF-8 JSONとして再取得する。前回のdry-run後に内容が変わった場合は、先にdry-runをやり直して内容を確認する。
5. `snapshot_cleanup_apply.py`、最新キューJSON、確認済み`cleanup-dry-run.json`をAndroidの`$HOME/CodexMobileDashboard/app/tools`へ配置する。

## 適用

Termuxで実行する。標準出力だけを結果JSONへ保存し、進捗と失敗理由は画面に表示する。

```sh
python "$HOME/CodexMobileDashboard/app/tools/snapshot_cleanup_apply.py" \
  --data "$HOME/CodexMobileDashboard/data" \
  --queue-status "$HOME/CodexMobileDashboard/app/tools/cleanup-queue-status.json" \
  --approved-report "$HOME/CodexMobileDashboard/app/tools/cleanup-dry-run.json" \
  --server-pid-file "$HOME/.cache/codex-mobile-dashboard/server.pid" \
  --server-script "$HOME/CodexMobileDashboard/app/server/server.py" \
  --backup-lock "$HOME/.cache/codex-mobile-dashboard/backup.lock" \
  --apply > "$HOME/CodexMobileDashboard/app/tools/cleanup-result.json"
echo "cleanup_exit=$?"
```

`candidate_set_changed`または`candidate_total_changed`で停止した場合は削除前なので、最新キューでdry-runをやり直す。削除開始後のストレージエラー等では一部候補だけが削除済みになり得るが、currentと未送信Snapshotは候補外である。再度dry-runから実施する。

## 実行後の確認

```sh
cat "$HOME/CodexMobileDashboard/app/tools/cleanup-result.json"
df -h "$HOME/CodexMobileDashboard/data"
du -h -d 1 "$HOME/CodexMobileDashboard/data"

cd "$HOME/CodexMobileDashboard/app/server"
./start_server.sh
```

起動後、各workspaceの`current.json`参照先が存在すること、HTTPSの`/health?workspace_id=...`が成功すること、スマートフォンで現在の表示データが開けることを確認する。結果確認が終わるまでPCの定期送信は再開しない。
