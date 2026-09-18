# MultiFirm PC ツール

M5StopWatch に複数のファームウェアを共存させるための書き込みツールです。
ホスト (ランチャー) を `ota_0`、ゲスト (アプリ) を `ota_1`〜`ota_3` に置く、MultiFirm v1 の固定レイアウト ([partitions.multifirm.csv](../docs/partitions.multifirm.csv)) だけを扱います。

2026-09-19 に実機 (ESP32-S3 / 16 MiB) で、新配置の導入、ゲストの書き込み、復旧、ホストの書き換えまで確認しています。

## 準備

Python 3.11 と **esptool 4.12.0** が必要です。

```powershell
py -3.11 -m venv tools\.venv
tools\.venv\Scripts\python.exe -m pip install -r tools\requirements.txt
```

`multifirm.ps1` は次の順に Python を探します。

1. `$env:MULTIFIRM_PYTHON` (esptool の版が違っても使います。警告を出します)
2. `tools\.venv\Scripts\python.exe` (esptool 4.12.0 の場合のみ)
3. `..\M5StopWatch-UserDemo\.tools\idf-tools\python_env\*\Scripts\python.exe` (同上)

実機に接続するコマンドは、esptool 4.12.0 以外では実行を拒否します。どうしても別の版で動かす場合は `--allow-untested-esptool` を付けます。
`inspect` と、`--check-device` / `--execute` を付けない計画の表示は、`python tools\multifirm.py ...` で直接起動すれば esptool がなくても動きます (`multifirm.ps1` は esptool 4.12.0 のある Python が見つからないと起動しません)。

## コマンド一覧

```text
multifirm.ps1 inspect <firmware.bin> [--role guest|host]
multifirm.ps1 status --port COMxx [--verify]
multifirm.ps1 backup --port COMxx [--out <dir>]
multifirm.ps1 install --slot {1,2,3} <firmware.bin> [--name "表示名"] [--port COMxx (--check-device | --execute)]
multifirm.ps1 install-host <host.bin> [--backup <file>] [--port COMxx (--check-device | --execute)]
multifirm.ps1 recover [--port COMxx (--check-device | --execute)]
multifirm.ps1 initial --host-build <ESP-IDF build dir> [--backup <file>] [--port COMxx (--check-device | --execute)]
```

| コマンド | 用途 |
|---|---|
| `inspect` | `.bin` を実機なしで検査する |
| `status` | 実機のレイアウト、起動先、各スロットの中身と表示名を見る |
| `backup` | 16 MiB 全体を保存する |
| `install` | ゲストを `ota_1`〜`ota_3` に書き込む |
| `install-host` | ホストを `ota_0` に書き込む |
| `recover` | ゲストから戻れなくなったとき、ホストを起動させる |
| `initial` | 旧配置の機体を MultiFirm v1 の配置にする |

## よくある使い方

### ゲストを書き込む

ゲストのリポジトリで:

```powershell
pio run
# 1. 計画を表示 (実機に接続しない)
..\M5StopWatch-MultiFirm\tools\multifirm.ps1 install --slot 2 .pio\build\m5stopwatch\firmware.bin --port COM11
# 2. 実機を読み取って確認 (書き込まない)
..\M5StopWatch-MultiFirm\tools\multifirm.ps1 install --slot 2 .pio\build\m5stopwatch\firmware.bin --port COM11 --check-device
# 3. 書き込む
..\M5StopWatch-MultiFirm\tools\multifirm.ps1 install --slot 2 .pio\build\m5stopwatch\firmware.bin --port COM11 --execute
```

書き込み先のスロットに入っていたアプリは上書きされます。ほかのスロット、設定 (NVS)、起動先 (otadata) は変更しません。
書き込み後はホストが起動するので、ランチャーから選んでください。

### 実機の状態を見る

```powershell
tools\multifirm.ps1 status --port COM11            # 速い。中身は未検証
tools\multifirm.ps1 status --port COM11 --verify   # イメージ全体を読んで検証する
```

### ゲストから戻れなくなった

```powershell
tools\multifirm.ps1 recover --port COM11 --execute
```

起動先の選択 (otadata) だけを消して、次回はホストを起動させます。ホスト自体が壊れているときは何も書かずに終了するので、`install-host` で書き直してください。

### 初めて MultiFirm v1 の配置にする

```powershell
tools\multifirm.ps1 initial --host-build ..\M5StopWatch-UserDemo\build --port COM11 --check-device
tools\multifirm.ps1 initial --host-build ..\M5StopWatch-UserDemo\build --port COM11 --execute
```

**NVS の設定と BLE のペアリング情報は消えます。ゲストもすべて消えるので、再インストールが必要です。** storage (バッジ画像など) と coredump の領域には触れません。
書き込み前に16 MiB 全体のバックアップを自動で取ります。

## 実行モード

書き込み系のコマンド (`install`、`install-host`、`recover`、`initial`) は、指定によって動作が変わります。

| 指定 | 実機への接続 | 動作 |
|---|---|---|
| なし | しない | 入力ファイルを検査し、書き込み計画を表示する。`--port` があっても接続しない |
| `--check-device` | する | 実機のレイアウトなどを読み取って確認する。書き込み・消去はしない |
| `--execute` | する | 実際に書き込み、読み戻して確認する |

`status` と `backup` は常に実機を読みます。読み取りだけでも、接続時に実機はリセットされます。

## 動作の詳細

### 書き込めるイメージ

`inspect` と各書き込みコマンドは、次のイメージを拒否します。

- ESP32-S3 用でない、構造が壊れている、チェックサムや付加 SHA-256 が合わない
- 付加 SHA-256 がない
- 付加 SHA-256 の後ろに余分なデータがある (merged bin、署名付きイメージ)
- ブートローダ単体
- 容量超過 (ゲスト 0x1f0000 = 2,031,616 bytes、ホスト 0x400000 = 4,194,304 bytes)
- 実機のチップ revision に対応していない

ヘッダのフラッシュ設定が DIO / 80m / 16MB 以外の場合は、警告だけ出して受け付けます。

### 表示名

ランチャーに出す名前は次の順で決めます。

1. `--name` で指定した名前
2. イメージの `project_name` (ただし `firmware`、`arduino-lib-builder`、`app-template` は使わない)
3. どちらもなければ `install` はエラーになるので、`--name` を付けてください

名前は UTF-8 で 1〜31 bytes、制御文字は使えません。長すぎる名前は切り詰めずにエラーにします。

### 各コマンドの動作

**install** は次の順に進みます。どこで止まっても、同じ `install` を再実行すれば完了します。

1. 対象スロットの表示名を消す
2. 対象スロット全体を消去する
3. イメージを書き込み、読み戻して確認する
4. 表示名を書き込み、読み戻して確認する
5. 書き込み対象外の領域が変わっていないことを確認する

**install-host** は、この機体の既存のバックアップが現状と一致するか確認し、一致しなければ新しく16 MiB のバックアップを取ります。そのうえで `ota_0` だけを書き換え、ブートローダ、表、ゲスト、設定が変わっていないことを確認します。ブートローダは書きません。

**initial** は ESP-IDF のビルドディレクトリ (`flasher_args.json` と `config/sdkconfig.json` があるもの) からブートローダ、パーティション表、ホストを取り出します。次の場合は拒否します。

- パーティション表が MultiFirm v1 と一致しない
- パーティション表の位置が 0x8000 でない、flash が 16MB でない
- ロールバック、Secure Boot、Flash Encryption のいずれかが有効
- `--backup` を指定したが、実機の内容と一致しない

PHY 初期化データをパーティションに置く設定なら、そのデータも書き込みます。

### リセット

- 操作の途中はダウンロードモードのままで、アプリは起動しません。
- 成功したときだけ、最後にアプリへリセットします。
- 書き込みや消去を始めた後に失敗した場合は、**リセットせずダウンロードモードのまま**にします。同じコマンドを再実行するか、`recover` やバックアップからの復元を行ってください。
- 書き込み前に中止した場合 (レイアウト不一致など) は、何も変更せずアプリへ戻します。

### ログとバックアップ

- ログ: `.multifirm/logs/<日時>-<コマンド>.log`。入力ファイル、ハッシュ、書き込み範囲、読み戻し結果、esptool の出力を記録します。
- バックアップ: `.multifirm/backups/backup-<MAC>-<日時>.bin` と、同名の `.json` (サイズ、SHA-256、MAC、レイアウト、ツールの版)。

**バックアップには BLE のペアリング鍵を含む NVS が入っています。共有しないでください。** `.multifirm/` は git 管理外です。

## バックアップから復元する

ホストの書き込みが途中で止まったときや、`initial` を取り消したいときに使います。数分かかります。

```powershell
$py = "tools\.venv\Scripts\python.exe"   # esptool 4.12.0 の Python
& $py -m esptool --chip esp32s3 --port COM11 write_flash --flash_mode keep --flash_freq keep --flash_size keep 0 .multifirm\backups\backup-XXXX.bin
& $py -m esptool --chip esp32s3 --port COM11 verify_flash --flash_mode keep --flash_freq keep --flash_size keep 0 .multifirm\backups\backup-XXXX.bin
```
