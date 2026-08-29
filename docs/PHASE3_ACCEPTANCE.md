# Phase 3 受入確認

## 確認済み

- `collect-once --no-send`を一時Gitリポジトリ、Codex JSONL、実設定形式のINIで実行し、`processed_workspaces: 1`を確認した。
- `dashboard.json`、`recent.json`、`metadata.json`など表示用JSON一式、読取位置状態、マスク済み履歴が生成されることを確認した。
- `test_collector_command`、`test_collector_history`、`test_https_sender`、`test_pending_snapshot_queue`の24件が成功した。
- HTTPS senderは成功応答、失敗分類、再試行、Delivery ID維持を単体テストで確認した。未送信キューは失敗時保持・成功時削除を単体テストで確認した。

## Phase完了判定

Phase 3のタスク表は全項目を完了とし、PC側collectorは`collect-once`で収集・変換・保存・送信キュー接続までを実行できる。Androidサーバー側の実受信・公開はPhase 4で確認する。
