# Phase 5 Androidブラウザ表示確認手順

## 目的

CAを信頼済みの閲覧用Androidスマートフォンから、Android・Termuxサーバーが配信するダッシュボードをHTTPSで開き、主要画面と更新動作を実機確認する。本手順を実行するまでは`TASKS.md`の「Androidブラウザで表示確認する」を完了扱いにしない。

## 準備するもの

- Androidサーバー端末と閲覧スマートフォンを同じLANへ接続する。
- Androidサーバーを起動し、最新の`server/`と`client/`が配置済みであることを確認する。
- 閲覧スマートフォンへサーバー証明書を発行したCAの公開証明書を信頼済み証明書として導入する。CA秘密鍵やサーバー秘密鍵は移動しない。
- PC collectorから確認対象workspaceのSnapshotを少なくとも1回生成し、Androidサーバーへcommitしておく。
- Androidサーバーの接続先を確認する。現在の確認値は`https://192.0.2.121:8765`である。IPまたはホスト名が変わった場合は、サーバー証明書のSANと一致していることを先に確認する。

実データではHTTPを使用せず、証明書警告を無視して続行しない。Token、秘密鍵、実設定の内容をURL、スクリーンショット、作業記録へ含めない。

## 対象workspace IDと表示名を確定する

1. 作業PCで`collector.ini`の`[storage] output_dir`を確認する。既定値の場合は次のPowerShellで、生成済みSnapshotのworkspace ID・表示プロジェクト名・生成日時を一覧にする。

   ```powershell
   $dataRoot = "$env:LOCALAPPDATA\CodexMobileDashboard\data"
   Get-ChildItem -LiteralPath $dataRoot -Directory | ForEach-Object {
     $dashboardPath = Join-Path $_.FullName "dashboard.json"
     if (Test-Path -LiteralPath $dashboardPath) {
       $dashboard = Get-Content -LiteralPath $dashboardPath -Raw | ConvertFrom-Json
       [pscustomobject]@{
         WorkspaceId = $dashboard.workspace_id
         ProjectName = $dashboard.project.name
         GeneratedAt = $dashboard.generated_at
       }
     }
   } | Sort-Object ProjectName | Format-Table -AutoSize
   ```

   `output_dir`を変更している場合は、`$dataRoot`をその実ディレクトリへ置き換える。一覧が空の場合は`collect-once`を実行してから再確認する。

2. 確認対象の`ProjectName`を持つ行から`WorkspaceId`を選ぶ。フォルダ名や過去の確認記録からIDを推測しない。
3. 選んだ`WorkspaceId`、期待する`ProjectName`、次の2つのURLを結果記録へ転記する。

   ```text
   https://<AndroidサーバーのIPまたはホスト名>:8765/health?workspace_id=<選んだWorkspaceId>
   https://<AndroidサーバーのIPまたはホスト名>:8765/?workspace_id=<選んだWorkspaceId>
   ```

同じ作業PCに複数workspaceがある場合も、確認のたびにこの対応表から対象行を選ぶ。workspace IDそのものは秘密情報ではないが、Tokenやローカルパスを確認記録へ含めない。

## 1. HTTPSと公開Snapshotの事前確認

1. 閲覧スマートフォンでWi-Fi接続先がAndroidサーバーと同じLANであることを確認する。
2. VPNやモバイル回線への自動切替がある場合は、確認中に通信経路が変わらない状態にする。
3. Androidブラウザで次を開く。

   ```text
   https://<AndroidサーバーのIPまたはホスト名>:8765/health?workspace_id=<workspace_id>
   ```

4. 証明書警告が出ず、JSON応答の`status`が`ok`であることを確認する。
5. 応答の`workspace_id`が、事前に選んで記録した`WorkspaceId`と完全一致することを確認する。
6. `current_snapshot_id`と`last_received_at`が空でないことを確認する。空の場合はPC collectorからSnapshotを送信してから再確認する。

## 2. ダッシュボードを開く

1. 同じブラウザで次を開く。

   ```text
   https://<AndroidサーバーのIPまたはホスト名>:8765/?workspace_id=<workspace_id>
   ```

2. 初期読込表示の後にダッシュボードが表示され、読込中表示が残り続けないことを確認する。
3. 次を確認する。

   - プロジェクト名とPhaseが表示される。
   - 表示されたプロジェクト名が、事前に記録した`ProjectName`と完全一致する。
   - Codex状態、現在作業または最新状態が表示される。
   - 最新の変更要約、次タスク、エラー件数、Git状態が表示される。
   - PC側の生成日時と最終更新日時が表示される。
   - 画面上にToken、秘密鍵、サーバー内部パス、Tracebackが表示されない。
   - ヘッダー、本文、固定下部ナビゲーションが重ならない。

workspace指定を求める画面が出た場合は、URLの`workspace_id`の綴りとクエリ部分を確認する。画面側でworkspaceを推測させない。

## 3. 各画面を確認する

固定下部ナビゲーションと「その他」から、次の順に画面を開く。画面を最初に開いた時だけ遅延データを取得するため、読込中から正常表示または明示的な「データなし」へ変わることを確認する。

| 画面 | 確認内容 |
|---|---|
| ダッシュボード | 概要カード、状態、日時、各詳細画面への導線が表示される。 |
| 最近の更新 | 現在turnと完了turnが新しい順に表示され、状態とプレビューのMarkdownが安全に整形される。 |
| チャット | 既定で通常チャットだけが表示される。「Codex内部進捗」「ツール情報」「内部指示」「すべて」へ切り替え、種別と折りたたみラベルが一致する。項目がある場合は「以前のメッセージを読み込む」を1回実行する。 |
| エラー | 未解決・重大・ロールバック済み・全履歴件数が表示され、各項目が未解決・ロールバック済み・解決済み・無視のいずれかに区分される。ダッシュボードの未解決・重大件数は`errors.json.counts`と比較し、全履歴件数とは比較しない。0件なら通信失敗ではなくデータなしと表示される。 |
| 決定事項 | 表題、状態、日時、内容が表示される。置換履歴がある場合は置換前後の関係を確認できる。 |
| ファイル | パス、変更状態、追加・削除行数などのGitメタデータだけが表示され、ファイル本文やdiff本文は表示されない。 |
| システム情報 | PC collectorとAndroidサーバーの状態、公開中Snapshot ID、最終受信日時を確認できる。 |

追加で次を確認する。

- 長文の折りたたみを開閉できる。
- Markdownの見出し、リスト、引用、インラインコード、コードブロックが読める。
- 横長コードはコード領域内だけ横スクロールし、画面全体が横へはみ出さない。
- チャットの種別フィルターを切り替えても、選択中の画面と表示種別が一致する。
- 現在画面が色だけでなく文字または構造でも判別できる。

## 4. 更新動作を確認する

1. ダッシュボードを表示したまま、PC collectorから対象workspaceの新しいSnapshotを1回送信する。
2. 最大15秒待ち、最終更新日時または表示内容が自動更新されることを確認する。
3. 別の新しいSnapshotを送信し、ヘッダーの手動更新ボタンを押して更新されることを確認する。
4. ブラウザを別アプリへ切り替えて20秒以上待ち、戻った直後に更新確認が行われることを確認する。
5. 更新中も直前の正常表示が消えず、更新完了後に新しいSnapshotへ揃うことを確認する。

Snapshot更新のために手動収集する場合は、作業用PCで実設定を指定して次を実行する。実設定やToken本文は画面共有・作業記録へ貼り付けない。

```powershell
python -m tools.collector --config "$env:LOCALAPPDATA\CodexMobileDashboard\config\collector.ini" collect-once
```

## 5. 失敗表示を確認する

正常表示を確認した後、可能であればAndroidサーバーを短時間停止するか、閲覧スマートフォンのWi-Fiを一時的に切って手動更新する。

1. 通信失敗が「データなし」と区別して表示されることを確認する。
2. 既に表示済みの正常データが不必要に消えないことを確認する。
3. サーバーまたはWi-Fiを復旧し、手動更新で正常表示へ戻ることを確認する。

この操作はSnapshot送信中には行わない。復旧後もサーバーの`/health`が`ok`であることを確認する。

## 合格条件

次をすべて満たせば「Androidブラウザで表示確認する」を合格とする。

- 証明書警告なしでHTTPS接続できる。
- workspace指定URLから初期ダッシュボードを表示できる。
- URLの`workspace_id`、health応答の`workspace_id`、表示プロジェクト名が事前記録と一致する。
- 7画面を開き、正常表示・データなし・遅延読込を区別できる。
- 下部ナビゲーション、折りたたみ、戻る・進むを操作できる。
- 手動更新、自動更新、アプリ復帰時更新が動作する。
- 一時的な通信失敗から復旧できる。
- 秘密情報、ファイル本文、Git diff本文が表示されない。
- 未解決の表示崩れ、操作不能、`critical`または`error`相当の問題がない。

一般的な画面幅の比較、詳細なアクセシビリティ、CA信頼の正式な実機受入記録は、`TASKS.md`の後続項目として別に完了判定する。

## 結果記録テンプレート

実施後は次を記録し、秘密情報を含まないスクリーンショットがあれば添付する。

```text
実施日時:
閲覧端末機種 / Androidバージョン:
ブラウザ名 / バージョン:
接続先IPまたはホスト名（Token等は書かない）:
workspace_id:
期待する表示プロジェクト名:
health URL:
ダッシュボードURL:
health応答のworkspace_id一致: 合格 / 不合格
画面のプロジェクト名一致: 合格 / 不合格
HTTPS・証明書警告なし: 合格 / 不合格
初期ダッシュボード: 合格 / 不合格
7画面の表示と遅延読込: 合格 / 不合格
折りたたみ・チャット種別フィルター: 合格 / 不合格
自動更新・手動更新・復帰時更新: 合格 / 不合格
通信失敗表示と復旧: 合格 / 不合格
Token・秘密鍵・内部パス・SSHアカウント識別情報・本文・diff非表示: 合格 / 不合格
発見事項:
総合判定: 合格 / 不合格
```

不合格の場合は、表示された安全なエラー種別、発生画面、再現操作、端末・ブラウザ情報を記録する。Token、証明書秘密鍵、実設定全文、実データ本文は記録しない。

## 切り分け

| 症状 | 確認箇所 |
|---|---|
| 接続できない | 同一LAN、Androidサーバー起動、IP、TCP 8765、Wi-Fiの端末間通信制限を確認する。 |
| 証明書警告 | CA公開証明書の信頼状態、端末時刻、接続先と証明書SANの一致、有効期限を確認する。警告は無視しない。 |
| workspace指定を求められる | URLの`?workspace_id=...`と文字列の欠落を確認する。 |
| 想定外のプロジェクト名が表示される | 作業PCの`dashboard.json`一覧から選んだ`WorkspaceId`と`ProjectName`の組、health応答の`workspace_id`、ダッシュボードURLのクエリを再照合する。 |
| 公開データなし | workspace指定付き`/health`の`current_snapshot_id`と、PC collectorのcommit成功を確認する。 |
| 「データを読み込み中」のまま変化しない | `/styles.css`、`/app.js`、`/vendor/`配下のJavaScriptが200で取得できることと、修正版サーバーの配置・再起動を確認する。 |
| 古い画面が出る | Androidへ`client/`全体が配置済みか確認し、対象サイトのキャッシュを更新して再読込する。CA証明書は削除しない。 |
| 一部画面だけ失敗 | 該当JSONの表示エラー種別、workspace指定付き`/health`、Androidのエラーログを確認する。 |
| 更新されない | 表示中か、15秒待ったか、手動更新結果、`last_received_at`、collector送信成功を確認する。 |

証明書の詳細は[`TLS_CERTIFICATES.md`](TLS_CERTIFICATES.md)、Android配置と起動は[`TERMUX_SETUP.md`](TERMUX_SETUP.md)、画面仕様は[`PHASE5_SCREEN_DESIGN.md`](PHASE5_SCREEN_DESIGN.md)を参照する。
