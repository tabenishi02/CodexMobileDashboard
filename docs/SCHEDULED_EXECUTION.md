# 定期実行

通常の新規収集には`scripts/install_collector_task.ps1`、未送信Snapshotの再送には既存の`scripts/install_pending_queue_retry_task.ps1`を使用する。どちらも既定1分間隔、ログオン中の同一Windowsユーザーで実行する。ログオンしていない状態では動作しない。

## 通常収集の登録・確認・解除

実設定を準備して次を実行する。登録すると、既存設定のHTTPS送信先へ表示用Snapshotを継続送信する。

```powershell
cd C:\path\to\CodexMobileDashboard
.\scripts\install_collector_task.ps1 -Preview
.\scripts\install_collector_task.ps1
.\scripts\get_collector_task_status.ps1 | Format-List
```

既定の設定ファイルは`%LOCALAPPDATA%\CodexMobileDashboard\config\collector.ini`。`-ConfigPath`、`-TaskName`、`-IntervalMinutes`（1～60）で変更できる。同名タスクの登録は更新になる。登録時のPython絶対パスとリポジトリの作業ディレクトリを記録し、`pythonw.exe`から`run_collector_hidden.pyw`を起動し、`CREATE_NO_WINDOW`で既存PowerShell runnerを実行する。初回は登録の約1分後で、以降は指定間隔で繰り返す。

実行内容は次の順序で、共通ロックを保持したまま実行する。再送が失敗した回は新規収集を行わず、その終了コードで終了する。

```text
python -m tools.collector --config <collector.ini> retry-queued --all
python -m tools.collector --config <collector.ini> collect-once --incremental
```

`--incremental`は実設定を書き換えず、この実行だけ通常増分モードを使用する。既存pendingを新規完了turnより先に処理し、設定済みの`max_calls_per_run`（既定3回）を維持する。大量の過去履歴補完は起動しない。モード`off`の設定もこのオプションで上書きするため、AIを停止する場合はタスクを無効化するか、実設定の`max_calls_per_run = 0`を使用する。

収集と定期再送のrunnerは同じWindowsセッション内の名前付きmutexを共有する。実行中の重複起動は処理せず正常終了し、前の実行が異常終了してmutexが放棄された場合は次回実行が取得する。直接`python -m tools.collector`を起動する手動操作はこのロックの対象外なので、定期タスクと同時に実行しない。タスクは実行時間による強制終了を無効化し、収集コマンドの終了コードを返す。

確認は`LastRunTime`、`NextRunTime`、`LastTaskResult`と、設定先の`collector.log`・`converter.log`・`sender.log`を組み合わせる。`LastTaskResult = 0`だけではロックによるスキップと区別できないため、収集ログの`inference_run_metrics`と送信ログの`snapshot_committed`も確認する。失敗時はログを確認する。未送信キューは次回の定期収集の先頭で再送し、再送成功後に新規収集へ進む。これにより保持済みの古いSnapshotを先に送信する。推論pendingは既存の永続状態から再処理する。

一時停止・再開・解除は次のとおり。

```powershell
Disable-ScheduledTask -TaskName 'CodexMobileDashboard-Collector'
Enable-ScheduledTask -TaskName 'CodexMobileDashboard-Collector'
.\scripts\uninstall_collector_task.ps1 -Preview
.\scripts\uninstall_collector_task.ps1
```

無効化・解除は実行中の収集の終了を保証しない。状態が`Running`なら自然終了を待ってから手動操作する。PC再起動後は同じWindowsユーザーでログオンし、上記の状態・ログ・Android側の公開Snapshot更新を確認する。再起動後の実機確認結果は下記を参照する。

2026年9月9日：利用者の継続収集・送信に対する明示承認後、`CodexMobileDashboard-Collector`を登録した。タスクはログオン中1分間隔（`PT1M`）で有効。関連テスト52件は実装時に成功済み。以下を実機で確認した（JST）。

- 19:19:05に定期起動し、複数workspaceのHTTPS送信・Snapshot commitに成功。19:19:47の集計は推論実行1回・成功1回・失敗0・pending残件0。
- 19:20:06に再度定期起動し、HTTPS送信・Snapshot commitに成功。19:20:40の集計は推論実行0回・キャッシュヒット5件・失敗0・pending残件0。
- 2回目の終了後、`LastTaskResult = 0`、状態`Ready`、次回実行予定を確認した。

実機では持ち越しpendingが0だったため、既存pending優先処理の根拠は関連テストとする。PC再起動後の確認、未送信キュー再送タスクの実登録・自動再送確認は今回の完了範囲に含めない。

2026年9月10日：通常増分収集では、実行開始時点でpendingを持つworkspaceを、持たないworkspaceより先に処理するよう修正した。同じ優先区分内では既存の探索順を維持する。各workspace内のpending優先処理と、実行全体の共有推論上限は維持する。

回帰テストでは実データ・CLI・送信を使わず推論処理を代替し、実際の状態ファイルの保存・再読込を通して4回の収集を実行した。既存pending 3件と別workspaceの新規turn 1件、共有枠1回の条件で既存pendingが先に処理され、各回終了時の残件が3→2→1→0となること、重複消化がないことを確認した。関連テスト60件が成功。これは定期起動先の収集処理の回帰検証であり、実機に人工的なpendingを投入した試験ではない。

2026年9月10日：collectorのrunner内で`retry-queued --all`→`collect-once --incremental`を同じロック下で実行するよう変更した。Snapshotは送信開始前にキューへ原子的保存し、commit成功後だけ削除する。送信途中の中断やcommit応答喪失でも同じ本文・Delivery IDを保持し、次回再送する。保存に失敗した場合は通信を開始しない。通常収集タスクだけでもキュー再送を行うため、再送専用タスクの追加登録は必須ではない。

関連77テストが成功（関連76件成功後、保存失敗テストを追加してキュー9件を再検証）。共通ロック中のcollector・再送のスキップ、再送失敗時の収集抑止と次回復帰、送信中断後のキュー再読込・同一Delivery ID再送、commit成功後だけ削除することを確認した。障害試験は一時データと代替senderで実施し、実機ネットワーク切断は行っていない。

## PC再起動後の復旧手順

1. 再起動前に`get_collector_task_status.ps1`で登録済み・有効であることを確認し、直近の実行日時とAndroid側の公開Snapshotを記録する。未送信キュー・collector状態・推論台帳は削除しない。
2. 利用者がPCを再起動し、登録時と同じWindowsユーザーでログオンする。ログオン前には動作しない。AndroidサーバーへのLAN接続も復帰させる。
3. 登録間隔と収集所要時間を待ち、次を実行する。

```powershell
cd C:\path\to\CodexMobileDashboard
.\scripts\get_collector_task_status.ps1 | Format-List
```

4. `Registered=True`、`Enabled=True`、`LastRunTime`がログオン後に更新され、完了時の`LastTaskResult=0`と次回予定があることを確認する。`Running`中は自然終了を待つ。`StartWhenAvailable=True`により実行機会を逃したタスクも実行可能になってから処理される。
5. `collector.log`の集計、`sender.log`の`snapshot_committed`、Android側の公開Snapshot・最終受信日時の更新を確認する。未送信があった場合は先頭で再送され、成功後に収集へ進む。推論pendingの残件はログで確認する。

| 状態 | 確認・復旧操作 |
| --- | --- |
| `Registered=False` | 実設定とPythonの存在を確認し、登録スクリプトを実行する。 |
| `Enabled=False` | 意図的な停止でなければ`Enable-ScheduledTask`で再開する。 |
| `Running`が長い | ログの進捗と推論・通信待ちを確認する。重複して手動起動しない。 |
| 完了時の結果が非0 | ログのエラー分類からLAN・CA・認証・保存容量を確認する。原因を解消し次回実行を待つ。 |
| Python・リポジトリの配置変更 | 設定の場所を確認して同名タスクを再登録し、実行パスを更新する。 |
| 結果0でも画面が更新されない | スキップと実収集をログで区別し、対象workspaceと公開Snapshotを確認する。 |

`get_collector_task_status.ps1 -TaskName <名前>`で別名タスクも確認できる。設定本文、Token、実行引数は表示しない。解除は既存の`uninstall_collector_task.ps1`で行い、キューや台帳は保持する。

## タスク管理の統合テスト

```powershell
$env:CODEX_DASHBOARD_TASK_TEST = '1'
python -m unittest tools.tests.test_scheduled_task_lifecycle
Remove-Item Env:CODEX_DASHBOARD_TASK_TEST
```

専用の一時タスクを実際のWindowsタスクスケジューラへ登録し、プレビューの非変更、対話ログオン・実行機会回復・重複起動抑止の設定、起動成功、状態取得、解除を確認する。テストrunnerは一時マーカーを作成するだけで、収集・推論・通信を行わない。テスト終了時に専用タスクを解除する。本番タスクは変更しない。

2026年9月10日、上記の実機統合テスト1件が成功。PC再起動後の実機確認は、その後の利用者確認により以下のとおり完了した。

## 再送専用タスクの登録

実設定とTokenファイルを準備したうえで、PowerShellから次を実行する。

```powershell
cd C:\path\to\CodexMobileDashboard
.\scripts\install_pending_queue_retry_task.ps1
```

既定のタスク名は`CodexMobileDashboard-PendingQueueRetry`、設定ファイルは`%LOCALAPPDATA%\CodexMobileDashboard\config\collector.ini`である。別名・別設定・間隔を指定する場合は次のとおり。

```powershell
.\scripts\install_pending_queue_retry_task.ps1 `
  -TaskName "CodexMobileDashboard-PendingQueueRetry" `
  -IntervalMinutes 5 `
  -ConfigPath "$env:LOCALAPPDATA\CodexMobileDashboard\config\collector.ini"
```

`-Preview`を付けると、タスクを変更せず登録内容だけを確認できる。登録済みの同名タスクがある場合、実行内容をこの再送タスクへ更新する。

## 実行内容と失敗時

タスクは`scripts/run_pending_queue_retry.ps1`を起動し、次と同じ処理を行う。

```text
python -m tools.collector --config <collector.ini> retry-queued --all
```

- queueが空なら成功して終了する。
- 失敗するとキュー項目は保持され、次回の定期実行で同じDelivery IDを使って再送する。
- 一度に長引く送信がある場合、重複した起動は行わない。
- Token、本文、HTTP応答本文はタスク引数・標準出力・ログに出力しない。

タスクの実行結果は、タスク スケジューラの履歴と`%LOCALAPPDATA%\CodexMobileDashboard\logs`で確認する。通信設定や証明書の恒久的な誤りは自動では直らないため、`queue-status`とログを確認して設定を修正する。

## 解除

次のコマンドで、このタスクだけを解除する。

```powershell
cd C:\path\to\CodexMobileDashboard
.\scripts\uninstall_pending_queue_retry_task.ps1
```

解除前に対象を確認するには`-Preview`を付ける。

## 新規収集の定期実行

通常`collect-once --incremental`の定期起動スクリプトは実装済み。実タスクの登録と定期実行結果の確認は上記のとおり完了した。ターン完了時の即時起動と、作業中30秒・停止中60秒の切替スケジュールは未実装で、今回は固定の分間隔で起動する。

大量の過去履歴を対象にする`backfill-ai`は通常定期収集から暗黙に起動しない。実装済みの`backfill-ai --until-complete --max-runs N`を利用者が明示実行し、1回ごとの共有ハード上限、全体の有限上限、失敗・進捗停止時の自動停止を維持する。これにより通常pendingの自動消化と、使用量が大きくなり得る過去補完を分離する。運用上の見積もりと再開方法は[`BACKFILL_OPERATION.md`](BACKFILL_OPERATION.md)を参照する。

2026年9月10日：通常収集とbackfillの分離を確認し、関連52テストが成功した。設定が`backfill`でも定期runnerの`--incremental`がこの実行だけ増分モードに固定する。上限到達・pending残存時も通常収集は1回で終了し、継続補完処理を呼ばない。`collect-once`へ`--until-complete`または`--max-runs`を渡すと引数エラーで処理開始前に終了する。過去履歴の連続補完は利用者が`backfill-ai --until-complete --max-runs N`を指定した場合だけ行う。

## PC再起動後の実機確認結果（2026年9月10日）

利用者から、PC再起動後の正常動作と閲覧スマートフォン側の正常動作を確認したとの報告を受け、定期送信の復旧確認を合格とした。

- 提示された2回の状態確認で`Registered=True`、`Enabled=True`、`Interval=PT1M`、`MissedRuns=0`。
- `LastRunTime`は01:07:06、01:12:06（JST）で、次回予定はそれぞれ01:08:05、01:13:05。再起動後も定期起動が継続している。
- 両確認時点は`State=Running`、`LastTaskResult=267009`で実行中を示す。これを完了時の終了コード0の証拠とは扱わない。
- 受信側の動作確認は利用者による閲覧スマートフォンの正常結果に基づく。今回の提示内容には個別Snapshot IDやcommitログは含まれない。

## 定期起動時のターミナル表示抑止

2026年9月10日、タスクの起動元を`pythonw.exe`へ変更した。ランチャーはコンソールを作らず既存PowerShell runnerを起動して終了を待ち、終了コードをタスクスケジューラへ返す。起動自体に失敗した場合は2を返す。標準入出力は非表示用に破棄し、収集・変換・送信の既存ファイルログを使用する。GitとCodex CLIの子プロセスにもWindowsの`CREATE_NO_WINDOW`を適用した。

関連113テスト（既存経路110件、ランチャー2件、一時タスク実登録・起動・解除1件）が成功。本番タスクもpythonwへ更新した。画面表示・フォーカス移動は次の手順で確認する。利用者による確認結果は本節末尾に記録する。

1. メモ帳など別アプリで入力しながら、定期実行を2～3回待つ。ターミナルが表示されず、入力先が変わらないことを確認する。
2. `get_collector_task_status.ps1`で起動日時の更新と、完了後の結果0を確認する。Running中の267009は実行中を示す。
3. 閲覧スマートフォンで公開Snapshot・最終受信日時の更新を確認する。
4. 通常の作業turnが完了しAI推論が実行された回も同じ確認を行う。推論の有無は`collector.log`の`inference_run_metrics`で確認する。確認のための大量backfillは不要。

起動前のエラーはコンポーネントログが生成されない場合がある。結果2で新しいログがない場合は、登録されたpythonw・PowerShell・runner・設定ファイルの存在を確認する。

2026年9月10日、利用者から修正の適用と正常動作の確認報告を受け、非表示起動修正の利用者確認を完了とした。提示された状態出力は以下のとおり（JST）。

| 最終実行日時 | 次回予定 | 状態 | 実行結果 | 実行漏れ |
| --- | --- | --- | --- | --- |
| 01:36:04 | 01:37:03 | Ready | 0 | 0 |
| 01:38:04 | 01:39:03 | Ready | 0 | 0 |

両時点とも登録済み・有効で、間隔PT1M、Interactive、StartWhenAvailable=True、MultipleInstances=IgnoreNewを維持している。画面上の改善は利用者の確認報告に基づき、状態出力からは定期実行の正常終了と次回予定を確認した。
