# 現在の引継ぎ状態

## 再開地点

Phase 8とPhase 8.1「ワークスペース選択・閲覧UI整理」は完了済み。最新のタスク状態は[`../TASKS.md`](../TASKS.md)、Phase 8.1の設計は[`PHASE8_1_WORKSPACE_SELECTION_UI.md`](PHASE8_1_WORKSPACE_SELECTION_UI.md)を正とする。

Phase 8.1では、`GET /workspaces`による公開中ワークスペース一覧、`/`の選択画面、プロジェクト名と最終更新日時だけの共通ヘッダー、システム情報内の手動更新、正常取得後の常設成功メッセージ廃止を実装した。既存の`/?workspace_id=<workspace_id>`直接URLは維持する。

2026年9月30日、Python 374件（環境条件による既存2件skip）とクライアント4テストファイルが成功した。

同日、Phase 8.1の`server/server.py`と`client/`3ファイルをAndroidへ配置してサーバーを再起動した。CA検証付きHTTPSでhealth、複数workspace一覧、ETag 304、トップページ、JavaScript、公開中dashboard取得が成功し、PC未送信キュー0件と定期Collector再開を確認した。閲覧スマートフォンでは、一覧選択、共通ヘッダー、システム情報、手動・15秒・画面復帰時更新、主要画面、従来の直接URLを受入確認した。workspace別の前回送信成功日時も表示され、公開中11 workspaceすべてで日時記録済みであることを確認してPhase 8.1を完了した。

再開時は次を確認する。

```powershell
git status --short
git log --oneline -10
```

続けて[`../TASKS.md`](../TASKS.md)のPhase 8.1、[`PHASE8_1_WORKSPACE_SELECTION_UI.md`](PHASE8_1_WORKSPACE_SELECTION_UI.md)、[`SCHEDULED_EXECUTION.md`](SCHEDULED_EXECUTION.md)を読む。実設定、Token、証明書、ログ、生成データは変更・コミットしない。

Phase 8では初回セットアップ、recovery/fullバックアップ、週次非表示実行、検証付き復元、Snapshot保持・自動整理、実機受入まで完了した。完了時の監査経緯は[PHASE8_TASK_AUDIT.md](PHASE8_TASK_AUDIT.md)、現行のバックアップ運用は[BACKUP_DESIGN.md](BACKUP_DESIGN.md)と各バックアップ手順を参照する。

## Phase 5完了状態

- workspace指定付きHTTPSダッシュボード、静的アセット、7画面、手動・15秒更新、画面復帰時更新を実装済み。
- SC-51E（Android 16、Chrome 152）で主要表示、更新、画面遷移、安全表示、一般的なスマートフォン幅を確認済み。
- 2026年9月2日に、CA信頼済み端末によるHTTPS実機受入T01～T12へ合格した。
- アクセシビリティ確認は利用者判断で対応不要とした。
- ブラウザ用のmarkdown-it、DOMPurify、highlight.jsはリポジトリへ同梱し、CDNへ依存しない。

実機確認の根拠は[`PHASE5_ANDROID_BROWSER_CHECK.md`](PHASE5_ANDROID_BROWSER_CHECK.md)、Phase 5開始時の記録は[`PHASE5_HANDOFF.md`](PHASE5_HANDOFF.md)を参照する。

## Phase 6の実施結果

1. 固定IPの指定は完了済み（2026年9月9日、利用者確認）。証明書SANと閲覧URLの整合を再確認する。
2. 停止防止アプリへの指定は完了済み（2026年9月9日、利用者確認）。画面消灯後5分・30分のAndroidサーバーとSSHの接続継続も利用者確認済み（2026年9月10日）。
3. ネットワーク切断後の復旧は確認済み（2026年9月9日、利用者確認・手動再送ログ）。定期実行による自動再送の実機確認とは区別する。
4. 通常`collect-once`の定期起動は登録・実行確認済み。workspace横断のpending優先処理と複数回消化の回帰検証も完了（2026年9月10日、関連60件成功）。共通ロック内の再送→収集と送信前のキュー保存を追加し、失敗・中断時の次回再送も関連テストで確認済み。
5. PC再起動後の定期収集・送信は利用者確認で完了。LAN内アクセス手順は[LAN_ACCESS.md](LAN_ACCESS.md)に文書化済み。

通常収集の`install_collector_task.ps1`と`run_collector.ps1`を追加し、`collect-once --incremental`の定期起動と再送runnerとの共通ロックを実装した。関連テスト52件と登録プレビューは成功した。2026年9月9日、利用者の明示承認後に通常収集タスクを登録した。1分間隔で2回の定期起動とHTTPS送信を確認し、2回目は終了コード0。タスクは有効である。実機のpending残件は0で、pending優先処理は関連テストで確認済み。既存の再送専用スクリプトは`retry-queued --all`を実行する。大量の過去履歴は通常定期収集から暗黙に処理せず、必要時に`backfill-ai --until-complete --max-runs N`を明示実行する。 この分離は2026年9月10日に関連52テストで確認済み。

## 既知の保留事項と解決状況

- PC送信Tokenは2026年9月28日に正規設計の`secrets/sender.token`へ移行済みである。旧`certificates`配置は削除し、PCバックアップも実collector設定から新配置を選択する。
- 自動起動サーバー停止時のSSH切断は、termux-wake-lock導入後に利用者が解決を実機確認した（2026年9月10日）。Termux内部の原因は断定しない。確認結果は[TERMUX_SETUP.md](TERMUX_SETUP.md)を参照する。
- Phase 7のTLS異常系・同時更新・長いファイルパスのテストは2026年9月10日に完了した。範囲と再実行手順は[PHASE7_TEST_RESULTS.md](PHASE7_TEST_RESULTS.md)を参照する。

2026年9月10日：定期実行の登録・解除・状態確認・PC再起動後の復旧手順を整備し、専用一時タスクによる実登録・起動・状態取得・解除の統合テストが成功した。状態確認は`scripts/get_collector_task_status.ps1`を使用する。定期実行統合の親タスクは完了。PC再起動後の定期送信は利用者の実機確認で完了（2026年9月10日）。LAN内アクセス手順は[LAN_ACCESS.md](LAN_ACCESS.md)に文書化済み。Termuxのwake lock導入後の改善も利用者が実機確認し、`TASKS.md`のPhase 6項目はすべて完了した。

2026年9月10日：ターミナル点滅対策として本番タスクをpythonwランチャーへ更新し、Git・Codex CLIにもコンソール抑止を適用した。関連113テスト成功。利用者が修正適用後の正常動作を確認し、確認待ちは解消した。提示された01:36:04・01:38:04（JST）の実行結果はいずれも0で、状態Ready・次回予定あり・実行漏れ0。詳細は`SCHEDULED_EXECUTION.md`を参照する。

## Phase 6残タスク照合（2026年9月10日）

従来のPhase 6チェック項目はLANアクセス手順の追加により完了。画面消灯後5分・30分のAndroidサーバーとSSHの接続継続は、利用者の確認済み報告により完了とした。自動起動サーバー停止時のSSH切断もwake lock導入後の実機確認により解決済みとし、Phase 6全体を完了とする。Phase 7の残テストはその後完了した。Phase 8のToken配置統一も2026年9月28日に完了した。
