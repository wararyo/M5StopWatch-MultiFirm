# Phase 1 記録: 共通ツールとレイアウト

[実装計画](multifirm-plan.md) 10章 Phase 1 の実施記録。ツールの使い方は [tools/README.md](../tools/README.md) にまとめ、この文書には開発時に必要な判断・構成・検証結果を残す。

最終更新: 2026-09-19

## 0. 実施状況

| 手順 | 内容 | 状態 |
|---|---|---|
| 1 | ライブラリ構成、Python 環境、レイアウトとメタデータの定義 | 完了 |
| 2 | `inspect` / `backup` / `status` / `install` / `install-host` / `recover` / `initial` | 完了。実機で確認 (4章) |
| 3 | install の順序、`status --verify`、`--check-device` | 完了。実機で確認 (4章) |
| 4 | PC テスト (容量、異常イメージ、レイアウト不一致、dry-run、書き込み範囲、読み戻し失敗) | 完了。Python 51件 + C++ 22ケースすべて成功 |
| 5 | バックアップから新配置を導入し、各ゲストを再インストール。更新・復旧経路と新 NVS の使用量を確認 | 完了 (4章) |

## 1. 構成

```text
CMakeLists.txt             ESP-IDF コンポーネント (現状は INCLUDE_DIRS src のみ)
library.json               PlatformIO ライブラリ (srcDir / includeDir = src)
src/multifirm_layout.h     IDF 非依存の定数、メタデータのデコード、CRC、名前の規則
tools/layout.py            同じ定義の Python 版、パーティション表の生成と分類
tools/esp_image.py         ESP イメージの検証
tools/device.py            esptool CLI による実機アクセス、テスト用のメモリデバイス
tools/multifirm.py         CLI 本体
tools/multifirm.ps1        Python を選んで multifirm.py を起動する
tools/requirements.txt     esptool==4.12.0
tests/test_multifirm.py    Python テスト
tests/layout_fixtures.py   Python と C++ が共有するメタデータの固定データを生成
tests/fixtures/meta/       上記の生成物 (22ケース + cases.txt)
tests/test_layout.cpp      C++ テスト
tests/CMakeLists.txt       ネイティブテスト用 CMake / CTest
```

計画 3章の `examples/upload_guard.py`、ゲスト用・ホスト用ヘッダは Phase 2 / 3 で追加する。

## 2. 実装で決めた詳細

### 2.1 実機アクセス

- esptool の Python API を直接使わず、**操作ごとに esptool CLI をサブプロセスで呼ぶ**。各回 `--before default_reset --after no_reset`、最後だけ `read_mac --after hard_reset`。
  - 理由: esptool の `main()` は接続時に SPI 接続、XMC フラッシュの起動処理、フラッシュのリセット、サイズ設定などを行う。API を直接使うとこれを再実装する必要があり、実機なしでは検証できない。CLI を続けて呼ぶ方式は、以前の共存ツール (KantanPlay `coexist.py`、UserDemo `update-userdemo.py`) がこの機体の USB-Serial/JTAG で使っていた実績がある。
  - 代償: 操作ごとに再接続とスタブのアップロードが入る。`install` 1回で20回以上になるので、所要時間を実機で測る。
- `write_flash` と `verify_flash` には常に `--flash_mode keep --flash_freq keep --flash_size keep` を付ける。Phase 0 で確認したとおり、esptool 4.x がヘッダを書き換えるのは 0x0 だけだが、ブートローダの書き込み (`initial`) も含めて変更しない。
- 成否の判定に使う esptool の出力: `Hash of data verified`、`-- verify OK`、`-- verify FAILED`、`Chip is ...`、`MAC: ...`、`revision vX.Y`、`Detected flash size: ...`。すべて esptool 4.12.0 のソースに存在することを確認した。`verify_flash` の不一致は終了コード 2 + `verify FAILED` になるので、通信エラーと区別できる。
- 6種類の呼び出し形式 (flash_id、read_flash、verify_flash、write_flash、erase_region、read_mac + hard_reset) を、存在しないポートに対して実行した。引数の解釈を通過し、ポートを開く段階まで進むことを確認した。
- `verify_flash` はデータを4 bytes 境界まで 0xFF で埋める。照合する範囲はすべてセクタ境界か、長さが16の倍数のイメージなので影響しない。

### 2.2 検証の方法

| 対象 | 方法 |
|---|---|
| パーティション表 | 0x8000 から 0xC00 bytes を、`gen_esp32part.py` 相当の生成結果 (MD5 エントリあり、0xFF 埋め) と完全一致で比べる |
| 消去 | 実機側 MD5 (`verify_flash`) を全 0xFF と比べる |
| 書き込み | 全 bytes を読み戻して比べる |
| 対象外領域の不変性 | 事前に読んだ内容と、事後の実機側 MD5 を比べる。install: NVS、otadata、phy_init、multifirm_nvs、他のメタデータセクタと予約セクタ。install-host: 前記4領域と、バックアップとのブートローダ・表・メタデータ・ゲスト3スロット |
| スロットのイメージ | 先頭 4 KiB を読み、足りなければ読む範囲を広げる (必要量と既読量の4倍の大きい方、最大でパーティションサイズ)。ヘッダの長さを信用してパーティション外を読まない |
| バックアップ | 16 MiB を読み、実機側 MD5 と照合してから保存する |

- `status` の既定は先頭セクタだけを読み、app_desc とメタデータを「未検証」「暫定」と表示する。`--verify` でホストと同じ判定 (計画 6.2節) を行う。
- 失敗時のリセット方針は `TrackedDevice` が消去・書き込みの有無を記録して判断する。

### 2.3 メタデータと名前

- 無効理由は Python と C++ で共通のコードにした: `short_read`、`empty`、`bad_magic`、`unknown_version`、`bad_length`、`crc_mismatch`、`name_not_terminated`、`name_padding_not_zero`、`name_invalid_utf8`、`name_empty`、`name_control_char`、`image_size_out_of_range`。判定順もこの順。
- 構造の検査 (`decode_meta`) と、実イメージとの照合 (`match_meta`: image_size、app_digest、設定されていれば elf_sha256) は分けた。
- 名前は UTF-8 で 1〜31 bytes。strict な UTF-8 (冗長表現、サロゲート、U+10FFFF 超を拒否) で、制御文字は Unicode の Cc (C0、DEL、C1) を拒否する。
- 既定の project_name は `firmware`、`arduino-lib-builder`、`app-template`。`app-template` は計画の「など」に当たるものとして追加した。

### 2.4 レイアウト

- `layout.py` で生成した v1 の表は、ESP-IDF v5.5.4 の `gen_esp32part.py` で `docs/partitions.multifirm.csv` から作った表とバイト一致する (テストで毎回確認。IDF が見つからなければスキップ)。
- 旧3ゲスト配置の生成結果は、Phase 0 の実機ダンプの表とも一致した。
- 表の分類: `multifirm-v1`、`legacy-3guest`、`legacy-2app`、`other`、`invalid`。`initial` 以外の変更系コマンドは `multifirm-v1` 以外を拒否する。
- `nvs` が `multifirm_nvs` より前にあること (Phase 0 の 5.3節) を、Python と C++ の両方のテストで確認している。

### 2.5 initial

- 読むのは ESP-IDF ビルドディレクトリの `flasher_args.json` (bootloader / partition-table / app のオフセットとファイル) と `config/sdkconfig.json`。
- 拒否する設定: パーティション表の位置が 0x8000 以外、flash が 16MB 以外、ロールバック有効、Secure Boot、Flash Encryption。
- `CONFIG_ESP_PHY_INIT_DATA_IN_PARTITION` が有効なら `flash_files` の 0x1b000 のファイルを書き、無効なら phy_init を消去する。
- 順序: 表の記録 → バックアップ (または `--backup` を実機全体と照合) → 表が変わっていないことの再確認 → otadata・ゲスト・multifirm_nvs・メタデータ・NVS・phy_init の消去 → ota_0 の消去と書き込み → 表 → ブートローダ領域 (0x0〜0x8000) の消去と書き込み → ホストと表の再確認。
- 旧 otadata (0xd000) と旧 NVS は新 NVS (0x9000〜0x19000) の消去に含まれる。旧 ota_0 の後半は新 ota_1 の消去に含まれる。

### 2.6 C++ ヘッダ

- **C++11 で書く**。Arduino-ESP32 2.0.17 の `tools/platformio-build-esp32s3.py` は `-std=gnu++11` を指定する (vibewatch はこれを上書きしていない)。当初 C++17 の `inline constexpr` 変数で書いたが、Arduino ゲストでビルドできなくなるため `static constexpr` に変えた。
- 確認: ネイティブ g++ 15.2 で C++11 / 14 / 17 / 20、2つの翻訳単位からの利用とリンク、xtensa-esp32s3-elf-g++ 8.4 (`-std=gnu++11`、Arduino 2.0.17 相当)、xtensa-esp-elf-g++ 14.2 (`-std=gnu++17`、ESP-IDF 5.5 相当) の構文チェック。
- テストは計画どおり C++17 でビルドする。

## 3. テスト

| 種類 | 内容 |
|---|---|
| LayoutTest | 配置の連続性と並び、メタデータのセクタ位置、CSV との一致、表の生成と分類、`gen_esp32part.py` との一致、C++ ヘッダの定数・表・既定名・無効理由コードとの一致 |
| MetaTest | CRC のテストベクトル、エンコードとデコードの往復、固定データが最新か、名前の規則、既定の project_name |
| ImageTest | 正常イメージ、ゲスト・ホストの容量ちょうどと超過、付加 SHA なし、余分な後続データ、chip_id 違い、切り詰め、セグメント・チェックサム・ダイジェストの破損、不正なセグメント長、ブートローダ単体、merged bin、署名付き相当、ヘッダの警告、チップ revision、Phase 0 の実ビルド (ある場合) |
| DryRunTest | 変更系4コマンドが `--port` 付きでも接続しない、inspect、表示名の決定、`--execute` と `--check-device` の排他 |
| InstallTest | 計画どおりの書き込み範囲と順序、対象外のバイトが不変、ログの内容、スロット全域の消去、旧配置の拒否、`--check-device` が読み取りだけ、読み戻し不一致、消去の失敗、通信失敗後の再実行、メタデータ書き込み失敗後もイメージは Ready、チップ revision の不一致 |
| StatusTest | 既定表示が暫定、`--verify` の判定、表示名の3段階の採用元、差し替えたイメージに古い名前を使わない、app_desc を残した後半の破損を検出、旧配置の表示、otadata の起動先 |
| HostAndRecoverTest | install-host のバックアップ取得と不変性、既存バックアップの再利用、古いバックアップでの再取得、容量超過、recover が otadata だけを消す、壊れたホストで recover を拒否、backup |
| InitialTest | 旧配置からの導入 (バックアップが先、storage と coredump を保持、各領域の消去)、PHY データの書き込み、不正なビルドの拒否、一致しない `--backup` の拒否、`--check-device` |
| EsptoolDeviceTest | esptool のコマンド列 (reset オプション、keep、erase の引数)、出力の解析、失敗の区別 |
| test_layout.cpp | CRC、配置定数、スロットとメタデータのオフセット、既定名、固定データ22ケースのデコード結果とフィールド |

実行方法:

```powershell
python -m unittest discover -s tests -v
```

```bash
export PATH=/c/msys64/ucrt64/bin:$PATH   # 3.2節
g++ -std=c++17 -Wall -Wextra -Werror -pedantic -Isrc tests/test_layout.cpp -o build/test_layout.exe
./build/test_layout.exe tests/fixtures/meta
```

```powershell
cmake -S tests -B build/tests -G Ninja -DCMAKE_CXX_COMPILER=g++
cmake --build build/tests
ctest --test-dir build/tests --output-on-failure
```

メタデータの固定データを変えたら `python tests/layout_fixtures.py` で作り直す。古いままだと Python テストが失敗する。

### 3.1 確認した環境 (2026-09-18)

| 項目 | 版 |
|---|---|
| Python | 3.13 (Windows Store 版)、3.11.7 (UserDemo の ESP-IDF 環境、esptool 4.12.0) |
| C++ | g++ 15.2.0 (MSYS2 ucrt64) |
| CMake / Ninja | 3.30.2 / 1.12.1 (UserDemo の ESP-IDF ツール同梱) |

### 3.2 開発環境の注意

- MSYS2 の g++ は `C:\msys64\ucrt64\bin` を PATH の先頭に置く。Git Bash の既定の PATH では別の MinGW の DLL を読み込み、`cc1plus` がメッセージを出さずに失敗する。
- UserDemo の `tools/build-local.ps1` は Git Bash からではなく PowerShell から起動する (Phase 0 の 6章)。

## 4. 実機での確認 (2026-09-19)

機体は Phase 0 と同じ ESP32-S3 (MAC 28:84:85:43:a7:c0)、COM11、esptool 4.12.0、Python 3.11.7。
ログは `.multifirm/logs/`、バックアップは `.multifirm/backups/` に残っている。

### 4.1 旧配置での読み取り

| 確認 | 結果 |
|---|---|
| `status` | `legacy-3guest` と分類し、表の内容を一覧表示。終了時に hard reset |
| `backup` | 16 MiB を読み、実機 MD5 と一致。マニフェストに MAC・レイアウト・版を記録 |
| `install --check-device` / `recover --check-device` | 旧配置を検出して拒否。書き込み・消去なしでアプリへ戻った |

### 4.2 新配置への移行 (`initial`)

- UserDemo の `partitions.csv` を v1 レイアウトに差し替えて再ビルドした (commit 69403a8 + 作業中変更)。生成された表は `multifirm-v1` と一致、アプリは 3,741,280 bytes で 4 MiB に対し 453,024 bytes の余裕。
- `initial --check-device` は現在の表 (`legacy-3guest`) を記録して終了。書き込みなし。
- `initial --execute` は取得済みバックアップを `--backup` で渡し、実機全体との MD5 一致を確認してから実行。消去8領域 → ota_0 消去と書き込み → 表 → ブートローダ領域の消去と書き込み → ホストと表の再確認まで、すべて読み戻しで確認して成功した。
- 移行後の `status --verify`: ホスト Ready、ゲスト3スロットは Empty、表示名は App1〜3 (fallback)。
- 移行直後の otadata は `ota_0 (seq 1, state 0x2)`。ホストが起動後に自分で書いた値で、ツールは otadata を書いていない。

### 4.3 ゲストのインストール

- 旧配置に入っていた3本をバックアップから取り出し、同じイメージを `install --execute` で書き戻した (KantanPlay / MuteHid / VibeWatch)。
- 1本あたり約 1分26秒 (826,784 bytes の場合、esptool の呼び出し22回)。
- 各回とも、メタデータ消去 → スロット全域消去 → 書き込みと読み戻し → メタデータ書き込みと照合 → NVS・otadata・phy_init・multifirm_nvs・他のメタデータセクタの不変確認まで成功。
- 全スロット導入後の `status --verify` は3本とも Ready、メタデータ有効、表示名は metadata 採用。

### 4.4 中断と破損

| 試験 | 結果 |
|---|---|
| イメージ書き込み直後に強制終了 | スロットは Ready、メタデータは空。表示名は project_name にフォールバック。同じ `install` の再実行で復旧 |
| インストール済みイメージの1セクタ (0x850000) を消去 | `status --verify` が `Invalid - checksum mismatch; appended SHA-256 mismatch` を報告。app_desc は読めるがメタデータは「無効 (image is not verified)」。再インストールで復旧 |
| `recover --execute` | ホストのイメージを検証したうえで otadata だけを消去 |

### 4.5 `install-host`

- 1回目: 既存バックアップがゲスト再インストール後の現状と一致しないことを検出し (`partition table, multifirm_meta, ota_1, ota_2, ota_3`)、16 MiB を取り直してから ota_0 のみを書き換えた。所要 11分12秒。
- 2回目: 直前に取得したバックアップが現状と一致することを確認し、16 MiB の読み取りを省いて ota_0 のみを書き換えた。
- どちらの回も、書き込み後にブートローダ・表・メタデータ・ゲスト3スロットがバックアップと一致し、NVS・otadata・phy_init・multifirm_nvs が不変であることを確認した。

### 4.6 所要時間の目安

| 操作 | 時間 |
|---|---|
| `status` (検証なし) | 約 30 秒 |
| `status --verify` (4スロット読み戻し) | 約 3 分 |
| `backup` (16 MiB 読み + MD5 照合) | 約 6 分 |
| `install` (826 KiB のゲスト) | 約 1分30秒 |
| `initial --execute` (バックアップ済みを再利用) | 約 5 分 |
| `install-host` (バックアップ取得を含む) | 約 11 分 |
| `install-host` (一致するバックアップを再利用) | 約 5 分 |

### 4.7 UI 操作 (ユーザーによる確認)

新配置のまま、ランチャーからのゲスト起動、A+B の長押しでのホストへの復帰、KEY.B を押しながらの起動での復帰、設定を保存したあとの往復を確認し、いずれも問題なしとの報告を得た。

### 4.8 新 NVS (64 KiB) の使用量

移行後にホストと3ゲストを一通り起動したあとの `nvs` パーティション (0x9000、16ページ)。

| 項目 | 値 |
|---|---|
| 初期化済みページ | 2 / 16 (page 0 Full、page 1 Active) |
| written エントリ | 92 / 2,016 |
| erased エントリ | 87 (GC で回収可能) |
| フォーマット版 | 0xFE (v2) |
| 名前空間 | `system`、`alarm`、`M5GFX`、`phy`、`bt_config.conf` (MuteHid)、`nimble_bond` (VibeWatch) |

旧配置の 16 KiB は written 204 / 378 でほぼ満杯だったのに対し、64 KiB では十分な余裕がある。

**PHY 校正データの書き直しを実測した**。Phase 0 では `phy:cal_version` が 701 (IDF 5.5 系) だったが、移行後に IDF 4.4.7 の VibeWatch を起動した状態では 640 になっていた。Phase 0 の 5.2節で挙げた「IDF の PHY ライブラリ版が異なるアプリ間を往復すると校正データが書き直される」ことが実際に起きている。1回の往復で数エントリ程度の書き込みであり、64 KiB の容量に対しては問題にならないが、NVS の摩耗と起動時間には影響しうる。
