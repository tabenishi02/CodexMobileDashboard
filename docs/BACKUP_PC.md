# PCバックアップ（Phase B）

Phase BでPC単体のrecoveryバックアップを実装した。Androidの停止・保存・再開、SSH、両端結果統合、週次登録はPhase C～Eで追加する。PC結果は成功でも`pair_state=incomplete`、`android_result=not_run`となる。現段階でシステム全体のバックアップ完了とは扱わない。

## 設定

`config/backup.example.ini`を`%LOCALAPPDATA%/CodexMobileDashboard/config/backup.ini`へ新規コピーし、次を編集する。既存設定を上書きしない。

| キー（backupセクション） | 内容 |
| --- | --- |
| collector_config | 実collector.iniのパス |
| output_dir | ZIPと結果・ログの保存先。既定例はLOCALAPPDATA配下のbackups |
| tls_sources | TLS発行資材のファイルまたは専用ディレクトリ。複数行指定。空欄はこのPCに発行資材を持たないという明示判断 |
| mutex_wait_seconds | collector終了の待機上限。既定300秒、0～86400 |
| minimum_free_bytes | ZIP作成後に残す予約容量。既定1 GiB |
| include_logs | 任意の運用ログを保存する場合だけtrue |
| task_names | 復元用に定義を記録するcollector/retryタスク名。独自名なら変更する。存在しないタスクは未登録として記録 |

パスは既存collectorと同じ環境変数展開を使用する。相対パスは実行時の作業ディレクトリが基準となるため、設定には絶対パスを使用する。秘密値を設定へ書かない。保存先は現在のWindowsユーザーだけがアクセスできる領域とし、ZIPとManifestには設定・Token・鍵・ローカルパスが含まれる。暗号化と外部コピーは行わない。

## 手動実行

collectorと同じWindowsユーザー・ログオンセッションで、リポジトリルートから実行する。

```powershell
python -m tools.backup --config "$env:LOCALAPPDATA\CodexMobileDashboard\config\backup.ini"
```

指定可能な`--backup-id`は`20260910T063000Z-a31f82c4`形式。通常は省略して自動発行する。同じIDのZIP・partial・成功結果を上書きしない。`--mode full`は未実装エラーとなり、recoveryへ置き換えない。

PythonからWin32 mutexを取得する。既存PowerShellの`Local\CodexMobileDashboard-Collector`と同じOSオブジェクトなので、Scheduled TaskをDisable/Enableせず排他できる。既存runnerはバックアップ保持中にスキップする。バックアップ側は上限まで正常終了を待ち、タイムアウト時は終了コード2。例外でもmutexを解放する。直接`tools.collector`を呼ぶ手動collect/backfill/retryは排他対象外なので併用しない。

保存は設定解決→対象選択→状態・履歴・推論台帳・queue検証→タスク定義の読み取り→容量確認→`.zip.partial`生成→再open/CRC/必須ファイル/size/SHA-256検証→ZIP全体hash→正式名renameの順。事前に存在しない状態はManifestでabsentとして区別する。既存の破損ファイルは省略せず失敗する。

保存先自身と一時ファイルを除外し、symlink/junction等のリンクは拒否する。Codex原本ディレクトリやGit管理データを誤って対象指定した場合も拒否する。既存正常ZIPの自動削除は行わない。検証失敗時のpartialは失敗品として残る。

## 結果と再検証

- `CodexMobileDashboard-PC-ID.zip`：正式なPC ZIP。
- `ID.PC.result.json`：PC成功、Android未実行、pair未完了、ZIP SHA-256。
- `ID.PC.failure.json`：対象選択後の失敗結果。保存領域自体に書けない場合は標準出力のエラーを確認する。
- `backup.log`：対象確定後のイベントログ。開始・mutex取得・早期失敗は標準出力にもJSONで出力する。例外本文・Token・SSH出力は記録しない。

終了コード0はPC単体の成功、2は失敗。ZIP作成後の結果書込み失敗でも0にはしない。partialや片側ZIPを正常な両端一組と取り違えない。

```powershell
python -m tools.backup --verify '<正式ZIPの絶対パス>'
Get-FileHash -Algorithm SHA256 -LiteralPath '<正式ZIPの絶対パス>'
```

`--verify`は内部整合性を検証する。別途result.jsonのZIP SHA-256とGet-FileHashの結果を照合する。暗号署名による真正性確認ではない。

## 一時復元・本番復元の境界

自動テストでは検証したZIPを新しい一時ディレクトリへ展開し、collector state、推論台帳、キューの既存ローダーで再読込して元データとの一致を確認する。Manifestのtargetsに元パスと論理パス、filesにサイズ・hash・modeがある。

本番パスへの自動復元コマンドはまだ追加していない。[復元手順](BACKUP_RESTORE.md)で停止・パスマッピング・片側復元の整合確認を行う。ZIPをそのまま実パスへ展開しない。PID/lockは復元しない。

## 検証

2026-09-10：以下の関連47テストが成功。実Windows mutex競合・放棄mutex・例外後解放も成功。タスク定義の読み取り専用取得、文書リンク、git diff --checkも確認した。本番バックアップは未実行。

```powershell
python -m unittest tools.tests.test_backup tools.tests.test_pending_snapshot_queue tools.tests.test_collector_state tools.tests.test_collector_history tools.tests.test_inference_ledger
```

Windowsでは専用の一時mutex名で.NETとの競合と解放を検証する。本番collector mutexの取得や本番ZIP生成はテストでは行わない。
