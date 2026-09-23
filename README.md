# M5StopWatch-MultiFirm

M5StopWatch に複数のファームウェアを共存させるための、PCツール・ゲスト復帰ライブラリ・
ホストスロットAPI。`ota_0` のランチャーを**ホスト**、`ota_1`〜`ota_3` のアプリを**ゲスト**と呼ぶ
v1 固定レイアウト（[partitions.multifirm.csv](docs/partitions.multifirm.csv)）だけを扱う。

ゲストは標準ビルドのまま共存スロットへ入る。共存専用のビルド環境もパーティション表も要らない。

## ゲストに組み込む

```ini
lib_deps =
    https://github.com/wararyo/M5StopWatch-MultiFirm.git#v1.0.0
extra_scripts = pre:tools/upload_guard.py
custom_allow_full_upload = no
```

```cpp
#include <multifirm_guest.h>

void setup() {
    multifirm::guest::checkStartupEscape();   // M5.begin() より前
    M5.begin();
}

void loop() {
    M5.update();
    multifirm::guest::poll(M5.BtnA.isPressed(), M5.BtnB.isPressed(),
                           M5.Touch.getCount() != 0, millis(),
                           [](void*) { /* 音・振動を止める */ });
}
```

`examples/upload_guard.py` をゲストの `tools/upload_guard.py` へコピーする。
ホスト側は `-DMULTIFIRM_HOST=1`（PlatformIO）または `CONFIG_MULTIFIRM_HOST=y`（ESP-IDF）で有効にする。
通常の ESP-IDF から使う場合は、リポジトリを `components/multifirm` へ置いて `REQUIRES` に足す。

## 書き込む

```powershell
pio run
tools\multifirm.ps1 install --slot 2 .pio\build\<env>\firmware.bin --name "MyApp" --port COM11
tools\multifirm.ps1 install --slot 2 .pio\build\<env>\firmware.bin --name "MyApp" --port COM11 --execute
```

## ドキュメント

- [PCツールの操作](tools/README.md) — 初回導入、ゲスト更新、ホスト更新、復旧、バックアップ復元
- [ゲストAPI・サンプル・書き込み保護](examples/README.md)
- [設計・全体計画](docs/multifirm-plan.md)
- 実装・検証記録: [Phase 1](docs/phase1-result.md) / [Phase 2](docs/phase2-result.md) /
  [Phase 3](docs/phase3-result.md) / [Phase 4](docs/phase4-result.md)
