# Codex CLI推論の現状と増分化設計

## Phase 3.1完了状態

Phase 3.1は原指示との最終照合と全ツール回帰テストを完了している。通常収集は新規情報がなければCodex CLIを起動せず、新規完了turnだけを共有上限内で処理し、成功結果を入力SHA-256付き永続台帳へ1件ずつ原子的保存する。同一入力はcollector再起動・中断後も再利用し、上限到達後の未処理推論はpendingへ持ち越す。過去履歴の補完は明示的な`backfill-ai`へ分離し、同一turnの適格な3推論は1回の構造化推論へ統合する。

原指示の設定例にあった`new_completed_turns_only`、`persistent_cache`、`combined_inference`は無効化すると大量・反復推論へ戻る中核仕様のため公開設定にせず常時有効とした。`max_prompt_bytes`も公開設定にはせず、検証済みの内部上限として統合promptは64 KiB、個別promptは128 KiBを使用する。公開設定は運用上変更する意味がある`mode`、`max_calls_per_run`、`inference_ledger_file`に限定し、timeoutは120秒固定である。

## 現在の呼び出し経路

`collect-once`は検出した各workspaceのSnapshotを構築する。通常の`incremental`で変更要約・決定事項・次タスクの対象が同じ単一turnに揃う場合は、統合経路で`codex exec`を1回だけ起動する。統合対象外または統合失敗時は、次の個別経路を必要に応じて使用する。

| 用途 | 経路 | 現在の単位 |
| --- | --- | --- |
| 変更要約 | `generate_change_summaries()` → `ChangeSummaryCliRunner.generate()` | 明示要約がない完了ターンごと |
| 決定事項 | `extract_decisions()` → `DecisionCliRunner.extract()` | 曖昧参照を含む決定候補メッセージごと |
| 次タスク | `extract_next_task()` → `CodexCliRunner.infer()` | 明示的な次タスクがないworkspaceごとに最大1回 |

CLIは`--ephemeral`、`--sandbox read-only`、`--ignore-user-config`、`--ignore-rules`、`--output-schema`を指定する。モデル指定は渡さないため、Codex CLI側の既定モデルと利用枠を使う。1呼び出しのタイムアウトは120秒である。個別推論の入力上限は128 KiB、統合推論promptの入力上限は64 KiBである。collectorは実行開始時に`max_calls_per_run`を上限とする共有予算を1つ生成し、全workspace・変更要約・決定事項・次タスクで共有する。既定は3回である。

各個別経路は永続キャッシュを確認した後、CLI起動直前に共有予算を1枠消費する。決定事項も同じ予算を使用する。上限到達後はCLIを起動せず`inference_limit_reached`として延期する。統合実行オーケストレータも同じ予算コールバックを受け取り、上限到達時は統合CLIも個別fallbackも起動しない。これにより統合失敗後の個別fallbackを含めても、1回のcollector実行で許可される推論数は共有上限を超えない。

## 推論モード

`[ai_inference] mode`は`off`、`incremental`、`backfill`を受け付け、未指定時は`incremental`である。

- `off`: 3経路のCodex CLIを起動せず、規則ベース抽出とフォールバックを使う。
- `incremental`: 今回の増分収集で`completed`または`aborted`のterminal eventが追加され、全履歴から再構築した状態が`completed`または`failed`になった未rollbackのturnだけをCLI推論対象にする。開始、commentary、途中出力だけが追加されたturnは対象にせず、後続収集でterminal eventを受信した時点で対象にする。初回導入時に取り込む既存履歴は対象外である。
- `backfill`: `python -m tools.collector --config <config> backfill-ai`で明示実行する過去未処理turnの補完モード。共有上限を適用する。

通常収集は初回導入時の過去履歴を推論対象にせず、増分収集で新たにterminal eventを受信した完了turnだけを対象にする。変更要約、決定事項、次タスクは同じ完了turn集合を使用し、次タスクの入力メッセージもその集合内へ限定する。

## 初回履歴除外と過去推論の補完

`incremental`で初めて検出したsessionは、その収集開始時点ですでに存在する全レコードを履歴と読取位置へ保存する一方、既存の完了turnを変更要約・決定事項・次タスクのCLI推論対象へ渡さない。初回収集後にterminal eventが追加されたturnだけが、通常収集の新規推論対象になる。

初回履歴にも規則ベース抽出と永続台帳の復元は適用する。このため、既存台帳に完全一致する成功結果がある場合や、明示情報を規則で抽出できる場合は表示へ残る。反対に、次の条件がすべて当てはまると、初回Snapshotの決定事項・変更要約は空になり得る。

- 表示候補が初回収集前から存在する過去turnだけに含まれる。
- workspace・session・turn・入力SHA-256が一致する再利用可能な推論台帳entryがない。
- 決定事項または変更要約を規則ベースで確定できる明示情報がない。

これは収集失敗ではなく、初回導入で過去履歴を自動推論しないための正常な空状態である。必要な場合だけ、作業PCで次を明示実行する。

```powershell
cd C:\path\to\CodexMobileDashboard
python -m tools.collector --config "$env:LOCALAPPDATA\CodexMobileDashboard\config\collector.ini" backfill-ai --no-send
```

`backfill-ai`は実行時だけ`backfill`モードを使用し、INIの通常設定を変更しない。過去の適格な未処理turnを対象にし、成功済みの同一入力は永続台帳から再利用する。`max_calls_per_run`は全workspace・全推論経路で共有されるため、既定では1回につきCodex CLIを最大3回まで起動する。上限到達分は1回の実行では完了しないので、`collector.log`の`inference_run_metrics`で`limit_reached`、`successes`、`failures`を確認し、`limit_reached`が0になるまで必要に応じて同じコマンドを再実行する。失敗結果は台帳へ保存されないため、`failures`が0でない場合は原因を確認してから再実行する。

`--no-send`を付けた実行はローカルSnapshotと推論台帳だけを更新する。確認後、通常の`collect-once`を実行して完全一致結果を台帳から復元し、Androidサーバーへ送信する。すぐ送信する場合は`backfill-ai`から`--no-send`を外す。`backfill`は通常`incremental`のpendingを変更しない。

## 計画中：backfillの上限付き自動継続

現行の`backfill-ai`は1回ごとに共有上限を適用する単発コマンドであり、`limit_reached=0`まで自動反復しない。大量履歴を持つOSS利用環境でも手作業の回数を増やさず、安全な使用量上限を維持するため、`TASKS.md`のPhase 3.2で`--until-complete`と有限の`--max-runs`を最優先実装対象とする。

自動継続は、各反復の`max_calls_per_run`を変更せず、構造化された実行結果で上限到達・成功・失敗・残件・進捗を判定する。`limit_reached > 0`かつ進捗がある間だけ継続し、`limit_reached = 0`で完了する。推論失敗、保存失敗、進捗なし、利用者による中断、または全体の有限上限到達時は停止し、原子的に保存済みの台帳から再開可能にする。暗黙の無制限実行やログ本文の文字列解析は採用しない。

通常の定期`collect-once`は新規完了turnと永続pendingを段階的に処理するが、過去履歴の大量backfillは自動起動しない。backfillは利用量と処理時間を利用者が明示的に承認する独立操作として維持する。送信ありの自動継続では中間Snapshotを反復送信せず、補完完了後の完全Snapshotだけを送る設計とする。

## 既存キャッシュと台帳

各Runnerはプロセス内のプロンプトSHA-256キャッシュを持つが、collector再起動後は再利用できない。変更要約と次タスクのExtractorにはキャッシュエントリ型があるが、従来はcollectorが永続化していなかった。決定事項Runnerのキャッシュもインスタンス内だけである。

`[storage] inference_ledger_file`として`%LOCALAPPDATA%\CodexMobileDashboard\state\ai-inference-ledger.json`を追加し、トップレベル`version: 1`とentries配列を原子的置換する実装がある。entryはworkspace、session、turn、推論種別、入力SHA-256、生成日時、resultを持つ。

変更要約・個別の決定事項・次タスクは完全payloadを台帳へ保存し、collector再起動後は各経路の照合条件と入力SHA-256が一致する結果を復元してCodex CLIを再実行しない。統合経路はproposalを既存の完全な決定履歴へ適用し、`supersedes`・`superseded_by`・時系列・根拠IDを含む表示モデルを構築してから保存処理へ渡す。`combined_turn`台帳には変更要約・次タスクの完全payloadと、proposal・完全な決定履歴を持つschema version 2の決定事項payloadを同じ1 entryへ原子的保存する。

- 変更要約はversioned payloadを保存・復元し、同一turn内の全履歴から入力SHA-256が一致するentryを再利用する。
- 個別の決定事項推論は`schema_version: 2`でCLI proposalと、その時点の完全な決定履歴を保存する。決定履歴には`supersedes`・`superseded_by`、決定時刻と時刻の由来、根拠session・message IDを含める。
- 決定事項キャッシュはworkspaceを絞り込んだうえでsession・turn・入力SHA-256の完全一致を要求する。collector再起動時は、個別`decision`と完全な`combined_turn`を`generated_at`のUTC時系列（同時刻は台帳内の後方を優先）で比較して最新の完全な決定履歴を復元し、既処理の根拠messageを再適用せず新しい決定だけを差分反映する。
- 従来のproposalだけを持つ個別`schema_version: 1`は完全な決定履歴として復元せず、安全に再推論候補へ戻す。旧`combined_turn`内のproposal-only payloadは、入力SHA-256が完全一致する統合結果キャッシュとして互換利用するが、最新の完全な決定履歴としては復元しない。
- entryごとに推論種別が対応する`schema_version`を必須にし、旧形式・不完全payloadは安全に再推論候補へ戻す。個別の決定事項と新しい`combined_turn`内の決定事項はversion 2、変更要約・次タスクと`combined_turn`外枠はversion 1を使用する。
- 次タスクも同じversioned payloadで保存・復元する。

## 現行の台帳設計と制約

成功レコードは`schema_version`、`workspace_id`、`session_id`、`turn_id`、`inference_kind`、`input_sha256`、`generated_at`、payloadを持つ。プロンプト本文、Token、未マスク本文は保存しない。個別の決定事項と`combined_turn`はworkspace・session・turn・入力SHA-256の完全一致を要求する。変更要約はworkspace・session内からturn・入力SHA-256が一致するentryを選ぶ。次タスクはworkspace・sessionの最新候補を取得し、Extractorで入力SHA-256を照合するため、turnは保存されるが検索条件には使用しない。入力SHA-256が変われば再推論する。

台帳の`append`は1 entryごとにファイルを原子的置換し、失敗・不完全payloadは成功キャッシュとして保存または復元しない。個別の決定事項は成功コールバック内、`combined_turn`は統合成功直後、次タスクは単一推論の成功後に保存する。変更要約も、CLI生成・検証と完全なcache entry構築が成功した直後に成功コールバックから1件を原子的保存するため、`generate_change_summaries`全体が返る前に中断しても保存済みの成功結果を維持できる。返却後のcache entry一括保存は行わず、明示要約・キャッシュヒット・失敗結果では成功コールバックを呼ばない。通常`incremental`の新規完了turn、既存pendingの再処理、`backfill-ai`が指定する`backfill`モードはいずれも同じ成功コールバックと完全payload保存経路を使用する。

変更要約payloadは表題、短文、詳細、highlights、verification、confidence、状態、根拠IDを持つ。個別および新しい`combined_turn`内の決定事項payloadは、CLI proposalに加えて、最終決定のID、状態、内容、理由、`topic_key`、`supersedes`、`superseded_by`、決定時刻、時刻の由来、根拠session・message IDを持つ。次タスクpayloadは本文、状態、origin、confidence、理由と根拠IDを持つ。個別と統合の完全履歴は`generated_at`をUTCへ正規化して比較し、最新のentryを復元する。同時刻の場合は台帳内で後にあるentryを採用する。

## これまでの実装順序

1. 変更要約保存経路の引数不一致を修正し、台帳専用テストを追加する。
2. 変更要約の完全payload保存・再起動後復元・ハッシュ照合を受入可能にする。
3. 決定事項の置換関係を含む保存・復元・ハッシュ照合を実装する。
4. 次タスクの保存・復元・ハッシュ照合を実装する。
5. 新規完了ターン判定、共有呼び出し上限、backfill専用コマンドを接続する。
6. 同一ターンの推論統合、入力削減、不要推論スキップを行う。

ログには実行数、キャッシュヒット、スキップ、上限到達、入力サイズ、成功・失敗だけを記録し、プロンプト全文や秘密情報を出力しない。

collector終了時には、実行したCLI回数と上限到達で次回へ持ち越した推論件数をログへ記録する。

過去未処理turnの補完はpython -m tools.collector --config <config> backfill-aiで明示実行する。max_calls_per_runの共有上限を適用する。

## 統合推論の対象条件

統合推論は、完了済みで今回のincremental対象となる同一turnに限る。入力はすべてマスク済みで、次タスク候補があり、turn外の文脈を必要としないことを必須とする。条件外、または統合推論の失敗時は既存の個別推論経路へfallbackする。
## 統合JSON schemaとCLI runner

`CombinedTurnCliRunner`は、変更要約（`summary`）、決定事項配列（`decisions`）、次タスク（`next_task`）を必須とする単一のJSON schemaを`codex exec`へ渡す。各値は既存の個別推論に変換できる最小の構造（表題・内容・理由・確信度など）を検証する。

runner自体は`--ephemeral`、`--sandbox read-only`、`--ignore-user-config`、`--ignore-rules`、`--output-schema`を指定して1回だけCLIを起動する。CLI失敗、JSON構文不正、schemaと異なる応答は成功結果として扱わない。`collector_runtime`は、3経路の対象が同じ単一完了turnに揃う場合に統合runnerを1回だけ呼び、成功時は個別経路を起動しない。非適格、統合runner失敗、または統合台帳の保存失敗時は追加の統合CLIを起動せず、既存の個別経路へ委譲する。
## 統合結果の保存形式

統合結果は既存の`ChangeSummary`、決定事項推論proposal、`NextTask`へ変換する。決定proposalは既存の完全履歴へ適用し、置換関係・時系列・根拠IDを含む`decision_history`として変換結果へ保持する。その後、変換結果を表示モデルと保存処理へ渡す。`combined_turn`台帳entryの`payload`には、変更要約と次タスクの完全なversioned payload、およびproposalと`decision_history`を含むschema version 2の決定事項payloadを、それぞれ`change_summary`、`decision`、`next_task`として格納する。proposalがあるのに完全履歴がない変換結果は保存しない。プロンプト本文や未マスク入力も保存しない。

統合結果は`combined_turn` 1 entryとして既存の原子的`append`で保存する。置換に失敗した場合は、直前の台帳を保持し一時ファイルを残さない。復元時はworkspace・session・turn・入力SHA-256がすべて一致する完全な`combined_turn`だけを採用する。不完全な統合entry、またはSHA-256不一致は復元せず、従来の個別台帳entryを安全に利用する。
## 統合推論の設定・制約・運用

統合推論専用の設定キーはない。`[ai_inference] mode`と`max_calls_per_run`、`[storage] inference_ledger_file`は既存の推論と共通であり、設定例では`incremental`、`3`、`%LOCALAPPDATA%\CodexMobileDashboard\state\ai-inference-ledger.json`を使用する。`max_calls_per_run`は0以上の整数である。統合オーケストレータは`collector_runtime`から個別経路と同じ共有予算を受け取り、統合CLIの起動時に1枠を消費する。

適格なのは、今回のincremental対象で完了済みの同一turnだけである。すべての入力がマスク済みで、次タスク候補があり、turn外の文脈を必要としないことが必要である。適格なら統合runnerは1回だけ実行し、成功時は個別経路を起動しない。非適格、CLI失敗、JSON/schema不正、台帳payload不完全、または入力SHA-256不一致なら、個別経路へfallbackする。

運用時に推論本文・Token・未マスク本文を台帳やログへ保存してはならない。`combined_turn`は3種のversioned payloadを1 entryとして原子的保存し、復元時はworkspace・session・turn・入力SHA-256の完全一致を要求する。通常の`incremental`収集では完全一致キャッシュをCLIより先に確認し、復元できた場合も個別経路を起動しない。統合proposalから構築した完全履歴も決定payloadへ保存し、個別`decision`台帳と`generated_at`の時系列で比較して最新履歴を復元する。`off`、`backfill`、複数turn、3経路の対象turn不一致は統合対象外であり、既存の個別経路を使用する。
## 統合promptの入力範囲と上限

統合promptには対象turnのマスク済みメッセージだけを入れる。Gitについては収集状態、branch、clean状態、変更ファイルのパス・旧パス・状態だけを含め、diff本文、ファイル本文、コミットメッセージは含めない。ファイル参照は採用済みの対象メッセージを根拠にするものだけを含める。

統合promptの上限は64 KiBである。メッセージを優先し、Gitファイルメタデータとファイル参照は上限内に収まる分だけを追加する。超過時はメッセージ本文を安全に切り詰め、それ以上のメタデータは省略する。未マスクの対象メッセージが含まれる場合はpromptを生成せず個別fallbackを選ぶ。

通常の`incremental` collectorもこの限定prompt builderを使用する。collector統合テストでは、対象turn本文とその根拠ファイル参照が含まれ、対象外turn本文と対象外メッセージだけを根拠とするファイル参照が含まれず、生成promptがUTF-8で64 KiB以内になることを確認する。
## 規則ベースでのAI推論スキップ

統合実行では、Git変更も対象メッセージ由来のファイル参照もなく、対象turnの全メッセージが「了解しました」「確認します」等の明確な短文・非変更応答だけである場合に限り、`rule_based_sufficient`としてAI推論をスキップする。この場合は統合CLIも個別fallbackも起動しない。

通常の`incremental` collectorはこの判定を統合オーケストレータの前段で使用する。Gitの`clean`が明示的に`true`で、変更ファイルがなく、対象メッセージを根拠とするファイル参照もない場合だけスキップを許可する。Git状態が不明な場合もスキップしない。スキップ時は統合CLIと個別3経路を起動せず、既存の決定履歴と次タスクキャッシュを表示へ引き継ぐ。

変更を示す語を含む短文、未認識の短文、Git変更、または対象ファイル参照が一つでもある場合はスキップしない。collector統合テストでは、既知の短文・非変更turnがCLI 0回になり、`実装しました。`を含む変更候補turnが統合CLIへ進むことを確認する。
## 推論メトリクスログ

`inference_metric`は`kind`、`event`、`input_bytes`だけを記録する。kindは`change_summary`、`decision`、`next_task`、`combined_turn`、eventは`execution`、`cache_hit`、`skipped`、`limit_reached`、`success`、`failure`、`fallback`の固定値だけを許可する。各経路はキャッシュ、スキップ、上限、CLI実行、成功・失敗、該当する場合のfallbackを同じ形式で記録する。

`inference_run_metrics`はcollector実行単位で各event件数、実際にCLIへ渡した入力バイト数の合計、保存後のpending残件数を記録する。入力バイト数は`execution`時だけ加算し、同じ入力に対するsuccess・failureでは二重加算しない。従来の`inference_run_completed`と`inference_pending_completed`では共有上限の許可回数・延期回数・上限値、持ち越し・追加・残件数を確認できる。

プロンプト本文、メッセージ本文、Token、入力SHA-256、workspace・session・turn ID、ファイルパスはメトリクスログに出力しない。kindとeventは固定値検証に失敗した値を拒否し、識別子やパスをラベルへ混入させない。専用テストでは集計値とpending残件数を確認し、これらの秘密情報・識別情報がログに含まれないことを検証する。
## 推論回帰テスト

回帰テストは、incrementalで新規完了turnだけを3経路へ渡し、追加途中では推論を許可せずterminal event受信後の収集で許可すること、台帳復元による再起動後のキャッシュ利用、台帳ファイル置換失敗時の直前データ保持、collector state保存中断後のpending復帰、共有上限、`off`、`backfill-ai`、入力が同一の場合のキャッシュ利用と入力変更時の再推論を確認する。`off`と上限到達ではCodex CLIを起動しない。個別決定と完全な統合決定を時系列で選択する台帳テストに加え、旧proposal-only統合payloadを完全一致時だけ統合結果キャッシュとして利用し、完全履歴には採用しない互換テストも実装済みである。統合決定は追加データなしの次回収集でも表示に残り、collector再起動後の後続決定で`supersedes`・`superseded_by`が相互に維持されることを連続実行テストで確認する。変更要約の複数turn処理では、1件目のCLI成功後に2件目で中断しても1件目が即時保存され、collector再起動後は2件目だけをCLIへ渡すことを確認する。

collector統合テストでは、新規完了turnを処理した次の収集に追加データがなければ全経路のCLI実行数が0になることを確認する。さらに初回履歴除外、共有上限3回、pendingへの持ち越し、状態保存中断、再起動後の再処理を連続したcollector実行として検証する。変更要約・決定事項・次タスク・`combined_turn`は、種別別テストと統合テストを合わせて、同一入力の再利用、入力変更時の再推論、失敗結果の永続台帳への非保存を確認する。統合推論の初回結果、永続キャッシュ復元結果、個別fallback結果から生成するSnapshot用JSONと表示モデルが同じ内容を維持することも比較する。

## 通常収集のpending管理

通常の`incremental`収集でCLI失敗、入力マスク拒否、または共有上限到達となった変更要約・決定事項・次タスクは、`state_file`の`pending_inferences`へworkspace・session・turn・推論種別単位で保存する。履歴カーソルとpendingは同じcollector stateの原子的置換で確定するため、state保存の中断時は更新前のカーソルとpendingが維持される。

次回の通常`incremental`収集では、既存pendingを今回新たに完了したturnより先に処理する。処理中に見つかった新規完了turnはpendingへ追加して次回へ回す。各経路で成功した項目だけをpendingから除外し、CLI失敗、入力マスク拒否、再度の上限到達、および処理されなかった項目は残す。`off`と`backfill`は通常収集のpendingを変更しない。

collector終了時の`inference_pending_completed`ログは、実行開始時の持ち越し件数`carried`、今回追加した件数`added`、保存後の残件数`remaining`だけを記録する。workspace・session・turn ID、入力SHA-256、プロンプト本文などは記録しない。
