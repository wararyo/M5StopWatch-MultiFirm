#include "multifirm_guest.h"
#include <cassert>
#include <algorithm>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

namespace {
std::vector<uint8_t> table, image, original;
esp_partition_t host = {0, 0x10, 0x20000, 0x400000, "ota_0", false};
esp_partition_t guest = {0, 0x11, 0x420000, 0x1F0000, "ota_1", false};
const esp_partition_t* running = &guest;
std::vector<std::string> events;
esp_err_t readError = ESP_OK, verifyError = ESP_OK, setError = ESP_OK, gpioError = ESP_OK;
int failImageRead = -1;
int imageReads = 0;
bool hostMissing = false;
int level = 1;
uint64_t gpioMask = 0;
std::vector<uint8_t> readFile(const char* path) {
    std::ifstream input(path, std::ios::binary);
    assert(input.good());
    return std::vector<uint8_t>((std::istreambuf_iterator<char>(input)), {});
}
void reset() {
    image = original;
    events.clear();
    running = &guest;
    hostMissing = false;
    readError = verifyError = setError = gpioError = ESP_OK;
    level = 1;
    failImageRead = -1;
    imageReads = 0;
}
void stop(void* context) {
    assert(context != nullptr);
    ++*static_cast<int*>(context);
    events.push_back("shutdown");
}
void mustFail() {
    int count = 0;
    assert(!multifirm::guest::returnToHost(stop, &count));
    assert(count == 0);
    for (const auto& event : events) assert(event != "restart" && event != "shutdown");
}
}

esp_err_t esp_flash_read(void*, void* out, uint32_t address, size_t n) {
    assert(address >= 0x8000 && address + n <= 0x8C00);
    if (readError != ESP_OK) return readError;
    std::memcpy(out, table.data() + address - 0x8000, n);
    return ESP_OK;
}
esp_err_t esp_partition_read(const esp_partition_t* p, size_t offset, void* out, size_t n) {
    assert(offset <= p->size && n <= p->size - offset);
    if (readError != ESP_OK) return readError;
    if (imageReads++ == failImageRead) return ESP_FAIL;
    std::memset(out, 0xFF, n);
    if (offset < image.size())
        std::memcpy(out, image.data() + offset, n < image.size() - offset ? n : image.size() - offset);
    return ESP_OK;
}
esp_err_t esp_image_verify(int, const esp_partition_pos_t* p, esp_image_metadata_t* result) {
    assert(p->offset == host.address && p->size == host.size);
    events.push_back("verify");
    result->image_len = static_cast<uint32_t>(original.size());
    return verifyError; // Deliberately no checksum/SHA: models debugger bypass.
}
const esp_partition_t* esp_ota_get_running_partition() { return running; }
const esp_partition_t* esp_partition_find_first(int, int, const char*) { return hostMissing ? nullptr : &host; }
esp_err_t esp_ota_set_boot_partition(const esp_partition_t*) { events.push_back("set"); return setError; }
void esp_restart() { events.push_back("restart"); }
esp_err_t gpio_config(const gpio_config_t* config) { gpioMask = config->pin_bit_mask; return gpioError; }
int gpio_get_level(gpio_num_t) { return level; }
extern bool pollFromSecond(bool, bool, uint32_t);

int main(int argc, char** argv) {
    assert(argc == 6);
    table = readFile(argv[1]);
    original = readFile(argv[2]);
    reset();
    int count = 0;
    assert(multifirm::guest::returnToHost(stop, &count));
    assert(count == 1);
    assert((events == std::vector<std::string>{"verify", "set", "shutdown", "restart"}));
    reset();
    multifirm::detail::VerifiedImage result;
    assert(multifirm::detail::verifyImage(&host, result) == ESP_OK);
    assert(result.image_size == original.size());
    assert(std::memcmp(result.app_digest, original.data() + original.size() - 32, 32) == 0);
    const int readsInVerification = imageReads;
    for (int i = 0; i < readsInVerification; ++i) {
        reset(); failImageRead = i; mustFail(); assert(events.empty());
    }
    const auto validTable = table;
    for (int i = 3; i < argc; ++i) {
        reset(); table = readFile(argv[i]); mustFail(); assert(events.empty());
    }
    table = validTable;
    for (int index = 1; index <= 3; ++index) {
        reset();
        const auto& def = multifirm::layout::kPartitions[4 + index];
        esp_partition_t slot = {def.type, def.subtype, def.offset, def.size, {}, false};
        std::strcpy(slot.label, def.label);
        running = &slot;
        assert(multifirm::guest::returnToHost());
        assert((events == std::vector<std::string>{"verify", "set", "restart"}));
    }
    reset(); std::fill(table.begin(), table.end(), 0xFF); mustFail(); table = validTable;
    for (size_t pos : {size_t(0), size_t(11 * 32 + 16), size_t(0xBFF)}) {
        reset(); table[pos] ^= 1; mustFail(); assert(events.empty()); table[pos] ^= 1;
    }
    reset(); running = &host; mustFail(); assert(events.empty());
    reset(); running = nullptr; mustFail(); assert(events.empty());
    reset(); guest.address += 0x10000; mustFail(); guest.address -= 0x10000;
    reset(); hostMissing = true; mustFail();
    reset(); readError = ESP_FAIL; mustFail();
    reset(); verifyError = ESP_FAIL; mustFail(); assert(events.size() == 1);
    reset(); setError = ESP_FAIL; mustFail(); assert(events.size() == 2);
    for (size_t pos : {size_t(0), size_t(12), size_t(23), size_t(32),
                       original.size() - 100, original.size() - 1}) {
        reset(); image[pos] ^= 1; mustFail(); assert(events.empty());
    }
    // Preserve XOR while corrupting the image: only independently recomputed SHA catches this.
    reset(); image[800] ^= 1; image[801] ^= 1; mustFail(); assert(events.empty());
    reset(); std::memset(image.data() + 28, 0xFF, 4); mustFail(); assert(events.empty());
    reset(); host.size = 24; mustFail(); host.size = 0x400000;
    reset();
    esp_partition_t tiny = host; tiny.size = 24;
    assert(multifirm::detail::verifyImage(&tiny, result) != ESP_OK);
    assert(result.image_size == 0);
    reset(); image[1] = 0; mustFail();
    reset(); image[1] = 17; mustFail();
    reset(); image[23] = 0; mustFail();
    reset(); gpioError = ESP_FAIL; level = 0;
    multifirm::guest::checkStartupEscape(); assert(events.empty());
    reset(); multifirm::guest::checkStartupEscape(); assert(events.empty());
    assert(gpioMask == 2);
    reset(); level = 0; multifirm::guest::checkStartupEscape(3);
    assert(gpioMask == 8);
    assert((events == std::vector<std::string>{"verify", "set", "restart"}));
    reset(); multifirm::guest::checkStartupEscape(-1); assert(events.empty());
    reset();
    assert(!multifirm::guest::poll(false, false, false, 0));
    assert(!pollFromSecond(true, true, 10));
    assert(multifirm::guest::poll(true, true, false, 1610));
    assert(!pollFromSecond(true, true, 4000));
    // A failed return attempt is not retried each loop while buttons stay held.
    reset(); hostMissing = true;
    assert(!multifirm::guest::poll(false, false, false, 4001));
    assert(!multifirm::guest::poll(true, true, false, 5000));
    assert(!multifirm::guest::poll(true, true, false, 6600));
    hostMissing = false;
    assert(!pollFromSecond(true, true, 8000));
    assert(!multifirm::guest::poll(true, true, true, 8001));
    assert(!pollFromSecond(true, true, 8002));
    assert(pollFromSecond(true, true, 9602));
}
