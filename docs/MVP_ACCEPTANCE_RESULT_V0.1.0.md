# v0.1.0 MVP最終受入結果

確認日: 2026-09-30

## 総合判定

**PASS**

Phase 0～8.1で実装・記録した自動テスト、PC・Androidサーバー・閲覧スマートフォンの実機受入、障害復旧、バックアップ運用を `MVP_ACCEPTANCE.md` のMVP-01～MVP-10へ照合した。既存証拠で必要条件を満たしているため、公開準備のために本番環境を再構築する追加実機試験は行わない。

Phase 9「外部アクセス」はMVP対象外であり、v0.1.0ではLAN内HTTPSを正式な利用範囲とする。

## 判定一覧

| 条件 | 判定 | 主な根拠 |
| --- | --- | --- |
| MVP-01 一連動作 | PASS | `PHASE4_ACCEPTANCE.md`のHTTPS POST/commit/GET、`PHASE5_ANDROID_BROWSER_RESULT_2026-09-01.md`のCA信頼済みスマートフォン表示、Phase 8.1のworkspace選択実機受入 |
| MVP-02 セッションデータ完全性 | PASS | Phase 3 collector受入、session/state/deduplication関連自動テスト、再起動・pending再利用の回帰試験 |
| MVP-03 Git変更メタデータ | PASS | `git_change_collector`関連テスト、Phase 5ファイル画面受入、長いパスを含むPhase 7追加テスト |
| MVP-04 送信対象制限 | PASS | Git本文・プロジェクト本体を送信しないデータ設計、JSON converter/collector関連テスト |
| MVP-05 大容量テキスト | PASS | 256KiB超の分割・復元、1MiB HTTP上限、長文表示の関連テストと実機確認 |
| MVP-06 スマートフォン確認 | PASS | Phase 5正式HTTPS受入、Phase 8.1の一覧選択・主要画面・手動/15秒/画面復帰更新の実機受入 |
| MVP-07 障害・再起動復旧 | PASS | Phase 4再送・冪等性、Phase 6のLAN切断/PC・Android再起動、Phase 8のrecovery/fullバックアップと復元受入 |
| MVP-08 秘密情報保護 | PASS | Bearer認証、TLS異常系、secret redactor、パストラバーサル拒否、公開前Git履歴監査 |
| MVP-09 運用手順 | PASS | `INITIAL_SETUP.md`、`LAN_ACCESS.md`、`SCHEDULED_EXECUTION.md`、Termux・バックアップ・復元文書 |
| MVP-10 未解決不具合 | PASS | Phase 0～8.1に未完了タスクなし、公開前全自動テストでFAIL 0、公開を妨げる既知critical/errorなし |

## 公開前自動テスト

2026-09-30に次を実行した。

- `python -m unittest discover`: 375 tests、成功、既存の環境条件による2件skip
- `node client/tests/app_test.js`: 成功
- `node client/tests/styles_test.js`: 成功
- `node client/tests/system_status_test.js`: 成功
- `node client/tests/workspace_selector_test.js`: 成功
- `git diff --check`: 成功

全Pythonテストの初回実行では、定期Collectorが本番mutexを保持している最中にテストが同じmutex名を使用したため2件がFAILした。製品処理の失敗ではなくテスト隔離の不足と判定し、runnerの既定mutex名を変更せず任意のテスト用mutex名を指定可能にして修正した。関連3テストと全Pythonテストの再実行で成功を確認した。

## 最終結論

v0.1.0のMVP条件はすべて満たす。Tailscale・インターネット経由アクセスはPhase 9へ保留し、LAN内HTTPS版を初回公開版とする。
