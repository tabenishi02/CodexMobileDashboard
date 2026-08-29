# 手動実行コマンド

現時点の`tools.collector`は、永続未送信キューの確認と再送だけを行う。Codex JSONLの探索、抽出、表示用JSON生成をまとめて1回実行するcollector本体は未実装であり、このコマンドはそれらを実行しない。

実設定を次へ配置する。

```text
%LOCALAPPDATA%\CodexMobileDashboard\config\collector.ini
```

設定例`config/collector.example.ini`をコピーして、AndroidサーバーのHTTPS URL、CA公開証明書、Tokenファイルを実値へ設定する。TokenはINIへ書かない。

## キュー状態の確認

```powershell
cd C:\path\to\CodexMobileDashboard
python -m tools.collector --config "$env:LOCALAPPDATA\CodexMobileDashboard\config\collector.ini" queue-status
```

標準出力へ、未送信件数、sequence、ワークスペースID、Snapshot ID、ファイル数、最後の安全なエラー分類だけをJSONで出力する。本文、Token、HTTP応答本文は出力しない。

## 最古の1件を再送

```powershell
python -m tools.collector --config "$env:LOCALAPPDATA\CodexMobileDashboard\config\collector.ini" retry-queued
```

最古のキュー項目を1件だけ再送する。commit成功後にだけその項目を削除し、失敗時は項目を保持したまま終了コード2を返す。

## 全件を順に再送

```powershell
python -m tools.collector --config "$env:LOCALAPPDATA\CodexMobileDashboard\config\collector.ini" retry-queued --all
```

先頭から順に再送する。いずれかの項目で失敗すると停止し、残りのキューは変更しない。再送時は保存済みDelivery IDを再利用する。

## 終了コード

| 値 | 意味 |
| --- | --- |
| `0` | 状態確認または指定再送が成功 |
| `2` | 設定、キュー、通信、認証、証明書のいずれかで失敗 |

未送信キューの定期再送はWindows タスク スケジューラで登録できる。手順は[`SCHEDULED_EXECUTION.md`](SCHEDULED_EXECUTION.md)を参照する。JSONL収集・変換・新規Snapshot送信をまとめて行うcollector本体は後続タスクで追加する。
