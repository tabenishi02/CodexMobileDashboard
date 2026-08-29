# プライベートCAとTLS証明書の運用

本書を証明書の保存場所、ファイル名、有効期間、更新・事故対応の正規資料とする。

## 方針

- CA秘密鍵は作業用PCのリポジトリ外・暗号化済み保管先だけに置き、Android端末へコピーしない。
- Android端末にはサーバー秘密鍵、サーバー証明書、CA公開証明書だけを置く。
- PC senderと閲覧端末はCA公開証明書を信頼し、証明書検証を無効化しない。
- サーバー証明書のSANには、LAN固定IPまたは予約済みホスト名を必ず入れる。

## 作成

作業用PCの`%LOCALAPPDATA%\CodexMobileDashboard\certificates\`で、OpenSSLによりCA鍵とCA公開証明書を作成する。このディレクトリはリポジトリ外である。CA鍵はパスフレーズで保護し、バックアップは暗号化して保管する。サーバー鍵とCSRを生成し、SANを含む設定でCA署名する。初期有効期間はCAを10年、サーバー証明書を1年とする。

SANの例：

```text
subjectAltName = IP:192.0.2.121,DNS:codex-dashboard.local
```

作業用PCの発行・保管ファイル名は次へ統一する。Bearer Tokenは証明書ディレクトリへ混在させず、`%LOCALAPPDATA%\CodexMobileDashboard\secrets\sender.token`へ置く。

```text
%LOCALAPPDATA%\CodexMobileDashboard\certificates\
├─ ca.key
├─ ca.crt
├─ ca.srl
├─ server.key
├─ server.csr
├─ server.crt
└─ server-ext.cnf
```

Android端末にはCA秘密鍵をコピーせず、次のリポジトリ外ディレクトリへサーバー証明書と秘密鍵を配置する。

```text
$HOME/.config/codex-mobile-dashboard/
├─ server.ini
├─ server.token           # 秘密情報、chmod 600
└─ tls/
   ├─ server.crt          # 公開情報
   └─ server.key          # 秘密情報、chmod 600
```

PC senderの`ca_file`には`%LOCALAPPDATA%\CodexMobileDashboard\certificates\ca.crt`を指定する。閲覧スマートフォンには同じCA公開証明書を利用者が信頼済み証明書として導入する。

## 更新

- サーバー証明書は有効期限の30日前までに新規鍵・CSR・証明書を作成する。
- 新しい証明書をAndroidへ配置し、HTTPS疎通を確認してからサーバーを再起動する。
- PC senderと閲覧端末のCA公開証明書は、CA更新時だけ置換する。
- IPまたはホスト名を変更する場合は、SANを更新した新しいサーバー証明書を発行してから切り替える。

## 失効・事故対応

サーバー秘密鍵またはAndroid端末が漏えい・紛失した場合は、直ちにLANから切り離し、当該サーバー証明書を使用停止する。新しいサーバー鍵と証明書を発行し、Androidへ再配置してTokenも更新する。プライベートCAの失効情報を全クライアントへ確実に配る運用はMVPでは行わないため、CA秘密鍵の漏えい時は新しいCAを作成し、PC sender・閲覧端末・Androidの信頼鎖をすべて更新する。

## 禁止事項

- CA秘密鍵、サーバー秘密鍵、実証明書、CSR、TokenをGitへ追加しない。
- `ssl`の証明書検証を無効化しない。
- SANなしの証明書、または接続先と一致しないSANを使用しない。
- 平文HTTPへ自動フォールバックしない。


## 接続先との一致確認

PC senderの`base_url`に指定するIPアドレスまたはホスト名は、サーバー証明書のSANに完全一致させる。例として`https://192.0.2.121:8765`へ接続する場合は`IP:192.0.2.121`、`https://codex-dashboard.local:8765`へ接続する場合は`DNS:codex-dashboard.local`をSANへ含める。CNだけでは代用しない。

発行後は、PC senderの通常TLS検証で接続し、SAN不一致時に接続が失敗することを確認する。IPまたはDNS名を変更する場合は、先にSANを更新した証明書を発行・配置し、`base_url`を切り替える。サーバー側はクライアントの接続先指定を知ることができないため、この一致を緩和・無効化する設定を持たない。
