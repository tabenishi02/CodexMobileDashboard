# 検証用Android端末 Termux動作環境

## 確認日

2026-08-04

## 確認結果

| 項目 | 値 | 判定 |
| --- | --- | --- |
| 端末 | 検証用Android端末 | 利用可能 |
| Termux | 0.118.3 | 利用可能 |
| Android | 12 | 利用可能 |
| CPUアーキテクチャ | `aarch64` | 利用可能 |
| Python | 3.14.6 | 利用可能 |
| Git | 2.55.0 | 利用可能 |
| ファイルシステム容量 | 109GB | 十分 |
| 使用量 | 29GB、27% | 問題なし |
| 空き容量 | 79GB | 十分 |
| パッケージ配布元 | `https://packages-cf.termux.dev/apt.termux-main/ stable main` | 利用可能 |

## パス

Termuxホーム：

```text
/data/data/com.termux/files/home
```

ダッシュボード配置先：

```text
/data/data/com.termux/files/home/CodexMobileDashboard
```

Termux上では次の形式を使用する。

```text
$HOME/CodexMobileDashboard
```

共有ストレージではなくTermuxのアプリ専用領域に配置されており、サーバー実行環境として適切である。

## Python互換性

作業用PCではPython 3.10.6、検証用Android端末ではPython 3.14.6を使用する。

実装では次を守る。

- Python 3.10以上で利用できる構文と標準ライブラリを使用する。
- Python 3.11以降で追加された機能に依存しない。
- PCのPython 3.10.6とAndroid端末のPython 3.14.6の両方でテストする。
- Python 3.14で削除された古い非推奨APIに依存しない。
- 外部Pythonライブラリは使用しない。

## Git

Git 2.55.0が利用できることを確認した。Android端末側の配置先ディレクトリがGitリポジトリであることはまだ確認していない。配置方法を決定した後に、必要に応じてcloneまたは同期を行う。

## ストレージ

空き容量は79GBであり、静的ファイル、JSON、直近7日分のローテーションログを保存するMVP用途には十分である。

データとログには上限を設定し、空き容量が十分でも無制限には保存しない。

## 今後の端末設定

サーバーの常時稼働を開始する前に、次を確認する。

- TermuxをAndroid端末の「スリープ状態にしないアプリ」へ追加する。
- Termux:Bootを使用する場合はTermux本体と同じ配布元から導入する。
- 手動起動とLAN内通信を確認してから自動起動を設定する。
- 必要な場合だけ`termux-wake-lock`を使用する。

## 結論

検証用Android端末のTermux環境は、Codex Mobile DashboardのMVPサーバーとして利用可能である。
