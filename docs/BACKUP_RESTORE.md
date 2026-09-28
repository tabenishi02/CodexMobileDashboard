# バックアップ復元・運用確認（Phase F）

ZIPを検証して新しい一時ディレクトリへ展開する`tools.backup_restore`を追加した。元パスへの自動上書き・削除はしない。PCのみ、Androidのみ、同一IDの両端復元を対象とする。

## 復元前の確認

- 両端のZIP、PCのID.pair.json、ZIP全体SHA-256を保管する。ZIPにはTokenと鍵があるため秘密ファイルとして扱う。通常運用では世代自動削除・外部転送・暗号化を行わない。
- 両端復元にはpair_state=completeと同じBackup IDを使う。recovery_state=failedの場合は作成時のサーバー復旧失敗であり、ZIP検証とは別に原因を確認する。
- PCのみの古いバックアップを復元すると、古いqueueの再送でAndroidのcurrentが一時的に戻る可能性がある。稼働側とキューを照合し、必要なら両端を同じIDへ戻す。queueを消して調整しない。
- 元のGitコミットを両端へ再配置する。Git dirtyが記録されている場合、未commitコードは別途必要。ユーザーGitリポジトリとCodex原本は別バックアップから復元する。本ZIPには含まれない。
- 証明書の期限・SAN・Tokenの対応を確認する。漏えいした鍵・Tokenはそのまま復元せず更新する。

## 検証付き一時展開

PCの例。引数は実際のファイルと、新規の存在しない展開先へ置き換える。

```powershell
python -m tools.backup_restore --side PC --zip '<PC ZIP>' --sha256 '<pair.jsonのpc.zip_sha256>' --pair '<ID.pair.json>' --destination '<新規の一時展開先>'
python -m tools.backup_restore --side Android --zip '<Android ZIP>' --sha256 '<pair.jsonのandroid.zip_sha256>' --pair '<ID.pair.json>' --destination '<別の新規一時展開先>'
```

Android/Termuxでも同じPythonモジュールを使用できる。片側単体バックアップでは--pairを省略できるが、ID.PC.result.jsonまたはAndroid ID/result.jsonのhashを照合する。この場合は両端の整合性を検証したことにはならない。

展開先は既存ディレクトリを指定できない。ZIP hash、内部Manifest、side/ID、全ファイルのsize/hash、安全なパスを検証する。リンク、PID/lock/partial、Windows予約名などを拒否する。展開後もhashを確認し、成功時だけRESTORE_VERIFIEDを作る。失敗した展開先は復元成功として使用せず、新規パスで再検証する。自動削除しない。

Manifestのtargetsは元パスと論理ラベルを示す。payload配下をそのままサーバー公開先へ置かない。POSIXでは記録されたowner権限だけを戻す。WindowsのACLはZIPで復元しないため、現在のユーザーだけがアクセスできる場所で作業する。

## PCのみ破損した場合

1. [初回セットアップ](INITIAL_SETUP.md)に従いPython・Git・SSHと同じ版のアプリを用意する。定期タスクはまだ登録・起動しない。
2. 稼働中のPCを復元する場合は定期collector/retry/backupの新規実行を止め、実行中の処理が自然終了したことを確認する。復元中に直接CLIを実行しない。
3. PC ZIPを上記コマンドで一時展開する。state・history・ledger・queue・dataは一組として戻す。
4. targetsのsourceと現在の設定を照合し、新規の復旧用ディレクトリへ配置する。既存データがあれば保持したまま別パスを使用する。collector.ini、backup.iniのパスを現在の環境に合わせる。Token、CA、TLS発行資材、registryも対応付ける。
5. `python -m tools.collector --config '<復旧したcollector.ini>' queue-status`でキューを読めることを確認する。原本とGitの配置を確認し、台帳の再利用条件となるworkspace・session・turn・入力hashを変えない。
6. Androidの公開状況と照合後、`retry-queued --all`で保持本文を再送する。正常commitとキュー解消を確認してからcollect-onceを実行する。不要なbackfillを開始しない。
7. [定期実行](SCHEDULED_EXECUTION.md)と[週次バックアップ](BACKUP_SCHEDULE.md)を現在のパスで登録し、正常な送信・表示・次回予定を確認する。タスクXMLは参照用であり、古い絶対パスのまま自動importしない。

## Androidのみ破損した場合

1. [Termuxセットアップ](TERMUX_SETUP.md)で環境と同じコミットを配置する。PCのcollector/retry/backupの新規実行を一時停止し、実行中の処理が終わるまで待つ。
2. Androidサーバーを停止した状態でZIPを一時展開する。
3. server_config、token、certificate、private_keyを現在の実設定先へ配置し、server.iniのパスを照合する。設定・鍵は600、ディレクトリは700。bootは内容と実行権を確認して戻す。
4. 各workspaceのcurrentラベル内のcurrent.jsonを`public/<workspace>/current.json`へ、snapshotラベル内の内容を`public/<workspace>/snapshots/<snapshot_id>/`へ配置する。IDはManifestとcurrent.jsonを照合する。過去全世代はrecovery ZIPに含まれない。
5. stagingは空の新規ディレクトリを設定する。古いreceiptだけを混在させない。PC queueには本文・Delivery ID・commit IDがあるためfile POSTから再送できる。PID・lockは復元しない。
6. health検証に必要なCA公開証明書はPCの保存物から配置する。`start_server.sh`で起動し、CA検証ありのHTTPS /health、workspace指定の表示を確認する。
7. PCのqueue-status、retry-queued --all、collect-onceで順に確認してから定期処理を再開する。

## 両端破損した場合

両ZIPを--pair付きで一時展開し、同じIDであることを検証する。先にAndroidを復元してHTTPS確認し、次にPCの状態・台帳・キュー・設定を復元する。PCの再送→新規収集→表示→定期収集→週次バックアップの順に再開する。

## 自動検証結果と実機受入

2026-09-13：一時復元、PC ZIP、両端統合、Android workerの関連49テスト成功。

- PCのみ：collector状態、推論台帳、キュー本文・Delivery IDの一致。
- Androidのみ：current参照の再配置と実HTTPサーバーからの取得、旧Snapshotが復元されないこと。
- 両端：同じpair IDとhashによる復元。
- 異常系：異なるID、不完全pair、異なるside、hash不一致、既存ディレクトリへの上書き拒否。

```powershell
python -m unittest tools.tests.test_backup_restore tools.tests.test_backup tools.tests.test_backup_pair server.tests.test_backup_worker
```

HTTP復元テストは一時ループバック用で、本番はHTTPS必須。新規端末でのcollector再開、実TLS・SSH切断、Termuxプロセス復旧、権限復元、週次本番登録は自動テストの対象外。以下を実機受入として別途確認する。

- 手動の両端バックアップがcomplete/restoredになり、両ZIPを取り出して一時展開できる。
- SSH切断後もAndroid workerが終了・再起動し、PCが同じIDを照会する。
- 停止前の容量不足、PC保存失敗、Android保存失敗で通常運用に復帰する。
- 別の復旧環境で上記3ケースを順に試し、実HTTPS送信・commit・閲覧・推論台帳再利用を確認する。
- 成功後に本番週次登録と再起動後の実行を確認する。

### 2026-09-28 実機受入結果

- 同じBackup IDで両端recoveryを実行し、`pair_state=complete`、`recovery_state=restored`、両ZIPのSHA-256・内部検証・検証付き一時展開を確認した。
- SSH起動セッションを終了した後もAndroid workerが独立して完走し、サーバー再起動後の再接続で同じBackup IDの成功結果を照会できた。
- 容量不足、PC保存失敗、Android保存失敗を個別に発生させ、既存の正常ZIPを維持したままcollectorとAndroidサーバーが通常運用へ復帰することを確認した。
- 検証済み展開物から同一Android上の分離ディレクトリ・別ポートに一時HTTPSサーバーを構成した。復元したPC側CA・Token・キューから実HTTPS再送とcommitを行い、キュー解消、health、HTML、`dashboard.json`、`metadata.json`、currentの一致を確認した。復元した推論台帳のcombined-turnキャッシュも再利用できた。一時環境は確認後に停止・削除した。

この一時復旧試験は本番ディレクトリ・本番ポートから分離しているが、別の物理Android端末への移行試験ではない。週次タスクは日曜03:00で登録し、PC再起動後も有効状態と次回予定を維持した。再起動後の手動実行でも新しい両端ZIPがcomplete/restoredとなり、両ZIPの内部検証と記録SHA-256一致、最終結果0を確認した。collectorの再開、未送信0件、再起動後の実HTTPS受信も確認した。
