# Phase 4 実装・検証記録

実施日: 2026-09-20。対象は実ゲスト3本のMultiFirmへの移行と、ホスト（UserDemo）のNVS修正。
運用手順の統合（計画10章 Phase 4 の項目4・5）とリリースタグの固定は後続とする。

固定したMultiFirmのコミットは `a5d9529`。移行中は各ゲストの `lib_deps` をこのSHAでピン止めし、
実機検証が通ってから `v1.0.0` を打ってタグ参照へ切り替える。

## 1. ホスト（M5StopWatch-UserDemo）

| コミット | 内容 |
|---|---|
| `41ad270` | パーティション表をMultiFirm v1レイアウトへ変更（Phase 1で実機投入済みだったが未コミットだった） |
| `f7ed0f3` | 共有NVSの自動全消去をやめ、旧書き込みツールを削除 |

計画7章に沿って、共有NVSを消す経路を全廃した。

- `main/hal/hal.cpp` の `Hal::init`：`NO_FREE_PAGES` / `NEW_VERSION_FOUND` での
  `nvs_flash_erase()` と `ESP_ERROR_CHECK(ret)` を削除した。失敗しても起動を続け、
  `isNvsAvailable()` が false になり、設定は既定値のまま保存されない。エラー種別はログで区別する。
- `main/hal/utils/config_ap/config_ap.cpp`：同じ全消去を削除。NVS初期化に失敗したら
  設定APを起動せず false を返す。Wi-Fi自身の書き込みは `cfg.nvs_enable = false` で元から抑止済み。
- `main/hal/utils/settings/settings.cc`：`ESP_ERROR_CHECK` を全廃した。ハンドル未取得のまま
  `nvs_set_*` を呼んで panic する経路があったため、`Writable()` で弾き、失敗は
  `LogWriteFailure()` でログに残して既定値のまま続行する。デストラクタの commit も同様。
- `Hal::factoryReset`：`nvs_flash_erase()`（パーティション全体）をやめ、UserDemoが所有する
  `system` / `alarm` / `wakeup` / `rtc_test` だけを消す。全体の初期化はPCツールの `initial` が担う。

あわせて、旧アドレス（`0x4f0000`、`0x510000/0x6a0000/0x830000` × `0x190000`）のままだった
`tools/update-userdemo.py` を削除し、`README.md` と `docs/rtc-alarm-usage.md` の手順を
`multifirm.ps1 install-host` へ差し替えた。`README.md` の `idf.py flash` には共存機での禁止を明記した。
`repos.json` のMultiFirmピンを `a5d9529` に更新し、ホストAPI統合で空になった
`main/apps/app_app{1,2,3}/` を削除した。

ビルドは `tools/build-local.ps1`（ESP-IDF v5.5.4）で成功。アプリは 3,747,312 bytes で
ホスト上限 4 MiB に対し 447,000 bytes ほどの余裕がある。

## 2. ゲスト3本

3本とも到達形は同じ。共存専用env・共存パーティション表・独自の復帰実装・旧書き込みツールを削除し、
`lib_deps` にMultiFirmを追加して `tools/upload_guard.py` を `examples/upload_guard.py` と
同一内容に置き換えた。**単独配置と共存スロットで同じ標準ビルドのbinを使う。**

| ゲスト | コミット | フレームワーク | binサイズ | 削除した独自実装 |
|---|---|---|---:|---|
| KantanPlay | `0cb307b` | PlatformIO / ESP-IDF 5.5.0 | 828,816 bytes | `FirmwareSwitch.*`、`ReturnGesture.h` |
| MuteHid | `c88aad9` | PlatformIO / ESP-IDF 5.5.0 | 1,191,904 bytes | `FirmwareSwitch.*`、A+B保持のインライン実装 |
| Vibe Watch | `6c8002c` | PlatformIO / Arduino 2.0.17 | 816,304 bytes | `firmware_switch.*`、`return_gesture.h` |

3本とも `pio run` が成功し、guardの容量検査（`multifirm_check_size`）が走り、
`pio run -t upload` が `custom_allow_full_upload = no` で拒否されることを確認した。
`multifirm.py inspect` は3本とも「受け付け可能」と判定した。

### 共通の挙動変更

- **A+Bの保持時間が 3000ms → 1600ms**（`multifirm::guest::kReturnHoldMs`）になった。
- 復帰は毎回、パーティション表の完全一致・実行中スロット・`ota_0` のイメージを検証してから行う。
  MuteHid が持っていた `project_name == "StopWatch-UserDemo"` の文字列一致検証より強い。
- shutdown コールバックは起動先の設定に成功したあとだけ呼ばれる。署名は `void (*)(void*)`。
- 単独配置では実行中スロットが `ota_1`〜`ota_3` ではないため、復帰操作は何もしない。

### KantanPlay

`m5stopwatch-coexist` env と `partitions.coexist.csv`、`tools/coexist.py` / `coexist.ps1` /
`coexist_upload_guard.py` / `test_coexist.py` / `test_return_gesture.cpp` を削除した。
`docs/11-coexistence-testing.md` はMultiFirm手順へ書き換え、`docs/10-coexistence.md` は
設計の経緯として残した。

### MuteHid

`MUTEHID_COEXIST` マクロを全廃した。これまで共存ビルドと単独ビルドを分け、単独ビルドでは
復帰処理ごと `#if` で除外していたため、標準ビルドでの往復は一度も試験されていなかった。
env は標準ビルド1本になり、`default_envs` も切り替えた。

UI の共存判定を実行時に置き換えた。

- 診断画面の `"coexist build (ota_1)"` 固定表示 → 実行中パーティションのラベル表示。
  以前はスロット2/3で動いていても常に `ota_1` と出ていた。
- 設定画面の復帰項目の有効・無効 → 実行中パーティションが `ota_1`〜`ota_3` かで判定。

`tools/device.py`（`app_name()` 内蔵）、`tools/pio_flash.py`（`-t update` / `-t backup`）、
`tools/test_device.py` を削除した。`tools/icons.py` はUI資産生成なので残す。
`docs/specification.md` の8章は新レイアウトの表とMultiFirmの契約に書き換えた。

### Vibe Watch

`tools/app_name.py` を削除した。ELFの `.flash.appdesc` を書き換えて `project_name` を
`VibeWatch` にしていたもので、共存env専用ではなく標準envでも動いていた。表示名は
`multifirm_meta` が持つため不要になる。

その結果、**`project_name` は `arduino-lib-builder` に戻り、MultiFirmの既定名除外リストに
入るため `install` には `--name` が必須**になった（`inspect` の「表示名候補: なし」で確認）。
メタデータが壊れた場合のランチャー表示は `App3` になる。これはユーザー確認済みの方針。

GitHub Actions の `m5stack-stopwatch-coexist` ビルドと `test_coexist.py` /
`test_return_gesture.cpp` のステップを削除した。標準envのフラッシュヘッダ設定は
Phase 0 で実機確認済みのため変更していない（`inspect` でも DIO/80m/16MB）。

## 3. PCテスト

MultiFirm 側のPythonテスト（`python -m unittest discover -s tests`）は変更なしで成功。
MultiFirm のライブラリ・ツールには今回変更を加えていない。

## 4. 実機検証

**未実施。** 2026-09-20 11:00 の時点で実機がPCに認識されず（COMポートなし、
USB VID 0x303A のデバイスなし）、`status --port COM11` が `could not connect` で失敗した。

実施予定の手順とスロット割り当て:

| スロット | アプリ | `--name` |
|---|---|---|
| ota_1 | KantanPlay | `KantanPlay` |
| ota_2 | MuteHid | `MuteHid` |
| ota_3 | Vibe Watch | `Vibe Watch` |

1. `backup` で16 MiB全体を取得する。
2. `install-host` でNVS修正済みホストを `ota_0` に入れる。
3. 各ゲストを `install --slot N` の3段（計画表示 → `--check-device` → `--execute`）で書く。
4. `status --verify` でホストと3スロットがすべて `Ready`、表示名がメタデータ由来であることを確認する。

実機で確認すること:

- [ ] ランチャーに3つの表示名が出て、各スロットが起動する
- [ ] A+Bを1.6秒保持で戻る。1秒では戻らない。タッチしながらでは戻らない
- [ ] KEY.Bを押しながらの起動で、ゲストの画面を経由せずホストへ戻る
- [ ] 往復時に音・振動・表示が正常（Phase 0 の8.3節の無音事象を観察するためシリアルログを保存する）
- [ ] 各アプリで設定を保存 → 往復 → 残っている（`player` / `mutehid` / `vibe-watch`、
      BLEボンド `bt_config.conf` / `nimble_bond`）
- [ ] `install` 前後で他スロット・他メタデータ・NVS・`multifirm_nvs`・otadata が不変
- [ ] ホストのNVS修正が正常系で回帰していない（起動、設定保存、アラーム）
- [ ] MuteHidは標準ビルドでの往復が初めてなので重点的に確認する（Phase 0 試験Cが未実施だった）

## 5. 後続

- 実機検証の完了後に `v1.0.0` を打ち、3ゲストの `lib_deps` をSHAからタグ参照へ切り替える。
  `library.json` と `tools/multifirm.py` の `TOOL_VERSION`（どちらも `0.1.0`）も合わせる。
- 計画10章 Phase 4 の項目4・5：初回導入・ゲスト更新・ホスト更新・単体用書き込み・復旧・
  バックアップ復元の手順をルートREADMEへ統合する。現状は `tools/README.md` と
  `examples/README.md` に分散している。
- Phase 3 で未確認の実機特殊ケース：無効スロットが選択不可であることの目視、走査後かつ
  起動直前のイメージ変化での失敗画面、連続10往復、デバッガ接続中の破損検出。
