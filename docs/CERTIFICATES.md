# 証明書運用資料の参照先

証明書の保存場所、ファイル名、有効期間、作成、更新、失効・漏えい対応は[`TLS_CERTIFICATES.md`](TLS_CERTIFICATES.md)へ統一した。

旧版で使用していた`android-server.crt`、`$HOME/CodexMobileDashboard/certificates/`、CA有効期間5年という定義は使用しない。現行のサーバー設定は`server.crt`と`server.key`を`$HOME/.config/codex-mobile-dashboard/tls/`から読み込む。
