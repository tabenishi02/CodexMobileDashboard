# PC・Androidバックアップ統合（Phase D）

PCがcollector mutexを保持し、SSHでAndroid workerを起動・照会する。PC/AndroidのZIPはそれぞれの端末へ保存し、転送・暗号化・自動削除は行わない。週次タスク登録は[BACKUP_SCHEDULE.md](BACKUP_SCHEDULE.md)を参照する。

## 準備

1. 両端を同じコミットへ更新する。Android workerのpreflightコマンドが必要。
2. [PC設定](BACKUP_PC.md)の外部backup.iniに、`config/backup.example.ini`のandroidセクションを追加する。
3. PCのSSH configに接続先alias、User、Port、IdentityFileを設定し、鍵認証とホスト鍵の照合を済ませる。コマンドのssh_hostはalias名で、任意のSSHオプションを受け付けない。
4. repository/config/output/caはAndroid上の絶対パスにする。`~`は使わない。health_urlは証明書SANに一致するHTTPS `/health`。CAは公開証明書のみ。
5. collectorと同じWindowsユーザー・同一ログオンセッションで実行する。直接CLIのcollect/backfill/retry、Android手動起動操作を重ねない。

`wait_seconds`はPCのZIP作成後にAndroidの終了結果を待つ上限（既定1800秒）。SSH要求ごとの上限は30秒、接続待ちは10秒。Androidは待機上限後も独立して続行する。

## 実行

```powershell
python -m tools.backup_pair --config "$env:LOCALAPPDATA\CodexMobileDashboard\config\backup.ini"
```

任意で`--backup-id 20260911T010000Z-a31f82c4`を指定できる。通常は省略し自動発行する。実行すると実データのバックアップとAndroidサーバーの停止・再開が発生する。既存Scheduled TaskをDisable/Enableしない。PC単体コマンド`tools.backup`は引き続き利用可能。

## 処理と結果

- mutex取得後にPC対象・キュー・タスク定義・容量を確認する。
- SSHのBatchMode/StrictHostKeyCheckingを有効にし、remote preflightで設定・対象・容量・CA読取・PID状態を確認する。失敗時はworkerを起動しない。
- 共通IDのworkerを1回だけ起動する。起動応答喪失時は再起動せず同じIDを照会する。
- PC ZIPを作成する。PCが失敗してもAndroidの終了結果を待つ。
- statusのID、状態、ZIP名、SHA-256を検証し、両端結果を`ID.pair.json`へatomic記録する。SSH経由でZIP本体は取得しないため、Android ZIPの検証結果は認証したworkerの結果を信頼する。
- 正常経路ではAndroidの終了・復旧結果を得るまでmutexを保持する。タイムアウト時はunknown/incompleteとして解放し、独立workerの処理を妨げない。

| 状態 | pair_state | recovery_state | 終了コード |
| --- | --- | --- | --- |
| 両ZIP成功・元の稼働状態へ復帰 | complete | restored | 0 |
| 両ZIP成功・再起動失敗 | complete | failed | 2 |
| 片側失敗 | incomplete | 復旧結果による | 2 |
| 通信断・待機上限 | incomplete | unknown | 2 |
| 停止前のpreflight失敗 | incomplete | unchanged | 2 |

保存・ログ記録に失敗した場合も全体成功にはしない。completeは両ZIPが成功したことだけを表し、通常運用へ復帰したことは別判定。`backup-pair.log`はIDと処理段階を記録し、SSH stderr・設定本文・Tokenを転記しない。pair.jsonには両ZIPの名前・hashと失敗分類を保存する。

## 通信断と復旧確認

上限到達後は[Android状態照会](BACKUP_ANDROID.md)で同じIDを確認する。unknownの処理を新しいバックアップとして重ねて開始しない。workerがfinishedになり、元々起動中ならHTTPSが戻ったことを確認する。PC側はmutex解放後に次回collectorで復帰し、停止中なら既存のキュー保持・再送動作となる。

終了済みpair.jsonを自動的にcompleteへ修正する再照合コマンドはまだない。timeout後の結果はAndroidのresult.jsonとPCのresult.jsonを同じIDで手動照合する。整合したバックアップとして使う前に両ZIPのhashと内部検証を確認する。

## 検証

自動テストはSSH代替応答と一時PCデータを使用し、共通ID、応答喪失後の照会、片側失敗、再起動失敗、待機上限、mutex保持、安全なSSH引数を検証する。本番SSH・Android停止再開は実行していない。

実機受入では通常成功→SSH切断後の同一ID照会→容量不足で停止しないこと→PC失敗後のAndroid復帰を順に確認する。ZIP復元と週次運用の受入はPhase E/Fで継続する。

2026-09-11：PC・Android worker・両端統合の関連42テスト成功。文書リンクとgit diff --checkを確認。

2026-09-28：実機で通常成功、SSH切断後の同一ID照会、容量不足、PC保存失敗、Android保存失敗を確認した。失敗時もAndroidサーバーを復旧し、既存の正常ZIPを維持した。正常系では両ZIPのhash・内部検証・一時展開まで確認した。
