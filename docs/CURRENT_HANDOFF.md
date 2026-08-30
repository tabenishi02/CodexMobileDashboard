# 現在の引継ぎ状態

## 再開時の最優先事項

Codex CLI推論の増分化を継続する。現在の次タスクは「決定事項の保存・復元・入力ハッシュ照合」である。ただし、その前提となる変更要約台帳にも既知不具合があるため、先に修正・テストする。

再開時は次を確認する。

```powershell
git status --short
git log --oneline -15
python -m unittest tools.tests.test_change_summary_generator tools.tests.test_decision_extractor tools.tests.test_next_task_extractor tools.tests.test_collector_command
git diff --check
```

## 実装済み範囲

- Android・Termux HTTPSサーバー、認証付きSnapshot受信、commit、公開JSON GETを実装済み。
- `GET /health?workspace_id=<workspace_id>`で最終受信日時と公開中Snapshot IDを取得できる。
- スマートフォン向け画面、ETag取得、15秒更新、非表示時停止、手動更新、空・通信・不正JSON・古いデータ表示を実装済み。
- チャットのMarkdownサブセット表示、長文折りたたみ、横長コードの横スクロール、ダークテーマ、固定下部ナビゲーションを実装済み。
- 推論モード`off`、`incremental`（既定）、`backfill`の設定値を導入済み。`off`は3つのCLI経路を抑止する。

## 実機確認済み事項

- Android側の古い`server/`を更新後、`GET /health`と`GET /health?workspace_id=workspace-47cfefa930c1ed4d`が`200`を返した。
- PC collectorから複数workspaceのSnapshot commit成功ログを確認した。
- Androidへ更新する対象は`server/`と`client/`の全内容であり、`client/vendor/`も含めて再帰的に配置する。実設定、Token、TLS鍵、公開・stagingデータは上書きしない。
- CA信頼済み閲覧スマートフォンによる最終的な画面表示・操作確認は未完了。

## PC側の実設定

手動試験用collector設定はリポジトリ外の`%LOCALAPPDATA%\CodexMobileDashboard\config\collector.ini`にある。この環境では送信Tokenを`%LOCALAPPDATA%\CodexMobileDashboard\certificates\sender.token`へ配置する判断をした。

一方、リポジトリの一般向け設定例と正規設計文書は`secrets\sender.token`を示している。移行先では実設定を不用意に書き換えず、配置方針を統一するタスクとして扱う。Token本文をGit、文書、ログ、チャットへ記載しない。

## Codex CLI推論の現状と既知問題

- 変更要約・決定事項・次タスクは別々にCodex CLIを起動し得る。
- `incremental`と`backfill`は現在、候補範囲が同じである。新規完了ターン限定、共有呼び出し上限、backfill専用コマンドは未実装。
- 変更要約と決定事項は、入力SHA-256・workspace・session・turnに関連付けたversioned payloadを台帳へ原子的保存する。保存時の`now`引数不一致は修正済みである。
- 台帳専用テストで、全識別子の保存と原子的置換失敗時の旧台帳保持を確認済み。変更要約の再起動後復元は受入未完了。
- 決定事項は永続台帳へ保存・復元する。次タスクの保存・復元は未実装。
- 決定事項は`supersedes`、`superseded_by`、`topic_key`と時系列を保存し、関係を壊さず復元する方針を採用した。
- 旧形式または不完全payloadは成功キャッシュとして復元せず、安全に再推論候補へ戻す。

推論仕様の詳細は[`AI_INFERENCE.md`](AI_INFERENCE.md)、実装タスクは[`../TASKS.md`](../TASKS.md)のPhase 3.1を参照する。

## collector実行時の観測

Codex CLI未導入時は`codex_not_found`警告が出た。CLI導入後は推論処理が実行され、利用量と処理時間が実用上問題となった。手動中断時のスタックは`generate_change_summaries()`から`subprocess.run()`の待機中だった。1呼び出しのタイムアウトは120秒だが、全体の呼び出し回数上限がないため総時間は長くなり得る。

この問題が解決するまで、実データで`incremental`または`backfill`を安易に実行しない。必要なら`off`を使用する。ただし、外部実設定の変更は利用者が内容を確認して行う。
