# LAN内ネットワーク確認結果

本書は、Androidサーバー端末の基準端末である検証用Android端末を使用した実測結果を記録する。記載された端末名とIPアドレスは確認時の値であり、サーバー対象をAndroid端末へ限定するものではない。他のAndroidサーバー端末では、同じ観点とコマンドで個別に接続を確認する。

## 確認日

2026-08-04

## 初期疎通確認で使用した通信方式

同一LAN内の到達性とTCP 8765番ポートを確認するため、架空データだけを扱う簡易HTTPサーバーを使用した。

```text
作業用PC ── HTTP接続 ──▶ 検証用Android端末
                               ▲
閲覧スマートフォン ─ HTTP GET ─┘
```

作業用PCと閲覧スマートフォンの直接通信は不要である。

## ネットワーク構成

| 機器 | IPv4アドレス | 接続 |
| --- | --- | --- |
| 作業用PC | `192.0.2.6` | Ethernet |
| 検証用Android端末 | `192.0.2.121` | Wi-Fi |
| 閲覧スマートフォン | `192.0.2.7` | Wi-Fi |
| デフォルトゲートウェイ | `192.0.2.1` | ルーター |

サブネットマスクは`255.255.255.0`で、3台とも`192.0.2.0/24`に属する。

ゲストWi-Fiと端末分離機能は使用していない。

## 使用ポート

MVPではTCP 8765番ポートを使用する。

Python標準ライブラリのソケットでAndroid端末上のポートへbindでき、利用可能であることを確認した。

```text
TCP 8765 is available
```

## Android上のポート確認

Termuxへ`iproute2`を導入したが、`ss -ltn`は次のエラーにより利用できなかった。

```text
Cannot open netlink socket: Permission denied
```

これはAndroidのNetlinkアクセス制限によるものであり、ポートやサーバーの異常ではない。root化や権限変更は行わない。

ポート確認はPython標準ライブラリで行う。

```bash
python -c "import socket; s=socket.socket(); s.bind(('0.0.0.0', 8765)); print('TCP 8765 is available'); s.close()"
```

初期疎通ではサーバーのbind成功と外部からのHTTP接続を到達性の判定とした。正式サーバーでは、これに加えてTLSハンドシェイクと証明書検証の成功を最終判定とする。

## 簡易HTTPサーバー

Android端末で次のテスト用サーバーを起動した。

```bash
cd "$HOME/CodexMobileDashboard"
python -m http.server 8765 --bind 0.0.0.0
```

`0.0.0.0`へbindし、LAN内のほかの端末から接続できる状態で確認した。

## 作業用PCからの確認

TCP接続確認：

```powershell
Test-NetConnection -ComputerName 192.0.2.121 -Port 8765
```

結果：

```text
TcpTestSucceeded : True
```

HTTP GET確認：

```powershell
Invoke-WebRequest -UseBasicParsing -Uri "http://192.0.2.121:8765/" -TimeoutSec 10
```

結果：

```text
StatusCode        : 200
StatusDescription : OK
Server            : SimpleHTTP/0.6 Python/3.14.6
```

Windows PowerShellでは、`-UseBasicParsing`を付けてHTML解析に関する対話的なセキュリティ警告を回避する。

## 閲覧スマートフォンからの確認

ブラウザで次のURLを開いた。

```text
http://192.0.2.121:8765/
```

`Directory listing for /`が表示され、HTTP GETに成功した。

## 確認済み

- 3台が同じLANに所属する。
- TCP 8765番ポートをAndroid端末で使用できる。
- Android端末が`0.0.0.0:8765`でHTTP接続を受け付けられる。
- 作業用PCからAndroid端末へTCP接続できる。
- 作業用PCからAndroid端末へHTTP GETできる。
- 閲覧スマートフォンからAndroid端末へHTTP GETできる。

## 確定した通常運用方式

実際のCodexセッションを扱うMVP受入試験と通常運用では、PCからAndroidサーバー端末へのPOSTと、閲覧スマートフォンからのGETをどちらもHTTPSにする。

- PythonサーバーとプライベートCAを使用する。
- Androidサーバー端末のIPアドレスをDHCP予約等で固定する。
- サーバー証明書のSANを接続先IPアドレスまたはホスト名と一致させる。
- PCは設定されたCA証明書で接続先を検証する。
- 閲覧スマートフォンにも同じCAの公開証明書を信頼させる。
- 証明書エラーや証明書検証を無視しない。
- CA秘密鍵とサーバー秘密鍵をGitやログへ保存しない。

HTTPは引き続き独立した疎通確認に限り、`data/samples/`の架空データだけで使用できる。実JSONL、Git状態、本番トークンをHTTPで送信しない。

## Phase 4で追加確認した項目

- 正式サーバーへの証明書検証付きHTTPS接続
- 認証付きSnapshot POST、明示的commit、公開JSON GET
- PC未送信キューからの再送とDelivery IDの冪等処理
- Android端末再起動後のサーバーとSSHの自動起動

詳細は[`PHASE4_ACCEPTANCE.md`](PHASE4_ACCEPTANCE.md)を参照する。

## Phase 5以降で確認する項目

- 閲覧スマートフォンへのCA公開証明書登録とHTTPS画面表示
- Androidサーバー端末のIPアドレス固定またはDHCP予約の運用確認
- 画面消灯後5分および30分の接続継続
- Termuxの省電力除外
- モバイル回線から接続できないこと

## 注意事項

- `python -m http.server`は通信テスト専用であり、本運用には使用しない。
- 確認済みのHTTP通信は暗号化されないため、到達性確認以外には使用しない。
- ルーターで外部向けポート転送を設定しない。
- Androidサーバー端末のIPアドレスは、固定方法を決定するまで変更される可能性がある。
