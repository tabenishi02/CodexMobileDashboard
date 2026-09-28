# バックアップ設計（Phase A）

状態：recoveryはPC・Android・両端統合・週次実行・検証付き一時復元を実装し、実機受入まで完了した。手順は[PC](BACKUP_PC.md)、[Android](BACKUP_ANDROID.md)、[両端統合](BACKUP_PAIR.md)、[週次実行](BACKUP_SCHEDULE.md)、[復元](BACKUP_RESTORE.md)を参照する。fullは明示操作、両端統合、容量事前確認、排他、Manifest・ZIP検証、検証付き一時復元を実装し、最初の両端実機バックアップと検証付き一時展開まで完了した。

## 方針

Windowsを起点に同じBackup IDでPC・AndroidそれぞれのローカルZIPを作る。通常はrecovery、fullは明示実行専用。外部コピー・暗号化・世代自動削除は行わない。ZIPとManifestは秘密情報を含む保護対象。対象ユーザーのprivate領域に保存し、Androidはumask 077とする。

## 現行実装の確認結果

- `tools/collector.py`の`_load_settings`・`_runtime_settings`が設定を解決する。追加機能も同じパス解決とhistory_fileのfallbackを使い、別の既定値を作らない。
- `CollectorRuntimeSettings`にはstate・history・output・queue・inference ledgerが含まれる。workspace_registryは設定例に存在するが、このruntime引数にはない。存在時に保全し、未生成は未生成として記録する。
- queueは本文、manifest、順序、Delivery ID、commit IDを保持し、commit成功後だけ削除する。sequence.jsonも保存対象。
- PC生成JSONは`json_writer.save_json_snapshot`のoutput_dir配下。全世代のAndroid公開履歴と同一とは仮定しない。
- Windows runnerは`Local\CodexMobileDashboard-Collector`を使用し、収集・再送を直列化する。取得できない定期runnerはスキップする。直接CLIによる手動collect/backfill/retryはmutexを経由しないため、バックアップと同時実行しない。
- 現在のcollectorタスクはInteractive、PT1M、IgnoreNew、StartWhenAvailable=True。バックアップも同一ユーザー・同一Windowsセッションで動かす。Local mutexのため別セッションからの実行は排他保証外。
- `server/start_server.sh`はPIDへ自身のPIDを保存しexec pythonする。`stop_server.sh`はPID・cmdlineを照合しTERM後20秒待つ。バックアップのために強制終了やwake unlockを追加しない。
- serverはpublic/workspace/current.jsonが参照するsnapshots/idを公開する。staging/.deliveriesとstaging/.commitsは受付履歴である。
- 同一内容を再送した場合、受付履歴がなくても保存・commitできる。既存公開Snapshotがある場合は内容一致を検証する。再送対象本文のないcommit単独再実行は成立しないため、キュー本文一式の復元を必須とする。

## 実際に保存する対象

設定値が正であり、表の既定パスへ固定しない。解決した絶対パスとアーカイブ内の論理名の対応をZIP内Manifestへ保存する。ログへ絶対パス・設定本文・Tokenは出さない。

| PC論理対象 | 決定元 | recovery |
| --- | --- | --- |
| collector設定 | 実行時指定のcollector.ini | 必須 |
| backup設定 | バックアップ実行用の外部設定 | 必須 |
| Token | sender.token_file | 必須。PC正規配置は`%LOCALAPPDATA%/CodexMobileDashboard/secrets/sender.token`。実collector設定から選択 |
| CA公開証明書 | sender.ca_file | 必須 |
| TLS発行資材 | バックアップ設定の明示的なtls_sources | CA鍵、サーバー鍵、CRT、CSR、serial、拡張設定。存在・不要を明示 |
| collector状態 | storage.state_file | 存在時必須 |
| collector履歴 | storage.history_file。未指定時は既存runtimeと同じqueue親/state/collector-history.json | 存在時必須 |
| 推論台帳 | storage.inference_ledger_file | 存在時必須 |
| workspace登録簿 | storage.workspace_registry | 存在時保存、未生成は記録 |
| 未送信キュー | storage.queue_dir | sequence.json、各項目manifestとfiles全体。事前に既存queueローダーで検証 |
| 生成JSON | storage.output_dir | 保持されている復旧用JSON一式 |
| 定期実行定義 | collector/retryタスクの読み取り専用取得 | 定義・登録状態を記録。復元時は現行登録スクリプトで再登録 |
| ログ | logging.directory | include_logs指定時のみ任意保存 |

PCのfull対象はrecoveryと同じである。PC側にはAndroidの全公開世代・staging・受付履歴の複製を作らず、同じBackup IDのPC運用状態とAndroid fullを組み合わせる。fullでPCのCodex原本、ユーザーGitリポジトリ、既存バックアップZIPを追加保存しない。

初回利用で状態ファイルが存在しない場合はabsentとして明示する。存在するファイルの読取失敗・破損は黙って省略せず失敗。TLS発行資材はCA公開証明書の親を無条件に再帰保存しない。バックアップ設定で専用ディレクトリまたは個別ファイルを明示する。

| Android論理対象 | 決定元 | recovery | full |
| --- | --- | --- | --- |
| server設定 | workerへ渡すserver.ini | 保存 | 保存 |
| Token・サーバー証明書・鍵 | load_server_settingsのtoken_file/certificate_file/private_key_file | 保存 | 保存 |
| Termux起動設定 | ~/.termux/boot内の通常ファイルと明示した追加設定 | 保存 | 保存 |
| current | server public_directoryの各workspace/current.json | 保存 | 保存 |
| 公開Snapshot | currentが参照するsnapshots/id | 現在の1世代のみ | 全公開世代 |
| staging・受付履歴 | server staging_directory | 除外 | 保存 |
| ログ | server log_directory | 任意 | 任意 |

currentのJSON破損、参照Snapshot欠落、安全でないworkspace/Snapshot IDは失敗。public内にworkspaceがない初期環境は空として記録する。復旧時に同じGit commitのserver/clientを配置するため、コード全体はZIPに含めずcommitを記録する。未commitのコード変更がある場合はその状態を記録し、完全再現可能とは表示しない。

共通除外：backup保存先自身、PID・lock、.partial・.tmp・.pending-*等の未完成データ。ユーザーGitリポジトリ、Codex原本sessions/archived_sessionsは対象外。symlink/junctionによる対象外への再帰は追跡せず失敗または明示除外とし、必要ファイルを除外した場合は成功にしない。

## recoveryで省略する根拠と復元限界

PC mutex取得後はcollector/retryから送信しない。既存の未送信キューにはSnapshot全本文とIDがあるため、Androidのstaging・receiptが消えてもfile POSTから再送できる。queueのない送信済み過去Snapshotは再送可能とは限らないが、現在公開中のものをAndroid ZIPへ保存するため通常復旧に不要。recoveryは過去全世代の再現を保証しない。

PCのみを古い時点へ戻すと、古いキュー再送でAndroidのcurrentが一時的に巻き戻る可能性がある。両端の同一ID復元を推奨し、片側復元では再開前にキューと公開Snapshotを照合する。原本が別途復元されていない場合、再収集や将来更新は保証できない。

## recovery・fullの復元範囲と期間

| モード | PC | Android | 復元できる状態 | 復元できない状態 |
| --- | --- | --- | --- | --- |
| recovery | 設定、秘密ファイル、CA/TLS資材、state、history、推論台帳、registry、未送信キュー、生成JSON、タスク定義、任意ログ | 設定、Token・証明書・鍵、boot、各workspaceのcurrentと参照中Snapshot、任意ログ | Backup ID作成時点のcollector状態、推論再利用状態、pending本文、現在表示中のSnapshotを使う通常運用 | 過去のpublic世代、未完了staging、受付履歴、削除済みSnapshot |
| full | recoveryと同じ | recovery対象に加えて、バックアップ開始時に存在する全public世代、全staging、`.deliveries`、`.commits`、任意ログ | Backup ID作成時点でAndroidに保持されていた公開履歴、未完了受信データ、冪等受付状態を含む手動チェックポイント | full作成前に既に削除された履歴、別保管のCodex原本・ユーザーGitリポジトリ、記録したGit commitに含まれない変更 |

両モードともPC mutexを保持し、Android serverを停止して対象を固定する。同じBackup IDの両ZIPと`pair_state=complete`を一組として扱う。作成時刻は両端で完全に同一ではないが、collector・再送・server書込みを止めるため、同じ停止区間の論理状態を復元点とする。

recovery ZIPが提供する復元点は各ZIPの`created_at`である。週次タスクが毎回成功している場合、障害時に選べる最新の定期復元点は最大で約7日前になる。ZIPを自動削除しないため保管期間自体に固定期限はないが、選択した時点より後のcollector状態や表示更新は復元されない。Androidではその時点のcurrentだけを保存するため、同じZIPからさらに古い表示世代へ戻ることはできない。

full ZIPが提供する履歴範囲は、その`created_at`時点で実際にpublic・stagingに残っていた最古データから最新データまでである。full作成前に整理済みのSnapshotは復元できない。自動整理を初めて有効にする前に両端fullを作成し、SHA-256・内部Manifest・検証付き一時展開を確認する。そのfullは、次のfullが同じ検証を完了するまで、整理前チェックポイントとして保持する。

fullからstagingと受付履歴を復元しても、対応するPCキューがなければ未完了Snapshotのcommit完遂を保証しない。復元後は[受付履歴の保持方針](SNAPSHOT_CLEANUP.md#deliveriescommitsの保持方針)に従い、PCキュー、public、staging、receiptの対応を確認してから送信を再開する。

fullは`tools.backup`、`tools.backup_pair`、Android worker、`tools.backup_restore`で`--mode full`を明示した場合だけ有効になる。PCはrecoveryと同一対象、Androidは全public・全staging（`.deliveries`・`.commits`を含む）を保存する。modeは要求、両端結果、Manifest、一時展開で一致を検証する。引数省略時はrecoveryであり、週次ランチャーもmodeを渡さないためrecoveryからfullを暗黙起動しない。

## 必要空き容量

PCとAndroidはそれぞれ自端末の保存先ファイルシステムで独立に容量を判定する。片側の空き容量を他方へ充当できない。現在のrecovery事前確認は次の値以上の空きを要求し、fullも同じ式を使用する。

```text
必要空き容量 = 1.02 × S + 2,048 × N + 1 MiB + R
```

- `S`：その端末でZIPへ入れる通常ファイルの非圧縮合計バイト数。
- `N`：ZIPへ入れるファイル数。
- `R`：バックアップ完了後にも残す予約容量。`minimum_free_bytes`で指定し、既定は1 GiB。
- 判定対象の空き容量には、既存ZIP、失敗したpartial、同じファイルシステム上の他データが消費している容量も反映される。

圧縮後のZIPサイズは内容に依存するため、容量判定には使用しない。元データを残したままpartial ZIPを作るので、fullではAndroidの全public・staging・受付履歴が`S`へ加わり、recoveryより大幅に多い空きが必要になる。Android workerは停止前のpreflightと停止後の再選択時に容量を確認する。容量不足時は既存の正常ZIPと元データを削除せず、`capacity_insufficient`で中止する。

最初のfullが容量不足になる場合、fullで保護する予定のSnapshotを先に削除して帳尻を合わせない。不要な別データを整理するか、十分な空きがある別の保存先ファイルシステムを設定する。自動整理開始後も、次のfullに必要な容量を確保できる範囲でpublic・stagingの保持量と既存ZIP数を監視する。

検証付き一時展開では、Manifest記載の非圧縮合計`U`とファイル数`N`に対し、展開先へ少なくとも`U + 2,048 × N + 1 MiB`の空きを用意する。運用再開後の余裕も必要な場合はさらに`R`を加える。同じファイルシステムへZIPを新たにコピーしてから展開する場合はZIPサイズ`Z`も加え、`Z + U + 2,048 × N + 1 MiB + R`を計画値とする。旧環境を残して新規ディレクトリへ展開するため、旧データを削除して空きを作る前に検証を完了する。

## 保存先・設定

新規既定案はPC `%LOCALAPPDATA%/CodexMobileDashboard/backups`、Android `$HOME/CodexMobileDashboard/backups`。どちらも外部backup設定で上書き可能。保存先が設定された対象ディレクトリと重なる場合も、resolve後の保存先ツリーを必ず除外する。保存先が必須ファイルを内包してしまう設定は拒否する。

外部backup設定にはcollector_config、output_dir、tls_sources、ssh_host（SSH config alias）、remote_repository、remote_server_config、remote_output_dir、mutex_wait_seconds、remote_wait_seconds、minimum_free_bytesを持つ。秘密値は設定へ直書きしない。SSHはBatchModeとStrictHostKeyCheckingを使用し、鍵・known_hostsは事前準備。設定例に実アカウントを書かない。

## 処理順序と復旧責任

1. IDを発行し開始ログを記録。重複バックアップを防止する。
2. collector mutexを上限付きで待機。取得失敗は強制終了せず中止。AbandonedMutexExceptionは取得成功として既存runner同様に扱う。
3. mutex内でPC対象・queue検証、容量計算、Android SSH到達とremote preflightを行う。容量は非圧縮サイズ＋ZIP管理領域＋予約容量を見積もる。事前確認失敗時はserverを停止しない。
4. 共通IDのAndroid workerをSSHから独立したセッションで起動し、同じIDを再要求しても再起動しない。workerは別のOSロックで並行実行を拒否する。
5. workerが開始時の稼働状態を記録し、PID/cmdline/configを確認してserverを正常停止。PC保存とAndroid保存は並列実行できる。
6. workerはtry/finallyで、元々稼働中なら成功・失敗にかかわらず再起動を試みる。既存start/stopを使用し、起動はバックグラウンドでwaitせずHTTPS healthで確認する。元々停止なら起動しない。
7. PCは再接続して同じIDの状態を取得。SSH起動応答が失われても同じIDを照会し、別IDで重複開始しない。
8. 両ZIPの検証成功でpair_state=complete。再起動は独立のrecovery_stateに記録。全体の終了コード0はcompleteかつ運用復帰成功の場合だけ。
9. PCはfinallyでmutexを解放。Scheduled TaskのDisable/Enableは使用しない。

workerは標準入出力をSSHから切り離し、start_new_session等を使用する。workerのSIGTERM/SIGHUPは復旧処理へ接続する。SIGKILL、Android OSによるTermux全体の終了、端末電源断まではtry/finallyで保証できない。この場合は永続状態を次回検出し、盲目的な二重起動を避けて手動復旧を促す。

PCがremote待機上限に達した場合はunknown/incompleteとし、Androidを通信断だけで再起動・強制停止しない。workerは独立継続する。mutex解放後のcollectorはserver停止中なら既存キュー保持で復帰を待つ。通常経路ではworker終了・health確認までmutexを保持する。

## ID・Manifest・ZIP

IDはUTC日時と乱数（例：20260910T063000Z-a31f82c4）。ファイル名に安全な文字のみを許可する。各端末の名前はCodexMobileDashboard-PC-ID.zip / CodexMobileDashboard-Android-ID.zip。同じIDの既存ZIPを上書きしない。

ZIP内部Manifestはschema_version、backup_id、created_at、mode、side、Git commit、dirty状態、workspace一覧、queue件数/ID、targets、absent/optional/excluded、files（論理パス・復元先対応・サイズ・SHA-256・POSIX mode）を持つ。Token本文はManifestへ入れない。

ZIP内に他端末の最終結果を無理に埋め込まない。ZIP検証後に各端末のresult.json、PCのpair.jsonをatomic更新する。pair.jsonにはpc_result/android_result、ZIP SHA-256、restart_result、health_result、pair_state、recovery_state、errors、開始終了日時を保存する。再起動失敗でもZIP自体の成功を保持するが、利用者向け全体成功にはしない。片側しかない場合は絶対にcompleteとしない。

作成は.zip.partial → close → 再open → testzip → 必須項目・Manifest・全ファイルsize/hash照合 → ZIP全体SHA-256 → 同一ディレクトリ内atomic rename。検証失敗品は正式名にしない。既存正常ZIPの削除は禁止。失敗したpartialは失敗品として記録し、自動世代整理は行わない。

エラーは安定した分類コードのみ（ssh_unavailable、capacity_insufficient、mutex_timeout、zip_invalid、remote_unknown、restart_failed等）。例外本文、SSH stderr、設定本文、Token、鍵をログへ転記しない。ログにはID、mode、mutex取得、接続、停止、各ZIP生成/検証、再起動、complete/incomplete、通常運用復帰結果を記録する。

## 復元仕様

復元は別の明示操作とする。ZIPハッシュ、内部Manifest、side/ID、全ファイル、パス安全性を検証し、一時ディレクトリへ展開してから停止中の対象へ配置する。絶対パス・..・重複名・symlink・展開先外へ出るエントリは拒否。Manifestの復元先へ無確認で書き込まず、現在のパスマッピングを確認する。

- PCのみ：収集・再送を停止し、config/Token/TLS/state/history/ledger/registry/queue/outputを同じPC ZIPから復元。原本とGitリポジトリは別バックアップから必要に応じて復元。古いqueueと稼働中Androidの公開状況を照合後、手動再送、collect、定期運用の順に確認する。
- Androidのみ：Termux・同じcommitを新規配置し、server停止中にAndroid ZIPの設定・鍵・boot・current・Snapshotを配置。recoveryではstagingを空の新規ディレクトリにし、古いreceiptだけを混在させない。POSIX権限を戻して起動、HTTPS health・画面を確認し、PCの未送信キューを本文から再送する。
- 両端：pair.jsonが示す同じIDの検証済みZIPを双方へ復元。Androidを先に正常起動し、PCキュー再送→収集→定期運用とする。

PID/lockは復元しない。旧環境のディレクトリをいきなり削除・上書きせず、退避または新規ディレクトリを使う。ZIP単体から元の秘密ファイル権限を復元する。秘密鍵紛失・漏えい時の再発行はTLS_CERTIFICATES.mdに従う。

## 段階実装と検証

| Phase | 内容 | 完了条件 |
| --- | --- | --- |
| A | 本書・現行実装調査 | 対象と除外、復元限界、失敗状態を明文化 |
| B | PC ZIP・Manifest・verify・mutex wrapper | 一時設定/データで往復復元、破損、容量、競合、秘密ログ除外 |
| C | Android独立worker | current選択、stop失敗、保存失敗、restart失敗、元々停止、通信断のテスト |
| D | PCオーケストレーター | 同一ID、起動応答喪失、片側失敗、timeout、complete判定 |
| E | 週次タスク | 非表示起動、Interactive、IgnoreNew、StartWhenAvailable、手動実行。collector登録に非干渉 |
| F | 復元・運用手順 | 一時ディレクトリで両端復元、実機手動試験手順、README・CHANGELOG整合 |

各Phaseで関連テスト・diff確認・日本語コミットを行う。fullは初期段階では予約値として拒否し、recoveryへ黙って置き換えない。full実装は後続タスクとする。

実機受入：手動1回で停止・ZIP・health・定期復帰を確認後、SSH切断中のworker継続、片側失敗後の復帰、専用一時週次タスクの登録/実行/解除を確認する。本番の週次登録は手動復元検証の成功後に行う。
