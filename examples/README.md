# ゲスト最小サンプルと導入

`guest-arduino` は Arduino-ESP32 2.0.17、`guest-espidf` は PlatformIO ESP-IDF 5.5.0 / 通常のESP-IDF 5.5.4を対象にする。どちらもM5StopWatch用で、標準の単体配置でビルドしたアプリbinを共存スロットへインストールする。実機での検証状況は [Phase 2記録](../docs/phase2-result.md) を参照。

## PlatformIOでビルド

リポジトリのルートから実行する。

```powershell
pio run -d examples/guest-arduino -j 2
pio run -d examples/guest-espidf -j 2
```

MultiFirmは `lib_deps = symlink://../..` でこのチェックアウトを参照する。M5GFX 0.2.28、M5Unified 0.2.16を固定している。M5GFXを先に指定し、M5Unifiedの間接依存で別版が先に取得されるのを避ける。

binは各サンプルの `.pio/build/<env>/firmware.bin` にできる。例：

```powershell
python tools/multifirm.py inspect examples/guest-arduino/.pio/build/guest-arduino/firmware.bin
tools/multifirm.ps1 install --slot 1 examples/guest-arduino/.pio/build/guest-arduino/firmware.bin --name "Guest Arduino" --port COM11
```

最後のコマンドはdry-run。実機はPhase 1の `initial` と更新・復旧経路の検証を済ませておき、予定を確認した上で `--execute` を付ける。ESP-IDF版はパスと表示名を置き換える。ゲストのブートローダ・パーティション表は共存機へ書かない。

## 通常のESP-IDFからビルド

ESP-IDF 5.5.4の環境を有効にし、M5依存を先に取得する。`pio pkg install` はパッケージ取得のみで実機に接続しない。

```powershell
pio pkg install -d examples/guest-espidf
cd examples/guest-espidf
idf.py -B build-idf -D IDF_TARGET=esp32s3 build
```

M5ライブラリは既定で `.pio/libdeps/guest-espidf/M5GFX` と `M5Unified` を使う。別の取得先を使う場合は、両ディレクトリを含む親を `-D M5_LIB_DIR=<絶対パス>` で指定する。MultiFirmは `idf-components/multifirm` のラッパーから取り込む。通常のアプリではMultiFirmリポジトリを `components/multifirm` に配置し、利用側の `REQUIRES` に `multifirm` を追加すればよい。

成果物は `build-idf/multifirm-guest.bin`。通常のIDFビルドでもアプリbinの容量を検査する。`idf.py flash` / `erase-flash` はPlatformIO用guardの対象外なので、共存機には使わずMultiFirmのPCツールでインストールする。

## アプリへの組み込み

```cpp
#include <multifirm_guest.h>

void setup() {
    multifirm::guest::checkStartupEscape(); // M5.begin()より前
    M5.begin();
}

void loop() {
    M5.update();
    multifirm::guest::poll(M5.BtnA.isPressed(), M5.BtnB.isPressed(),
        M5.Touch.getCount() != 0, millis(),
        [](void*) { M5.Speaker.end(); M5.Power.setVibration(0); });
}
```

- `checkStartupEscape(gpio_num_t pin = kStartupEscapeGpio)`：既定GPIO1、入力・内部pull-up、active low。GPIO設定失敗や復帰不能なら呼び出し元へ戻る。設定したGPIOは入力のままとし、以降はアプリの初期化で引き継ぐ。
- `returnToHost(Shutdown shutdown = nullptr, void* context = nullptr)`：毎回レイアウト、実行中ゲスト、ホストイメージを検証する。失敗はfalseとログ、成功は起動先設定→shutdown→再起動。shutdownは同期実行し、contextを保存しない。例外を投げず終了するコールバックを渡す。
- `ReturnGesture(uint32_t holdMs = kReturnHoldMs)`：既定1600ms。`update(a,b,touched,nowMs)` は保持ごとに1回だけtrue。片方解除またはタッチで解除され、次の有効入力から再計測。時間は32bit ms、定期的に呼ぶ。
- `poll(...)`：共有の既定ジェスチャと復帰を組み合わせる。複数翻訳単位でも状態は1つ。単一タスクから呼ぶ。復帰に失敗した場合も、その保持中には再試行しない。成功時にtrueを受け取れることには依存しない。

保持時間を変更したい場合や独立した判定が必要な場合は、`ReturnGesture gesture(2000)` を保持し、`update()` がtrueのときに `returnToHost()` を呼ぶ。GPIO変更例は `checkStartupEscape(GPIO_NUM_1)`。ライブラリ自体はM5Unifiedに依存せず、ESP-IDFからも同じAPIを使う。

単体配置、旧配置、ホスト自身、不明な実行中スロットでは起動先を変更しない。v1はESP32-S3・未署名の付加SHA付きアプリ・Secure Boot / Flash Encryptionなし・ホストのrollback無効が前提。

## ローカルguard

`examples/upload_guard.py` をゲストの `tools/upload_guard.py` へコピーし、次を追加する。サンプル内のコピーとの一致はPCテストで検査する。

```ini
extra_scripts = pre:tools/upload_guard.py
custom_allow_full_upload = no
```

`upload` / `uploadfs` / `uploadfsota` / `erase` を拒否する。`yes` と `no` 以外は設定エラー。単体用の全体書き込みを意図するときだけ `yes` に変更する。この操作は共存表・ホスト・他アプリ・共有設定を上書きし得る。

guardはlib_depsのファイルを探索・実行しない。スクリプトのパスが間違っていればビルドエラーになる。PlatformIO自体の依存取得がpreスクリプトより前に行われることはある。

新規bin生成直後と、キャッシュ済みbinを使う通常ビルド・uploadの前に、ファイル実サイズが0x1f0000以下か検査する。opt-inでも容量制限は解除しない。espressif32の既定名 `firmware` をpre段階で設定するため、成果物名を独自に設定する場合はguardより前のスクリプトで `PROGNAME` を設定する。直接esptoolやIDFコマンドを呼ぶ経路まで保護するものではない。

## 実機での確認

両サンプルはA+Bの1.6秒保持でホストへ戻り、タッチ中は取消。KEY.Bを押した起動はM5初期化前に脱出する。通常のジェスチャ復帰ではshutdownのログ、音・振動の停止を確認できる。

新配置への導入と復旧経路の確認後、両サンプルをota_1〜3それぞれで起動し、各サンプルで10往復する。1秒解除、タッチ取消、KEY.B脱出、表示・音・振動を確認し、シリアルログを保存する。単体配置のno-opは別の単体試験機で確認する。ホスト破損時の非再起動はまずPCテストで確認し、試験機で破損試験をする場合はバックアップとホスト再書き込み経路を用意する。

Arduinoでは `setup()` より前にコアが初期化されるため、KEY.B脱出でもコアのNVS全消去を防げない。コアにはパッチしない。ESP-IDFサンプルはNVS初期化失敗時に消去せず、ログを出して周辺機器の初期化前に停止する。M5ライブラリの通常のNVS利用以外に、サンプル独自の設定保存は追加しない。
