# LAN内アクセス手順

通常運用はPC→AndroidサーバーへのHTTPS送信と、閲覧スマートフォン→AndroidサーバーへのHTTPS閲覧で構成する。初期HTTP疎通試験の記録は[NETWORK.md](NETWORK.md)、初回構築は[TERMUX_SETUP.md](TERMUX_SETUP.md)と[TLS_CERTIFICATES.md](TLS_CERTIFICATES.md)を参照する。

## 接続前の確認

- PC・Androidサーバー・閲覧スマートフォンを同じLANへ接続する。ゲストWi-Fiや端末分離で相互通信が遮断されていないことを確認する。
- 本環境ではAndroidサーバーを固定IPに設定済み。これまでの確認先は`192.0.2.121`、HTTPSポートは`8765`。別環境では実設定の値へ置き換える。
- AndroidのHTTPSサーバーが起動済みであることを確認する。起動・停止方法は[TERMUX_SETUP.md](TERMUX_SETUP.md)。起動済みなら重ねて起動しない。
- PCの`collector.ini`の`[sender] base_url`と`ca_file`、サーバー証明書のSAN、閲覧URLを一致させる。閲覧スマートフォンには対応するCA公開証明書を信頼させる。証明書警告を無視しない。

## PCから疎通を確認する

```powershell
Test-NetConnection -ComputerName 192.0.2.121 -Port 8765
```

`TcpTestSucceeded=True`はTCPの到達確認であり、TLSや認証の成功ではない。閲覧スマートフォンのCA信頼済みブラウザで次を開き、証明書警告なしで応答することを確認する。

```text
https://192.0.2.121:8765/health
```

PCの定期送信は設定済みのCAで証明書を検証する。定期タスクの状態・送信成功ログは[SCHEDULED_EXECUTION.md](SCHEDULED_EXECUTION.md)で確認する。

## 閲覧するworkspaceを選ぶ

CA信頼済みブラウザでAndroidサーバーのトップページを開く。

```text
https://192.0.2.121:8765/
```

トップページは`GET /workspaces`を使用し、Androidサーバーで現在公開中のcommit済みSnapshotを持つワークスペースだけをプロジェクト名と最終更新日時で一覧表示する。利用者が`workspace_id`を調べたり入力したりする必要はない。

一覧から対象プロジェクトを選ぶと、内部的には次の既存URLへ遷移する。

```text
https://192.0.2.121:8765/?workspace_id=<workspace_id>
```

既存ブックマークや診断用途では直接URLも継続して利用できる。TokenをURLに含めない。閲覧操作にPCの送信Tokenを入力する必要はない。

## 通常運用

PCで登録時と同じユーザーにログオンする。定期タスクは1分間隔で未送信キュー再送→新規収集を行い、HTTPSでSnapshotを送信・commitする。画面は表示中15秒間隔、画面復帰時、または手動更新で更新する。収集に時間がかかる場合があるため、毎分必ず新Snapshotが届くとは限らない。

```powershell
cd C:\path\to\CodexMobileDashboard
.\scripts\get_collector_task_status.ps1 | Format-List
```

完了時の結果0と、`sender.log`の`snapshot_committed`、閲覧側の受信日時更新を組み合わせて確認する。`Running`中の267009は実行中を示す。手動収集が必要な場合は、定期タスクと同時実行しないよう[手動コマンド](MANUAL_COMMAND.md)と[定期実行手順](SCHEDULED_EXECUTION.md)を参照する。

## 接続できない・更新されない場合

| 症状 | 確認箇所 |
| --- | --- |
| TCP接続できない | 同一LAN、固定IP、AndroidのWi-Fi、サーバー起動、端末分離設定 |
| 証明書警告 | CAの信頼、証明書期限、SANとURLのIP・ホスト名の一致 |
| ワークスペース一覧が空 | commit済みpublic Snapshotの有無、PC送信成功、`/workspaces`応答を確認 |
| データが取得できない | IDの誤り、PC送信の成功、対象workspaceのcommit済みSnapshot |
| 更新日時が古い | PCのログオン、タスク有効状態、実行結果、collector・senderログ |
| 再送失敗が続く | LAN復旧後、ログの分類からCA・認証・容量を確認。キューや台帳を削除しない |

ネットワーク切断後はLANを復帰させ、次回の定期再送・収集と画面更新を確認する。PC再起動後の復旧・無効化・解除は[SCHEDULED_EXECUTION.md](SCHEDULED_EXECUTION.md)。実データはHTTPSで扱い、外部向けポート転送は設定しない。モバイル回線等からの外部アクセスはPhase 9の対象とする。
