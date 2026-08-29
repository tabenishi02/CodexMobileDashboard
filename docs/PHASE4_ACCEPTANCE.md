# Phase 4 実機受入結果

## 確認日時と環境

- 確認日: 2026-08-30
- 作業用PC: Windows、IPv4 `192.0.2.6`
- Androidサーバー: Termux、IPv4 `192.0.2.121`、HTTPS `8765`
- TLS: PC側でプライベートCA公開証明書を明示し、通常の証明書・接続先検証を実施

## 実施結果

| 確認項目 | 結果 | 証拠・内容 |
|---|---|---|
| HTTPSヘルスチェック | 成功 | `GET /health` は `200`、`status: ok`。public、staging、ログ、保存容量はいずれも利用可能。 |
| 認証付きJSON POST | 成功 | PC側の既存senderで受入用Snapshotの`metadata.json`を送信し、`stored`応答を確認。 |
| 明示的commit | 成功 | 同じSnapshotのcommitが`committed`応答を返した。 |
| commit済みJSON GET | 成功 | `GET /data/acceptance-android/metadata.json` が `200`を返し、送信したUTF-8 JSONと完全一致した。 |
| 静的画面ルート | 保留 | `GET /` は`404`。`client/index.html`を含む閲覧画面はPhase 5で実装する。 |
| PC送信失敗後の再送・冪等性 | 成功 | 接続不能なHTTPSポートへの送信で`connect_timeout`を発生させ、PC未送信キューへ保存。キューを再読込後、実機へ同一Delivery IDで再送・commitし、キュー削除、同一Delivery再送の成功、公開JSONの内容一致を確認。 |
| 閲覧スマートフォンでの画面表示 | 保留 | Phase 5の閲覧画面実装後に、CA信頼済みスマートフォンで受入確認する。 |

## 判定

Phase 4のサーバー受信・公開経路（HTTPS、POST、commit、GET）と、PC送信失敗後のキュー再送・冪等性は実機で確認済みとする。スマートフォン向け画面の表示受入は、Phase 5の画面実装完了後に実施する。現時点では`/`に表示可能な画面がないため、スマートフォン閲覧の完了条件には含めない。