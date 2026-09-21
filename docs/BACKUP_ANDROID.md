# Androidバックアップworker（Phase C）

Android側のrecovery ZIP作成とサーバー停止・再開を実装した。PCからのSSHオーケストレーション・両端complete判定はPhase D、週次登録はPhase Eで追加する。本workerの成功だけで両端一組の完了とはしない。fullは提供しない。

## 実行条件

- PCと同じコミットのリポジトリをAndroidの`~/CodexMobileDashboard/app`へ配置し、リポジトリルートから`python -m server.backup_worker`を使う。`tools/`も必要。
- `.git`を含まない配布環境ではManifestの`git_commit`を`null`、`git_dirty`を`true`とし、バックアップ・停止復旧に関係する主要コード5ファイルの`source_fingerprint`を記録する。
- collectorと再送を停止・待機済みで、バックアップ中に送信が発生しないこと。Phase Dまでは定期タスクを手動で一時停止するか、PC側のcollector mutexを保持する管理操作が必要。直接CLIのcollect/backfill/retryも実行しない。
- `--collector-paused`は上記条件の確認を表す。AndroidからWindows mutexを検証・取得するものではない。
- `start_server.sh`経由で起動したPIDを管理対象とする。別手段で起動してPIDがないサーバーでは使わない。手動でのサーバー起動操作も重ねない。
- 設定・秘密ファイル・current Snapshotが読めること。health検証用に同じCA公開証明書をAndroidへ配置する。CA秘密鍵は転送しない。
- Python 3.14の既定strict X.509検証で既存証明書のAuthority Key Identifier欠落が拒否されるため、workerのhealth確認はstrictフラグだけを解除する。CA署名・証明書名・期限の検証は維持する。

## 手動起動と状態照会

SSHまたはTermuxでリポジトリルートへ移動し、同じIDをPCとAndroidで使う。IPとパスは実環境へ置き換える。

```sh
cd "$HOME/CodexMobileDashboard/app"
python -m server.backup_worker start \
  --backup-id 20260911T010000Z-a31f82c4 \
  --config "$HOME/.config/codex-mobile-dashboard/server.ini" \
  --output "$HOME/CodexMobileDashboard/backups" \
  --ca "$HOME/.config/codex-mobile-dashboard/tls/ca.crt" \
  --health-url "https://192.0.2.10:8765/health" \
  --collector-paused
```

`started`は起動受付であり保存成功ではない。同じID・保存先で再度startしても`existing`となり重複起動しない。別IDの同時workerは`backup_busy`。SSHから切断してもworkerは独立セッションで続行する。

```sh
python -m server.backup_worker status \
  --backup-id 20260911T010000Z-a31f82c4 \
  --output "$HOME/CodexMobileDashboard/backups"
```

`state=finished`まで照会する。`android_result=success`と`restart_result=success`（元々停止なら`not_needed`）を確認する。`pair_state`は常に`incomplete`、`pc_result=not_run`。同じIDの再実行には新しいIDを使用し、既存ファイルを削除して上書きしない。

## 停止・保存・復旧

保存前に対象と空き容量を確認し、稼働状態を記録する。稼働中なら既存stop_server.shで正常停止後、対象を再選択する。未完成ファイル・欠損current・不正ID・リンクを検出した場合は失敗。ZIPはpartialへ保存し、CRC・Manifest・必須対象・SHA-256を検証して正式名へrenameする。

保存対象は実server.ini、設定が指すToken/証明書/秘密鍵、`~/.termux/boot`の通常ファイル、各workspaceのcurrent.jsonとその参照Snapshotだけ。古いSnapshot、staging、receipt、ログ、PID、lockを保存しない。保存先が必要なSnapshotと重なる場合は安全のため拒否する。

元々起動中なら、停止・保存の失敗やSIGTERM/SIGHUPでもfinallyで起動を試みる。元々停止なら起動しない。再起動は既存start_server.shを独立セッションで起動し、CA検証ありのHTTPS healthとPID管理対象の稼働を確認する。wake lockは解放しない。workerが復旧中に無視するSIGTERM/SIGHUPは子プロセス起動直前に既定値へ戻し、復旧したserver.pyが次回の正常停止を受け付ける状態を維持する。

SSH切断から独立するためstart_new_session、DEVNULL、pass_fdsによるflock引継ぎを使用する。ロックファイルは`~/.cache/codex-mobile-dashboard/backup.lock`。プロセス終了でOSロックが解放されるため、lockファイルを手動削除しない。

## 保存物と異常時

- `CodexMobileDashboard-Android-ID.zip`：Android単体ZIP。
- `ID/request.json`：起動要求（秘密値を含めない）。
- `ID/result.json`：atomic更新する状態・ZIP hash・保存結果・再起動結果。
- `ID/events.jsonl`：IDと処理段階のみ。設定本文・秘密値・例外本文を出さない。

umask 077で生成し、既存保存先も自分だけがアクセスできる権限にする。ZIP自体は暗号化しない。既存ZIPの自動削除はしない。

容量不足の事前検出ではserverを止めない。バックアップ失敗でも再起動を試みる。ZIP成功/restart失敗ならZIPは保持し、再起動失敗として対応する。health失敗時はserver.iniのパス・CA・SAN・ポートとPID状態を確認する。

SIGKILL、Termux全体の終了、電源断ではfinallyは実行できない。長時間starting/runningのままならworker/PID/healthを確認し、停止していれば既存起動手順で復旧する。状態ファイルだけを根拠に二重起動しない。標準出力を切り離しているため、終了判定はstatusで行う。

## 検証・一時復元

```sh
python -m server.backup_worker verify "$HOME/CodexMobileDashboard/backups/CodexMobileDashboard-Android-20260911T010000Z-a31f82c4.zip"
```

result.jsonのZIP SHA-256とも照合する。ZIP内Manifestのtargetsとfilesに元パス・論理パス・POSIX modeを保存する。一時ディレクトリへ展開し、current.jsonが示すSnapshotと内容を照合する。本番へは同じコミットと設定パスを用意し、停止中に手動で対応付けて復元する。鍵は600、bootスクリプトは記録した実行権に戻す。PID/lockを復元しない。統合復元は[復元手順](BACKUP_RESTORE.md)を参照。

Windows自動テストではcurrentのみの往復復元、容量・保存・停止・再開失敗、重複起動、独立起動の引数を検証する。実Termuxのflock・シグナル・SSH切断は次の手動受入で確認する。

1. PCの送信を止めて正常終了を確認し、元の稼働状態を記録する。
2. workerをstartし、SSHを切断する。再接続して同じIDをstatus照会する。
3. ZIP成功と元々稼働中の場合のHTTPS復帰を確認する。
4. 別のIDで容量不足、保存先書込不可、元々server停止のケースを確認する。正常ZIPを変更・削除しない。
5. 検証・一時復元後にPC送信を再開し、次回の送信・画面更新を確認する。

2026-09-22：Termux実機でAndroid単体recoveryバックアップを実行し、ZIP検証、resultとのSHA-256一致、8workspaceの保存、partial不在、サーバー再起動、全workspaceのCA検証付きHTTPS health・current ID・公開metadata.jsonを確認した。両端バックアップ、実HTTPS再送、週次本番登録は別の実機受入項目で追跡する。

2026-09-11：Android worker・PCバックアップ・既存起動停止の関連40テスト成功。文書リンクとgit diff --checkも成功。実機状態には変更を加えていない。
