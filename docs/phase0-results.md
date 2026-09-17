# Phase 0 記録: 標準ビルドと共有資源の確認

[実装計画](multifirm-plan.md) 10章 Phase 0 の実測記録。観測と推測を区別し、未実施の項目は **未実施** と明記する。

最終更新: 2026-09-18

## 0. 実施状況

| 手順 | 内容 | 状態 |
|---|---|---|
| 1 | バックアップ、ブートローダ・表・各アプリの SHA / IDF 版の記録 | 完了 |
| 2 | 標準ビルドのオフライン検証 | 完了 |
| 2 | 標準ビルドを ota_1 などから起動し、周辺機器・復帰を確認 | 試験 A (ESP-IDF)・B (Arduino) は合格。KEY.B 脱出後に UserDemo の音が出ない低頻度の事象あり（再現せず。8.3節で既知事象として管理）。試験 C は未実施 |
| 3 | 起動に問題がある場合の切り分け | 起動の問題なし。標準 env の設定変更は不要 |
| 4 | 標準ビルドとブートローダの対応表 | 完了（MuteHid 標準ビルドの実機起動のみ未実施） |
| 5 | app_desc・付加 SHA の実物確認、表位置・size override の記録 | 完了 |
| 6 | NVS 名前空間・フォーマット・全消去経路・使用量 | ダンプからの解析とソース確認は完了。実機上の `nvs_get_stats()` は未実施 |
| 7 | ビルド条件・esptool 版の固定、UserDemo が 4 MiB 以下 | 完了 |

## 1. 機材と環境

| 項目 | 値 |
|---|---|
| チップ | ESP32-S3 (QFN56) rev v0.2、Embedded PSRAM 8MB (AP_3v3)、40 MHz 水晶 |
| MAC | `28:84:85:43:a7:c0` |
| Flash | Manufacturer 0x20 / Device 0x4018、16 MB、eFuse の flash type は quad、3.3 V |
| 接続 | USB-Serial/JTAG (VID:PID 303A:1001)、COM11 |
| セキュリティ | Secure Boot 無効、Flash Encryption 無効、SPI_BOOT_CRYPT_CNT 0 |
| PC | Windows 11 Home 10.0.26200 |

### ツールの版

| 用途 | 版 |
|---|---|
| 実機の読み取り・検証に使用 | Python 3.11.7 + esptool **4.12.0**（UserDemo `.tools/idf-tools/python_env/idf5.5_py3.11_env`） |
| PlatformIO 同梱 | PlatformIO Core 6.2.0、tool-esptoolpy 2.40900.250804（esptool **4.9.0**）、Python 3.11.7 |
| PlatformIO platform | espressif32 @ 6.12.0 |
| ESP-IDF (PlatformIO) | framework-espidf 3.50500.0 = **ESP-IDF 5.5.0** |
| Arduino (PlatformIO) | framework-arduinoespressif32 3.20017.241212 = Arduino-ESP32 **2.0.17**（ESP-IDF v4.4.7） |
| ESP-IDF (UserDemo) | **v5.5.4**（`.tools/esp-idf`） |

esptool 4.9.0 / 4.12.0 の `write_flash` は、`--flash_mode` などを指定しても**ブートローダ位置 (0x0) 以外のイメージを書き換えない**（`cmds.py` の `_update_image_flash_params` が `address != BOOTLOADER_FLASH_OFFSET` で素通し）。既存ツールの `--flash_mode dio --flash_freq 80m --flash_size 16MB` 付き書き込みでもアプリは無変更で書かれており、実機の付加ダイジェストがビルド成果物と一致したことと整合する。

**Phase 1 で固定する候補**: esptool 4.12.0（実機読み書きの主経路）。4.9.0 も同じヘッダ非変更の挙動を確認済み。

## 2. 既存フラッシュ（旧3ゲスト配置）

### 2.1 バックアップ

| 項目 | 値 |
|---|---|
| ファイル | `.phase0/backups/full-20260917-225252.bin`（git 管理外。NVS に BLE ボンド鍵を含むため共有しない） |
| サイズ | 16,777,216 bytes |
| SHA-256 | `708d907536ceeb8d1713a8307c91c26a05a968b1ac9be6ccc701c1d70e20fe50` |
| MD5 | `107521d088a43489fbadf9ff8fb1ace4` |
| 検証 | `esptool verify_flash --flash_size keep 0 <file>` で実機側 MD5 と一致 (`verify OK (digest matched)`) |
| 読み取り | `read_flash 0 0x1000000`、1,500,000 baud 指定、175 秒。終了時は esptool 既定の hard reset（RTS） |

解析は `python tools/phase0/analyze_dump.py <dump> --json <out>`。esptool を使わない独立実装で、`esptool image_info --version 2` の結果（チェックサム・Validation hash）と全イメージで一致した。

### 2.2 ブートローダ

| 項目 | 値 |
|---|---|
| サイズ | 20,832 bytes（0x5160） |
| ファイル SHA-256 | `76f02fbf13873f60e527352928563164c127326546a5753c1039d28aa4a74bd8` |
| 付加 SHA | `9ae574fdb2f93ed6a8b28a98e7e884cdb74c2c72e2e8709734c47c2c86694e0e`（valid） |
| esp_bootloader_desc | idf_ver **v5.5.4**、version 1、`Sep  7 2026 17:31:17` |
| ヘッダ | DIO / 80m / 16MB、chip_id 9、max rev v0.99 |
| 設定（UserDemo sdkconfig） | `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE` 無効、`SKIP_VALIDATE_*` すべて無効、Secure Boot 無効 |

UserDemo の現行ビルド `build/bootloader/bootloader.bin` とバイト一致。

### 2.3 パーティション表

0x8000 に9エントリ + MD5 エントリ（+0x120）、MD5 `babd188270acb55ba249052f12beb15e` は再計算と一致。0xC00 bytes の SHA-256 は `3c8179eb071a31b6197c03cf322604dcee63c7959525115c0fca4c43da867bcf`。UserDemo `build/partition_table/partition-table.bin` とバイト一致。

| 名前 | type / subtype | オフセット | サイズ | 領域全体の SHA-256 |
|---|---|---:|---:|---|
| nvs | data / 0x02 | 0x9000 | 0x4000 | `559526e3…0974d491` |
| otadata | data / 0x00 | 0xd000 | 0x2000 | `48fbceb4…736295bf` |
| phy_init | data / 0x01 | 0xf000 | 0x1000 | `58b59eb3…0038aa788` |
| ota_0 | app / 0x10 | 0x20000 | 0x4f0000 | `1cd40200…c37397670` |
| ota_1 | app / 0x11 | 0x510000 | 0x190000 | `78c09aa0…4d24a108` |
| ota_2 | app / 0x12 | 0x6a0000 | 0x190000 | `6898c105…c67477eb` |
| ota_3 | app / 0x13 | 0x830000 | 0x190000 | `873aa88d…49a1c237`|
| storage | data / 0x81 | 0xa00000 | 0x400000 | `6993d41a…4f14a18a` |
| coredump | data / 0x03 | 0xe00000 | 0x10000 | 全 0xFF |

完全な値は `.phase0/dump-analysis.json` に保存した。

otadata は両セクタとも CRC 正常。seq 3 / seq 5、state はともに 0xFFFFFFFF (UNDEFINED)。有効な最大 seq 5 → `(5-1) % 4 = 0` で **ota_0 (UserDemo) が起動対象**。

### 2.4 アプリスロット

| スロット | project_name | version | idf_ver | イメージ長 | 付加 SHA | 対応ソース |
|---|---|---|---|---:|---|---|
| ota_0 | StopWatch-UserDemo | V0.5-2-gc6d2818-dirty | v5.5.4 | 3,741,280 | `94e323d4…1a27f310` | UserDemo `build/StopWatch-UserDemo.bin` と一致 |
| ota_1 | M5StopWatch-KantanPlay | 0.1.0 | 5.5.0 | 826,800 | `1ee8bd04…d6c9955b` | KantanPlay `m5stopwatch-coexist` ビルドと一致 |
| ota_2 | M5StopWatch-MuteHid | 0.0.1-phase0 | 5.5.0 | 1,188,032 | `1e81c98b…83092ab8` | MuteHid 共存ビルド（現ローカル成果物より古い版） |
| ota_3 | **arduino-lib-builder** | esp-idf: v4.4.7 38eeba213a | v4.4.7-dirty | 813,632 | `cb427467…375dbfde` | vibewatch 共存ビルド（`app_name.py` 導入前の古い版） |

- 全スロットでヘッダ DIO / 80m / 16MB、chip_id 9、セグメントチェックサムと付加 SHA-256 が正常。PC で再計算した SHA（先頭からチェックサムバイトまで）が付加ダイジェストと全て一致。
- app_elf_sha256 は4本とも非ゼロ。**Arduino ビルドでも全 0 ではなかった**（計画 12章の懸念は現物では発生せず）。
- ESP-IDF 5.5 のビルドは `mmu_page_size` 64 KB を記録。Arduino (IDF 4.4.7) は 0（フィールドなし）。
- max chip rev は UserDemo (IDF 5.5.4) が v0.99、PlatformIO の 5.5.0 と Arduino は v655.35。
- ota_0 と ota_1 はイメージ末尾以降にも 0xFF 以外の残骸がある（過去の大きいイメージ）。ota_2 / ota_3 の余白は全 0xFF。v1 のスロット全域消去の方針で解消される。
- ota_3 の project_name が既定値 `arduino-lib-builder` のため、既定名除外規則（5.3節）の実例になる。

## 3. 標準ビルドのオフライン検証

2026-09-17 に各リポジトリの標準 env を再ビルドした（ログ `.phase0/builds/`）。

| アプリ | 環境 | commit | env | サイズ | ヘッダ | 付加 SHA | ゲスト上限 0x1f0000 |
|---|---|---|---|---:|---|---|---|
| KantanPlay | PlatformIO / ESP-IDF 5.5.0 | c2d6606（未追跡 `.vscode/` のみ） | `m5stopwatch` | 826,784 | DIO/80m/16MB | valid `0f7fcf21…41e97a11` | 可 |
| MuteHid | PlatformIO / ESP-IDF 5.5.0 | d7d169b + 作業中変更（platformio.ini、TelephonyHid.cpp） | `m5stopwatch` | 1,179,120 | DIO/80m/16MB | valid `56614e99…223ad2a0` | 可 |
| vibewatch | PlatformIO / Arduino 2.0.17 | f252087 + 未コミット変更22件 | `m5stack-stopwatch` | 813,472 | DIO/80m/16MB | valid `2d6f733f…1dcf4dbc` | 可 |

- 3本とも単体アプリイメージで、付加ダイジェストより後の余分な bytes はない。chip_id esp32s3。
- KantanPlay の標準ビルドは 2026-09-09 の成果物と同一ハッシュ（再現性あり）。共存 env との差は16 bytes。
- MuteHid の標準 env は `-DMUTEHID_COEXIST=0` で**復帰処理と KEY.B 脱出がコンパイル時に無効**。計画 9章「専用ビルドフラグの実機判定への置き換え」の対象。KantanPlay と vibewatch は標準 env にも復帰処理が入っている。

### 3.1 QIO ヘッダについて

今回の3本に **QIO ヘッダのイメージはなかった**。

- ESP-IDF の2本は `CONFIG_ESPTOOLPY_FLASHMODE_DIO=y`。
- vibewatch のボード `esp32s3box` は `flash_mode: qio`、`memory_type: qio_opi` で、Arduino のビルド済み SDK は `CONFIG_ESPTOOLPY_FLASHMODE_QIO` でコンパイルされている。一方 platform-espressif32 6.12.0 の `builder/main.py` `_get_board_flash_mode()` は qio / qout を **dio に置き換えて** `elf2image` に渡すため、ヘッダは DIO になる。アプリ内部の SPI flash ドライバ設定は QIO のまま。

したがって「ヘッダは DIO だが SDK は QIO 設定」という設定差が Arduino ゲストにある。起動可否は手順2の実機試験で確認する。

### 3.2 IDF ソースで確認した起動時の扱い

- ESP-IDF v5.5.4 と 5.5.0 の `components/esp_system/port/cpu_start.c` は、アプリヘッダの mode / freq による `configure_flash` 相当を `CONFIG_IDF_TARGET_ESP32` の場合だけ行う。S3 では行わない。
- ヘッダのフラッシュサイズは `CONFIG_SPI_FLASH_SIZE_OVERRIDE` 時のみ `bootloader_flash_update_size()` に使う。UserDemo、KantanPlay、MuteHid はこの設定が無効。
- `CONFIG_PARTITION_TABLE_OFFSET=0x8000` は UserDemo、KantanPlay（両 env）、MuteHid（両 env）、Arduino 2.0.17 SDK のすべてで一致。
- MuteHid の標準 env の sdkconfig も DIO、ロールバック無効、size override 無効、coredump to flash 有効で、共存 env と同じ。
- `esp_image_format.c` (v5.5.4) は `esp_cpu_dbgr_is_attached()` が真のときチェックサム確認 (205行) と単純 SHA 確認 (225行) を省略する。計画 6.2節の前提どおり。
- `esp_partition` はパーティションを表の順に連結リストへ追加する（`partition.c` の `SLIST_INSERT_AFTER(last, …)`）。`esp_partition_find_first` は表で最初の一致を返す。

## 4. 標準ビルドとブートローダの対応表

ブートローダはすべて実機の **ESP-IDF v5.5.4、ロールバック無効、DIO/80m/16MB**。

| アプリ | フレームワーク / IDF | ヘッダ | SDK の flash mode | PSRAM | rollback（アプリ側） | 表位置 | size override | オフライン検証 | 実機起動（標準ビルド） |
|---|---|---|---|---|---|---|---|---|---|
| UserDemo (host) | ESP-IDF v5.5.4 | DIO/80m/16MB | DIO | Octal 80M | 無効 | 0x8000 | 無効 | 可 | 稼働中（ota_0） |
| KantanPlay | PIO ESP-IDF 5.5.0 | DIO/80m/16MB | DIO | Octal 80M | 無効 | 0x8000 | 無効 | 可 | **可**（ota_1、試験A） |
| MuteHid | PIO ESP-IDF 5.5.0 | DIO/80m/16MB | DIO | Octal 80M | 無効 | 0x8000 | 無効 | 可 | **未実施**（共存 env は ota_2 で稼働実績あり） |
| vibewatch | PIO Arduino 2.0.17 / IDF 4.4.7 | DIO/80m/16MB | **QIO** (qio_opi) | Octal 80M | **有効** | 0x8000 | 項目なし（IDF 4.4.7 SDK の sdkconfig に存在しない） | 可 | **可**（ota_3、試験B） |

MuteHid（両 env）は `CONFIG_ESP_COREDUMP_ENABLE_TO_FLASH=y`。Arduino 2.0.17 SDK も coredump to flash が有効。共有 `coredump` パーティションへ書く主体は複数ある。

### 4.1 Arduino ゲストのアプリ側ロールバック設定

Arduino 2.0.17 の SDK は `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=1`（アプリ側の `CONFIG_APP_ROLLBACK_ENABLE`）でビルドされている。ソース上の影響:

- `initArduino()` は起動中スロットの状態が `ESP_OTA_IMG_PENDING_VERIFY` のときだけ `esp_ota_mark_app_valid_cancel_rollback()` を呼ぶ。ロールバック無効のブートローダは PENDING_VERIFY を作らないため、通常は otadata を書かない。
- Arduino ゲストが `esp_ota_set_boot_partition()` を呼ぶと、アプリ側のロールバック有効設定により選択エントリの state が `ESP_OTA_IMG_NEW` になる見込み。v5.5.4 のブートローダが無効とみなすのは seq 0xFFFFFFFF と INVALID / ABORTED なので、起動先には影響しない見込み。
- **実機で確認（試験B）**: UserDemo が App3 を選んだ時点のエントリは seq 20 / state 0xFFFFFFFF。vibewatch が戻るときに書いたエントリは seq 21 / **state 0x00000000 (ESP_OTA_IMG_NEW)**。どちらも CRC 正常。ブートローダは seq 21 → ota_0 を選び、UserDemo が起動した。NEW 状態は起動先の選択に影響しなかった。
- ホスト API は otadata の state を起動可否の判定に使わない。将来ブートローダでロールバックを有効にする場合は、Arduino ゲストが残す NEW 状態を含めて再評価する。

## 5. NVS

### 5.1 実物（旧配置 16 KiB、4ページ）

`nvs_tool.py`（ESP-IDF v5.5.4 同梱）とページビットマップの独自集計による。

| ページ | 状態 | seq | フォーマット | written | erased | empty |
|---|---|---:|---|---:|---:|---:|
| 0 | 未初期化（予備ページ） | — | — | — | — | 126 |
| 1 | Full | 14 | 0xFE (v2) | 80 | 46 | 0 |
| 2 | Active | 43 | 0xFE (v2) | 109 | 16 | 1 |
| 3 | Full | 42 | 0xFE (v2) | 15 | 106 | 5 |

- 全ページ CRC 正常。written 204 / erased 168 / empty 6（初期化済み3ページ = 378 エントリ中）。
- `nvs_get_stats()` 相当の推定値: used_entries 204、free_entries 132（未初期化ページ 126 を含む）、available_entries 約 6（予備1ページ分を除く）。**旧 16 KiB 領域は GC なしでほぼ満杯**。erased 168 は GC で回収できる。
- 実機上の `nvs_get_stats()` 呼び出しによる実測は未実施。

### 5.2 名前空間と所有者

| 名前空間 | 所有者（ソース・内容から推定） | 主なキー |
|---|---|---|
| `system` | UserDemo | launch_count、btn_sfx、btn_vibrate、bl_lev、spk_vol |
| `alarm` | UserDemo | storage (blob) |
| `wakeup` | UserDemo (`hal_wakeup.cpp`) | state (blob) |
| `rtc_test` | UserDemo の RTC セルフテスト | original、phase、phase_v2 |
| `player` | KantanPlay | key、volume、vibration_on |
| `mutehid` | MuteHid | cfgver、vibrate、bright、input_ccc、pwr_drain |
| `bt_config.conf` | Bluedroid（MuteHid。ESP-IDF の BLE HID） | bt_cfg_key0 (blob) |
| `nimble_bond` | NimBLE（vibewatch の NimBLE-Arduino） | our_sec / peer_sec / cccd_sec 等 |
| `M5GFX` | M5GFX（全ゲストが**共有**） | AUTODETECT |
| `phy` | ESP-IDF PHY 校正（Wi-Fi/BLE を使う全アプリで**共有**） | cal_version 701、cal_mac、cal_data |
| `vibe-watch` | vibewatch (`Preferences`) | 現物には未作成 |

- `phy` と `M5GFX` はアプリ間で共有される。IDF の PHY ライブラリ版が異なる BLE アプリ間を往復すると、校正データの書き直しが繰り返される可能性がある（未確認。新配置導入後の反復往復で `cal_version` と NVS 使用量を観測する）。
- **フォーマット互換性**: 現物は IDF 4.4.7（Arduino）、5.5.0、5.5.4 のアプリがすべて同じ NVS を使った後の状態で、全ページが v2 (0xFE)、CRC 正常。どのアプリも `NEW_VERSION_FOUND` による全消去を起こした形跡はない。互換性を示す実例だが、網羅的な保証ではない。

### 5.3 全消去経路

| 場所 | 条件 | 対象 | 対応方針（計画 7章） |
|---|---|---|---|
| UserDemo `main/hal/hal.cpp:28-32` `Hal::init` | `NO_FREE_PAGES` / `NEW_VERSION_FOUND` | `nvs_flash_erase()`（既定 nvs 全体） | Phase 2 で修正対象 |
| UserDemo `main/hal/hal.cpp:95` `Hal::factoryReset` | 工場出荷リセット操作 | `nvs_flash_erase()` | 「全アプリ設定の初期化」と明示するか、名前空間単位にする |
| UserDemo `main/hal/utils/config_ap/config_ap.cpp:60-64` | 同上の2エラー | `nvs_flash_erase()` | Phase 2 で修正対象 |
| KantanPlay `src/domain/Settings.cpp:9` | 初期化失敗 | 消去しない（既定値で動作） | 対応済み |
| MuteHid `src/main.cpp:49` | 初期化失敗 | 消去しない（エラー表示で停止） | 対応済み |
| Arduino 2.0.17 `cores/esp32/esp32-hal-misc.c:249-255` `initArduino` | `NO_FREE_PAGES` / `NEW_VERSION_FOUND` | `esp_partition_find_first(DATA, NVS, NULL)` を `esp_partition_erase_range` で全域消去 | パッチしない（計画どおりリスクを許容） |
| NimBLE-Arduino 2.5.1 `src/NimBLEDevice.cpp:904-909` `NimBLEDevice::init` | 同上の2エラー | `nvs_flash_erase()`（既定 nvs） | ライブラリ。Arduino ゲスト側の既知リスクとして記録 |

**新レイアウトへの影響**: Arduino コアは**ラベル指定ではなく「表で最初の data/nvs」**を消す。計画 4章の「`multifirm_nvs` は全消去の影響を受けない」は、`partitions.multifirm.csv` で `nvs` (0x9000) が `multifirm_nvs` (0x9f0000) より前にあるため成立する。**表の中で `nvs` を `multifirm_nvs` より前に置くことをレイアウトの不変条件として扱う**。追加のレイアウト変更は不要。

## 6. UserDemo のサイズ

- 2026-09-17 に `tools/build-local.ps1`（ESP-IDF v5.5.4）で再ビルドした。ソース変更がないため、アプリはリンクし直されなかった（commit c6d2818 + 未コミット変更35件、2026-09-15 のビルドと同じ状態）。
- `StopWatch-UserDemo.bin` 3,741,280 bytes ≤ 4,194,304 bytes。余裕は 453,024 bytes（約 442 KiB）。付加 SHA valid。実機 ota_0 とバイト一致。
- 注意: `build-local.ps1` を Git Bash から起動すると `idf_tools.py export` が `MSys/Mingw is not supported` で失敗する。PowerShell から起動する。
- 新レイアウトの表でビルドし直したイメージのサイズ確認は、Phase 1 の表変更時に再度行う。

## 7. 判断事項への反映

| 計画 12章の項目 | Phase 0 時点の結論 |
|---|---|
| 対応するビルドとツールの版 | ブートローダ ESP-IDF v5.5.4（ロールバック無効）。ゲストは PIO ESP-IDF 5.5.0 と Arduino 2.0.17 (IDF 4.4.7) をオフライン検証済み。実機でも ESP-IDF 5.5.0（試験A）と Arduino 2.0.17（試験B）の標準ビルドが ota_1 / ota_3 から起動した。esptool は 4.12.0 に固定する |
| 標準 env の追加設定の要否 | オフラインでは不要。3本とも DIO ヘッダ、表位置 0x8000。ESP-IDF の2本は size override 無効（Arduino の IDF 4.4.7 SDK には該当項目なし）。Arduino の「DIO ヘッダ / QIO SDK」も実機で起動し、`spi_flash` 等の異常はなかった。**追加設定は不要** |
| NVS 異常時の各アプリの継続範囲 | KantanPlay・MuteHid は全消去しない実装済み。UserDemo の3箇所が修正対象。Arduino コアと NimBLE-Arduino の全消去は許容リスクとして記録 |

追加の気づき:

- `nvs` を `multifirm_nvs` より前に置くことを不変条件にする（5.3節）。
- MuteHid の標準 env では復帰処理がビルドフラグで無効になる。Phase 2 で実行時のレイアウト判定に置き換えるまで、標準ビルドの往復試験はできない。
- 既存ツールは `--flash_mode dio` 等を指定しているが、esptool の仕様上アプリイメージには影響しない。MultiFirm のツールでは誤解を避けるため指定しない（keep）。
- 旧 16 KiB NVS はほぼ満杯。新配置の 64 KiB への移行で余裕ができるが、移行時は計画どおり NVS を初期化し、BLE の再ペアリングが必要になる。

## 8. 実機での標準ビルド起動試験（手順2）

2026-09-17 に実施した。書き込み直前に 0x8000 の 0xC00 bytes を読み、バックアップ時の表とバイト一致することを確認した。その後、esptool 4.12.0 の `write_flash --flash_mode keep --flash_freq keep --flash_size keep` で対象スロットだけを書き、`verify_flash` で読み戻して一致を確認した。otadata・NVS・他スロットには PC から書いていない。ログは `.phase0/step2/`（write.log、serial.log）。

| 試験 | 書き込み先 | イメージ | 付加 SHA | 結果 |
|---|---|---|---|---|
| A: ESP-IDF 標準ビルド | ota_1 (0x510000) | `kantanplay-std.bin` 826,784 bytes | `0f7fcf21…41e97a11` | 合格 |
| B: Arduino 標準ビルド (QIO SDK) | ota_3 (0x830000) | `vibewatch-std.bin` 813,472 bytes | `2d6f733f…1dcf4dbc` | 合格（8.3節の既知事象あり） |
| C (任意): MuteHid 標準ビルド | ota_2 (0x6a0000) | `mutehid-std.bin` 1,179,120 bytes | — | 未実施 |

ota_1 / ota_3 は試験後も標準ビルドのまま。元の共存ビルドは `.phase0/extract/ota_1.bin`、`ota_3.bin` から書き戻せる。

### 8.1 試験A: KantanPlay 標準ビルド（ota_1）

- UserDemo の App1 から起動した。ログ: `Loaded app from partition at offset 0x510000`、`spi_flash: flash io: dio`、Octal PSRAM 8MB / 80MHz のメモリテスト OK、`[Settings] restored key=0 volume=10 vibration=1`（NVS の `player` を読めた）、Audio / Input の初期化。
- A+B の3秒保持で `Firmware: Booting UserDemo (ota_0)` → UserDemo が起動した。
- 電源を入れ直し、KEY.B を押したまま起動すると、起動 399 ms で UserDemo へ切り替わった。
- ユーザーの目視: 表示・音・タッチ・ボタン・振動に異常なし。

### 8.2 試験B: vibewatch 標準ビルド（ota_3）

- App3 から起動した（`Loaded app from partition at offset 0x830000`）。PSRAM 有効、M5GFX は NVS の自動判定結果 board 30 を読み込んだ。NimBLE が起動し、既存のボンドで BLE HID に再接続した（`nimble_bond` を共有 NVS から利用）。`Preferences` の `nvs_open failed: NOT_FOUND` は `vibe-watch` 名前空間が未作成なためで、害はない。
- A+B 保持で UserDemo へ戻った。戻った直後の otadata は 4.1節のとおり。
- KEY.B を押したままの起動で UserDemo へ戻った。
- ヘッダ DIO / SDK QIO の組み合わせで起動・フラッシュアクセスの異常はなかった。

### 8.3 観測事象: KEY.B 脱出後に UserDemo の操作音が出ないことがある

- vibewatch から KEY.B 起動で UserDemo へ戻ったとき、UserDemo の操作音が**出ないことがある**（出る回もある）。以前、vibewatch などの共存 env でも1回遭遇した記憶があるとのこと（ユーザー報告）。
- この回のシリアルログは、otadata の読み取りのためにログ記録を止めていたので残っていない。
- 記録できた3回の UserDemo 起動（KantanPlay の KEY.B 脱出後を含む）では、`ES8311: Work in Slave mode` → `Adev_Codec: Open codec device OK` → `HAL-IOE set speaker enable`（M5IOE1 pin 9 = 1、`hal_ioe.cpp` の PA 制御）→ `play boot sfx` まで、いずれもエラーなく進んでいた。
- 候補（未検証）: (1) 脱出時の esp_restart では ES8311・M5IOE1・PMIC などの外部デバイスがリセットされず、電源投入直後の短時間の再起動でデバイスの状態やタイミングが通常と変わる、(2) UserDemo 起動中も KEY.B が押されたままの状態が入力処理に影響する、(3) 共存やスロットとは無関係な UserDemo 単体の間欠不具合。
- **無音の回の症状**（ユーザー報告）: 起動音もボタンの操作音も出ない。音量を変えても無音。効果音の再生処理ではなく、コーデック・I2S・スピーカーアンプのどこかで音声経路全体が止まっていると考えられる。このため候補 (2) の可能性は低い。
- **再現試験（2026-09-18 00:00〜00:05、ログ `.phase0/step2/serial-sound.log`）**: vibewatch からの KEY.B 脱出、KantanPlay からの KEY.B 脱出（ログで3回確認）、ボタンを押さない通常起動（4回）を繰り返したが、再現しなかった。発生頻度は低い。記録された UserDemo の全起動で、ES8311 の初期化 → `Open codec device OK` → speaker enable → `play boot sfx` までエラーなく進んだ。
- vibewatch の `checkStartupEscape()` は `setup()` の先頭（`M5.begin()` より前）にあり、ログでも I2C 初期化の前に再起動していた。脱出する回の vibewatch は音声系のハードウェアに触れていない。
- UserDemo のログはコーデック API の戻り値だけを示すので、無音の回でも同じログになる可能性がある。ログの比較だけで切り分けられるとは限らない。
- ユーザーは共存 env でも1回遭遇した記憶があるとのこと。標準ビルド固有の問題ではない可能性が高く、Phase 0 の判断（標準ビルドへの統一）は変えない。
- **扱い**: 未解決の既知事象とする。Phase 2 の往復試験（計画 11章「10往復して音・振動・表示・設定に異常がない」）ではシリアルログを常に取得する。再現したら、ES8311 のレジスタ、M5IOE1 の PA ピン、I2S の状態を UserDemo 側で読み出す診断を追加して原因を切り分ける。ホスト側で音声デバイスを明示的にリセットする必要があるかは、その結果で判断する。

## 9. 再現用ファイル

| ファイル | 内容 |
|---|---|
| `tools/phase0/espimage.py` | ESP イメージ・app_desc・bootloader_desc・パーティション表の独立パーサ |
| `tools/phase0/analyze_dump.py` | 16 MiB ダンプの解析（表、ブートローダ、スロット、otadata、NVS ページ） |
| `tools/phase0/inspect_bins.py` | 単体 `.bin` の検査 |
| `.phase0/`（git 管理外） | バックアップ、解析 JSON、抽出イメージ、ビルドログ、試験用 bin |
