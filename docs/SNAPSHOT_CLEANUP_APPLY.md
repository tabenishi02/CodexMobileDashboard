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

## 実行前

1. 検証済みの両端full ZIPを整理前チェックポイントとして保持する。
2. Androidの`server.py`、`snapshot_cleanup_inventory.py`、`snapshot_cleanup_apply.py`を同じGitコミットへ揃える。
3. 複数PCから送信する場合は全送信元のキューを取得する。1台でも取得不能なら整理しない。
4. PCごとに`queue-status`をUTF-8 JSONとして保存し、Snapshotのworkspace IDとSnapshot IDを統合した最新の保護一覧をAndroidへ配置する。
5. `server.ini`の`[maintenance] lock_file`と`--backup-lock`が同じパスであることを確認する。

PCのqueue-status保存例：

```powershell
$queue = python -m tools.collector --config "$env:LOCALAPPDATA/CodexMobileDashboard/config/collector.ini" queue-status
if ($LASTEXITCODE -ne 0) { throw 'queue-status failed' }
[IO.File]::WriteAllText("$PWD/tmp/cleanup-queue-status.json", ($queue -join "`n"), [Text.UTF8Encoding]::new($false))
```

## dry-run

最新の全未送信・再送キューを反映したJSONを使用する。dry-runは排他lockを保持して候補を再計算するが削除しない。

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

`maintenance_busy`、`candidate_set_changed`、`candidate_total_changed`、`candidate_content_changed`、`current_changed_during_cleanup`では安全側に停止する。

## 失敗時の復旧

- `maintenance_busy`：受信・公開またはrecovery/fullバックアップが先にlockを取得している。実行中処理を強制終了せず、終了後に最新キューでdry-runからやり直す。
- `candidate_set_changed`、`candidate_total_changed`：dry-run後にcurrent、キュー、受信状態、候補が変化した。承認済みJSONを再利用せず、最新キューを取得してdry-runを再確認する。
- `candidate_content_changed`、`current_changed_during_cleanup`：削除直前の内容またはcurrentが変化した。該当候補を手動削除せず、サーバー・backup worker・送信キューの状態を確認してdry-runからやり直す。
- `OSError`等の削除中エラー：それ以前の候補だけ削除済みの場合がある。`.deliveries`、`.commits`、PCキューを手動削除せず、`current.json`と参照先、各候補の存在を確認する。最新dry-runで`public_missing`、`public_mismatch`、不完全なtreeなどが出た対象は自動整理せず手動調査する。
- cleanup中にPOSTが`503`になった送信は、PCキューから削除せず通常の再送へ戻す。Delivery IDとcommit Delivery IDを作り直さない。

失敗後に古い`cleanup-result.json`を成功結果として扱わない。終了コード、標準エラー、最新dry-runの3点を保存し、現在表示と未送信キューが正常と確認できるまで次のapplyを実行しない。

## 安全性テスト

2026-09-29に以下を自動テストで確認した。

| ケース | 期待結果 |
| --- | --- |
| public・stagingのcurrent | 両方を候補外として維持 |
| PC未送信／再送キュー | workspace/Snapshot組を両方の領域で保護 |
| commit前の受信途中Snapshot | `commit_not_confirmed`として保留 |
| dry-run後のキュー追加・current切替 | `candidate_set_changed`で削除前停止 |
| 候補内容の同時変更 | `candidate_content_changed`で削除前停止 |
| 削除直前のcurrent変更 | `current_changed_during_cleanup`で削除前停止 |
| 削除途中のI/O失敗 | current・pending・`.deliveries`・`.commits`を維持し、未処理候補を残す |
| cleanup中の受信・公開 | 共通lockによりPOSTを`503`で拒否し、保存内容を変更しない |

関連するinventory、apply、サーバー、Android backup workerの85テストが成功している。実機での定期整理と容量推移の確認は次タスクで行う。

## 実行後の確認

```sh
cat "$HOME/CodexMobileDashboard/app/tools/cleanup-result.json"
df -h "$HOME/CodexMobileDashboard/data"
du -h -d 1 "$HOME/CodexMobileDashboard/data"
```

各workspaceの`current.json`参照先が存在すること、HTTPSの`/health?workspace_id=...`、定期送信、スマートフォンの現在表示が正常なことを確認する。
