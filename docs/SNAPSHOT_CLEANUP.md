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

このJSONと`tools/snapshot_cleanup_inventory.py`をAndroidの`$HOME`へ転送する。実設定・Tokenの転送は不要。複数PCから送信する場合は全送信元のキューを同形式で集約するまで実施しない。

Termuxで実行する。

```sh
python "$HOME/snapshot_cleanup_inventory.py" \
  --data "$HOME/CodexMobileDashboard/data" \
  --queue-status "$HOME/cleanup-queue-status.json" \
  --writers-stopped > "$HOME/cleanup-dry-run.json"
echo "dry_run_exit=$?"
python - <<'PY'
import json
from pathlib import Path
r = json.loads((Path.home() / 'cleanup-dry-run.json').read_text())
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
