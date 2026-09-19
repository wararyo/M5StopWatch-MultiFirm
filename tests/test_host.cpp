#include "multifirm_host.h"

#include <algorithm>
#include <cassert>
#include <cstring>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

namespace {
std::vector<uint8_t> table, app, originalApp, metadata, originalMetadata;
esp_partition_t slots[3] = {
    {0, 0x11, 0x420000, 0x1F0000, "ota_1", false},
    {0, 0x12, 0x610000, 0x1F0000, "ota_2", false},
    {0, 0x13, 0x800000, 0x1F0000, "ota_3", false},
};
esp_partition_t metaPartition = {1, 0x40, 0x1C000, 0x4000, "multifirm_meta", false};
std::vector<std::string> events;
int64_t nowUs = 0;
int failRead = -1, readCount = 0;
bool missingMeta = false, missingSlot = false;
esp_err_t verifyError = ESP_OK, setError = ESP_OK, descriptionError = ESP_OK;

std::vector<uint8_t> readFile(const std::string& path) {
    std::ifstream input(path, std::ios::binary);
    assert(input.good());
    return std::vector<uint8_t>((std::istreambuf_iterator<char>(input)), {});
}

void reset() {
    app = originalApp;
    metadata = originalMetadata;
    events.clear();
    nowUs = 0;
    failRead = -1;
    readCount = 0;
    missingMeta = missingSlot = false;
    verifyError = setError = descriptionError = ESP_OK;
}

void shutdown(void* context) {
    ++*static_cast<int*>(context);
    events.push_back("shutdown");
}
}

esp_err_t esp_flash_read(void*, void* out, uint32_t address, size_t size) {
    if (address < 0x8000 || address + size > 0x8C00) return ESP_FAIL;
    std::memcpy(out, table.data() + address - 0x8000, size);
    return ESP_OK;
}

esp_err_t esp_partition_read(const esp_partition_t* partition, size_t offset, void* out, size_t size) {
    if (readCount++ == failRead) return ESP_FAIL;
    const std::vector<uint8_t>& source = partition == &metaPartition ? metadata : app;
    std::memset(out, 0xFF, size);
    if (offset < source.size()) std::memcpy(out, source.data() + offset, std::min(size, source.size() - offset));
    return ESP_OK;
}

esp_err_t esp_image_verify(int, const esp_partition_pos_t*, esp_image_metadata_t* result) {
    events.push_back("verify");
    result->image_len = static_cast<uint32_t>(originalApp.size());
    return verifyError;
}

const esp_partition_t* esp_partition_find_first(int type, int subtype, const char*) {
    if (type == ESP_PARTITION_TYPE_DATA) return missingMeta ? nullptr : &metaPartition;
    if (missingSlot) return nullptr;
    for (auto& slot : slots) if (slot.subtype == subtype) return &slot;
    return nullptr;
}

esp_err_t esp_ota_get_partition_description(const esp_partition_t*, esp_app_desc_t* description) {
    if (descriptionError != ESP_OK) return descriptionError;
    std::memcpy(description, app.data() + 32, sizeof(*description));
    return ESP_OK;
}

esp_err_t esp_ota_set_boot_partition(const esp_partition_t*) {
    events.push_back("set");
    return setError;
}

const esp_partition_t* esp_ota_get_running_partition() { return &slots[0]; }
void esp_restart() { events.push_back("restart"); }
int64_t esp_timer_get_time() { nowUs += 5000; return nowUs; }
esp_err_t gpio_config(const gpio_config_t*) { return ESP_OK; }
int gpio_get_level(gpio_num_t) { return 1; }

int main(int argc, char** argv) {
    assert(argc == 11);
    table = readFile(argv[1]);
    originalApp = readFile(argv[2]);
    originalMetadata.assign(3 * multifirm::layout::kSectorSize, 0xFF);
    const auto validMeta = readFile(argv[3]);
    std::copy(validMeta.begin(), validMeta.end(), originalMetadata.begin());
    reset();

    using namespace multifirm::host;
    SlotInfo slot;
    assert(inspectSlot(1, slot) == ESP_OK);
    assert(slot.state == SlotState::Ready && slot.meta_valid);
    assert(slot.name == "Installed Name" && slot.name_source == NameSource::Metadata);
    assert(slot.project_name == "TestApp" && slot.version == "1.0");

    const int successfulReadCount = readCount;
    assert(successfulReadCount > 5);
    for (int failedRead = 0; failedRead < successfulReadCount - 1; ++failedRead) {
        reset();
        failRead = failedRead;
        assert(inspectSlot(1, slot) == ESP_OK);
        assert(slot.state == SlotState::ReadError);
    }
    reset();
    failRead = successfulReadCount - 1;
    assert(inspectSlot(1, slot) == ESP_OK && slot.state == SlotState::Ready);
    assert(slot.meta_status == MetaStatus::ReadError && slot.meta_read_error == ESP_FAIL);

    reset();
    std::fill(metadata.begin(), metadata.end(), 0xFF);
    assert(inspectSlot(1, slot) == ESP_OK && slot.state == SlotState::Ready);
    assert(!slot.meta_valid && slot.meta_status == MetaStatus::Empty);
    assert(slot.name == "TestApp" && slot.name_source == NameSource::ProjectName);

    reset();
    const auto badMeta = readFile(argv[4]);
    std::copy(badMeta.begin(), badMeta.end(), metadata.begin());
    assert(inspectSlot(1, slot) == ESP_OK && slot.state == SlotState::Ready);
    assert(slot.meta_status == MetaStatus::DecodeError && slot.name == "TestApp");

    for (int fixture = 7; fixture <= 9; ++fixture) {
        reset();
        const auto mismatch = readFile(argv[fixture]);
        std::copy(mismatch.begin(), mismatch.end(), metadata.begin());
        assert(inspectSlot(1, slot) == ESP_OK && slot.state == SlotState::Ready);
        const MetaStatus expected[] = {MetaStatus::ImageSizeMismatch,
                                       MetaStatus::AppDigestMismatch, MetaStatus::ElfShaMismatch};
        assert(slot.meta_status == expected[fixture - 7]);
        assert(!slot.meta_valid && slot.name == "TestApp");
    }
    reset();
    const auto zeroElf = readFile(argv[10]);
    std::copy(zeroElf.begin(), zeroElf.end(), metadata.begin());
    assert(inspectSlot(1, slot) == ESP_OK && slot.meta_valid && slot.name == "Zero ELF");

    reset(); app[app.size() - 1] ^= 1;
    assert(inspectSlot(1, slot) == ESP_OK && slot.state == SlotState::Invalid);
    reset(); app[0] = 0;
    assert(inspectSlot(1, slot) == ESP_OK && slot.state == SlotState::Invalid);

    reset(); app = readFile(argv[5]); std::fill(metadata.begin(), metadata.end(), 0xFF);
    assert(inspectSlot(1, slot) == ESP_OK && slot.state == SlotState::Ready);
    assert(slot.name == "App1" && slot.name_source == NameSource::Fallback);

    reset(); app = readFile(argv[6]); std::fill(metadata.begin(), metadata.end(), 0xFF);
    assert(inspectSlot(1, slot) == ESP_OK && slot.state == SlotState::Ready);
    assert(slot.name == "App1" && slot.name_source == NameSource::Fallback);

    reset(); app.clear();
    assert(inspectSlot(1, slot) == ESP_OK && slot.state == SlotState::Empty);
    reset(); failRead = 0;
    assert(inspectSlot(1, slot) == ESP_OK && slot.state == SlotState::ReadError);
    reset(); missingMeta = true;
    assert(inspectSlot(1, slot) == ESP_OK && slot.state == SlotState::Ready);
    assert(slot.meta_status == MetaStatus::ReadError && slot.name == "TestApp");
    reset(); descriptionError = ESP_FAIL;
    assert(inspectSlot(1, slot) == ESP_OK && slot.state == SlotState::ReadError);
    reset(); missingSlot = true;
    assert(inspectSlot(1, slot) == ESP_OK && slot.state == SlotState::ReadError);

    reset();
    std::vector<SlotInfo> scanned;
    assert(scanSlots(scanned) == ESP_OK && scanned.size() == 3);
    assert(scanned[0].index == 1 && scanned[1].index == 2 && scanned[2].index == 3);
    reset(); failRead = 0;
    assert(scanSlots(scanned) == ESP_OK && scanned.size() == 3);
    assert(scanned[0].state == SlotState::ReadError);
    assert(scanned[1].state == SlotState::Ready && scanned[2].state == SlotState::Ready);
    auto savedTable = table; table[0] ^= 1;
    assert(scanSlots(scanned) != ESP_OK && scanned.empty());
    table = savedTable;
    assert(inspectSlot(0, slot) == ESP_ERR_INVALID_ARG);

    reset(); int shutdownCount = 0;
    assert(bootSlot(2, shutdown, &shutdownCount) == ESP_OK);
    assert(shutdownCount == 1);
    assert((events == std::vector<std::string>{"verify", "set", "shutdown", "restart"}));
    reset(); setError = ESP_FAIL;
    assert(bootSlot(1, shutdown, &shutdownCount) == ESP_FAIL);
    assert(shutdownCount == 1 && events == std::vector<std::string>({"verify", "set"}));
    reset(); app[app.size() - 100] ^= 1;
    assert(bootSlot(1, shutdown, &shutdownCount) == ESP_ERR_IMAGE_INVALID);
    assert(events.empty());
    reset(); assert(bootSlot(4) == ESP_ERR_INVALID_ARG);
}
