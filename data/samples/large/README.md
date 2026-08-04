# 大容量サンプル

`generate_large_sample.py`は、標準ライブラリだけで256KiBを超える架空メッセージを生成し、複数断片へ分割する。

```powershell
python data/samples/large/generate_large_sample.py --output data/runtime/large-sample
```

生成先はGit管理外の`data/runtime/`を指定する。各断片の本文は256KiB以下で、索引には元本文のUTF-8バイト数とSHA-256が記録される。断片を番号順に連結すると元本文へ戻る。
