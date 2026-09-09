# Phase 6引継ぎ

## 目的

本書は、Phase 5完了後にPhase 6以降を別チャットで再開するための正規の引継ぎ資料である。秘密値、実設定の本文、生成データは記載しない。最新のタスク状態は[`../TASKS.md`](../TASKS.md)を正とする。

## 再開時の確認

最初に作業ツリーと直近履歴を確認する。

```powershell
cd C:\path\to\CodexMobileDashboard
git status --short
git log --oneline -10
```

次に以下を読む。

1. [`../TASKS.md`](../TASKS.md)のPhase 6
2. [`CURRENT_HANDOFF.md`](CURRENT_HANDOFF.md)
3. [`SCHEDULED_EXECUTION.md`](SCHEDULED_EXECUTION.md)
4. [`NETWORK.md`](NETWORK.md)
5. [`TERMUX_SETUP.md`](TERMUX_SETUP.md)
6. [`MANUAL_COMMAND.md`](MANUAL_COMMAND.md)
7. [`BACKFILL_OPERATION.md`](BACKFILL_OPERATION.md)

実設定、Token、証明書、ログ、生成データはリポジトリ外にあり、再開確認で上書きまたはコミットしない。live collectorを診断目的だけで実行すると新しいSnapshotを生成・送信し得るため、必要性を確認してから実行する。

## Phase 5完了時の実装

### PC collector

- Codex JSONLとGitメタデータから7種類の表示用JSONをSnapshot単位で生成する。
- `collect-once`は収集・変換後、認証付きHTTPSでSnapshotを送信して明示的にcommitする。
- 送信失敗時は未送信キューへ保持し、同じDelivery IDで再送する。
- 手動収集では`collector.log`、`converter.log`、`sender.log`へ秘密情報を除いた結果を記録する。
- AI推論は既定`incremental`で新規完了turnと永続pendingを処理し、1回のcollector実行における全CLI呼び出しへ共有上限（既定3回）を適用する。
- 推論成功結果は入力SHA-256、workspace、session、turnに関連付けた永続台帳へ1件ずつ原子的保存し、再起動後も同一入力を再利用する。
- 適格な同一turnでは変更要約・決定事項・次タスクを`combined_turn`として1回の構造化推論へ統合し、条件外または失敗時だけ個別経路へfallbackする。
- 過去補完は`backfill-ai`で明示実行でき、`--until-complete --max-runs N`により有限回で継続する。失敗、進捗停止、中断、上限到達時は再開可能な台帳を維持して停止する。

### Androidサーバー

- HTTPS、Bearer認証付きSnapshot POST、明示的commit、公開JSON GET、ETag、workspace指定`/health`を実装済み。
- commit済みSnapshotだけを`current.json`で公開し、複数JSONを同一世代として切り替える。
- `GET /?workspace_id=<workspace_id>`で`client/index.html`を返し、安全な相対パスに限って静的アセットを配信する。
- Android端末再起動後のHTTPSサーバーとSSHの自動起動を確認済み。

### ブラウザクライアント

- 概要、最近、チャット、エラー、決定事項、ファイル、システム情報を実装済み。
- 初期データとworkspace指定`/health`を取得し、手動更新、表示中15秒更新、画面復帰時の即時更新を行う。
- UTC ISO 8601値はJSON内で維持し、表示時だけ`Asia/Tokyo`へ変換する。
- MarkdownはHTMLを無効化してサニタイズし、内部パス・アカウント識別情報は表示用データ生成境界でマスクする。
- 2026年9月2日、SC-51E（Android 16、Chrome 152）でCA信頼済みHTTPS受入T01～T12へ合格した。一般的なAndroidスマートフォン相当の画面幅、主要画面、更新、同一Snapshot表示も確認済み。

実機確認の詳細は[`PHASE5_ANDROID_BROWSER_CHECK.md`](PHASE5_ANDROID_BROWSER_CHECK.md)を参照する。

## Phase 6で確認済みの範囲

- Androidサーバー端末の確認時IPは`192.0.2.121`、HTTPSポートは`8765`である。固定化前の環境値なので再開時に再確認する。
- 作業PCからAndroidサーバー端末へのSnapshot POSTとcommitを確認済み。
- 閲覧スマートフォンからworkspace指定ダッシュボードを開き、JSON更新が画面へ反映されることを確認済み。
- 更新完了後に表示項目が同一Snapshotの内容へ揃うことを実機確認済み。
- Androidサーバー端末再起動後の復旧を確認済み。

## Phase 6の残作業

次の順序を推奨する。

1. 固定IPの指定は完了済み（2026年9月9日、利用者確認）。証明書SANと閲覧URLが一致することを再確認する。
2. Androidの省電力設定による停止防止は、停止防止アプリへの指定により完了済み（2026年9月9日、利用者確認）。画面消灯後5分・30分のサーバーとSSH継続を確認する。
3. サーバー再起動時にTermux自体が終了した観測について再現条件を確認する。原因を決めつけず、再起動スクリプト、Androidの省電力制御、Termuxプロセス状態を分けて確認する。
4. LAN切断・復帰後の復旧は確認済み（2026年9月9日、利用者確認）。添付ログで接続タイムアウト後のSnapshot保持と手動再送・commit成功、キュー解消を確認した。詳細は[`NETWORK.md`](NETWORK.md)を参照する。
5. Windowsタスクスケジューラから通常`collect-once`を定期起動し、新規完了turnと既存pendingを処理する。
6. `collect-once`と`retry-queued`の重複起動を防ぎ、失敗・中断時のデータを次回へ安全に持ち越す。
7. PC再起動後の定期収集・送信を確認し、登録・解除・状態確認を文書化する。
8. 固定後のIP、ポート、証明書確認、workspace指定URLを含むLAN内アクセス手順を完成させる。

## 定期実行とAI補完の境界

現在のWindows定期タスクは`retry-queued --all`による未送信Snapshot再送だけであり、Codex JSONLの新規収集や新しいSnapshot生成は行わない。通常`collect-once`の定期実行統合はPhase 6の未実装項目である。

通常定期収集では新規完了turnと永続pendingを段階的に処理する一方、大量の過去履歴を対象にするbackfillを暗黙起動してはならない。過去補完が必要な場合だけ、利用者が次のように有限上限を指定して実行する。

```powershell
python -m tools.collector --config <collector.ini> backfill-ai --until-complete --max-runs <有限回数>
```

呼び出し数、時間の見積もり、停止条件、再開方法は[`BACKFILL_OPERATION.md`](BACKFILL_OPERATION.md)を参照する。

## Phase 7以降へ残る確認

Phase 5または既存テストで確認済みの項目は`TASKS.md`へ反映した。次は未確認のまま残す。

- 正しいCA、誤ったCA、証明書名不一致の自動テスト
- Snapshot同時更新のテスト
- 長いファイルパスの表示テスト
- Phase 8のPC送信Token配置方針（`certificates`と`secrets`）の統一

## 関連テスト

コード変更時は変更範囲に応じて関連テストを選ぶ。引継ぎ時点の主要確認コマンドは次のとおりである。

```powershell
python -m unittest server.tests.test_server tools.tests.test_https_sender tools.tests.test_json_converter tools.tests.test_json_writer tools.tests.test_collector_runtime tools.tests.test_collector_command
node client/tests/app_test.js
node client/tests/system_status_test.js
node client/tests/styles_test.js
git diff --check
```
