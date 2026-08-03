# LAN内ネットワーク確認結果

## 確認日

2026-08-04

## Phase 1通信方式

Phase 1では、同一LAN内のIPv4とHTTPを使用する。

```text
作業用PC ── HTTP POST ──▶ 検証用Android端末
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

実運用ではサーバーのbind成功と外部からのHTTP接続を最終判定とする。

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

## 後続タスクで確認する項目

- 正式サーバーへのHTTP POST
- 認証成功と認証失敗
- JSON保存とブラウザ表示への反映
- Android端末のIPアドレス固定またはDHCP予約
- 画面消灯後5分および30分の接続継続
- Termuxの省電力除外
- Android端末再起動後の自動復旧
- モバイル回線から接続できないこと
- 閲覧用HTTP GETのアクセス制御

## 注意事項

- `python -m http.server`は通信テスト専用であり、本運用には使用しない。
- Phase 1のHTTP通信は暗号化されないため、信頼できるLAN内だけで使用する。
- ルーターで外部向けポート転送を設定しない。
- Android端末のIPアドレスは、固定方法を決定するまで変更される可能性がある。
