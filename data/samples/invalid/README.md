# 意図的な異常サンプル

| ファイル | 想定する異常 |
|---|---|
| `invalid-json.json` | 末尾の閉じ括弧がなく、JSON構文解析に失敗する |
| `missing-required-field.json` | `schema_version`と`session_id`がない |
| `unsupported-schema.json` | MVPが対応しないメジャーバージョン`2.0` |
| `snapshot-mismatch.json` | 他の正常サンプルと`snapshot_id`が一致しない |
| `invalid-reference.json` | 存在しないメッセージIDを参照する |

これらは異常処理の試験用であり、正常データとして読み込んではならない。
