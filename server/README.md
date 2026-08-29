# Android・Termux側サーバー

このディレクトリには、Androidサーバー端末のTermux上で動作するHTTPSサーバーを配置します。

> [!IMPORTANT]
> Python 3.10以上で動作する最小サーバー基盤と`/health`を実装済みです。HTTPS、認証、Snapshot API、静的配信は後続タスクです。

## 実装・テスト結果

### 実装済み

- Python 3.10以上・標準ライブラリの`ThreadingHTTPServer`基盤
- UTF-8 INIの`--config`による実設定読込と、リポジトリ外Tokenファイルの読込
- `GET /health`（バージョン、稼働秒数、public・staging・ログ・保存容量の利用可否と空き容量予約値）
- `GET /data/{workspace_id}/{relative_json_path}`でcommit済みpublic配下のJSONだけを返す（ETagとIf-None-Matchによる304対応）
- `--static-dir`で固定する実在ディレクトリ
- `GET /`からstatic_dir直下の`index.html`配信
- HTML、CSS、JavaScriptのContent-Type判定
- URLデコード後のパストラバーサル、絶対パス、Windowsドライブ指定子、バックスラッシュ拒否
- ディレクトリURLと存在しない静的ファイルの404

### 未実装

保存容量は`[storage] minimum_free_bytes`（既定1GiB）を予約し、空き容量不足または取得不能時は既存データを削除せず、新規POST・commitを`507 Insufficient Storage`で停止する。

### 確認済みテスト

`server/tests/test_server.py`で、ヘルスチェック、ルートindex配信、Content-Type、危険な静的パス拒否、ディレクトリ・存在しないファイルの404、Snapshot POST・commitの正常系、認証失敗、サイズ超過、不正JSON、危険URL、Delivery再送、容量不足時のPOST・commit停止を確認している。

## 役割

Androidサーバー端末は、作業用PCが生成した表示用JSONと固定のWeb画面を保存・配信します。

- 認証付きHTTPS POSTの受付
- リクエストサイズの確認
- JSON形式と送信先の検証
- 許可されたJSONの安全な保存
- 固定HTML、CSS、JavaScriptの配信
- JSON取得用HTTPS GETの提供
- ヘルスチェックの提供
- アクセスログとエラーログの保存
- 保存容量の監視

## 行わない処理

データの収集・解析・整形は作業用PC側で完了させます。Androidサーバー端末では次の処理を行いません。

- Codex JSONLの探索・読み取り・解析
- Gitコマンドの実行と変更内容の解析
- プロジェクトファイルの読み取り
- 会話、エラー、決定事項、TODOの抽出
- 変更内容のセマンティックな要約
- 表示用JSONの分類・再構成
- プロジェクトファイルやGit差分本文の保存

## 実装条件

- Python 3.10以上で動作すること
- Python標準ライブラリだけを使用すること
- Python 3.11以降で追加された機能に依存しないこと
- 基準端末の検証用Android端末とPython 3.14.6で実機確認すること
- 特定メーカーやCPUアーキテクチャに依存しないこと
- Termuxのアプリ専用領域に配置すること
- TCP 8765番ポートを初期値とし、INI設定で変更可能にすること
- `ssl.SSLContext`を使用し、Python 3.10以上でHTTPSを提供すること
- Python 3.14で追加された`HTTPSServer`には依存しないこと

## 静的配信ルート

サーバーは`--static-dir`で指定された実在ディレクトリを解決済みの固定ルートとして保持する。未指定時は起動ディレクトリを使用する。`/`はstatic_dir直下の`index.html`だけを配信する。存在しない場合は404を返す。HTMLは`text/html; charset=utf-8`、CSSは`text/css; charset=utf-8`、JavaScriptは`text/javascript; charset=utf-8`として配信する。静的パスはURLデコード後に検証し、`..`、絶対パス、Windowsドライブ指定子、バックスラッシュを拒否する。ディレクトリURLと存在しない静的ファイルは一覧や詳細を返さず404で拒否する。static_dirの`index.html`配信、Content-Type、404、危険なパス拒否は単体テストで確認する。その他の固定ファイル配信規則は後続タスクで追加する。

## 予定構成

実装時点で必要性を再確認し、不要なファイルやクラスは作成しません。

```text
server/
├─ README.md           # 本書
├─ server.py           # サーバーの起動点
└─ tests/              # サーバー単体テスト
```

固定HTML、CSS、JavaScriptは`client/`、受信した表示用JSONは実行環境のデータ保存先に配置します。実際の設定ファイルと認証トークンはリポジトリ外に置きます。

## PC senderとの通信契約

PCは`POST /api/v1/snapshots/{workspace_id}/{snapshot_id}/{relative_json_path}`へ、Bearer Token、UUIDの`X-Delivery-Id`、UTF-8 JSON生バイト列を送信する。サーバーは永続化完了後に`status: stored`、同じDelivery ID、Snapshot IDをJSONで返す。

全ファイルの保存後にPCは`POST /api/v1/snapshots/{workspace_id}/{snapshot_id}/commit`を呼ぶ。commit時にはmanifestとstaging内の全JSONのパス・サイズ・SHA-256・スキーマ・識別情報を照合し、`public/<workspace>/snapshots/<snapshot>/`へ世代として配置する。最後に`current.json`を原子的に置換するため、GETは常に公開済み世代だけを参照する。staging内に同じworkspace・ファイル名があってもGETは404とし、未commitデータを公開しない。`metadata.json`受信だけで公開してはならない。同じDelivery IDの再送は、staging内の非公開receiptを参照してJSONを再保存せず冪等に扱う。異なる送信先・本文での同一ID再利用は409で拒否する。

URLデコード後にもworkspace ID、snapshot ID、相対JSONパスを検証し、絶対パス、`..`、`\\`、保存ルート外への解決を拒否する。詳細は[`../docs/TRANSPORT_API.md`](../docs/TRANSPORT_API.md)を正とする。

## 処理の流れ

### JSON更新

```text
作業用PC
  ↓ 認証付きHTTPS POST
リクエストサイズ確認
  ↓
認証確認
  ↓
許可された送信先とJSON形式を検証
  ↓
一時ファイルへ書き込み
  ↓
既存ファイルと原子的に置換
  ↓
成功応答
```

検証または保存に失敗した場合は既存の正常なJSONを維持し、適切なHTTPステータスを返します。

### ブラウザ表示

```text
閲覧スマートフォン
  ↓ HTTPS GET
固定HTML・CSS・JavaScript
  ↓ HTTPS GET
表示用JSON
```

内容が変わっていない場合は、ETagと`If-None-Match`により`304 Not Modified`を返します。

## セキュリティ

- POSTには認証トークンを必須とする。
- トークンはリポジトリ外の秘密ファイルから読み取る。
- トークン比較には`hmac.compare_digest()`を使用する。
- トークン、HTTP本文、チャット全文をログへ出力しない。
- 許可されたJSON以外への書き込みを拒否する。
- パストラバーサルを拒否する。
- 不正JSONとサイズ超過を保存前に拒否する。
- サーバー内部の詳細エラーを作業用PCへ逆送しない。
- MVPでは信頼できるLAN内だけで使用し、ルーターのポート転送を行わない。
- プライベートCAが発行したサーバー証明書と秘密鍵を使用する。
- 証明書のSANを接続先IPアドレスまたはホスト名と一致させ、PC senderの通常のTLSホスト名検証で確認する。
- CA秘密鍵とサーバー秘密鍵をGit、ログ、表示用JSONへ含めない。
- 平文HTTPは架空のサンプルデータを使う独立した疎通確認だけに限定する。

## 保存と復旧

- JSONはUTF-8で保存する。
- 一時ファイルへ完全に書き込んでから置換する。
- 同時読み書きでJSONが破損しないようにする。
- 同じ送信識別子の再送を重複保存しない。
- 容量不足時は過去データを自動削除せず、新規保存を停止する。
- HTTPSアクセスは端末内の`logs/server.access.log`、保存・公開障害は`logs/server.error.log`へ保存する。Authorization、Token、本文は記録しない。
- アクセス・エラーログは日付が変わるタイミングでローテーションし、直近7世代を保持する。

## 関連資料

- [`../PROJECT.md`](../PROJECT.md)：全体設計
- [`../docs/CONFIGURATION.md`](../docs/CONFIGURATION.md)：設定と秘密情報
- [`../docs/DATA_LIMITS.md`](../docs/DATA_LIMITS.md)：送信対象とサイズ制限
- [`../docs/ERROR_HANDLING.md`](../docs/ERROR_HANDLING.md)：エラー処理
- [`../docs/LOGGING.md`](../docs/LOGGING.md)：ログ仕様
- [`../docs/NETWORK.md`](../docs/NETWORK.md)：LAN内通信
- [`../docs/TERMUX_ENVIRONMENT.md`](../docs/TERMUX_ENVIRONMENT.md)：Android端末要件と基準端末
- [`../docs/UPDATE_POLICY.md`](../docs/UPDATE_POLICY.md)：更新頻度と保存期間
- [`../docs/MVP_ACCEPTANCE.md`](../docs/MVP_ACCEPTANCE.md)：MVP受入条件

## 実設定とBearer Token

通常運用では、Git管理外のUTF-8 INIを指定して起動する。設定例は[`../config/server.example.ini`](../config/server.example.ini)であり、実ファイルにはコピー先を使用する。

```sh
mkdir -p ~/.config/codex-mobile-dashboard/tls
chmod 700 ~/.config/codex-mobile-dashboard ~/.config/codex-mobile-dashboard/tls
# server.iniとserver.token、TLS証明書・秘密鍵をリポジトリ外に配置する
chmod 600 ~/.config/codex-mobile-dashboard/server.ini ~/.config/codex-mobile-dashboard/server.token ~/.config/codex-mobile-dashboard/tls/server.key
python server.py --config ~/.config/codex-mobile-dashboard/server.ini
```

`[server]`には接続先、ポート、証明書・秘密鍵、静的画面、公開JSONの各パスを設定する。`[logging] directory`にはアクセス・エラーログ用のディレクトリを指定する。`[storage] minimum_free_bytes`には書込み前に確保する空き容量（既定1GiB）を指定する。未指定時はserver.iniと同階層の`logs/`を使用する。`[auth] token_file`にはTokenを1行だけ保存した別ファイルを指定する。Tokenの値をINI、コマンドライン、ログ、Gitへ置かない。読み込み時には前後の空白・改行を除去し、空ファイルは`token_file_invalid`として起動を中止する。`--config`起動はHTTPS証明書と秘密鍵を必須とし、平文HTTPへフォールバックしない。

## 起動スクリプト

Termuxでは、リポジトリ内の[`start_server.sh`](start_server.sh)を使ってHTTPSサーバーを前面起動する。既定ではリポジトリ外の`~/.config/codex-mobile-dashboard/server.ini`を使い、別の実設定を使う場合だけパスを1つ指定する。Tokenは引数へ渡さない。

```sh
cd "$HOME/CodexMobileDashboard/app/server"
chmod 700 start_server.sh
./start_server.sh
# または ./start_server.sh "$HOME/CodexMobileDashboard/config/server.ini"
```

設定ファイルが存在しない・読み取れない場合は固定エラーコードだけを標準エラーへ出して終了する。スクリプトは`exec`で`python server.py --config`へ置き換わるため、停止時はサーバープロセスも終了する。自動起動、停止・再起動手順は後続タスクで扱う。
### Termux:Bootへの登録

Termux:BootをTermux本体と同じ配布元から導入して一度開いた後、次で自動起動エントリーを登録する。

```sh
chmod 700 termux_boot_start_server.sh install_termux_boot.sh
./install_termux_boot.sh
```

[`install_termux_boot.sh`](install_termux_boot.sh)は`~/.termux/boot/codex-mobile-dashboard`を新規作成し、[`termux_boot_start_server.sh`](termux_boot_start_server.sh)から既存の`start_server.sh`を実行する。既存ファイルは上書きしない。再起動後は`/health`で確認する。停止・再起動手順は後続タスクで扱う。
`/api/`配下のPOSTは`Authorization: Bearer <token>`を必須とし、設定済みTokenとの比較には`hmac.compare_digest()`を使用する。Token未設定、ヘッダー欠落、形式不正、不一致は`401 Unauthorized`と`WWW-Authenticate: Bearer`だけを返す。認証済みでも、まだ実装されていないSnapshot APIは404を返す。

Snapshot JSON POSTのURLは/api/v1/snapshots/{workspace_id}/{snapshot_id}/{relative_json_path}だけを受理候補とする。IDとパス要素をURLデコード後に検証し、危険な区切り文字、空要素、..、非JSONファイル、クエリ文字列を404で拒否する。

設定読込の失敗は固定の安全なエラーコードだけを表示する。HTTP要求のAuthorizationヘッダーおよびURL中にTokenらしき値があっても、サーバーの標準ログとHTTPエラー応答には出力しないことを単体テストで確認している。
## 受信JSONの事前検証

API POSTは保存前に、BOMなしUTF-8、JSONオブジェクト、重複キーなし、共通必須フィールド、`1.x`スキーマ、URLとの`workspace_id`・`snapshot_id`一致を確認する。不正値は本文や詳細を返さず400で拒否する。個別ファイルの保存は後続タスクで追加する。
## staging配下の原子的保存

`staging_directory`は実在する専用ディレクトリとして設定し、`public_directory`と同一または親子関係にしてはならない。検証済みJSONは`<staging_directory>/<workspace_id>/<snapshot_id>/`配下へだけ保存し、親ディレクトリ内の一時ファイルを`os.replace()`で置換する。保存関数は公開先を操作しないため、commit実装までブラウザへは公開されない。
## Snapshot POST保存入口

`POST /api/v1/snapshots/{workspace_id}/{snapshot_id}/{relative_json_path}`は、検証済み本文を専用stagingへ保存する。小文字ハイフン形式UUIDの`X-Delivery-Id`を必須とし、リクエスト値不正は400、危険URLは404、Deliveryまたはstaging不整合は409、staging未設定は503、保存障害は500を返す。Content-Length未指定は411、1MiB超過は413とする。成功時は200と`status: stored`、要求と同じ`delivery_id`、URLと同じ`snapshot_id`を含むUTF-8 JSONを返す。