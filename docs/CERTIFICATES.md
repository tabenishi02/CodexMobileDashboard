# プライベートCAと証明書の運用

## 方針

CA秘密鍵は作業用PCのリポジトリ外で保護し、Android端末へコピーしない。Android端末にはサーバー証明書と対応する秘密鍵だけを配置する。PC senderと閲覧端末はCA公開証明書だけを信頼する。実セッションはHTTPSのみで扱う。

## 保存場所

```text
作業用PC（Git管理外）
%LOCALAPPDATA%\CodexMobileDashboard\certificates\ca\ca.key
%LOCALAPPDATA%\CodexMobileDashboard\certificates\ca\ca.crt
%LOCALAPPDATA%\CodexMobileDashboard\certificates\server\android-server.crt
%LOCALAPPDATA%\CodexMobileDashboard\certificates\server\android-server.key

Android端末（Git管理外）
$HOME/CodexMobileDashboard/certificates/android-server.crt
$HOME/CodexMobileDashboard/secrets/android-server.key
```

秘密鍵のファイル権限は所有者だけが読める状態にする。Tokenや秘密鍵をGit、ログ、チャット、表示JSONへ含めない。

## 作成

OpenSSLでRSA 3072bitのCAを作成し、CAは5年、サーバー証明書は1年を初期有効期間とする。SANにはAndroid端末の固定LAN IPアドレスを`IP:`形式で設定する。ホスト名も使用する場合だけ`DNS:`を追加する。

```sh
openssl genrsa -out ca.key 3072
openssl req -x509 -new -key ca.key -sha256 -days 1825 -out ca.crt -subj "/CN=CodexMobileDashboard Private CA"
openssl genrsa -out android-server.key 3072
openssl req -new -key android-server.key -out android-server.csr -subj "/CN=CodexMobileDashboard Android Server"
printf "subjectAltName=IP:192.0.2.121\nextendedKeyUsage=serverAuth\n" > server-ext.cnf
openssl x509 -req -in android-server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out android-server.crt -days 365 -sha256 -extfile server-ext.cnf
```

`192.0.2.121`は確認済み端末の例であり、実運用では固定化したAndroid端末IPへ置き換える。PC senderの`ca_file`には`ca.crt`を指定する。

## 更新

サーバー証明書は有効期限の30日前までに、新しいCSRと同じSANで再発行する。新証明書と秘密鍵をAndroidへ安全に配置し、サーバー再起動後にPCから証明書検証付きHTTPS接続を確認する。CA更新は通常行わない。CAを更新する場合は新旧CA公開証明書を一時的に信頼させ、全端末の切替確認後に旧CAを削除する。

## 失効・漏えい

MVPではCRLやOCSPを実装しない。サーバー秘密鍵の漏えい、端末紛失、SAN変更時は直ちに新しいサーバー鍵と証明書を発行し、旧証明書を端末から削除してサーバーを停止・再起動する。CA秘密鍵が漏えいした場合はCAを廃棄し、新CAと全サーバー証明書を作り直し、PC senderと閲覧端末の信頼CAを新しい公開証明書へ更新する。

## 確認

```sh
openssl x509 -in android-server.crt -noout -subject -issuer -dates -ext subjectAltName
openssl verify -CAfile ca.crt android-server.crt
```

SAN、発行者、有効期間、検証成功を確認してからHTTPSタスクへ進む。
