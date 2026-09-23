# Snapshot整理のdry-run

`tools/snapshot_cleanup_inventory.py`は読み取り専用で、削除機能はない。

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
