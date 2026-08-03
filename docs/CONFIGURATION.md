# 設定ファイルと秘密情報の管理仕様

## 採用方式

通常設定はINI形式で管理し、秘密情報はリポジトリ外の専用ファイルに保存する。

Python標準ライブラリの`configparser`を使用し、設定ファイルはUTF-8で読み取る。

## ファイル構成

リポジトリには、秘密情報を含まない設定例だけを登録する。

```text
CodexMobileDashboard/
└── config/
    ├── collector.example.ini
    └── server.example.ini
```

端末固有の実設定はリポジトリへ登録しない。

```text
config/collector.ini
config/server.ini
```

## 秘密ファイル

API認証トークンは、リポジトリ外のファイルへ1行で保存する。

作業用PC：

```text
%USERPROFILE%\.config\CodexMobileDashboard\sender.token
```

検証用Android端末：

```text
~/.config/codex-mobile-dashboard/server.token
```

PCとAndroid端末には同じトークンを設定する。前後の空白と改行は読み取り時に除去する。

## 設定例

作業用PCの設定例：

```ini
[collector]
workspace = C:\path\to\CodexMobileDashboard
sessions_dir = %USERPROFILE%\.codex\sessions
state_file = data\collector-state.json
poll_seconds = 5

[sender]
url = http://192.168.1.20:8765
timeout_seconds = 10
token_file = %USERPROFILE%\.config\CodexMobileDashboard\sender.token
```

検証用Android端末の設定例：

```ini
[server]
host = 0.0.0.0
port = 8765
data_dir = data
client_dir = client
max_body_bytes = 1048576
token_file = ~/.config/codex-mobile-dashboard/server.token
```

## トークン生成

Python標準ライブラリで十分に長いトークンを生成する。

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

生成したトークンはチャット、ログ、コミット、設定例へ貼り付けない。

## アクセス権

- 秘密ファイルはセッション収集ツールまたはサーバーを実行するユーザーだけが読み取れるようにする。
- Termuxでは`chmod 600 ~/.config/codex-mobile-dashboard/server.token`を設定する。
- Windowsでは秘密ファイルをユーザープロファイル配下に置き、他ユーザーへ共有しない。
- 秘密ファイルをクラウド同期対象にしない。

## 起動時の検証

次の場合は安全のため起動を中止し、秘密値を含まないエラーを記録する。

- 設定ファイルが存在しない。
- 必須セクションまたは必須項目がない。
- 数値項目が範囲外である。
- 秘密ファイルが存在しない、空、または読み取れない。
- URL、ポート、保存先が不正である。

不明な設定項目は警告として記録し、互換性を考慮して無視する。

## 認証

- PC側は秘密ファイルからトークンを読み取り、HTTP認証ヘッダーへ設定する。
- Android端末側は同じ秘密ファイルからトークンを読み取る。
- トークンの比較には`hmac.compare_digest()`を使用する。
- 認証ヘッダー、トークン、秘密ファイルの内容をログへ出力しない。
- 認証失敗時は、入力されたトークンをレスポンスやログへ含めない。

## Git管理

次のファイルはGitへ登録しない。

- `config/collector.ini`
- `config/server.ini`
- `*.token`
- `.env`
- 実行ログ
- 生成されたJSON

設定例には実在しないダミー値だけを使用する。

## トークン更新

1. 新しいトークンを生成する。
2. PCとAndroid端末の秘密ファイルを更新する。
3. Android端末側サーバーを再起動する。
4. PC側送信処理を再起動する。
5. 認証成功を確認する。
6. 古いトークンが使用できないことを確認する。
