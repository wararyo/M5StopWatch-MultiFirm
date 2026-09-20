# M5StopWatch-MultiFirm 共存基盤の実装計画

## 1. 目的と範囲

M5StopWatch-MultiFirm に、複数のファームウェアを共存させるための PC ツール、ゲスト用ライブラリ、ホスト用ライブラリをまとめる。
現在各ゲストに複製されているコードと書き込み手順を共通化し、新しいゲストの基本的な組み込みを `lib_deps` の追加と起動時・ループ内の呼び出しで行えるようにする。
書き込み保護の設定と、共有資源の確認も導入手順に含める。

1. **汎用インストールツール**: 対応条件を満たすアプリ単体の `.bin` を、指定したゲストスロットへ書く。
2. **スロットメタデータ**: 表示名とアプリの付加 SHA-256 ダイジェストを専用領域に保存し、実イメージの検証後に一致する場合だけ名前を採用する。
3. **ゲスト用ライブラリ**: A+B の1.6秒保持と、KEY.B を押したままの起動でホストへ戻る。
4. **ホスト用ライブラリ**: スロット列挙、表示名の決定、イメージ検証、起動先切り替えを UI から分離する。

v1 では `ota_0` のランチャーを **ホスト**、`ota_1`〜`ota_3` のアプリを **ゲスト** と呼ぶ。
現在のホストは UserDemo。API とツールは `returnToHost`、`install-host` など、特定のホスト名に依存しない名前にする。
ホスト位置は v1 全体で `ota_0` に固定する。位置変更は将来のレイアウト改版として扱う。

今回の対象外:

- ブートローダ内でのボタン復帰、独立ブートメニュー。
- 実機上インストール、USB MSC / storage からの更新。
- ゲスト専用ファイルシステムや `storage` の共用。
- `multifirm_nvs` を使う共有状態の API。v1 ではパーティションを予約するだけで、読み書きしない。
- Secure Boot、署名付きイメージ、Flash Encryption への対応。
- ホスト位置の動的探索、任意レイアウトへの汎用化。
- Arduino コアへのパッチ、NVS を保持したブートローダ単独更新。

## 2. 前提と対応条件

### 2.1 現状 (2026-09-17現在)

既存のゲスト対応では、パーティション表、専用ビルド環境、ホストへの復帰処理、書き込みツール、標準 upload の保護をアプリごとに管理している。

既存のゲスト対応の例

* [M5StopWatch-MuteHid](https://github.com/wararyo/M5StopWatch-MuteHid/tree/e018658e69c8468fc208dce5a4ff984bbe45f32a)
* [M5StopWatch-KantanPlay](https://github.com/wararyo/M5StopWatch-KantanPlay/tree/c2d6606e90959cd7e4b8e80ca17a87a4392ba075)

MultiFirm はこれらの機能と導入手順を整理し、特定のゲストアプリに依存せず利用できる基盤にする。
UserDemo は `AppOtaSlot` で app_desc の読み取りと起動先設定を行い、App1〜3 を個別クラスで登録している。

### 2.2 技術的前提

- 対応する ESP32-S3 アプリは、ブートローダのマッピングにより別のアプリスロットから起動できる。ビルド時 CSV が異なるだけで起動不能になるわけではない。ただし、配置やパーティション名をアプリが独自に決め打ちしていないことを確認する。
- `esp_partition`、NVS、`esp_ota_*` は実機のパーティション表を参照する。標準ビルドへの統一は、実機上の共存表を維持してアプリだけを書き込むことで実現する。
- 確認した ESP-IDF v5.5.4 の S3 実装は、ブートローダ自身のヘッダをフラッシュ初期化に使用する。アプリ側の `configure_flash(&fhdr)` は ESP32 限定であり、S3 では通らない。アプリヘッダのサイズ参照には `CONFIG_SPI_FLASH_SIZE_OVERRIDE` などの条件がある。一般ドキュメントの説明だけで S3 の動作を判断せず、対象 SDK・設定を確認する。
- 現行 UserDemo の `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE` は無効。v1 はこの構成を前提とする。ブートローダ設定を変更した場合は復帰・起動手順を再評価する。
- ブートローダと各アプリの IDF バージョンは実物から記録する。全アプリが同じ IDF 系統であるとは仮定しない。ブートローダを更新すれば互換性問題が解決するとも仮定しない。
- ゲストのブートローダとビルド時パーティション表は実機へ書かない。実行時に使う表の位置は全アプリで `CONFIG_PARTITION_TABLE_OFFSET=0x8000` とする。

### 2.3 ゲストの対応条件

- ESP32-S3 と本機のハードウェア、Octal PSRAM に対応するビルドである。
- ツールが実際に書くイメージが **0x1f0000 = 2,031,616 bytes（1,984 KiB）以下**である。
- 付加 SHA-256 を持つ未署名の単体アプリイメージである。パーティション表位置が 0x8000 である。
- 対応ブートローダとの起動確認が済んでいる。
- 独自データパーティションを必須とせず、固定アドレスへの書き込みをしない。
- ESP-IDF 環境を推奨する。NVS 名前空間を分離し、管理可能なアプリ・IDF 初期化処理では自動全消去しない。Arduino 環境は7章の共有 NVS 消去リスクを許容して対応する。
- 通常の OTA 更新やファイルシステム書き込みが、他アプリのスロットを選んで上書きしない。v1 で更新する経路は MultiFirm の PC ツールとする。

`inspect` で確認できるのはイメージ形式や容量などに限られる。NVS、周辺機器、パーティション依存はソース確認と実機試験で確認する。

## 3. 構成と版管理

```text
M5StopWatch-MultiFirm/
  library.json
  CMakeLists.txt
  src/multifirm_layout.h        # IDF 非依存の定数、レコードのデコード、CRC
  src/multifirm_guest.h         # inline によるヘッダオンリー実装
  src/return_gesture.h
  src/multifirm_host.h
  src/multifirm_host.cpp        # MULTIFIRM_HOST 定義時だけ実体を持つ
  tools/multifirm.py
  tools/multifirm.ps1
  tools/layout.py
  examples/upload_guard.py   # 各ゲストへ配置する小さな guard のひな形
  tools/requirements.txt      # 検証済み esptool の版を固定
  tests/test_multifirm.py
  tests/test_return_gesture.cpp
  tests/test_layout.cpp
  docs/
```

- ツール・ライブラリ・レイアウト仕様を同じリリースで管理する。
- 取り込み方法は PlatformIO の `lib_deps` と ESP-IDF コンポーネントの両方に対応し、ゲスト・ホストのどちらの役割でもどちらの方法でも使えるようにする。ホスト実装を有効にするかどうかは取り込み方法ではなく、ビルド定義で切り替える。
- 現行の組み合わせは、ゲストが PlatformIO の `lib_deps`、UserDemo が `repos.json` と `fetch_repos.py` による `components/multifirm` への取り込みである。
- リリース時は同じタグを固定する。以下の `v1.0.0` は最初のリリースを想定した記述であり、存在するタグを前提とした実装済みの手順ではない。

```ini
lib_deps =
    https://github.com/wararyo/M5StopWatch-MultiFirm.git#v1.0.0
```

ゲスト API はヘッダオンリーとし、ホスト実装 `src/multifirm_host.cpp` は `MULTIFIRM_HOST` が定義されたときだけ実体を持ち、未定義なら空の翻訳単位になる。PlatformIO では `build_flags = -DMULTIFIRM_HOST=1`、ESP-IDF コンポーネントでは Kconfig の `CONFIG_MULTIFIRM_HOST` で有効化し、ソース側は両方の定義を受け付ける。
ホスト実装が依存する `app_update`、`bootloader_support`、`esp_partition` などは PlatformIO の espidf・arduino 両フレームワークにも含まれるため、有効化した側のビルド方法を問わない。ホストが Arduino 環境の場合は、7章の共有 NVS 消去リスクがホストにも及ぶ。
ヘッダオンリーの部分は Arduino・ESP-IDF 双方のコンパイル確認を行う。共有状態は inline 関数内の static 等で複数翻訳単位からの利用を考慮する。
`multifirm_layout.h` と `return_gesture.h` は IDF ヘッダに依存させず、C++17 のネイティブコンパイラで tests を実行できるようにする。テスト用 CMake/CTest と実行コマンドを用意する。

## 4. レイアウト

ホスト4 MiB、ゲスト各1,984 KiB、共有 NVS 64 KiB、専用メタデータ16 KiB、MultiFirm 専用 NVS 64 KiB の配置を v1 の固定レイアウトとする。

| 名前 | 種類 / subtype | オフセット | サイズ |
|---|---|---:|---:|
| nvs | data / nvs | 0x9000 | 0x10000 |
| otadata | data / ota | 0x19000 | 0x2000 |
| phy_init | data / phy | 0x1b000 | 0x1000 |
| multifirm_meta | data / 0x40 | 0x1c000 | 0x4000 |
| ota_0 | app / ota_0 | 0x20000 | 0x400000 |
| ota_1 | app / ota_1 | 0x420000 | 0x1f0000 |
| ota_2 | app / ota_2 | 0x610000 | 0x1f0000 |
| ota_3 | app / ota_3 | 0x800000 | 0x1f0000 |
| multifirm_nvs | data / nvs | 0x9f0000 | 0x10000 |
| storage | data / fat | 0xa00000 | 0x400000 |
| coredump | data / coredump | 0xe00000 | 0x010000 |

パーティション表は 0x8000、ブートローダは 0x0、フラッシュ全体は 16 MiB とする。
アプリスロット内のメタデータ予約は行わない。0xe10000〜0x1000000 の1,984 KiB は未割り当てで残す。

`multifirm_nvs` は、将来の RTC アラーム表やタイムゾーンなど、実機上で複数のアプリが読み書きする共有状態のために予約する。書く主体を分けるため、`multifirm_meta` は PC ツールが書いて実機は読むだけ、`multifirm_nvs` は実機が書いて PC ツールは `initial` で消すだけとする。
Arduino コアの起動時消去は既定の `nvs` パーティションだけを対象とするため、ラベル指定で開く `multifirm_nvs` は7章の全消去の影響を受けない。v1 のライブラリとツールはこの領域を読み書きせず、`install` と `install-host` は内容を保持する。将来 API を追加する際は、`nvs_flash_init_partition("multifirm_nvs")` でラベル指定して開き、既定の `nvs` と混同しない。
採用 CSV は [partitions.multifirm.csv](partitions.multifirm.csv) に置く。現時点では設計用であり、各アプリへの適用・実機への書き込みは実装フェーズで行う。

判断の基準となった UserDemo のビルド済みイメージ（2026-09-15）は3,741,280 bytesで、4 MiB に約442 KiBの余裕がある。統合後の再ビルドでも上限を検査する。

旧3ゲスト配置からもアプリの境界が変わるため、全体バックアップ後に `initial` と全ゲストの再インストールを行う。ゲストの生パーティションを新位置へ丸ごと復元しない。
旧 NVS 領域に続く旧 otadata 等も新 NVS に含まれるため、移行時の NVS は初期化する。
旧 `recover` の消去先 0xd000 は新配置では NVS 内である。旧ツールを使用せず、全変更系コマンドで新しい表の一致を確認する。

`storage` はホスト専用ではなく、将来ゲストからも共有可能。今回は共有 API を実装しないが、共有時は FAT / wear levelling 設定を統一し、アプリ別ディレクトリを使い、マウント失敗時の自動フォーマットを禁止する。切り替え前にファイルを閉じる。

`layout.py` と `multifirm_layout.h` の定数は一致検査を行い、Python が生成した固定テストデータを C++ でも読む。
PC ツールは正規のパーティション表バイナリとの一致を検証する。比較する長さ、MD5、末尾パディングの扱いを固定し、ホスト側のレイアウト判定も同じ条件にそろえる。
旧2アプリ配置、旧3ゲスト配置、単体配置を、更新コマンドが暗黙に移行させることはしない。

## 5. PC ツール

### 5.1 コマンド

```text
multifirm.py status --port COMxx [--verify]
multifirm.py inspect <firmware.bin>
multifirm.py install --slot {1,2,3} <firmware.bin> [--name "表示名"] --port COMxx [--execute]
multifirm.py install-host <host.bin> --port COMxx [--execute]
multifirm.py recover --port COMxx [--execute]
multifirm.py backup --port COMxx [--out <dir>]
multifirm.py initial --host-build <dir> --port COMxx [--execute]
```

| コマンド | 動作 |
|---|---|
| `status` | 既定は表・先頭セクタ（ヘッダ、app_desc）・メタデータのみを読む。名前候補と採用元、version、idf_ver、ELF SHA を表示し、内容未検証・メタデータ名は暫定と明記する。`--verify` で実イメージを読み戻し、イメージ検証とメタデータ照合を行う。 |
| `inspect` | 実機に接続せず、単体アプリイメージの構造、chip_id、容量、ヘッダ、app_desc、チェックサム・付加 SHA を検証する。イメージは変換しない。 |
| `install` | イメージ・容量・実機の表を検証し、指定スロットと対応するメタデータセクタだけを更新する。NVS・`multifirm_nvs`・otadata・他スロットは書かない。 |
| `install-host` | 表を検証し、検査済みホストイメージを `ota_0` のみに書く。既存の全体バックアップと他スロット検証の保護を引き継ぐ。 |
| `recover` | 主用途は、起動するが操作で戻れないゲストからの復帰。共存表とホストを検証後、otadata（0x19000、0x2000 bytes）のみ消去する。ホスト破損には `install-host` が必要。破損ゲストからの自動フォールバックはブートローダにもあるが、その行き先が常にホストとは限らない。 |
| `backup` | レイアウトに関係なく 16 MiB 全体を読み、サイズ、SHA-256、機体識別情報、ツールの版を添えて保存する。 |
| `initial` | 全体バックアップを保存・検証し、新配置のブートローダ・表・ホストを書き、NVS・`multifirm_nvs`・otadata・全ゲスト領域・メタデータを初期化する。PHY データは必要な構成なら対応データを書き、不要な構成では消去する。storage・coredump は書かない。 |

### 5.2 実行と検証の契約

- `--execute` は `install`、`install-host`、`recover`、`initial` の変更系コマンドに適用する。省略時は実機へ接続せず、ローカル検査と書き込み予定の表示だけを行う。実機レイアウトは未検証と明記する。
- 変更系コマンドには `--check-device` も用意する。`--execute` なしでこれを指定した場合だけ、実機の表を読み取って検証する。書き込み・消去はしない。`--port` の存在だけでは接続しない。`initial` では現状の表を記録し、入力する新しい表を検証する。実行時は表を再確認する。
- `status` と `backup` は `--execute` なしで実機を読む。読み取りでも接続時のリセットやダウンロードモードへの移行は起こり得る。終了時のリセット方針をログへ記録する。
- 更新系コマンドは書き込み直前に共存表を確認し、不一致なら書かない。
- `initial` は既存表の一致を要求しない例外とする。代わりに入力ビルドの表・ブートローダ・ホスト・各書き込み範囲を検証し、既存データのバックアップ完了前には書かない。
- `initial` の「storage・coredump に書かない」は物理範囲を変更しないという意味であり、旧レイアウトのデータが新レイアウトで使えることを保証しない。
- `initial` 後はホストのみを起動対象とし、ゲストは空から再インストールする。旧配置を保持する移行モードは v1 に含めない。
- merged bin、ブートローダ単体、不正なセグメント長、異なる chip_id、未対応の署名付きイメージなどは拒否する。容量だけで対応可否を決めない。
- 入力ファイル、書き込み範囲、読み戻し結果、ファイル全体の SHA-256、付加ダイジェスト、使用ツールの版をログへ残す。
- 書き込み後はイメージとメタデータを読み戻し、予定した bytes と一致した場合だけ成功とする。消去は対象領域が消去済みであることを確認する。

`install` は次の順序とし、処理全体を通じて途中の自動リセットを抑止する。

1. 対応するメタデータの1セクタを消去し、古い名前を無効化する。
2. 対象ゲストスロット全域を消去する。v1 は余白の残骸を残さない単純な手順を優先する。
3. イメージを書き込み、読み戻してバイト一致とイメージの妥当性を検証する。
4. メタデータを最後に書き、読み戻して検証する。

更新は原子的ではない。中断点に応じて旧イメージ・空・不完全イメージ・新イメージだが名前なしになり得る。メタデータなしだけを理由に Invalid とせず、実イメージを検証する。どの中断点からも同じ `install` を再実行できるようにする。
検証失敗時は成功扱いでアプリへリセットしない。選択中のゲストを更新して途中停止した場合は、必要に応じてダウンロードモードへ入り、`recover` または再インストールを行う。
ホスト破損には `recover` だけでは対応できないため、ホスト再書き込みとバックアップ復元の手順も用意する。

`install-host` はブートローダを書かない。v1 のブートローダ更新経路はバックアップ後の `initial` とゲスト再インストールに限定し、NVS を保持した単独更新オプションは設けない。通常のホスト更新でブートローダ更新が必要とは限らず、対応組み合わせを確認する。

### 5.3 表示名

インストール時の優先順位:

1. `--name`。
2. `.bin` の `project_name`。空文字や `firmware`、`arduino-lib-builder` など既定値は除外する。
3. 有効な名前がなければエラーにして `--name` を要求する。

表示名は UTF-8 で最大31 bytes、空文字・埋め込み NUL・制御文字は不可。超過時は黙って切り詰めず拒否する。
既定 project_name の判定と表示名のフォールバック規則はツールとホストでそろえる。

### 5.4 Python 環境

検証済み Python と esptool のバージョンを MultiFirm 側で記録・固定する。
`multifirm.ps1` は MultiFirm 用の環境または明示指定された Python を使用し、起動時に依存の版を確認する。
UserDemo の `.tools` 環境も、固定した版を満たす場合には選択可能とするが、必須にはしない。

### 5.5 イメージヘッダの扱い

v1 はヘッダ正規化機構を作らず、検証済みの標準ビルドをそのまま書く。ヘッダの mode / frequency / size は `inspect` に表示するが、QIO であることだけを理由に拒否しない。
Phase 0 で検証用ゲストの標準ビルドを実機確認する。QIO ヘッダなどの設定差は検証条件として記録する。問題があれば原因を切り分け、必要な設定はアプリの標準 env に明示して再ビルドする。PSRAM 等のビルド設定までヘッダ変更で解決できるとは扱わない。
既知の不適合条件は拒否し、未確認の設定は警告する。対応条件は実際の SDK・ボード設定・ブートローダの組み合わせで記録する。

## 6. スロットメタデータ

### 6.1 配置と形式

専用パーティション `multifirm_meta` をラベル・type・subtype と新レイアウトの一致で特定する。各ゲストに1セクタを割り当てる。

| 対象 | アドレス | サイズ |
|---|---:|---:|
| ota_1 | 0x1c000 | 0x1000 |
| ota_2 | 0x1d000 | 0x1000 |
| ota_3 | 0x1e000 | 0x1000 |
| 予約（書き込まない） | 0x1f000 | 0x1000 |

レコードはリトルエンディアンの固定バイト列であり、C++ 構造体のアラインメントや生のメモリ配置には依存しない。

| オフセット | 型 | 名前 | 内容 |
|---:|---|---|---|
| 0x00 | u32 | magic | `0x4D52464D`、バイト列 `MFRM` |
| 0x04 | u16 | version | 1 |
| 0x06 | u16 | length | CRC を除く長さ。v1 は `0x74` |
| 0x08 | char[32] | name | UTF-8、NUL 終端、未使用 bytes は 0 |
| 0x28 | u32 | image_size | 付加ダイジェスト終端までのイメージ長 |
| 0x2C | u8[32] | elf_sha256 | app_desc の app_elf_sha256。未設定なら全 0。診断用 |
| 0x4C | u8[32] | app_digest | ESP アプリイメージの検証済み付加 SHA-256 |
| 0x6C | u64 | installed_at | Unix 秒。表示・記録用 |
| 0x74 | u32 | crc32 | 0x00〜0x73 の CRC-32/ISO-HDLC |

レコードは 0x78 = 120 bytes、セクタの残りは 0xFF とする。
CRC は反転多項式 `0xEDB88320`、初期値 `0xFFFFFFFF`、最終 XOR `0xFFFFFFFF` とし、Python の `zlib.crc32` と同じ結果にする。
テストベクトル `123456789` の結果は `0xCBF43926` とする。

### 6.2 ホストと PC の共通判定

1. 読み取り成功、magic、version、length、CRC、名前の形式を確認する。
2. `image_size` が正の値で有効容量以下であることを、加算や読み取り前に検証する。
3. ホストは `esp_image_verify()` でイメージ構造・内容を検証し、付加 SHA が存在すること、返されたイメージ長が `image_size` と等しいことを確認する。検証済み `image_digest` と `app_digest` を比較する。メタデータ中の長さを信頼して検証範囲を決めない。
4. `elf_sha256` が設定されていれば app_desc とも照合する。ただし ELF SHA 一致で実イメージ検証を省略しない。

付加 SHA はダイジェスト自身を除く ESP イメージ形式所定の範囲に対するハッシュであり、ファイル全体の SHA とは区別する。v1 の入力ファイルは付加ダイジェスト終端までとし、その後に余分な bytes を持つものは拒否する。形式内部のパディングは許容する。
PC の `inspect` / `install` / `status --verify` も同じ形式に従って SHA を計算・検証する。ファイル全体の SHA はログ・読み戻し一致の確認用とする。
通常のホスト走査はイメージ検証で計算した SHA を再利用し、別の全量ハッシュ処理を重ねない。イメージ全体を RAM へ読み込まない。
確認した IDF はデバッガ接続中に単純 SHA 比較を省略するため、その場合は形式所定の範囲を明示的に逐次ハッシュして付加 SHA と照合する。検証省略を検証成功として扱わず、デバッガあり・なしの両方で破損検出を試験する。

構造検査や SHA 比較に失敗したメタデータは表示名に採用しない。未知の version は破損と同様にフォールバックする。
メタデータの有効性と ESP イメージの起動可能性は別に判定する。メタデータがなくても、有効なイメージなら起動できる。

ホストの `inspectSlot` と `scanSlots` は実データを読み直す。PC の通常 `status` は内容未検証とし、同じ判定を求める場合は `status --verify` を使う。
キャッシュは実測後の改善候補とし、導入する場合は更新・再走査時の無効化条件を定義する。

## 7. 共有 NVS の取り扱い

ホスト・ゲストのアプリ設定は、それぞれ固有の名前空間を使う。
名前空間が分かれていても、`nvs_flash_erase()` は全体を消すため、名前空間分離だけでは保護できない。

**IDF 環境を推奨し、Arduino コアにはパッチしない。** 確認した Arduino コアの `initArduino()` は `setup()` より前に、`NO_FREE_PAGES` または `NEW_VERSION_FOUND` で NVS 全体を消す。Arduino ゲストを使用する場合は、他アプリの設定や BLE ボンド情報を含む共有 NVS 全体が消える可能性を許容する。専用の `multifirm_meta`、`multifirm_nvs`、storage はこの NVS 消去の対象外である。
以下の自動消去禁止は、管理可能なアプリコードと IDF 初期化経路に適用する。Arduino コアについて同じ保証はしない。

- アプリの設定初期化は、自分が所有する名前空間内のキーだけを消し、commit する。
- **NVS 全体の初期化に成功し、名前空間を開ける場合**は、その名前空間だけで復旧する。
- **`nvs_flash_init()` 自体が失敗した場合**は、名前空間を開けるとは限らない。名前空間だけの消去で直るとは仮定せず、自動全消去を行わずにエラーを通知する。
- NVS が使えない場合の動作は、設定保存を停止して既定値で動くか、依存機能を停止するかを各アプリで定義する。BLE 等の依存処理へ未初期化のまま進まない。
- NVS 全消去は「全アプリの設定初期化」として明示した操作、または `initial` に限定する。個別アプリのリセットとは区別する。
- UserDemo の `Hal::init`、`Hal::factoryReset`、設定 AP の初期化処理を見直す。BLE/Wi-Fi ライブラリの内部名前空間も確認する。
- 各アプリの名前空間・保存量を一覧化し、0x10000 bytes（16セクタ）の共有 NVS で `nvs_get_stats()` を実測する。全アプリ設定・BLE ボンド等を保存してから、繰り返し更新時の余裕も確認する。
- 各 SDK の NVS フォーマット版と利用するデータ形式の相互互換性を確認する。IDF のバージョン番号が違うだけで非互換とは判断しない。
- 通常書き込み時の `NOT_ENOUGH_SPACE` と、初期化時の `NO_FREE_PAGES` を区別する。容量拡張・互換性確認はリスク低減策であり、Arduino コアの全消去を防ぐ保証ではない。

## 8. ライブラリ API

以下は API の設計案。エラー型やヘッダ構成は実装時に調整するが、レイアウト判定、再検証、失敗時の非再起動という契約は維持する。

### 8.1 ゲスト

```cpp
namespace multifirm::guest {
using Shutdown = void (*)(void*);
void checkStartupEscape();
bool returnToHost(Shutdown shutdown = nullptr, void* context = nullptr);

class ReturnGesture {
public:
    bool update(bool a, bool b, bool touched, uint32_t nowMs);
};

bool poll(bool a, bool b, bool touched, uint32_t nowMs,
          Shutdown shutdown = nullptr, void* context = nullptr);
}
```

- `checkStartupEscape` は M5.begin() より前に呼び、KEY.B（GPIO1、active low）を確認する。
- `returnToHost` は共存レイアウト、実行中スロット、`ota_0` の存在とイメージを確認する。
- 共存レイアウトでない場合や、既に `ota_0` で動作している場合は起動先を変更せず false を返す。単体配置に `ota_0` が存在していても、それだけでホストとみなさない。
- イメージの検証と起動先設定に成功した後にだけ `shutdown` を呼び、再起動する。失敗はログに残す。
- A+B をタッチなしで1600ms保持すると、1回の保持につき1回だけ発火する。ボタン解除・タッチによるキャンセルと再受付、時刻の wraparound をテストする。
- `poll` はジェスチャ判定と `returnToHost` を組み合わせる。成功時は再起動するため、呼び出し元が true の戻りを受け取れることに依存しない。
- GPIO と保持時間は既定値を公開し、変更方法を明示する。
- shutdown は同期実行し、context を保存しない。関数ポインタと context によりインスタンスの停止処理も渡せるようにし、`std::function` は必須にしない。
- 依存は ESP-IDF の OTA・partition・GPIO・system 等に限り、M5Unified や UI に依存させない。

### 8.2 ホスト

```cpp
namespace multifirm::host {
using Shutdown = void (*)(void*);
enum class SlotState { Empty, Invalid, Ready, ReadError };

struct SlotInfo {
    int index;                      // 1..3
    const esp_partition_t* partition;
    SlotState state;                // メタデータとは独立したイメージ検証結果
    std::string name;
    std::string project_name;
    std::string version;
    bool meta_valid;
    esp_err_t error;
};

bool isMultiFirmLayout();
esp_err_t scanSlots(std::vector<SlotInfo>& slots);
esp_err_t inspectSlot(int index, SlotInfo& slot);
esp_err_t bootSlot(int index, Shutdown shutdown = nullptr, void* context = nullptr);
}
```

- ホストは `ota_0`、列挙対象は `ota_1`〜`ota_3`。非対応レイアウトや不正 index はエラーを返す。
- app_desc が読めることだけでは `Ready` にしない。起動先を変更しないイメージ検証を走査時に実施する。
- `Empty` は先頭4 KiBが全0xFFである状態とする。全スロットが消去済みであることは意味しない。先頭セクタにデータがあり magic が不正な場合は `Invalid`、読み取り失敗は `ReadError` とする。
- 表示名は有効メタデータ → 有効な project_name → `App{n}`。名前の採用元と無効理由も診断できるようにする。
- `bootSlot` は index からパーティションを再取得し、レイアウトとイメージを再検証する。以前の `SlotInfo` の内容だけで起動しない。
- `esp_ota_set_boot_partition` の成功後に shutdown・再起動する。エラー時は再起動しない。
- metadata の対応付けにはイメージ検証のダイジェストを再利用する。起動直前の再検証は走査とは別に行い、起動時の速度は実測する。
- partition・OTA・app format・イメージ検証・SHA 計算に必要な IDF コンポーネントへ依存し、mooncake、LVGL、M5GFX からは独立する。

### 8.3 UserDemo への組み込み

- `AppOtaSlot` を `SlotInfo` と AppAbility をつなぐラッパーにする。
- `main.cpp` は走査結果をループしてアプリを生成し、`AppApp1/2/3` の個別クラスを削除する。
- 空・不正・読み取り失敗のスロットは選択不可にし、既存の `disabled_app_ids` を利用する。
- 起動失敗の理由を UI に通知し、ランチャーへ戻れるようにする。
- NVS の個別初期化と全体初期化の扱いを7章にそろえる。

## 9. ビルドと書き込み保護

標準 env で作った同じアプリイメージを、単体動作と共存動作に使用する。
共存専用 CSV と env は、標準ビルドの動作確認後に削除する。**upload guard は各ゲスト内の小さなローカルスクリプトとして残し、契約とひな形を統一する。**

- 共存スロットへの書き込みは `multifirm.py install` を使用する。
- 通常の `upload`、`uploadfs`、`uploadfsota`、`erase` は保護対象とし、既定では拒否する。
- 単体用の全体書き込みは `custom_allow_full_upload = yes` の明示的な opt-in を必要とする。既定値は no とし、共存表・ホスト・共有データを上書きし得る操作であることを表示する。
- 各ゲストの `extra_scripts = pre:tools/upload_guard.py` でリポジトリ内の guard を読み込む。`lib_deps` 内のコードを実行時に探索せず、依存取得前から保護する。パス不備はビルドエラーにする。
- ビルド後の `.bin` に対してゲスト上限0x1f0000を検査し、超過ならビルドを失敗させる。`board_upload.maximum_size` はビルダーに上書きされる場合があるため、その指定だけに依存しない。install 側の容量検査も残す。
- 専用ビルドフラグによる画面表示等は、必要に応じて実機レイアウトの判定へ置き換える。

```cpp
#include <multifirm_guest.h>

void setup() {
    multifirm::guest::checkStartupEscape();
    // M5.begin() とアプリ初期化
}

void loop() {
    M5.update();
    multifirm::guest::poll(M5.BtnA.isPressed(), M5.BtnB.isPressed(),
                         M5.Touch.getCount() != 0, millis(),
                         [](void*) { /* 音・振動等を停止 */ });
    // アプリ処理
}
```

```powershell
pio run
..\M5StopWatch-MultiFirm\tools\multifirm.ps1 install --slot 2 .pio\build\m5stopwatch\firmware.bin --name "MyApp" --port COM11 --execute
```

## 10. 実施手順

### Phase 0: 標準ビルドと共有資源の確認

1. 使用する機体と既存フラッシュをバックアップし、ブートローダ、表、各アプリの SHA と IDF バージョンを記録する。
2. 検証用ゲストの標準ビルドをオフライン検証し、現行のパーティション表を確認した試験機の ota_1 から起動・周辺機器・復帰を確認する。QIO ヘッダを含む設定差も記録する。この予備試験を旧配置で行う場合は対応する現行手順を使用し、新旧アドレスを混在させない。
3. 起動に問題がある場合だけ原因を切り分け、標準 env の設定変更を検討する。正規化機構の実装や正規化前後の比較試験は既定の作業に含めない。
4. ESP-IDF ゲストを基本に、Arduino ゲストも検証対象に含め、標準ビルドとブートローダの対応表を作る。対象は特定のアプリ名に固定しない。
5. app_desc と付加 SHA を実物で確認し、PC で再計算した SHA が付加ダイジェストに一致することを確認する。パーティション表位置・flash size override の設定も記録する。
6. NVS 名前空間・フォーマット互換性・全消去経路と `nvs_get_stats()` を記録する。新配置導入後にも全アプリの設定保存・反復更新で再測定する。
7. 対応ビルド条件・esptool の版を固定し、UserDemo の再ビルドが4 MiB以下であることを確認する。

新レイアウトと Arduino コア無変更の方針は確定済み。標準ビルド統一を断念する変更や、追加のレイアウト変更が必要と判明した場合にのみ再判断する。

### Phase 1: 共通ツールとレイアウト

1. MultiFirm のライブラリ構成、Python 環境、レイアウトとメタデータの定義を整える。
2. `inspect`、`backup`、`status`、`install`、`install-host`、`recover`、`initial` を実装する。
3. メタデータ無効化 → スロット消去 → イメージ書き込み・検証 → メタデータ確定の順序と、`status --verify` / `--check-device` を実装する。
4. PC テストで容量、異常イメージ、レイアウト不一致、dry-run、書き込み範囲、読み戻し失敗を確認する。
5. バックアップから新配置を初期導入し、各ゲストを再インストールする。更新・復旧経路と新 NVS の使用量を確認する。旧ツールは移行完了まで保管するが、新配置機体には使わない。

### Phase 2: ゲスト復帰と書き込み保護

共通基盤と最小サンプルの実装・検証は [Phase 2記録](phase2-result.md) を参照。実アプリ移行とUserDemoのNVS修正は後続作業とし、ホスト役割のビルド確認はPhase 3で行う。

1. 復帰処理と ReturnGesture を共通化し、Arduino・ESP-IDF の両方でビルドする。
2. ローカル guard と単体書き込みの opt-in、ビルド成果物の容量検査を実装する。
3. まず最小の検証用ゲストで確認し、その後に対象ゲストを順次移行する。各ゲストで標準ビルドからのインストールと往復操作を確認する。
4. IDF / アプリ側の NVS 自動全消去と個別設定リセットを修正する。Arduino コアにはパッチせず、既知の全消去リスクを明記する。
5. `MULTIFIRM_HOST` 未定義のゲストビルドでホスト実装が空になることと、ヘッダの複数翻訳単位での使用を確認する。PlatformIO と ESP-IDF コンポーネントの両方の取り込み方法で、ゲスト・ホスト両方の役割をビルドする。

### Phase 3: ホストライブラリと UI

実装と検証状況は [Phase 3記録](phase3-result.md) を参照。

1. スロット状態判定、イメージ検証結果を再利用したメタデータ照合、表示名決定、起動処理を実装する。
2. Python / C++ のレコード解釈とハッシュ結果の一致を検査する。
3. UserDemo をホスト API に接続し、App1〜3 の登録を共通化する。
4. メタデータあり・なし・破損・イメージ差し替えを確認する。
5. 3スロット走査の時間と UI の応答を測り、必要なら UI 側で読み込み中を表示する。

### Phase 4: 移行完了とドキュメント

実装と検証状況は [Phase 4記録](phase4-result.md) を参照。項目1〜3と UserDemo の NVS 修正までを
実施し、項目4〜5（運用手順の統合とリリースタグ）は後続とする。

1. 代替経路の検証完了後、各ゲストの共存 env・CSV・重複した復帰実装・旧ツールを削除する。
2. 各ゲスト内の guard を統一した契約にそろえ、ローカルスクリプトとして残す。
3. `app_name.py` 等は他の用途がないことを確認してから削除する。vibewatch のみが持っており、
   表示名はメタデータが担うため削除した。以後 Arduino ゲストの `install` には `--name` が要る。
4. 初回導入、ゲスト更新、ホスト更新、単体用書き込み、復旧、バックアップ復元を文書化する。
5. 各アプリの README は MultiFirm の手順へリンクし、リリースタグを固定する。

## 11. 検証と完了条件

### PC テスト

- [ ] Python / C++ の配置定数、CRC、レコードの解釈が一致する。
- [ ] PC とホストが同じ付加 SHA を検証し、メタデータの app_digest と照合する。ファイル全体の SHA と混同しない。
- [ ] image_size の 0・容量超過、未知 version、不正 length、CRC 不一致、UTF-8 不正、NUL 不足を拒否する。
- [ ] 容量判定はホスト0x400000・ゲスト0x1f0000を上限とし、上限+1 byteを拒否する。ビルド後と install の両方で検査する。
- [ ] 付加 SHA なし、付加ダイジェストより後に余分な bytes がある入力を拒否する。
- [ ] chip_id 不一致、切断されたイメージ、破損したセグメント・チェックサム・付加 SHA を拒否する。
- [ ] 変更系コマンドの通常 dry-run は実機接続しない。`--check-device` 付きでは読み取り検証だけを行い、消去・書き込みはしない。
- [ ] `initial` 以外は単体配置・旧2アプリ配置・旧3ゲスト配置を拒否する。
- [ ] `status` 既定表示を検証済みと誤表示せず、`--verify` でホストと同じ判定になる。
- [ ] IDF 非依存の C++ テストをネイティブ CMake/CTest で実行できる。
- [ ] 読み戻し不一致や通信失敗を成功扱いにしない。
- [ ] ジェスチャの閾値、途中解除、タッチ、連続保持、時刻 wraparound を確認する。

### 実機テスト

- [ ] 検証対象の各ゲストを `ota_1`〜`ota_3` の全スロットで起動できる。
- [ ] 正常メタデータの名前、メタデータなしの project_name、既定名の AppN フォールバックが一致する。
- [ ] メタデータ破損だけなら正常イメージは起動できる。
- [ ] 別イメージへの差し替え後、古い名前を採用しない。
- [ ] app_desc / ELF SHA を残して後半を破損させたイメージを検出し、起動不可と表示する。
- [ ] デバッガ接続中も SHA 検証を省略せず、同じ破損を検出する。
- [ ] 空・不正・読み取り失敗を区別し、起動不可のスロットを選択できない。
- [ ] A+B 1.6秒で復帰する。1秒で解除、保持中のタッチでは復帰しない。
- [ ] KEY.B 起動でゲスト画面を経由せずホストへ戻れる。アプリ初期化以前の障害を救済できる機能とは扱わない。
- [ ] 単体配置で復帰 API が起動先を変更せず no-op になる。
- [ ] `install` 前後で他スロット・他ゲストのメタデータ・NVS・`multifirm_nvs`・otadata の bytes または SHA が一致する。
- [ ] 上記の不変性確認はアプリ再起動前に行い、通常起動による NVS 更新と区別する。`status` 比較だけで代用しない。
- [ ] `install-host` がゲスト・NVS・`multifirm_nvs`・otadata を保持する。
- [ ] `recover` で正常なホストへ戻り、破損ホストの場合は書き換えが必要と通知する。
- [ ] 書き込み途中停止後に再インストール・復旧できる。ホスト更新の失敗にはホスト再書き込み手順を確認する。
- [ ] 各アプリに設定を保存し、正常な初期化での往復・個別リセット後も他アプリの設定が残る。
- [ ] IDF / 管理可能なアプリ経路は NVS 初期化失敗時に自動全消去せず、定義したエラー動作になる。
- [ ] Arduino の全消去経路が残ることを記録し、試験用 NVS の明示的な全消去後も、アプリが既定設定で復旧でき、メタデータ・`multifirm_nvs`・storage が保持されることを確認する。
- [ ] 64 KiB の NVS 使用量とフォーマット互換性を確認し、空き容量不足のエラーと初期化失敗を区別する。
- [ ] 標準 upload 等を guard が拒否し、明示した単体用 opt-in だけが全体書き込みへ進める。
- [ ] 10往復して音・振動・表示・設定に異常がない。
- [ ] 走査時間、ハッシュ計算時間、使用したブートローダと各ビルドの版を記録する。

## 12. 未確定事項と判断時点

| 項目 | 判断する時点 | 判断材料 |
|---|---|---|
| 対応するビルドとツールの版 | Phase 0 | 実物の IDF・Arduino・esptool とブートローダの組み合わせ |
| 標準 env の追加設定の要否 | Phase 0 | QIO ヘッダの標準ビルド等の実機結果。ツールによる正規化は実装しない |
| NVS 異常時の各アプリの継続範囲 | Phase 0〜2 | IDF は自動全消去しない。Arduino コアの全消去リスクは許容し、消失後の既定値を確認 |
| ハッシュ計算の UI への影響 | Phase 3 | 全3スロット走査時間の実測 |

ホスト4 MiBの新配置、専用メタデータ、予約のみの `multifirm_nvs`、Arduino コア無変更、ローカル guard、付加ダイジェストの再利用は確定事項。追加のレイアウト変更や標準ビルド統一を断念する変更は、結果と選択肢を整理して判断する。

## 13. 参考資料

- [ESP-IDF v5.5: Bootloader / SPI Flash Configuration / Compatibility](https://docs.espressif.com/projects/esp-idf/en/v5.5/esp32s3/api-guides/bootloader.html)
- [ESP-IDF v5.5: Partitions API](https://docs.espressif.com/projects/esp-idf/en/v5.5/esp32s3/api-reference/storage/partition.html)
- [ESP-IDF v5.5.4: S3 flash 初期化の実装](https://github.com/espressif/esp-idf/blob/v5.5.4/components/bootloader_support/bootloader_flash/src/bootloader_flash_config_esp32s3.c)
- [ESP-IDF v5.5.4: アプリ起動処理](https://github.com/espressif/esp-idf/blob/v5.5.4/components/esp_system/port/cpu_start.c)
- [ESP-IDF v5.5.4: イメージ検証とデバッガ接続時の分岐](https://github.com/espressif/esp-idf/blob/v5.5.4/components/bootloader_support/src/esp_image_format.c)
- ゲスト側の既存実装: [M5StopWatch-MuteHid](https://github.com/wararyo/M5StopWatch-MuteHid/tree/e018658e69c8468fc208dce5a4ff984bbe45f32a), [M5StopWatch-KantanPlay](https://github.com/wararyo/M5StopWatch-KantanPlay/tree/c2d6606e90959cd7e4b8e80ca17a87a4392ba075)

実装時は上記の一般仕様と、Phase 0 で固定した実際の SDK・ツールの挙動を照合する。
