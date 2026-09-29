# v0.1.0 公開前監査

確認日: 2026-09-30

## 判定

**PASS**

対象はv0.1.0公開前の追跡ファイル、Git履歴、コミットメタデータ、第三者ライセンス、公開用文書である。実設定・Token・証明書・ログ・生成データを新たに読み込んで公開対象へ追加する操作は行っていない。

## 追跡ファイルと履歴

監査開始時点で追跡ファイルは192件、Git履歴は314コミットだった。

危険性の高いファイル名を現在の追跡対象と履歴から確認した結果、履歴上の該当は次の設定例だけだった。

- `config/backup.example.ini`
- `config/collector.example.ini`
- `config/server.example.ini`

実Token、秘密鍵、PEM、CSR、ログ、`.env`、runtime data、`tmp/`、`logs/`の追跡履歴は検出されなかった。

## 秘密情報パターン

全履歴のテキストblobを対象に、秘密鍵ヘッダー、OpenAI形式キー、GitHub Token、Bearer literal、JWT等を検査した。

検出された秘密値形式はテスト用フィクスチャだけだった。

- 秘密鍵ヘッダー形式: `tools/tests/test_secret_redactor.py`
- OpenAIキー形式: `tools/tests/test_secret_redactor.py`
- Bearer literal: `server/tests/test_snapshot_integration.py`

値そのものは監査出力へ保存せず、履歴差分の文脈を伏字化して確認した。GitHub Token形式とJWT形式は検出されなかった。
## 個人情報・環境固有情報

- ローカルWindowsユーザー名を示す実文字列はGit履歴blobから検出されなかった。
- 利用者の氏名に一致する実文字列はGit履歴blobから検出されなかった。
- コミットメールは1種類で、GitHubのnoreplyアドレスのみだった。
- グローバル到達可能なIPv4アドレスは履歴から検出されなかった。
- 文書内IPはRFC 5737の文書用レンジ、loopback、bind用またはテスト用のプライベート値に限定されていた。
- `tmp/`には実機確認時のローカル資料が存在するが、`.gitignore`対象であり追跡されていない。

## ライセンス

CodexMobileDashboard本体はMIT Licenseを採用し、ルートの`LICENSE`へ記載した。

同梱ブラウザライブラリは`THIRD_PARTY_NOTICES.md`と`licenses/`で確認した。

- markdown-it 14.1.0: MIT
- DOMPurify 3.2.6: Apache-2.0 or MPL-2.0
- highlight.js 11.11.1: BSD-3-Clause

各同梱ライセンスファイルが存在し、通知内容とパッケージ構成に矛盾がないことを確認した。

## 公開前テスト

- Python: 375 tests成功、既存の環境条件による2件skip
- クライアント: 4テストファイル成功
- `git diff --check`: 成功
- MVP-01～MVP-10: PASS

詳細は`MVP_ACCEPTANCE_RESULT_V0.1.0.md`を参照する。

## 公開範囲

v0.1.0はLAN内HTTPS版として公開する。Tailscaleおよびインターネット経由アクセスはPhase 9へ保留する。

Public化の直前には、公開対象commitのclean状態、追跡ファイル、LICENSE、README、CHANGELOG、テスト結果を再確認する。
