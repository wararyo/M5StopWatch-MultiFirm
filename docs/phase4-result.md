# Phase 4 実装・検証記録

実施日: 2026-09-20（実装）、2026-09-22〜23（実機検証）。実機検証は完了。
対象は実ゲスト3本のMultiFirmへの移行と、UserDemo の NVS 修正。
運用手順の統合（計画10章 Phase 4 の項目4・5）とリリースタグの固定は後続とする。

往復確認の時点では `ota_0` に別のホストが入っていた。2026-09-23 に UserDemo を
書き戻し、NVS 修正の実機動作を確認している（4.4節）。

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

2026-09-22にユーザーが実施し、2026-09-23に結果を確認した。機体はPhase 0以降と同じ
ESP32-S3 rev v0.2、MAC `28:84:85:43:a7:c0`、16 MiB、COM11。

### 4.1 インストール

3本とも今回移行した標準ビルドのbinをそのまま書き込んだ。入力ファイルのSHA-256は
`inspect` の結果と一致する。

| スロット | ログ | 入力 | ファイルSHA-256 | 表示名 |
|---|---|---|---:|---|
| ota_1 | `20260922-063755-install-slot1.log` | KantanPlay 828,816 bytes | `78ed727a…1a2cc855` | `M5StopWatch-KantanPlay`（project_name） |
| ota_2 | `20260922-063930-install-slot2.log` | MuteHid 1,191,904 bytes | `61cbf3ff…66797348` | `M5StopWatch-MuteHid`（project_name） |
| ota_3 | `20260922-064235-install-slot3.log` | Vibe Watch 816,304 bytes | `23468862…a753e7961c` | `VibeWatch`（`--name`） |

3回とも `multifirm-v1` の表を確認したうえで、メタデータ消去 → スロット全域消去 → 書き込みと
読み戻し → メタデータ書き込みと照合まで成功した。

Vibe Watch は予告どおり `project_name` が `arduino-lib-builder` になり、`--name "VibeWatch"`
で表示名を与えている。`app_name.py` の削除が想定どおりに効いていることの実地確認になった。

### 4.2 `status --verify`（2026-09-23 16:58）

ホストと3スロットのすべてが `Ready`、3スロットのメタデータはすべて有効で、
表示名はメタデータから採用された。読み戻した付加ダイジェストは、移行後のビルドを
`inspect` したときの値と3本とも一致する。

| スロット | イメージ長 | 付加ダイジェスト | 状態 |
|---|---:|---|---|
| ota_0 ホスト（当時） | 1,051,136 | `8ab10665…0ee112ae` | Ready |
| ota_1 KantanPlay | 828,816 | `aa139707…9918afc1` | Ready / メタデータ有効 |
| ota_2 MuteHid | 1,191,904 | `e1b34eb1…f4849ded` | Ready / メタデータ有効 |
| ota_3 Vibe Watch | 816,304 | `2748c86b…7640547` | Ready / メタデータ有効 |

起動先は `ota_0`（seq 53）。パーティション表は `multifirm-v1`
（SHA-256 `6c92c0cb…cbae0079`）。

### 4.3 ユーザーによる操作確認

3本すべてについて次を確認し、問題なしとの報告を得た。

- ランチャーから起動できる
- A+B の保持でホストへ戻る
- KEY.B を押しながらの起動でホストへ戻る

**MuteHid の標準ビルドでの往復はこれが初めて**（従来は `MUTEHID_COEXIST=0` の単独ビルドから
復帰処理ごと除外されており、Phase 0 の試験Cも未実施だった）。マクロ廃止と実行時判定への
置き換えが実機で成立することを確認できた。

### 4.4 UserDemo（NVS修正版）のホスト確認（2026-09-23）

実機が空いたあと、UserDemo を `f7ed0f3` からクリーンに再ビルドして `install-host` した。

- イメージ: `StopWatch-UserDemo.bin` 3,747,312 bytes、`V0.5-6-gf7ed0f3`、idf v5.5.4、
  ファイルSHA-256 `21c9ed99…048e2d36`、付加ダイジェスト `8a84f2a6…e7891c59`
- `--check-device`: 表が `multifirm-v1` と一致、既存バックアップ
  `backup-28848543a7c0-20260922-072744.bin` が現状と一致することを確認（書き込みなし）
- `--execute`（ログ `20260923-173310-install-host.log`）: `ota_0` 全域消去 → 書き込み →
  読み戻し一致 → イメージ検証OK。ブートローダ・表・メタデータ・ゲスト3スロットが
  バックアップと一致し、**`nvs` / `otadata` / `phy_init` / `multifirm_nvs` の不変を確認**

書き込み後の起動ログ（USB-Serial/JTAG からリセットして取得）で、計画7章の修正が
意図どおり効いていることを確認した。

- `nvs_flash_init()` が成功し、全消去も abort も起きない
- **ホストを別のファームウェアから UserDemo へ入れ替えたあとも共有NVSの設定が残っている**:
  `brightness loaded from settings: 80`、`volume loaded from settings: 80`、
  `config loaded from settings: sfx=true, vibrate=true`、`launch count: 4 -> 5`、
  アラーム保存領域の読み込み（count 0）
- 3スロットの走査がすべて `state=ready`、`source=metadata`、`meta=valid`
- `play boot sfx` まで到達。Phase 0 8.3節の無音事象は今回発生していない

走査時間は Phase 3（最小サンプル3本）より伸びた。実アプリの方がイメージが大きいため。

| スロット | アプリ | イメージ長 | 検証 | うちSHA-256 |
|---|---|---:|---:|---:|
| 1 | KantanPlay | 828,816 | 515 ms | 209 ms |
| 2 | MuteHid | 1,191,904 | 702 ms | 286 ms |
| 3 | Vibe Watch | 816,304 | 484 ms | 196 ms |
| | 3スロット合計 | | **1,742 ms** | |

Phase 3 の最小サンプル（490〜514 KiB）では 322/318/309 ms・合計989 ms だった。

`status --verify`（ログ `20260923-173836-status.log`）でも ota_0 が
`StopWatch-UserDemo` `V0.5-6-gf7ed0f3` で Ready、3スロットは Ready・メタデータ有効のままだった。

### 4.5 共有NVSの現状（設定保持試験のベースライン）

`nvs`（0x9000、64 KiB）を読み出して名前空間を数えた。16ページ中10ページが初期化済み。

| 名前空間 | エントリ | 所有 |
|---|---:|---|
| `system` | 1 | UserDemo |
| `alarm` | 2 | UserDemo |
| `wakeup` | 2 | UserDemo |
| `launcher` | 2 | 別のホストファームウェア |
| `launcher_drn` | 2 | 別のホストファームウェア |
| `phy` | 5 | 共有（PHY校正） |
| `M5GFX` | 1 | 共有 |
| `bt_config.conf` | 2 | MuteHid（Bluedroid） |
| `nimble_bond` | 2 | Vibe Watch（NimBLE） |

ゲスト自身のアプリ設定（`player` / `mutehid` / `vibe-watch`）はまだ作られていない。

### 4.6 設定保持と往復（2026-09-23）

ユーザーが各アプリで設定を変更したうえで合計10往復を実施し、A+Bの閾値（1600ms）も
問題ないと報告した。その後に同じ要領で `nvs` を読み直した。初期化済みページは10→12。

| 名前空間 | 往復前 | 往復後 | 所有 |
|---|---:|---:|---|
| `system` | 1 | 1 | UserDemo |
| `alarm` | 2 | 2 | UserDemo |
| `wakeup` | 2 | 2 | UserDemo |
| `launcher` | 2 | 2 | 別のホストファームウェア |
| `launcher_drn` | 2 | 2 | 別のホストファームウェア |
| `phy` | 5 | 5 | 共有（PHY校正） |
| `M5GFX` | 1 | 1 | 共有 |
| `bt_config.conf` | 2 | 2 | MuteHid（Bluedroid） |
| `nimble_bond` | 2 | **22** | Vibe Watch（NimBLE） |
| `player` | なし | **3** | KantanPlay |
| `mutehid` | なし | **2** | MuteHid |
| `vibe-watch` | なし | **なし** | Vibe Watch |

確認できたこと:

- **往復前からあった9つの名前空間がすべて残り、エントリ数も変わっていない。**
  ゲストを10往復してもホストや他アプリの設定が失われないことを名前空間単位で確認できた。
- KantanPlay の `player` と MuteHid の `mutehid` が新たに作られ、設定が保存されている。
- `nimble_bond` が 2 → 22 エントリに増えた。Vibe Watch の NimBLE が書いたもの。
- この時点では Vibe Watch の `vibe-watch` が作られていなかった。Vibe Watch でこのとき
  行ったのはペアリングだけで、保存経路を通っていなかったため（4.7節で解決）。

PHY校正データ（`phy` 5エントリ）は往復前後で変わらなかった。IDF 5.5系のホスト・ゲストと
IDF 4.4.7 の Vibe Watch を往復しても、今回は校正データの書き直しが起きていない。

### 4.7 Vibe Watch の設定保存（2026-09-23）

4.6の時点で `vibe-watch` 名前空間がなかったため、Vibe Watch の設定画面で音量スライダと
振動強度スライダを操作してからランチャーへ戻り、`nvs` を読み直した。

| 名前空間 | 直前 | 操作後 |
|---|---:|---:|
| `vibe-watch` | なし | **3** |
| 他の11名前空間 | — | すべて維持（エントリ数も同じ） |

`vibe-watch` が作られ、他の名前空間は影響を受けなかった。初期化済みページは12のまま。
4.6で作られていなかったのは保存経路を操作していなかっただけで、**Vibe Watch 側の不具合ではない。**

Vibe Watch が設定を書くのは、設定画面で音量スライダを離したとき（`saveSeVolume`）、
振動強度スライダを離したとき・エージェント振動をトグルしたとき（`saveFeedbackSettings`）、
PAIRをタップしたとき（`saveDeviceSlot`）の4経路。macOS 側からのペアリングはこれに含まれない。

これで**ゲスト3本すべてについて、自分の設定が保存され、往復しても他アプリの設定が
失われないことを名前空間単位で確認できた。**

### 4.8 未確認のまま残る項目

- Phase 3 から持ち越しの特殊ケース（無効スロットの選択不可、走査後の差し替え、
  デバッガ接続中の破損検出）

Phase 0 8.3節の「KEY.B脱出後に無音」事象は、今回の10往復では報告されていない。

## 5. 後続

- `library.json` の `version` と `tools/multifirm.py` の `TOOL_VERSION` を `1.0.0` に
  合わせた。リリースタグ `v1.0.0` はユーザーがこのコミットに打つ。
- タグを push したあと、3ゲストの `lib_deps` を `#a5d9529` から `#v1.0.0` へ切り替える。
  `a5d9529` 以降に src / tools のコードは変わっていないので、再ビルド確認だけでよく、
  実機への再インストールは不要。
- 計画10章 Phase 4 の項目4・5：初回導入・ゲスト更新・ホスト更新・単体用書き込み・復旧・
  バックアップ復元の手順をルートREADMEへ統合する。現状は `tools/README.md` と
  `examples/README.md` に分散している。
- Phase 3 で未確認の実機特殊ケース：無効スロットが選択不可であることの目視、走査後かつ
  起動直前のイメージ変化での失敗画面、デバッガ接続中の破損検出。
