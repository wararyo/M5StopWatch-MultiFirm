#include <M5Unified.h>
#include <multifirm_guest.h>
#include "esp_timer.h"
#include "nvs_flash.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <cstdio>

extern void secondTranslationUnit();
static unsigned shutdownCount = 0;
static void shutdown(void* context) {
    ++*static_cast<unsigned*>(context);
    std::printf("MultiFirm shutdown: speaker and vibration off\n");
    std::fflush(stdout);
    M5.Speaker.end();
    M5.Power.setVibration(0);
}

extern "C" void app_main() {
    multifirm::guest::checkStartupEscape();
    const esp_err_t err = nvs_flash_init();
    if (err != ESP_OK) {
        // No global erase and no dependent peripheral initialization on failure.
        std::printf("NVS unavailable: %s; stopped without erasing settings\n", esp_err_to_name(err));
        return;
    }
    auto cfg = M5.config();
    cfg.internal_imu = false;
    cfg.internal_rtc = false;
    cfg.internal_mic = false;
    M5.begin(cfg);
    M5.Display.setTextSize(2);
    M5.Display.println("MultiFirm ESP-IDF\nA+B: hold 1.6s\nTouch cancels\nBoot with B: host");
    std::printf("MultiFirm ESP-IDF ready\n");
    secondTranslationUnit();
    while (true) {
        M5.update();
        multifirm::guest::poll(M5.BtnA.isPressed(), M5.BtnB.isPressed(),
            M5.Touch.getCount() != 0, uint32_t(esp_timer_get_time() / 1000),
            shutdown, &shutdownCount);
        vTaskDelay(pdMS_TO_TICKS(5));
    }
}
