#pragma once
#include "return_gesture.h"
#include "detail/esp_validation.h"
#include "driver/gpio.h"
#include "esp_ota_ops.h"
#include "esp_system.h"
#include "esp_log.h"

namespace multifirm { namespace guest {
using Shutdown = void (*)(void*);
static constexpr gpio_num_t kStartupEscapeGpio = GPIO_NUM_1;

inline bool returnToHost(Shutdown shutdown = nullptr, void* context = nullptr) {
    esp_err_t err = detail::checkLayout();
    if (err != ESP_OK) {
        ESP_LOGW("MultiFirm", "Layout unavailable: %s", esp_err_to_name(err));
        return false;
    }
    const esp_partition_t* running = esp_ota_get_running_partition();
    bool guest = false;
    for (int i = 1; i <= layout::kGuestSlotCount; ++i)
        guest = guest || detail::matchesPartition(running, layout::kPartitions[4 + i]);
    if (!guest) {
        ESP_LOGW("MultiFirm", "Not running in a v1 guest slot");
        return false;
    }
    const esp_partition_t* host = esp_partition_find_first(
        ESP_PARTITION_TYPE_APP, ESP_PARTITION_SUBTYPE_APP_OTA_0, "ota_0");
    if (!detail::matchesPartition(host, layout::kPartitions[4])) {
        ESP_LOGW("MultiFirm", "Host partition unavailable");
        return false;
    }
    detail::VerifiedImage image;
    err = detail::verifyImage(host, image);
    if (err == ESP_OK) err = esp_ota_set_boot_partition(host);
    if (err != ESP_OK) {
        ESP_LOGE("MultiFirm", "Cannot return to host: %s", esp_err_to_name(err));
        return false;
    }
    if (shutdown) shutdown(context);
    esp_restart();
    return true; // Real esp_restart() does not return.
}

// Call before M5.begin(). Arduino core initialization has already run by setup().
inline void checkStartupEscape(gpio_num_t pin = kStartupEscapeGpio) {
    if (!GPIO_IS_VALID_GPIO(pin)) {
        ESP_LOGW("MultiFirm", "Invalid escape GPIO");
        return;
    }
    gpio_config_t config = {};
    config.pin_bit_mask = uint64_t(1) << pin;
    config.mode = GPIO_MODE_INPUT;
    config.pull_up_en = GPIO_PULLUP_ENABLE;
    config.pull_down_en = GPIO_PULLDOWN_DISABLE;
    config.intr_type = GPIO_INTR_DISABLE;
    const esp_err_t err = gpio_config(&config);
    if (err != ESP_OK) {
        ESP_LOGW("MultiFirm", "Escape GPIO setup failed: %s", esp_err_to_name(err));
        return;
    }
    if (gpio_get_level(pin) == 0) returnToHost();
}

inline bool poll(bool a, bool b, bool touched, uint32_t nowMs,
                 Shutdown shutdown = nullptr, void* context = nullptr) {
    // An external-linkage inline function has one local static across TUs.
    static ReturnGesture gesture;
    return gesture.update(a, b, touched, nowMs) && returnToHost(shutdown, context);
}
}}
