#pragma once
#include <cstddef>
#include <cstdint>
using esp_err_t = int;
static const int ESP_OK = 0, ESP_FAIL = -1, ESP_ERR_NO_MEM = 0x101, ESP_ERR_INVALID_ARG = 0x102;
static const int ESP_ERR_INVALID_STATE = 0x103, ESP_ERR_NOT_FOUND = 0x105, ESP_ERR_IMAGE_INVALID = 0x2002;
static const int ESP_PARTITION_TYPE_APP = 0, ESP_PARTITION_TYPE_DATA = 1;
using esp_partition_subtype_t = int;
static const int ESP_PARTITION_SUBTYPE_APP_OTA_0 = 0x10;
struct esp_partition_t {
    int type, subtype;
    uint32_t address, size;
    char label[17];
    bool encrypted;
};
struct esp_partition_pos_t { uint32_t offset, size; };
struct esp_image_metadata_t { uint32_t image_len; };
struct esp_app_desc_t {
    uint32_t magic_word, secure_version, reserv1[2];
    char version[32], project_name[32], time[16], date[16], idf_ver[32];
    uint8_t app_elf_sha256[32];
    uint8_t reserved[80];
};
static const int ESP_IMAGE_VERIFY = 0;
esp_err_t esp_flash_read(void*, void*, uint32_t, size_t);
esp_err_t esp_partition_read(const esp_partition_t*, size_t, void*, size_t);
esp_err_t esp_image_verify(int, const esp_partition_pos_t*, esp_image_metadata_t*);
const esp_partition_t* esp_ota_get_running_partition();
const esp_partition_t* esp_partition_find_first(int, int, const char*);
esp_err_t esp_ota_set_boot_partition(const esp_partition_t*);
esp_err_t esp_ota_get_partition_description(const esp_partition_t*, esp_app_desc_t*);
void esp_restart();
int64_t esp_timer_get_time();
inline const char* esp_err_to_name(esp_err_t) { return "test error"; }
inline void test_log(const char*, const char*, ...) {}
#define ESP_LOGW(...) test_log(__VA_ARGS__)
#define ESP_LOGE(...) test_log(__VA_ARGS__)
#define ESP_LOGI(...) test_log(__VA_ARGS__)
using gpio_num_t = int;
static const int GPIO_NUM_1 = 1, GPIO_MODE_INPUT = 1, GPIO_PULLUP_ENABLE = 1;
static const int GPIO_PULLDOWN_DISABLE = 0, GPIO_INTR_DISABLE = 0;
#define GPIO_IS_VALID_GPIO(pin) ((pin) >= 0 && (pin) < 49)
struct gpio_config_t {
    uint64_t pin_bit_mask;
    int mode, pull_up_en, pull_down_en, intr_type;
};
esp_err_t gpio_config(const gpio_config_t*);
int gpio_get_level(gpio_num_t);
