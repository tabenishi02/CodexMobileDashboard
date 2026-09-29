# Snapshot整理のdry-run

`tools/snapshot_cleanup_inventory.py`は読み取り専用で、削除機能はない。削除承認用の確認では、受信・公開・recovery/fullバックアップと排他する`tools/snapshot_cleanup_apply.py --dry-run`を使用する。

## 恒常運用の保持方針

この方針は`tools/snapshot_retention.py`に実装済みである。`snapshot_cleanup_inventory.py`と`snapshot_cleanup_apply.py`は容量障害から復旧するための確認付き緊急整理として残す。2026-09-29に初回実機dry-run・apply、継続観測、定期タスクの受入を完了しており、通常運用では[Snapshot保持整理](SNAPSHOT_RETENTION.md)に従う。

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

公開済みstagingはpublicの保持世代数とは独立して整理できるが、受付履歴と本文を以下の方針で整合させる。v0.1.0のretention処理は関連receiptを同じ整理処理で先に失効させ、安全条件を満たすstaging/publicだけを削除する。

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

すべての候補で、共通lock、最新の全PCキュー、current、ファイル内容、最終活動日時をdry-runと適用直前に再検証する。適用では候補集合と合計に加え、各候補の相対パス・サイズ・SHA-256から作る`tree_sha256`を削除直前に再計算する。current、候補集合、または内容指紋が変わった場合はその候補を削除せず停止する。容量不足でもcurrentと未送信キューの保護は緩和しない。

## dry-runと適用

削除承認に使用するdry-runと適用コマンドは[Snapshot整理の適用](SNAPSHOT_CLEANUP_APPLY.md)に従う。`snapshot_cleanup_apply.py`はAndroidサーバーとrecovery/fullバックアップが共有するlockを排他取得し、受信・公開・バックアップと同時更新しない。dry-runは削除せず、applyは承認済み候補との一致と各候補の`tree_sha256`を削除直前に再確認する。

`tools/snapshot_cleanup_inventory.py`はlockを取得しない読み取り専用の調査ツールとして残す。その出力だけを自動削除の承認に使用せず、適用前には必ず共通lock下の`--dry-run`を実行する。

## 保護条件と制限

- 全workspaceのcurrent参照先の存在を確認する。current.json自体を候補にせず、参照先はpublic・stagingとも保護する。
- PC未送信キューのworkspace/Snapshot組を両側とも保護する。
- commit受付記録のないSnapshotは保留する。stagingは対応publicの存在と全ファイルのSHA-256一致が必須。両側が存在して不一致なら両側とも保留する。
- 受付記録・未知のworkspace・受信途中や一時ディレクトリは候補にしない。不正なcurrentや受付記録、リンク等の異常では安全側に停止する。
- dry-runとapplyで候補集合、件数、論理容量、内容指紋が変化した場合は削除しない。適用中も各候補の内容指紋とcurrentを削除直前に確認する。
- 複数PCから送信する場合は、全送信元の最新キューを集約できるまで適用しない。
- 恒常保持期間とreceipt同時整理は実装・自動テスト・実機受入済みであり、通常運用では定期保持整理を使用する。
- 同時更新と整理失敗時の復旧手順・検証結果は[Snapshot整理の適用](SNAPSHOT_CLEANUP_APPLY.md#失敗時の復旧)を参照する。
