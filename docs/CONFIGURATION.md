# 設定ファイルと秘密情報の管理仕様

## 採用方式

- 通常設定はUTF-8のINI形式とし、Python標準ライブラリの`configparser`で読み取る。
- PC側は1つの共通INIを使用し、`C:\codex`配下のCodexワークスペースを自動検出する。
- PC側の設定、秘密、状態、未送信キュー、生成JSON、ログは`%LOCALAPPDATA%\CodexMobileDashboard`配下へ用途別に保存する。
- Androidサーバー端末側の実設定、認証トークン、TLS秘密鍵もリポジトリ外へ保存する。
- 開発時の疎通確認だけHTTPを許可し、架空のサンプルデータだけを使用する。実セッションを扱うMVP受入試験と通常運用はHTTPSを必須とする。
- HTTPSはPythonサーバーとプライベートCAで実装する。証明書管理が複雑になった場合だけCaddyを再検討する。

## リポジトリ内のファイル

Gitには秘密情報を含まない設定例だけを登録する。

```text
CodexMobileDashboard/
└─ config/
   ├─ collector.example.ini
   └─ server.example.ini
```

`collector.example.ini`と`server.example.ini`は設定の説明に使用し、実設定として直接編集しない。

## PC側のローカル保存構成

実際のルートは環境変数`LOCALAPPDATA`から取得する。現在のWindows環境では次の場所に相当する。

```text
%USERPROFILE%\AppData\Local\CodexMobileDashboard\
```

構成：

```text
%LOCALAPPDATA%\CodexMobileDashboard\
├─ config\
│  └─ collector.ini
├─ secrets\
│  └─ sender.token
├─ certificates\
│  └─ ca.crt
├─ state\
│  ├─ collector-state.json
│  └─ workspaces.json
├─ data\
│  └─ <workspace-id>\
├─ queue\
│  └─ <workspace-id>\
└─ logs\
```

用途：

| 場所 | 内容 |
|---|---|
| `config` | PC固有の実設定 |
| `secrets` | Androidサーバー認証用トークン |
| `certificates` | PCが信頼するプライベートCAの公開証明書 |
| `state` | JSONL読み取り位置、送信状態、ワークスペース登録情報 |
| `data` | Androidへ送信する前の表示用JSON |
| `queue` | 受信成功まで保持する未送信スナップショット |

キュー内のSnapshotは<workspace-id>\<sequence>-<snapshot-id>\へ保存し、manifestと本文複製を一緒に管理する。Androidのcommit成功確認前に削除しない。
| `logs` | PC収集ツールのローテーションログ |

`%LOCALAPPDATA%`は設定ファイルの文字列置換だけに依存せず、実装時に`os.environ["LOCALAPPDATA"]`から取得する。環境変数がない場合は推測で別の場所へ保存せず、起動を中止する。

## ワークスペースの自動検出

### 許可ルート

初期許可ルートは次の1つとする。

```text
C:\codex
```

ユーザーはCodexに管理させたいプロジェクトと成果物を今後もこの配下だけへ配置する。JSONLに他フォルダのパスが記録されていても、許可ルート外ならワークスペースとして登録しない。

対象外の例：

- チャットへ一時的に添付したダウンロードファイル
- 調査のため読み取ったPDFや画像
- `C:\codex`外の参考資料
- 一時ディレクトリ
- Codexや別アプリの設定・キャッシュ

### 検出元

次をワークスペース候補として取得する。

- `session_meta.cwd`
- `turn_context.cwd`
- `turn_context.workspace_roots[]`

候補パスを正規化した後、次の条件をすべて満たす場合だけ登録する。

1. 解決後の絶対パスが`C:\codex`の子孫であり、`C:\codex`自身ではない。
2. パスが存在し、ディレクトリである。
3. Gitワークツリー内である。
4. `git rev-parse --show-toplevel`でリポジトリルートを取得できる。
5. 取得したリポジトリルートも許可ルートの子孫であり、許可ルート自身ではない。
6. `HEAD`が存在し、少なくとも1つコミットがある。

単純な文字列前方一致は使用しない。たとえば`C:\codex-other`を`C:\codex`配下と誤判定しないよう、正規化したパスの親子関係で判定する。シンボリックリンク等で許可ルート外へ解決される場合も拒否する。

`C:\codex`直下のGitリポジトリは対象外とする。`C:\path\to\CodexMobileDashboard`のように、許可ルート配下へ個別に配置したGitリポジトリだけをワークスペースとして登録する。

セッション探索時は、まず`session_meta`と`turn_context`の識別情報だけを確認する。許可されたGitルートに関連付けられないセッションのメッセージ本文、ツール入出力、ファイル参照は抽出・保存・送信しない。同一セッション内で作業場所が変わる場合は`turn_id`と各`turn_context.cwd`で関連付け、許可されたワークスペースのターンだけを処理する。許可ワークスペースの会話内で一時的に添付・参照された外部ファイルは会話情報として扱い得るが、その外部パス自体を新しいワークスペースとして登録しない。

### ワークスペース登録簿

検出したプロジェクトは次へ保存する。

```text
%LOCALAPPDATA%\CodexMobileDashboard\state\workspaces.json
```

最低限、次を保持する。

- `workspace_id`
- 正規化済みGitルート
- 表示名
- 初回検出日時
- 最終検出日時
- 有効・無効状態
- 最後に関連付けたセッションID

同じGitルートを複数セッションで使用しても、同一ワークスペースとして扱う。新しいプロジェクトを`C:\codex`配下でCodexが使用した場合、プロジェクトごとのINI追加は不要である。

初回登録時の`workspace_id`は、Gitルートのフォルダ名を英小文字・数字・ハイフンへ正規化した値と、正規化済み絶対パスのSHA-256先頭8桁を組み合わせる。たとえば`CodexMobileDashboard`なら`codex-mobile-dashboard-1a2b3c4d`のような形式になる。登録後は`workspaces.json`のIDを正とし、毎回生成し直さない。パスを移動したプロジェクトを同一とみなす方法は、実装時に誤結合を避けて別途検討する。

生成JSONと未送信キューは`workspace_id`別のディレクトリへ保存し、すべての表示用JSONにも`workspace_id`を持たせる。これにより複数ワークスペースのデータを混在させない。

## PC収集ツールの設定項目

### `[discovery]`

| キー | 必須 | 型・既定値 | 規則 |
|---|---|---|---|
| `allowed_roots` | 必須 | 複数行パス | 初期値は`C:\codex` |
| `sessions_dir` | 必須 | path | 読み取り専用で扱う |
| `archived_sessions_dir` | 任意 | path | 存在しなければ警告して継続 |
| `scan_archived_sessions` | 必須 | boolean、`true` | アーカイブ移動後も履歴を追跡 |
| `require_git` | 必須 | boolean、`true` | MVPでは`false`を許可しない |
| `require_initial_commit` | 必須 | boolean、`true` | MVPでは`false`を許可しない |
| `auto_register` | 必須 | boolean、`true` | 条件を満たすGitルートを登録 |

### `[collector]`

| キー | 必須 | 型・既定値 | 許容範囲・規則 |
|---|---|---|---|
| `working_poll_seconds` | 必須 | integer、`30` | 5～300秒 |
| `idle_poll_seconds` | 必須 | integer、`60` | 10～600秒 |
| `heartbeat_seconds` | 必須 | integer、`300` | 60～3,600秒 |

`recent.json`の完了済みターン数は要件として`2`に固定し、設定項目にしない。

### `[sender]`

| キー | 必須 | 型・既定値 | 規則 |
|---|---|---|---|
| `base_url` | 必須 | URL | 実データでは`https`だけを許可 |
| `timeout_seconds` | 必須 | integer、`10` | 1～120秒 |
| `max_attempts` | 必須 | integer、`5` | 初回を含む1～10回 |
| `backoff_initial_seconds` | 必須 | integer、`1` | 1～60秒 |
| `backoff_max_seconds` | 必須 | integer、`16` | 初期待機以上、最大300秒 |
| `request_target_bytes` | 必須 | integer、`819200` | 通常分割目標の800KiB |
| `request_max_bytes` | 必須 | integer、`1048576` | 絶対上限1MiB |
| `token_file` | 必須 | path | `%LOCALAPPDATA%`配下の秘密ファイル |
| `ca_file` | 必須 | path | サーバー証明書を発行したCAの公開証明書 |

`base_url`にユーザー名、パスワード、クエリ、フラグメントを含めない。証明書の検証を無効化する設定は設けない。

### `[storage]`

| キー | 必須 | 型・既定値 | 規則 |
|---|---|---|---|
| `output_dir` | 必須 | path | ワークスペースID別に生成JSONを保存 |
| `state_file` | 必須 | path | JSONL読み取り位置と送信状態 |
| `workspace_registry` | 必須 | path | 自動検出したワークスペース登録簿 |
| `queue_dir` | 必須 | path | 受信成功まで削除しない |
| `message_chunk_bytes` | 必須 | integer、`262144` | 最大256KiB |
| `page_max_items` | 必須 | integer、`100` | 1ページの最大件数 |
| `page_max_bytes` | 必須 | integer、`524288` | 最大512KiB |

### `[logging]`

| キー | 必須 | 型・既定値 | 規則 |
|---|---|---|---|
| `directory` | 必須 | path | `%LOCALAPPDATA%`配下 |
| `level` | 必須 | string、`INFO` | `DEBUG`、`INFO`、`WARNING`、`ERROR`、`CRITICAL` |
| `retention_days` | 必須 | integer、`7` | 1～90日 |

## 秘密ファイル

PC側：

```text
%LOCALAPPDATA%\CodexMobileDashboard\secrets\sender.token
```

Androidサーバー端末側：

```text
~/.config/codex-mobile-dashboard/server.token
```

PCとAndroidサーバー端末には同じトークンを1行で保存する。前後の空白と改行は読み取り時に除去する。

トークン生成例：

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

生成した値はチャット、INI、ログ、コミット、設定例へ貼り付けない。

## HTTPSと証明書

### 通常運用

- PCからAndroidへのPOSTと、閲覧スマートフォンからAndroidへのGETの両方をHTTPSにする。
- AndroidサーバーはプライベートCAが発行したサーバー証明書と秘密鍵を使用する。
- PCは`ca_file`でCA証明書を指定してサーバー証明書を検証する。
- 閲覧スマートフォンにも同じCAの公開証明書を信頼させる。
- 証明書のSANは実際の接続先IPアドレスまたはホスト名と一致させる。
- Androidサーバー端末のIPアドレスをDHCP予約等で固定する。
- 証明書検証を無効にしない。
- CA秘密鍵とサーバー秘密鍵をGit、ログ、表示用JSONへ含めない。

AndroidサーバーはPython 3.10対応を維持するため、Python 3.14の`HTTPSServer`には依存せず、`ssl.SSLContext`で`ThreadingHTTPServer`のソケットをTLS化する。

### HTTPを許可する範囲

HTTPは独立した疎通確認としてだけ使用する。

- `data/samples/`の架空データだけを使用する。
- 実際のCodex JSONL、Git状態、本番トークンを読み取らない。
- 通常のcollector実行は`http://`の`base_url`を拒否する。
- HTTP確認手順と通常運用手順を分離する。

## Androidサーバー端末側のリポジトリ外データ

予定構成：

```text
~/.config/codex-mobile-dashboard/
├─ server.ini
├─ server.token
└─ tls/
   ├─ server.crt
   └─ server.key
```

CA秘密鍵はAndroidサーバーの通常実行場所へ常置せず、証明書発行手順を決めるタスクで安全な保管場所を確定する。証明書管理が標準ライブラリ運用では複雑すぎると判明した場合は、CaddyをHTTPS終端として再提案する。

## 起動時の検証順序

1. `LOCALAPPDATA`と実設定ファイルを確認する。
2. INIをUTF-8かつ補間無効で読み取る。
3. 必須セクション、必須キー、未知キーを確認する。
4. 数値、真偽値、URLを検証する。
5. 許可ルートとセッションディレクトリを検証する。
6. ローカル保存先が許可ルート内に収まることを確認し、必要な出力ディレクトリを作成する。
7. トークンファイルを確認する。
8. CA証明書ファイルを確認する。
9. `base_url`がHTTPSであることを確認する。
10. すべて成功した後でセッション探索と送信を開始する。

不明な設定項目は警告して無視する。必須項目不足、範囲外、未解決の環境変数、不正URL、秘密ファイル不正、CAファイル不正では起動を中止する。秘密値はエラーやログへ含めない。

## Git管理

次をGitへ登録しない。

- 実際の`collector.ini`、`server.ini`
- `*.token`
- `*.key`
- 実行時JSON
- 状態ファイル
- ワークスペース登録簿
- 未送信キュー
- 実行ログ

設定例には予約済みの例示用IP・ホスト名とダミーパスだけを使用する。

## トークン更新

1. 新しいトークンを生成する。
2. PCとAndroidサーバー端末の秘密ファイルを更新する。
3. Androidサーバーを再起動する。
4. PC収集ツールを再起動する。
5. HTTPSと認証の成功を確認する。
6. 古いトークンが拒否されることを確認する。

## 参考資料

- [Windows Known Folders](https://learn.microsoft.com/en-us/windows/win32/shell/knownfolderid)：`LOCALAPPDATA`のWindows標準位置
- [Android cleartext communications](https://developer.android.com/privacy-and-security/risks/cleartext-communications)：平文通信の盗聴・改ざんリスク
- [RFC 6750](https://www.rfc-editor.org/info/rfc6750/)：BearerトークンをTLSで保護する要件
- [Python `ssl`](https://docs.python.org/3.10/library/ssl.html)：Python 3.10標準ライブラリのTLS機能

## Androidサーバーの実設定

Androidサーバーは、リポジトリ外の`~/.config/codex-mobile-dashboard/server.ini`を`python server.py --config`で指定する。INIはUTF-8、補間無効で読み取る。`[server]`の`host`、`port`、`certificate_file`、`private_key_file`、`static_directory`、`public_directory`と、`[auth]`の`token_file`を必須とする。`token_file`はUTF-8の1行Tokenであり、前後の空白と改行を除去して読む。空のTokenファイルは起動を中止する。

`server.ini`、`server.token`、TLS秘密鍵はGit管理せず、同じユーザーだけが読める権限にする。設定例は`config/server.example.ini`を使用する。環境変数と`~`はパスに限り展開する。Tokenの値はINI、コマンドライン、ログ、例外、HTTP応答へ出力しない。