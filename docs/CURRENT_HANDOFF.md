# 現在の引継ぎ状態

## 再開地点

Phase 5は完了した。Phase 6以降は別チャットで再開し、LAN内の通常運用を完成させる。詳細な実装状況、残作業、運用上の注意は[`PHASE6_HANDOFF.md`](PHASE6_HANDOFF.md)を正とする。

再開時は次を確認する。

```powershell
git status --short
git log --oneline -10
```

続けて[`../TASKS.md`](../TASKS.md)のPhase 6、[`PHASE6_HANDOFF.md`](PHASE6_HANDOFF.md)、[`SCHEDULED_EXECUTION.md`](SCHEDULED_EXECUTION.md)を読む。実設定、Token、証明書、ログ、生成データは変更・コミットしない。

## Phase 5完了状態

- workspace指定付きHTTPSダッシュボード、静的アセット、7画面、手動・15秒更新、画面復帰時更新を実装済み。
- SC-51E（Android 16、Chrome 152）で主要表示、更新、画面遷移、安全表示、一般的なスマートフォン幅を確認済み。
- 2026年9月2日に、CA信頼済み端末によるHTTPS実機受入T01～T12へ合格した。
- アクセシビリティ確認は利用者判断で対応不要とした。
- ブラウザ用のmarkdown-it、DOMPurify、highlight.jsはリポジトリへ同梱し、CDNへ依存しない。

実機確認の根拠は[`PHASE5_ANDROID_BROWSER_CHECK.md`](PHASE5_ANDROID_BROWSER_CHECK.md)、Phase 5開始時の記録は[`PHASE5_HANDOFF.md`](PHASE5_HANDOFF.md)を参照する。

## Phase 6の最優先残作業

1. 固定IPの指定は完了済み（2026年9月9日、利用者確認）。証明書SANと閲覧URLの整合を再確認する。
2. 停止防止アプリへの指定は完了済み（2026年9月9日、利用者確認）。Termuxのバックグラウンド継続を確認する。
3. ネットワーク切断後の復旧は確認済み（2026年9月9日、利用者確認・手動再送ログ）。定期実行による自動再送の実機確認とは区別する。
4. 通常`collect-once`の定期起動は登録・実行確認済み。workspace横断のpending優先処理と複数回消化の回帰検証も完了（2026年9月10日、関連60件成功）。共通ロック内の再送→収集と送信前のキュー保存を追加し、失敗・中断時の次回再送も関連テストで確認済み。
5. PC再起動後の定期収集・送信を確認し、LAN内アクセス手順を完成させる。

通常収集の`install_collector_task.ps1`と`run_collector.ps1`を追加し、`collect-once --incremental`の定期起動と再送runnerとの共通ロックを実装した。関連テスト52件と登録プレビューは成功した。2026年9月9日、利用者の明示承認後に通常収集タスクを登録した。1分間隔で2回の定期起動とHTTPS送信を確認し、2回目は終了コード0。タスクは有効である。実機のpending残件は0で、pending優先処理は関連テストで確認済み。既存の再送専用スクリプトは`retry-queued --all`を実行する。大量の過去履歴は通常定期収集から暗黙に処理せず、必要時に`backfill-ai --until-complete --max-runs N`を明示実行する。 この分離は2026年9月10日に関連52テストで確認済み。

## 既知の保留事項

- PC送信Tokenの実配置`certificates`と正規設計`secrets`の統一はPhase 8の未完了タスクである。実設定を引継ぎ時に自動変更しない。
- Androidでサーバー再起動時にSSH切断とTermux終了が観測されたが、原因は未確定である。Phase 6の省電力・バックグラウンド確認で再現条件を切り分ける。
- Phase 7には、誤ったCA・証明書名不一致、同時更新、長いファイルパスのテストが残っている。

2026年9月10日：定期実行の登録・解除・状態確認・PC再起動後の復旧手順を整備し、専用一時タスクによる実登録・起動・状態取得・解除の統合テストが成功した。状態確認は`scripts/get_collector_task_status.ps1`を使用する。定期実行統合の親タスクは完了。実際のPC再起動後の送信確認とLAN内アクセス手順の完成は残作業である。
