# Phase 7追加テスト結果

2026年9月10日、残っていた3項目の自動テストを追加した。関連Pythonテスト78件（統合・パス抽出21件、既存サーバー・HTTPS送信57件）とクライアントテスト2本が成功した。TLSのskipは0件。`git diff --check`も成功した。

| 対象 | 確認内容 |
| --- | --- |
| TLS | 正しいCAとlocalhostのSANで実HTTPS送信・commitが成功。別CAとIPアドレスによる名前不一致は証明書検証エラーとなり、再試行不可・staging/public未作成を確認 |
| 同時更新 | 同一workspaceへの2つのSnapshotの並行POST・commitが成功し、各公開ファイルのバイト列とcurrentの整合を確認。同一Delivery IDの並行commitは同じ応答・受付記録1回。公開コピー途中のGETは旧Snapshotを返し、完了後は新Snapshotを返す |
| 長いパス | 260文字超の実ファイルパスの抽出・行番号保持。深い階層、日本語・絵文字、長いファイル名、HTML風文字列を含むパスのDOM全文保持とテキスト表示。空白なしパスを折り返すCSS指定を確認 |

実装コードの変更は不要だった。画面テストは既存のDOMスタブとCSS検証を使用し、実ブラウザでのピクセル単位のレイアウト検証は含まない。並行更新テストは公開切替の原子性を確認するもので、切替をまたぐ独立した複数GETの一括トランザクションを保証するものではない。

## 再実行

リポジトリルートから実行する。

```powershell
python -m unittest server.tests.test_snapshot_integration tools.tests.test_file_reference_extractor
python -m unittest server.tests.test_server tools.tests.test_https_sender
node client/tests/app_test.js
node client/tests/styles_test.js
git diff --check
```

TLSテストにはPATH上のOpenSSLが必要。未導入の場合はTLSの3件がskipとなるため、TLS確認完了とは扱わない。今回の実行では3件すべて成功した。CA・秘密鍵はテストごとに一時ディレクトリへ生成し、終了時に削除する。固定の証明書期限や実機設定へ依存しない。通信先はループバックの一時ポートのみで、本番サーバーや定期タスクは操作しない。
