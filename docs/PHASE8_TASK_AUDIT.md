# Phase 8タスク監査

2026年9月27日、Phase 8のタスクを実装、文書、実機記録、現在の運用状態と照合した。完了済み機能の再実施を避けるため、recoveryバックアップの「機能実装」と「実機受入」、Snapshotの「緊急整理」と「継続的な自動整理」を分離した。

## 完了済み判定

- 初回セットアップ、PC・Androidの起動、通常運用、障害切り分けの各手順は既存文書で完成している。
- バックアップ方式の仕様とrecovery機能のPhase A～Fは完了している。PC・Android ZIP、SSH統合、共通Backup ID、非表示週次ランチャー、検証付き一時復元を再実装しない。
- Android単体recoveryは実機でZIP検証、サーバー再起動、HTTPS healthまで確認済みである。
- PC送信Tokenは2026年9月28日に`secrets/sender.token`へ移行した。新配置で実HTTPS認証を通過し、旧`certificates`配置を削除した。PCバックアップは実collector設定から新配置を1件だけ選択する。
- 2026年9月の容量不足対応では、currentと未送信データを保護して確認済みSnapshotを整理し、PCキュー再送、定期HTTPS送信、スマートフォン表示まで復旧した。この緊急整理を自動保持機能の未実装と混同しない。
- ログ保持、容量不足時の停止・再送、Token更新手順、データ互換性、バージョン規則は既存文書で決定済みである。

## 未完了判定と順序

| 順序 | タスク | 判定根拠 |
| --- | --- | --- |
| 1 | recovery実機受入 | Android単体と実HTTPS再送は完了。手動両端complete/restored、SSH切断後の同一ID照会、失敗時復帰、別環境での両端復旧、週次本番登録が残る。週次タスクは同日時点で未登録。 |
| 2 | 古いJSONの保持・削除方針 | 緊急の一回整理は完了したが、publicの世代・期間、staging、`.deliveries`、`.commits`の継続保持条件は未決定。fullの対象と容量見積りに先立って確定する。 |
| 3 | fullバックアップ | 現在のCLIは予約値として拒否する。保持方針確定後に明示操作専用で実装し、自動整理より先に最初の実機fullを取得・検証する。 |
| 4 | 古いJSONの自動整理 | full成功後に実装・有効化する。current、受信途中、PC未送信、再送受付、バックアップとの排他とdry-runを必須とする。 |
| 5 | CHANGELOG更新 | Termux wake lock、Phase 7追加テスト、容量復旧とSnapshot整理、大容量message page修正、実機バックアップ受入結果が`Unreleased`へ未反映である。残作業の完了時にまとめて整合させる。 |

## recovery実機受入の重複除外

次の確認は完了済みなので再実施対象にしない。

- Android単体recovery ZIPの作成・検証とサーバー復旧。
- 容量復旧後の未送信キュー再送、HTTPS commit、定期送信、スマートフォン表示。

残る実機受入は、[Android worker](BACKUP_ANDROID.md)、[両端統合](BACKUP_PAIR.md)、[週次実行](BACKUP_SCHEDULE.md)、[復元手順](BACKUP_RESTORE.md)に記載した未実施ケースだけを対象とする。

## fullと自動整理の依存関係

保持方針の決定はfullより先に行う。実際の自動削除は、recovery実機受入、full実装、最初の両端full ZIP検証、一時展開が成功するまで有効にしない。これにより、削除後に初めてfullの不足へ気付く順序を避ける。
