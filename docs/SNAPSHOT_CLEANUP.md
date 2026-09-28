# Snapshot整理のdry-run

`tools/snapshot_cleanup_inventory.py`は読み取り専用で、削除機能はない。

## 恒常運用の保持方針

この方針は将来の自動整理に適用する。現行の`snapshot_cleanup_inventory.py`と`snapshot_cleanup_apply.py`は容量障害から復旧するための確認付き緊急整理であり、以下の世代数・保持期間をまだ実装していない。最初の両端fullバックアップと検証付き一時展開が成功し、受付履歴の保持方針と自動整理が実装・テストされるまでは、自動削除を有効にしない。

| 対象 | 最低保持 | 削除候補にできる条件 |
| --- | --- | --- |
| publicのcurrent参照先 | 無期限 | 常に保護し、削除候補にしない |
| publicの過去Snapshot | currentに加えて直前2世代、かつ最終活動から1時間 | current・直前2世代・1時間以内のいずれにも該当せず、PC未送信キューにもなく、正常な公開済みSnapshotと確認できる |
| stagingのcurrent参照先 | currentである間は無期限 | 常に保護し、削除候補にしない |
| 公開済みstaging | 最終活動から1時間 | 対応するpublicとファイル集合・サイズ・SHA-256が一致し、currentでもPC未送信キュー対象でもなく、関連する受付履歴を安全に失効できる |
| 未完了staging | 最終活動から7日 | 既知workspaceの正常なSnapshotディレクトリで、current・PC未送信キュー・公開済みのいずれでもなく、7日間更新されておらず、関連する受付履歴を安全に失効できる |
| 異常または判定不能なstaging | 自動削除しない | commit済みなのにpublicがない、publicと不一致、未知workspace、不正ID、リンク、特殊ファイル、未来時刻、読取エラーは保留して手動調査する |

「直前2世代」はworkspaceごとにcurrentを除いたpublic Snapshotを最終活動日時の新しい順で選ぶ。同時刻の場合はSnapshot IDで順序を固定する。過去2世代が存在する場合はcurrentと合わせた3世代を期間に関係なく残し、更新が続くworkspaceでは直近1時間分も残す。

保持期間の基準となる最終活動日時は、Snapshotディレクトリ自身と配下の通常ファイルのmtimeの最大値とする。整理開始時刻との差がそれぞれ1時間または7日以上の場合だけ期限経過と判定する。mtimeが整理開始時刻より5分を超えて未来の場合は時刻異常として保留する。保持期間は作成途中を年齢だけで消さないための猶予であり、世代数と期間の両方を外れた場合だけ削除できる。

公開済みstagingはpublicの保持世代数とは独立して整理できるが、受付履歴と本文を以下の方針で整合させる。受付履歴との同時整理が未実装の間は、恒常運用としてstagingを削除しない。

### `.deliveries`・`.commits`の保持方針

`.deliveries`は各JSONのDelivery IDに対する保存済み判定、`.commits`はcommit Delivery IDに対する受付済み判定である。PCキューはSnapshot本文、各Delivery ID、commit Delivery IDをcommit成功確認まで保持し、成功後だけ削除する。

| 受付履歴 | 保持する条件 | 削除できる条件 |
| --- | --- | --- |
| `.deliveries` | 対応するworkspace/SnapshotがいずれかのPC未送信キュー、current、または保持対象stagingにある | 対応stagingを削除する同じ整理処理で、全PCキュー・currentの対象外と再確認できる |
| `.commits` | 対応するworkspace/SnapshotがいずれかのPC未送信キュー、current、または保持対象publicにある | 対応publicを削除する同じ整理処理で、全PCキュー・currentの対象外と再確認できる |
| 対応データがない正常な孤立receipt | 最終更新から7日 | 7日以上変化せず、全PCキュー・current・保持対象データのいずれにも対応しない |
| 不正または判定不能なreceipt | 自動削除しない | JSON構造・Delivery ID・workspace/Snapshot ID・hash・通常ファイル性を検証できない場合は手動調査する |

関連付けにはファイル名だけでなくreceipt本文のworkspace IDとSnapshot IDを使用する。PCキューに同じ組がある場合は年齢に関係なく、その組に属するすべてのreceiptを保護する。将来の整理実装では、検証済みqueue manifestから各ファイルのDelivery IDとcommit Delivery IDも取得し、複数PCがある場合は全送信元の保護集合を統合する。

削除順序は、stagingを削除する場合は対応する`.deliveries`を先、publicを削除する場合は対応する`.commits`を先とする。両方を削除する場合は両receipt群を先に失効させてからstaging、publicの順に削除する。receipt削除後に処理が中断してデータだけ残っても、同じDelivery IDの再送は本文保存・commit検証を再実行できる。データを先に削除してreceiptだけを残す順序は禁止する。

同一receiptを保持している間は、同じDelivery IDと同じ内容の再送を成功として扱い、異なる内容は競合として拒否する。安全にreceiptを削除した後は、PCキューに本文一式があれば通常のfile POSTから再保存できる。`.commits`をpublicより先に削除することで、過去Snapshotの再commitでも空き容量確認と公開検証を省略しない。

すべての候補で、整理開始前のwriter停止、backup lock、最新の全PCキュー、current、ファイル内容、最終活動日時をdry-runと適用直前に再検証する。適用中にcurrentや候補集合が変わった場合は削除前に停止する。容量不足でもcurrentと未送信キューの保護は緩和しない。

## 前提

PCの定期送信を無効化し、手動送信・再送・バックアップも終了させる。Androidの独立backup workerが終了していることを確認したうえで、`stop_server.sh`でサーバーを停止する。候補調査が終わるまで書き込みを再開しない。`--writers-stopped`はこの状態の申告であり、自動停止や排他ロックではない。

PCで停止後の最新キュー一覧を取得する（PowerShellのリダイレクトによるUTF-16を避ける）。

```powershell
$queue = python -m tools.collector --config "$env:LOCALAPPDATA/CodexMobileDashboard/config/collector.ini" queue-status
if ($LASTEXITCODE -ne 0) { throw 'queue-status failed' }
[IO.File]::WriteAllText("$PWD/tmp/cleanup-queue-status.json", ($queue -join "`n"), (New-Object Text.UTF8Encoding($false)))
```

このJSONと`tools/snapshot_cleanup_inventory.py`をAndroidの`$HOME/CodexMobileDashboard/app/tools`へ転送する。実設定・Tokenの転送は不要。複数PCから送信する場合は全送信元のキューを同形式で集約するまで実施しない。

Termuxで実行する。指定ディレクトリが存在し、スクリプトとキューJSONを配置済みであることを確認する。

開始時と5秒ごとに`[running 経過秒数] 処理段階`を標準エラーへ表示する。通常の`>`リダイレクトでも画面に表示され、結果JSONには混入しない。成功時は`[completed]`、失敗時は`[failed]`を表示する。同じ段階の表示が続く場合は大きな処理やI/O待ちの可能性があり、表示の継続だけで処理が前進しているとは断定できない。既に実行中の旧版には反映されないため、必要ならCtrl+Cで旧版を中断し、更新版で再実行する。中断した結果JSONは使用しない。

```sh
python "$HOME/CodexMobileDashboard/app/tools/snapshot_cleanup_inventory.py" \
  --data "$HOME/CodexMobileDashboard/data" \
  --queue-status "$HOME/CodexMobileDashboard/app/tools/cleanup-queue-status.json" \
  --writers-stopped > "$HOME/CodexMobileDashboard/app/tools/cleanup-dry-run.json"
echo "dry_run_exit=$?"
python - <<'PY'
import json
from pathlib import Path
r = json.loads((Path.home() / 'CodexMobileDashboard/app/tools/cleanup-dry-run.json').read_text())
print({k: r.get(k) for k in ('error', 'candidate_count', 'logical_bytes')})
print('protected:', len(r.get('protected', [])))
print('deferred:', len(r.get('deferred', [])))
PY
```

終了コード0のJSONをPCへ回収して確認する。`candidates`は候補ごとのパス・ファイル件数・論理バイト数、`candidate_count`は候補ディレクトリ総数、`logical_bytes`は合計。実ディスク解放量とは異なる。内容照合で数十GBを読む可能性があり、処理に時間がかかる。

## 保護条件と制限

- 全workspaceのcurrent参照先の存在を確認する。current.json自体を候補にせず、参照先はpublic・stagingとも保護する。
- PC未送信キューのworkspace/Snapshot組を両側とも保護する。
- commit受付記録のないSnapshotは保留する。stagingは対応publicの存在と全ファイルのSHA-256一致が必須。両側が存在して不一致なら両側とも保留する。
- 受付記録・未知のworkspace・受信途中や一時ディレクトリは候補にしない。不正なcurrentや受付記録、リンク等の異常では安全側に停止する。
- 処理終了時にもcurrentが変化していないことを確認する。候補一覧は削除の許可リストではない。削除を実装する次タスクで、書き込み停止・キュー・current・内容を再検証する。
- 今回PCで取得した一覧とローカルテストだけではAndroidの実機確認は完了しない。実機の終了コードとJSONを確認してからTASKS.mdを完了にする。
