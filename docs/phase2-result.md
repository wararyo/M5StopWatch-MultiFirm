# Phase 2記録：ゲスト復帰・書き込み保護・最小サンプル

最終更新：2026-09-19。対象はMultiFirm内の共通基盤と最小サンプル。導入・操作は [examples/README.md](../examples/README.md) を参照。

## 実装範囲

- `return_gesture.h`：IDF非依存、既定1600ms、取消・再受付・1保持1回・32bit時刻wraparound。
- `multifirm_guest.h`：KEY.B起動脱出、同期shutdown/context、毎回検証してからホストへ復帰。ヘッダオンリー・C++11互換。
- `detail/esp_validation.h`：v1表の完全一致、範囲を制限したイメージ読み取り、独自のXORチェックサム・SHA再計算、IDF構造検証。検証済みイメージ長と付加ダイジェストを返す。
- `examples/upload_guard.py`：PlatformIO preスクリプト、4書き込みターゲットの既定拒否、単体用opt-in、ゲストbin容量検査。
- Arduino / ESP-IDF最小サンプル。通常のIDFコンポーネント取り込みにも対応し、同一ヘッダを2翻訳単位から利用。

既存ゲスト3本の移行、UserDemoのNVS処理修正、旧ツール・専用env削除は今回の対象外。元計画Phase 2項目5のホスト役割のビルドとホスト実装の空翻訳単位確認は、実装が揃うPhase 3へ送る。`MULTIFIRM_HOST` / `CONFIG_MULTIFIRM_HOST` の切り替えやホストAPIはまだ追加していない。

## 実装上の判断

表は0x8000から0xC00 bytesを128-byte単位で読む。正規エントリ・MD5の固定配列と残りの0xFFを比較し、Python側が生成する表との一致をテストする。ラベルやota_0の有無だけでは共存配置と判断しない。

イメージ検証は最初にヘッダ・全セグメントの範囲を確認し、チェックサム・付加SHAを独立して再計算する。その後 `esp_image_verify()` でSDKの構造・チップ互換性検証を行い、双方のイメージ長も照合する。デバッガ接続によるSDKの検証省略に依存しない。SHAはMbed TLS 2.xの `_ret` APIと3.xのAPIを内部で切り替える。読み取りエラーはそのまま返し、検証結果は失敗時に空のままとする。

復帰は実行中のパーティションがv1のota_1〜3と一致する場合だけ行う。起動先設定に成功するまでshutdownを呼ばず、全失敗経路で再起動しない。起動脱出は失敗したら通常初期化へ戻る。`poll()` の状態はinline関数内staticで共有し、呼び出しは単一タスクを前提とする。

PlatformIOの `checkprogsize` はbin生成より前なので、そこでファイル容量を調べることはできない。guardはbinのpost-actionと、binを依存先に持つ専用aliasを使う。aliasをAlwaysBuildにし、通常ビルド・uploadから依存させることでキャッシュ済みbinも検査する。PlatformIOが後から置き換えるbuildprog/upload本体のactionには依存しない。

サンプルはM5GFXを先に固定してからM5Unifiedを取得する。PlatformIOの取り込みと通常IDFの取り込みは `MULTIFIRM_PLATFORMIO` CMakeオプションで分ける。通常IDFには `multifirm_size_check` ターゲットも追加し、bin生成後に同じ容量検査を実行する。

## PCテスト

Python 56件（既存51件＋追加5件）、CMake/CTest 5ターゲットが成功。C++のレイアウトテストは既存のメタデータ22ケースを含む。

追加した確認：

- 1599/1600ms境界、片方解除、タッチ取消、再保持、連続保持、wraparound、保持時間の変更。
- v1の全3ゲストからの正常復帰、単体OTA表・旧2アプリ表・旧3ゲスト表・消去済み表の拒否、MD5・パディング破損。
- ホスト自身・不明な実行中スロット・ホスト欠落、読み取り失敗、IDF検証失敗、起動先設定失敗。shutdown/contextと再起動の順序。
- 不正ヘッダ、chip_id、app_desc、付加SHAなし、不正セグメント長、後半破損、XOR値を維持した破損。IDF検証スタブを成功させても独自SHA検証で拒否。
- 全イメージ読み取り呼び出しへの順次エラー注入、パーティション境界外を読まないこと。
- GPIO設定失敗、非押下、起動脱出の既定GPIO・変更GPIO、2翻訳単位間でのpoll状態共有、復帰失敗後の保持中再試行抑止。
- guardの全対象ターゲット、複数指定、opt-in、不正値、容量上限±1 byte、各サンプルへのコピーの一致。

実際のPlatformIOでも、生成済みArduino binを一時的に2,031,617 bytesへ拡張し、`nobuild` と `multifirm_check_size` を組み合わせた読み取り専用のビルド検査で拒否されることを確認した。試験後は元のbinへ復元した。uploadターゲットやシリアルポート接続は実行していない。

再実行（g++ / CMake / NinjaをPATHへ入れた環境）：

```powershell
python -m unittest discover -s tests -v
cmake -S tests -B build/tests -G Ninja -DMULTIFIRM_MBEDTLS_DIR="$env:IDF_PATH/components/mbedtls/mbedtls"
cmake --build build/tests
ctest --test-dir build/tests --output-on-failure
```

`MULTIFIRM_MBEDTLS_DIR` はインストール済みMbed TLSソースを指す。SDKアダプタ試験は実際のポータブルSHA実装を使い、フラッシュ・OTA・GPIO・再起動だけをスタブ化する。指定を省略した場合もIDF非依存のジェスチャ・レイアウト・Pythonテストは動くが、guest試験はスキップされる。今回のguest試験はC++11、その他のC++試験はC++17、g++ 15.2.0で確認した。Release構成でもassertを有効にする。

## ビルドと成果物検査

| 取り込み・フレームワーク | アプリbinのサイズ | 結果 |
|---|---:|---|
| PlatformIO 6.12.0 / Arduino 2.0.17（IDF v4.4.7-dirty）/ lib_deps | 490,816 bytes | ビルド・リンク・容量検査成功 |
| PlatformIO 6.12.0 / ESP-IDF 5.5.0 / lib_deps | 514,416 bytes | ビルド・リンク・容量検査成功 |
| 通常ESP-IDF 5.5.4 / コンポーネント | 499,728 bytes | ビルド・リンク・容量検査成功 |

表のPlatformIO 6.12.0はespressif32プラットフォームの版。すべてM5GFX 0.2.28 / M5Unified 0.2.16を使用し、ホスト定義なしで2翻訳単位をリンクした。3つのbinはPCツールの `verify_app_file()` でも正常（ESP32-S3、app_desc、チェックサム・付加SHA一致、容量内、余分な後続bytesなし）と確認した。

ローカルのビルドログは `build-phase2-arduino.log`、`build-phase2-espidf.log`、`build-phase2-idf-native.log`、容量超過の拒否ログは `build-phase2-guard-oversize.log` に保存した（git管理外）。イメージサイズ・ファイルSHA・付加SHAは `build/phase2-images.json` に保存した。

## 実機で未確認の項目

**Phase 1の実機検証は完了し、問題なく動作したことをユーザーが2026-09-19に確認した。** バックアップ、新配置導入、更新・復旧、UI操作、NVS使用量の詳細は [Phase 1記録](phase1-result.md) の4章を参照。Phase 2の実機試験を進める前提は満たされた。

今回追加したPhase 2サンプルの実機書き込みと基本操作の確認は完了した（次節）。以下は未確認として残す。

- 単体配置のno-op、失敗時の非再起動、各サンプル10往復。
- 実機でのホスト検証時間、デバッガ接続時の破損検出。
- Phase 0で記録したKEY.B復帰後のUserDemo無音事象の観察。再現時はシリアルログを保存し、ホスト側調査へ引き継ぐ。

Arduinoの脱出処理はコア初期化後なので、コアによるNVS全消去を防げない。既知リスクとして維持し、パッチは加えない。ESP-IDFサンプルはNVS初期化失敗時に自動消去せず、周辺機器初期化前で停止する。共有NVS使用量・他アプリ設定保持の評価は、実アプリ移行と実機試験で確認する。

## Phase 2サンプルの実機書き込み（2026-09-19）

ユーザーの許可を得て、Phase 1の検証に使った実機（ESP32-S3 revision v0.2、16 MiB、COM11）に3種類の成果物を書き込んだ。既存のKantanPlay、MuteHid、VibeWatchは各スロットの全域消去後に置き換えた。

| スロット | 表示名 | ビルド | サイズ | 結果 |
|---|---|---|---:|---|
| ota_1 | MultiFirm Arduino | PlatformIO / Arduino 2.0.17 / IDF v4.4.7-dirty | 490,816 bytes | Ready、メタデータ有効 |
| ota_2 | MultiFirm IDF 5.5.0 | PlatformIO / ESP-IDF 5.5.0 | 514,416 bytes | Ready、メタデータ有効 |
| ota_3 | MultiFirm IDF 5.5.4 | 通常ESP-IDF 5.5.4 | 499,728 bytes | Ready、メタデータ有効 |

書き込み前にホストと旧3ゲストを `status --verify` で検証し、すべてReadyであることを確認した。各 `install --execute` は、対象メタデータ消去、対象スロット全域消去、書き込み、読み戻し一致、イメージと付加SHA検証、メタデータ検証まで成功した。各回でNVS、otadata、phy_init、multifirm_nvs、他スロットのメタデータが不変であることも確認した。

書き込み後の `status --verify` では、ホストと上記3ゲストがすべてReadyで、表示名は各メタデータから採用された。起動先はota_0のまま保持された。ログは `.multifirm/logs/20260919-150047-install-slot1.log`、`20260919-150222-install-slot2.log`、`20260919-150351-install-slot3.log`、最終確認は `20260919-150515-status.log` に保存した（git管理外）。

PCから確認できる書き込み・整合性試験に加え、ユーザーが実機上で次を確認し、すべて問題なしと2026-09-19に報告した。

- ランチャーに3つの表示名が現れ、各スロットのサンプルを起動できる。
- A+Bを1.6秒保持するとホストへ戻る。
- タッチしながらA+Bを保持した場合は復帰しない。
- ゲスト起動中に電源を切り、KEY.Bを押したまま起動するとホストへ戻る。

これによりPhase 2の基本的な実機確認は完了。10往復の耐久確認、単体配置でのno-op、意図的な失敗・破損条件の実機確認は別途未実施。
