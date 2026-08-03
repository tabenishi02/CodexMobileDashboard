# Codexセッションデータ調査結果

## 調査日

2026-08-03

## 保存場所

`CODEX_HOME`環境変数は未設定であり、既定の保存場所が使用されている。

```text
%USERPROFILE%\.codex
```

セッション本体は次のディレクトリに保存される。

```text
%USERPROFILE%\.codex\sessions\YYYY\MM\DD\rollout-*.jsonl
```

アーカイブ済みセッションは次のディレクトリに保存される。

```text
%USERPROFILE%\.codex\archived_sessions
```

## 確認したファイル

```text
%USERPROFILE%\.codex\sessions\2026\08\03\rollout-YYYY-MM-DDThh-mm-ss-SESSION_ID.jsonl
```

調査時点でJSONLとして正常に解析でき、Codexの実行中に追記されていることを確認した。

## レコード構造

各行は独立したJSONオブジェクトで、共通のトップレベルキーは次のとおり。

- `timestamp`
- `type`
- `payload`

確認した主なレコード種別は次のとおり。

- `session_meta`
- `turn_context`
- `response_item`
- `event_msg`
- `world_state`

確認した主なペイロード種別は次のとおり。

- `message`
- `user_message`
- `agent_message`
- `task_started`
- `task_complete`
- `custom_tool_call`
- `custom_tool_call_output`
- `function_call`
- `function_call_output`
- `patch_apply_end`
- `token_count`
- `thread_settings_applied`
- `reasoning`

## ダッシュボードデータとの対応

| 表示データ | 主な取得元 | 注意事項 |
| --- | --- | --- |
| チャット | `message`、`user_message`、`agent_message` | 同じ内容の重複を除外する |
| 実行状況 | `task_started`、`task_complete` | 未完了ターンを実行中として扱える |
| 最近の更新 | 各レコードの`timestamp` | 日時形式を正規化する |
| エラー | ツール出力、関数出力、`patch_apply_end` | 専用のエラーイベントは確認できない |
| 変更ファイル | `patch_apply_end` | 最終状態はGitを正とする |
| セッション情報 | `session_meta`、`turn_context` | `cwd`で対象ワークスペースを判別する |
| 決定事項 | アシスタントメッセージ | 専用イベントがないため抽出処理が必要 |
| 次の作業 | アシスタントメッセージ、`TASKS.md` | `TASKS.md`を優先情報として扱う |

`reasoning.encrypted_content`は解析対象にしない。

## 読み取り方法

- セッションファイルは読み取り専用で扱い、変更しない。
- 最新の`rollout-*.jsonl`だけに依存せず、`session_meta.cwd`または`turn_context.cwd`で対象ワークスペースを判定する。
- 前回読み取り位置をバイトオフセットで保存し、追記分だけを処理する。
- 書き込み途中の最終行が不完全な場合は破棄せず、次回読み取り時に再処理する。
- 未知のレコード種別やフィールドはエラーにせず無視する。
- アーカイブ移動後も必要に応じて`archived_sessions`から追跡できるようにする。
- `--ephemeral`で開始されたセッションは保存されないため収集対象にできない。

## セキュリティ

セッションにはユーザーメッセージ、コード、ファイルパス、コマンド、ツール引数、ツール出力、実行時指示が含まれる可能性がある。

- `auth.json`は読み取らない。
- `config.toml`全体を送信しない。
- `reasoning.encrypted_content`を送信しない。
- APIキー、トークン、パスワード、秘密鍵、環境変数をJSON生成前に除外する。
- Android端末へは表示に必要な情報だけを送信する。

## 必要な環境と権限

- 通常はセッションを作成したWindowsユーザーの読み取り権限だけでよい。
- 収集ツールやタスクスケジューラは、セッションを作成したユーザーと同じアカウントで実行する。
- 管理者権限やCodex設定変更は不要。
- 作業用PCにはPython 3.10.6（64-bit）がインストールされており、標準ライブラリとpipが正常に利用できる。
- 初期実装では既存のPython 3.10.6を使用し、Python 3.12の追加導入は不要。
- VSCodeでは`C:\path\to\CodexMobileDashboard`をワークスペースとして開き直し、新しいセッションの`cwd`を正しいプロジェクトパスにする。

## 現時点で不要なファイル

- `history.jsonl`は存在しないが、セッションJSONLが取得できているためMVPには不要。
- `log`ディレクトリは存在しないが、診断ログ用でありMVPには不要。
- `auth.json`は認証情報を含むため使用禁止。
- SQLite状態ファイルは現時点では使用しない。
