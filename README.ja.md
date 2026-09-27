# 三菱／KEYENCE PLC対応 MC 3Eバイナリクライアント

[English](README.md)

`mc_3e.py`は、MCプロトコル／SLMPのQnA互換3Eフレーム・バイナリ形式を
TCPで送受信する、外部パッケージ不要のPythonクライアント兼CLIツールです。

> [!WARNING]
> MCプロトコル通信は暗号化も認証も行いません。PLCポートをインターネットへ
> 直接公開しないでください。信頼できる隔離制御ネットワーク、または適切に保護
> されたVPN内で使用してください。書込みは設備の動作を変える可能性があるため、
> 対象デバイス、インターロック、PLCモード、オンライン書込み設定を確認してください。

次の機能を三菱PLCとKEYENCE PLCで共通利用できます。

- 三菱MELSECの`D`ワードデバイスと`M`ビットデバイス
- KEYENCE KVの`DM`ワードデバイスと`MR`ビットデバイス
- ワード／ビットの一括読出し・書込み
- CPU型式、動作状態、および一部の診断情報の取得
- 読出し専用コマンドによるメーカー／TCPポート自動判定
- 日付別の実行ログ自動保存
- 再利用可能なインスタンスメソッドとstatic method

## 必要環境

- Python 3.6以降
- QnA互換MC 3EバイナリTCP通信を有効にしたPLC
- PLCに設定されたTCPポートへ接続できるネットワーク

必要なファイルは`mc_3e.py`、`README.md`、`README.ja.md`だけです。
プログラムはPython標準ライブラリだけを使用します。

## クイックスタート

メーカーとポートを自動判定し、取得可能なCPU情報をまとめて表示します。

```console
python mc_3e.py status --ip 192.168.0.10
```

KEYENCE KV-7500での出力例です。

```text
Detected PLC: vendor=keyence, model=V7500 (0x0037), port=5000
CPU model: V7500 (code 0x0037)
CPU mode = RUN (CR2007=ON)
CR2012 arithmetic error = OFF; CR3500 alarm = OFF
CM5150..CM5176 = 0x0000 ...
CR2002..CR2007 = ON OFF OFF OFF ON ON
Log file: ...\output\20260927_log.txt
```

自動判定では、候補ポート`5000`、`5002`、`1025`、`1026`、`4999`、`5010`へ
読出し専用のCPU型式取得コマンド`0101/0000`を送信します。この候補は、PLC案件で
よく使われるポートと開発時に確認した設定を基にした便宜的な一覧であり、MCプロトコル
仕様で予約されたポートではありません。それ以外のポートを使用する場合は`--port`を
指定してください。現在はCPU型式名の先頭がKEYENCEの`V`／`KV`、三菱の
`Q`／`L`／`R`／`FX`／`A`である機種を判定します。

## コマンドラインで使用

### ワードデバイス

```console
# KEYENCE：DM60000～DM60010を読出し
python mc_3e.py read DM 60000 11 --vendor keyence --ip 192.168.0.10 --port 5000

# KEYENCE：DM60002へ12345を書込み
python mc_3e.py write DM 60002 12345 --vendor keyence --ip 192.168.0.10 --port 5000

# 三菱：D100～D102を読出し
python mc_3e.py read D 100 3 --vendor mitsubishi --ip 192.168.0.20 --port 5002

# 三菱：2ワードを書込み
python mc_3e.py write D 100 10 20 --vendor mitsubishi --ip 192.168.0.20 --port 5002
```

ワード値は0～65535の16ビット符号なし整数です。書込み値には10進数と、
`0x1234`のようなPython形式のプレフィックス付き数値を指定できます。

### ビットデバイス

```console
# KEYENCE
python mc_3e.py readbit MR 60000 2 --vendor keyence --ip 192.168.0.10 --port 5000
python mc_3e.py writebit MR 60000 1 --vendor keyence --ip 192.168.0.10 --port 5000

# 三菱
python mc_3e.py readbit M 100 8 --vendor mitsubishi --ip 192.168.0.20 --port 5002
python mc_3e.py writebit M 100 1 0 1 --vendor mitsubishi --ip 192.168.0.20 --port 5002
```

KEYENCEの`MR`はワード・ビット表記で、末尾2桁には`00`～`15`を指定します。
たとえば`MR60015`の次は`MR60100`であり、`MR60016`は無効です。
三菱の`M`には連続したビットアドレスを指定します。

### CPU情報

CPU情報のサブコマンドはすべて読出し専用です。

```console
python mc_3e.py model   --ip 192.168.0.10
python mc_3e.py status  --ip 192.168.0.10
python mc_3e.py errors  --ip 192.168.0.10
python mc_3e.py summary --ip 192.168.0.10
python mc_3e.py info    --ip 192.168.0.10
```

`status`と`info`は、CPU型式、状態、診断情報、システム概要をまとめて表示します。

| コマンド | 三菱 | KEYENCE |
| --- | --- | --- |
| `model` | `0101/0000`によるCPU型式名・コード | `0101/0000`によるCPU型式名・コード |
| `status` / `info` | 型式、SD200～SD203、SM0／SM1、SD0 | 型式、CR2002～CR2007、CR2012、CR3500、CM5150～CM5176 |
| `errors` | SM0、SM1、SD0 | CR2012、CR3500、CM5150～CM5176 |
| `summary` | SD200～SD203とCPU状態の解釈 | CR2002～CR2007とCPUモードの解釈 |

三菱の状態解釈は、現在Qシリーズ互換の特殊レジスタを対象としています。
特殊レジスタの意味はCPUシリーズによって異なる可能性があります。

各コマンドの詳細は次のとおりです。

- 三菱の`errors`は、SM0／SM1を診断フラグ、SD0を最新の診断エラーコードとして
  読み出します。
- 三菱の`summary`はSD200～SD203の生値を返し、SD200からCPUスイッチ、SD203から
  動作状態とSTOP／PAUSE要因を解釈します。SD201とSD202は機種依存の生値として
  そのまま表示します。
- KEYENCEの`errors`は、CR2012（演算実行エラー）、CR3500（アラーム）、
  CM5150～CM5176（演算エラー詳細領域）を読み出します。
- KEYENCEの`summary`はCR2002～CR2007を読み出し、CR2007からRUN／PROGRAMを
  判定します。

KEYENCEのCRとCMは、それぞれMCの特殊リレー（`SM`）と特殊レジスタ（`SD`）の
デバイスコードを使ってアクセスします。通常のDM／MRアクセスほど一般に知られて
いない対応なので、対象KVシリーズのマニュアルで使用アドレスを確認してください。

### オプション

接続オプションはサブコマンドの前後どちらにも記述できます。

```console
python mc_3e.py --vendor keyence --ip 192.168.0.10 --port 5000 read DM 60000 1
python mc_3e.py read DM 60000 1 --vendor keyence --ip 192.168.0.10 --port 5000
```

| オプション | 内容 |
| --- | --- |
| `--ip ADDRESS` | PLCのIPv4／ホストアドレス。必須 |
| `--vendor auto\|keyence\|mitsubishi` | メーカー指定。既定値は`auto` |
| `--port PORT` | MCプロトコルTCPポート。省略時は自動判定 |
| `--timeout SECONDS` | ソケットタイムアウト。既定値は3秒 |

## 実行ログの自動保存

CLIを実行するたびに、`mc_3e.py`と同じ場所へ`output`フォルダーを作成し、
コマンド、標準出力／エラー出力、開始・終了日時、終了コードをUTF-8の日別ログへ
追記します。

```text
output/20260927_log.txt
```

ファイル名は`yyyyMMdd_log.txt`形式です。同じ日の既存ログは消去せず追記します。
ログの管理ラベルとコマンド出力は英語です。

## Python API

同じPLCへ繰り返しアクセスする場合はインスタンスを作成します。

```python
from mc_3e import MC3EClient

kv = MC3EClient("192.168.0.10", "keyence", 5000, timeout=3.0)
words = kv.read("DM", 60000, 11)      # list[int]
bits = kv.read_bits("MR", 60000, 2)  # list[bool]
kv.write("DM", 60002, [12345])
kv.write_bits("MR", 60000, [1, 0])
cpu = kv.read_cpu_information()

plc = MC3EClient("192.168.0.20", "mitsubishi", 5002)
words = plc.read("D", 100, 3)
bits = plc.read_bits("M", 100, 8)
```

インスタンスを作らずに通信できるstatic methodも用意しています。

```python
from mc_3e import MC3EClient

words = MC3EClient.read_words_at(
    "192.168.0.10", "keyence", 5000, "DM", 60000, 11
)
MC3EClient.write_words_at(
    "192.168.0.10", "keyence", 5000, "DM", 60002, [12345]
)
bits = MC3EClient.read_bits_at(
    "192.168.0.20", "mitsubishi", 5002, "M", 100, 8
)
MC3EClient.write_bits_at(
    "192.168.0.20", "mitsubishi", 5002, "M", 100, [1, 0, 1]
)

detected = MC3EClient.detect_plc_at("192.168.0.10")
```

## 対応範囲と制限

- TCP上のQnA互換3Eバイナリフレームのみ
- 経路はネットワーク`0`、PC番号`FF`、I/O番号`03FF`、局番`0`に固定
- 本クライアントでは1回の操作を最大256点に制限。この値はサンプルとして安全側に
  設けた意図的な上限であり、MCプロトコルや各PLCの最大点数ではない
- 通常デバイスは三菱`D`／`M`、KEYENCE `DM`／`MR`に対応
- ワード値は16ビット符号なし整数のみ
- ASCII形式、UDP、1E／4Eフレーム、他局への中継、その他デバイスは未対応
- 利用可能なアドレスとオンライン書込み許可はPLCの機種・設定に依存
- CPU情報は一部診断情報の概要であり、拡張ユニット一覧や完全なエラー履歴ではない

## 安全性とセキュリティ

書込みコマンドはPLCメモリを変更し、接続された設備へ影響する可能性があります。
未使用であることを確認したテスト用デバイスだけを使用し、必要に応じて元の値へ
戻してください。書込み前にPLCプロジェクト、動作モード、インターロック、
オンライン書込み許可設定を確認してください。

このスクリプトのMCプロトコルTCP通信は暗号化も認証も行いません。信頼できる
隔離された産業用ネットワーク、または適切に保護されたVPN内だけで使用し、
PLCポートをインターネットへ直接公開しないでください。

## 3E応答ヘッダを厳密に確認する理由

本クライアントは3Eバイナリ応答のサブヘッダ`D0 00`を必須とします。4E応答や別の
フレーム形式はヘッダ構造が異なるため、3E応答として解析してはいけません。そのため、
未知のサブヘッダを受け入れず、明示的なプロトコルエラーを返します。別形式へ対応する
場合は、専用のパーサーとして実装する必要があります。

## 実機確認

次の機器で動作を確認しています。

- KEYENCE KV-7500：型式／状態／診断情報、DM／MRの読出し・書込み
- 三菱Q00UJCPU：型式／状態／診断情報、D／Mの読出し・書込み

他機種での互換性は、その機種のMCプロトコル対応、デバイスマップ、Ethernet設定に
依存します。

## 参考資料

- [MELSEC Communication Protocol Reference Manual](https://dl.mitsubishielectric.com/dl/fa/document/manual/plc/sh080008/sh080008ab.pdf)
- [MELSEC-Q/L Ethernet Interface User's Manual](https://dl.mitsubishielectric.com/dl/fa/document/manual/plc/sh080811eng/sh080811engy.pdf)
- [KEYENCE PLCマニュアル](https://www.keyence.com/support/user/controls/plc/manual/building/)

三菱電機、MELSEC、KEYENCE、および記載されている製品名は、各社の商標または
登録商標です。本プロジェクトは各メーカーによる公式プロジェクトではありません。
