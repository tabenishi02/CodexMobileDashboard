# Phase 5引継ぎ

## 目的

本書は、Phase 5以降を別チャットで開始する際の正規の引継ぎ資料である。秘密値や実データは記載せず、実装済み範囲、実機環境、参照資料、未確定事項を示す。

## 完了済み範囲

- Phase 3の作業PC側collectorは実装・受入済みである。
- Phase 4のAndroid・Termux向けHTTPSサーバーは実装・実機受入済みである。
- Android実機で、証明書検証付きHTTPS、Bearer認証付きSnapshot POST、明示的commit、公開JSON GETを確認済みである。
- PC側の送信失敗後に未送信キューから再送し、同じDelivery IDを使う冪等処理を実機確認済みである。
- Android端末再起動後のHTTPSサーバーとSSHの自動起動を確認済みである。
- 閲覧画面は`client/index.html`、`client/styles.css`、`client/app.js`として実装済みである。画面にはダッシュボード、最近の更新、チャット、エラー、決定事項、ファイル、システム情報がある。

> 本書の名称はPhase 5開始時の引継ぎを示す。現在の再開地点と既知問題は[`CURRENT_HANDOFF.md`](CURRENT_HANDOFF.md)を正とする。

Phase 4の受入根拠は[`PHASE4_ACCEPTANCE.md`](PHASE4_ACCEPTANCE.md)を正とする。Phase 4完了時点の基準コミットは`12e1f8b`である。

## 実機・配置情報

| 項目 | 確認値 |
|---|---|
| 作業用PC | Windows、`192.0.2.6` |
| PCリポジトリ | `C:\path\to\CodexMobileDashboard` |
| Androidサーバー | 検証用Android端末、Android 12、`192.0.2.121` |
| Termux | 0.118.3、aarch64、Python 3.14.6、Git 2.55.0 |
| HTTPSポート | TCP 8765 |
| Androidリポジトリ | `$HOME/CodexMobileDashboard/app` |
| 公開・stagingデータ | `$HOME/CodexMobileDashboard/data` |
| Androidログ | `$HOME/CodexMobileDashboard/logs` |
| Android実設定 | `$HOME/.config/codex-mobile-dashboard/server.ini` |
| Android Token | `$HOME/.config/codex-mobile-dashboard/server.token` |
| Android TLS | `$HOME/.config/codex-mobile-dashboard/tls/server.crt`、`server.key` |

Token、秘密鍵、実INIの内容はチャット、Git、ログ、表示用JSONへ記載しない。PC側の正規構成は[`CONFIGURATION.md`](CONFIGURATION.md)、証明書は[`TLS_CERTIFICATES.md`](TLS_CERTIFICATES.md)を参照する。

実INIと秘密ファイルはリポジトリ外にあるため、今回の文書修正では変更していない。Phase 5の静的ファイルを配置する前に、Android実機の`server.ini`で`static_directory = ~/CodexMobileDashboard/app/client`になっていることを確認する。PC側`collector.ini`も、Tokenが`%LOCALAPPDATA%\CodexMobileDashboard\secrets\sender.token`、CA公開証明書が`%LOCALAPPDATA%\CodexMobileDashboard\certificates\ca.crt`を指す正規構成か確認する。値やToken本文をチャットへ貼り付けない。

## 実装済み通信契約

PCは各JSONを次へ送信する。

```text
POST /api/v1/snapshots/{workspace_id}/{snapshot_id}/{relative_json_path}
```

全対象ファイルの保存成功後に次を呼ぶ。

```text
POST /api/v1/snapshots/{workspace_id}/{snapshot_id}/commit
```

Androidはcommit成功時だけ`current.json`を原子的に切り替える。`metadata.json`の受信順序は公開境界ではなく、未commitのstaging SnapshotはGETで公開しない。ブラウザはcommit済みの現在世代を次から取得する。

```text
GET /data/{workspace_id}/{relative_json_path}
```

ETagと`If-None-Match`による`304 Not Modified`に対応している。詳細は[`TRANSPORT_API.md`](TRANSPORT_API.md)を正とする。

## 表示用JSON

PC側collectorは`dashboard.json`、`recent.json`、`messages.json`、`errors.json`、`decisions.json`、`files.json`、`metadata.json`を生成する。会話履歴と変更要約は`messages.json`の索引から`messages/pages/`、`messages/chunks/`、`messages/summaries/`を遅延取得する。

フィールド、列挙値、null、ページ・断片構造は[`DATA_SCHEMA.md`](DATA_SCHEMA.md)を正とする。画面側で推測によるフィールド追加や要約生成を行わない。

## Phase 5開始時に必要な判断

### 1. `workspace_id`の選択・受渡し

`workspace_id`はURLクエリで受け取る。URLの形式は次のとおりとする。

```text
/?workspace_id=<workspace_id>
```

現行サーバーにはworkspace一覧APIがなく、ディレクトリ一覧も公開しない。したがって画面はIDを推測せず、URLに`workspace_id`がない場合は、IDを指定するよう案内する。URLクエリ方式はサーバー改修なしで複数ワークスペースを扱え、閲覧URLをブックマークできる。URLパス方式と一覧APIはMVP後の拡張候補とする。

### 2. サーバー状態画面の情報源

`GET /health?workspace_id=<workspace_id>`は、状態、サーバーバージョン、稼働秒数、public・staging・ログ・保存容量の利用可否、予約空き容量に加え、指定workspaceの最終受信日時（commit受理時刻）と公開中Snapshot IDを返す。Phase 5ではこれらをシステム情報画面に表示する。workspace IDなし・公開済みSnapshotなし・旧形式の`current.json`では、該当フィールドは`null`とする。

## Phase 5で最初に読む資料

1. [`../PROJECT.md`](../PROJECT.md)
2. [`../CODING_RULES.md`](../CODING_RULES.md)
3. [`../TASKS.md`](../TASKS.md)のPhase 5
4. [`../client/README.md`](../client/README.md)
5. [`PHASE5_SCREEN_DESIGN.md`](PHASE5_SCREEN_DESIGN.md)
6. [`DATA_SCHEMA.md`](DATA_SCHEMA.md)
7. [`TRANSPORT_API.md`](TRANSPORT_API.md)
8. [`PHASE4_ACCEPTANCE.md`](PHASE4_ACCEPTANCE.md)
9. [`CONFIGURATION.md`](CONFIGURATION.md)

## Phase 5の開始条件

次の順序で開始する。

1. サーバー状態画面へ必要な情報を確定する。
2. `TASKS.md`の画面構成設計から実装する。
3. `client/index.html`、CSS、JavaScriptをAndroidの`$HOME/CodexMobileDashboard/app/client`へ反映する。
4. CA信頼済みの閲覧スマートフォンで`https://192.0.2.121:8765/`を実機確認する。

実機確認は[`PHASE5_ANDROID_BROWSER_CHECK.md`](PHASE5_ANDROID_BROWSER_CHECK.md)のチェックリストと結果記録テンプレートに従う。

画面実装後、Android実機へ`server/`と`client/`を再配置し、`GET /health`とworkspace指定healthの`200`、collectorからの複数Snapshot commit成功を確認した。SC-51E（Android 16、Chrome 152.0.7977.64）による表示・操作確認と、そこで判明した表示データ・画面遷移・安全表示の修正を完了した。2026年9月2日にはCA信頼済みHTTPS正式受入（T01～T12）へ合格し、一般的なAndroidスマートフォン相当の画面幅も確認済みとした。アクセシビリティは別タスクで判定する。
