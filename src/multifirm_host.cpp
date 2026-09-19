#if __has_include("sdkconfig.h")
#include "sdkconfig.h"
#endif

#if defined(MULTIFIRM_HOST) || defined(CONFIG_MULTIFIRM_HOST)

#include "multifirm_host.h"
#include "detail/esp_validation.h"

#if __has_include(<esp_app_desc.h>)
#include <esp_app_desc.h>
#else
#include <esp_app_format.h>
#endif
#include <esp_log.h>
#include <esp_ota_ops.h>
#include <esp_system.h>
#include <esp_timer.h>
#include <cstdio>
#include <cstring>

namespace multifirm {
namespace host {
namespace {

static const char* kTag = "multifirm-host";

const layout::PartitionDef& slotDefinition(int index) {
    return layout::kPartitions[4 + index];
}

std::string fixedString(const char* value, size_t capacity) {
    size_t length = 0;
    while (length < capacity && value[length] != '\0') ++length;
    return std::string(value, length);
}

std::string fallbackName(int index) {
    char value[8];
    std::snprintf(value, sizeof(value), "App%d", index);
    return value;
}

bool allZero(const uint8_t* value, size_t size) {
    for (size_t i = 0; i < size; ++i) {
        if (value[i] != 0) return false;
    }
    return true;
}

bool allErased(const uint8_t* value, size_t size) {
    for (size_t i = 0; i < size; ++i) {
        if (value[i] != 0xFF) return false;
    }
    return true;
}

const esp_partition_t* findSlot(int index) {
    const auto& expected = slotDefinition(index);
    const esp_partition_t* partition = esp_partition_find_first(
        ESP_PARTITION_TYPE_APP, static_cast<esp_partition_subtype_t>(expected.subtype), nullptr);
    return detail::matchesPartition(partition, expected) ? partition : nullptr;
}

const esp_partition_t* findMetadata() {
    const auto* expected = layout::findPartition("multifirm_meta");
    if (!expected) return nullptr;
    const esp_partition_t* partition = esp_partition_find_first(
        ESP_PARTITION_TYPE_DATA, static_cast<esp_partition_subtype_t>(layout::kSubtypeMultifirmMeta), nullptr);
    return detail::matchesPartition(partition, *expected) ? partition : nullptr;
}

void selectFallbackName(SlotInfo& slot, const esp_app_desc_t* description) {
    slot.name = fallbackName(slot.index);
    slot.name_source = NameSource::Fallback;
    if (description && layout::isUsableProjectName(description->project_name, sizeof(description->project_name))) {
        slot.name = fixedString(description->project_name, sizeof(description->project_name));
        slot.name_source = NameSource::ProjectName;
    }
}

void inspectMetadata(SlotInfo& slot, const detail::VerifiedImage& verified,
                     const esp_app_desc_t& description) {
    selectFallbackName(slot, &description);
    const esp_partition_t* metadata = findMetadata();
    if (!metadata) {
        slot.meta_status = MetaStatus::ReadError;
        slot.meta_read_error = ESP_ERR_NOT_FOUND;
        return;
    }

    uint8_t bytes[layout::kMetaRecordSize];
    const size_t offset = static_cast<size_t>(slot.index - 1) * layout::kSectorSize;
    slot.meta_read_error = esp_partition_read(metadata, offset, bytes, sizeof(bytes));
    if (slot.meta_read_error != ESP_OK) {
        slot.meta_status = MetaStatus::ReadError;
        return;
    }

    layout::MetaRecord record = {};
    slot.meta_error = layout::decodeMeta(bytes, sizeof(bytes), slot.partition->size, record);
    if (slot.meta_error != layout::MetaError::Ok) {
        slot.meta_status = slot.meta_error == layout::MetaError::Empty ?
            MetaStatus::Empty : MetaStatus::DecodeError;
        return;
    }
    if (record.image_size != verified.image_size) {
        slot.meta_status = MetaStatus::ImageSizeMismatch;
        return;
    }
    if (std::memcmp(record.app_digest, verified.app_digest, sizeof(record.app_digest)) != 0) {
        slot.meta_status = MetaStatus::AppDigestMismatch;
        return;
    }
    if (!allZero(record.elf_sha256, sizeof(record.elf_sha256)) &&
        std::memcmp(record.elf_sha256, description.app_elf_sha256,
                    sizeof(record.elf_sha256)) != 0) {
        slot.meta_status = MetaStatus::ElfShaMismatch;
        return;
    }

    slot.meta_valid = true;
    slot.meta_status = MetaStatus::Valid;
    slot.name.assign(record.name);
    slot.name_source = NameSource::Metadata;
}

esp_err_t inspectSlotWithoutLayoutCheck(int index, SlotInfo& slot) {
    slot = SlotInfo();
    slot.index = index;
    slot.name = fallbackName(index);
    slot.partition = findSlot(index);
    if (!slot.partition) {
        slot.state = SlotState::ReadError;
        slot.error = ESP_ERR_NOT_FOUND;
        return ESP_OK;
    }

    uint8_t firstBlock[512];
    bool firstSectorErased = true;
    for (size_t offset = 0; offset < layout::kSectorSize; offset += sizeof(firstBlock)) {
        slot.error = esp_partition_read(slot.partition, offset, firstBlock, sizeof(firstBlock));
        if (slot.error != ESP_OK) {
            slot.state = SlotState::ReadError;
            return ESP_OK;
        }
        if (!allErased(firstBlock, sizeof(firstBlock))) {
            firstSectorErased = false;
            break;
        }
    }
    if (firstSectorErased) {
        slot.state = SlotState::Empty;
        slot.error = ESP_OK;
        return ESP_OK;
    }

    const int64_t started = esp_timer_get_time();
    detail::VerifiedImage verified;
    slot.error = detail::verifyImage(slot.partition, verified, esp_timer_get_time);
    const int64_t elapsed = esp_timer_get_time() - started;
    ESP_LOGI(kTag, "slot %d image verification took %lld ms", index,
             static_cast<long long>(elapsed / 1000));
    ESP_LOGI(kTag, "slot %d SHA-256 calculation took %lld ms", index,
             static_cast<long long>(verified.sha_elapsed_us / 1000));
    if (slot.error != ESP_OK) {
        slot.state = slot.error == ESP_ERR_IMAGE_INVALID ? SlotState::Invalid : SlotState::ReadError;
        return ESP_OK;
    }

    esp_app_desc_t description = {};
    slot.error = esp_ota_get_partition_description(slot.partition, &description);
    if (slot.error != ESP_OK) {
        slot.state = SlotState::ReadError;
        return ESP_OK;
    }

    slot.project_name = fixedString(description.project_name, sizeof(description.project_name));
    slot.version = fixedString(description.version, sizeof(description.version));
    slot.state = SlotState::Ready;
    slot.error = ESP_OK;
    inspectMetadata(slot, verified, description);
    return ESP_OK;
}

}  // namespace

bool isMultiFirmLayout() { return detail::checkLayout() == ESP_OK; }

esp_err_t inspectSlot(int index, SlotInfo& slot) {
    slot = SlotInfo();
    if (index < 1 || index > layout::kGuestSlotCount) return ESP_ERR_INVALID_ARG;
    const esp_err_t layoutError = detail::checkLayout();
    if (layoutError != ESP_OK) return layoutError;
    return inspectSlotWithoutLayoutCheck(index, slot);
}

esp_err_t scanSlots(std::vector<SlotInfo>& slots) {
    slots.clear();
    const esp_err_t layoutError = detail::checkLayout();
    if (layoutError != ESP_OK) return layoutError;
    slots.reserve(layout::kGuestSlotCount);
    for (int index = 1; index <= layout::kGuestSlotCount; ++index) {
        SlotInfo slot;
        inspectSlotWithoutLayoutCheck(index, slot);
        slots.push_back(slot);
    }
    return ESP_OK;
}

esp_err_t bootSlot(int index, Shutdown shutdown, void* context) {
    if (index < 1 || index > layout::kGuestSlotCount) return ESP_ERR_INVALID_ARG;
    esp_err_t error = detail::checkLayout();
    if (error != ESP_OK) return error;
    const esp_partition_t* partition = findSlot(index);
    if (!partition) return ESP_ERR_NOT_FOUND;

    const int64_t started = esp_timer_get_time();
    detail::VerifiedImage verified;
    error = detail::verifyImage(partition, verified, esp_timer_get_time);
    ESP_LOGI(kTag, "slot %d boot verification took %lld ms", index,
             static_cast<long long>((esp_timer_get_time() - started) / 1000));
    ESP_LOGI(kTag, "slot %d boot SHA-256 calculation took %lld ms", index,
             static_cast<long long>(verified.sha_elapsed_us / 1000));
    if (error != ESP_OK) return error;
    error = esp_ota_set_boot_partition(partition);
    if (error != ESP_OK) return error;
    if (shutdown) shutdown(context);
    esp_restart();
    return ESP_OK;
}

const char* slotStateName(SlotState state) {
    switch (state) {
        case SlotState::Empty: return "empty";
        case SlotState::Invalid: return "invalid";
        case SlotState::Ready: return "ready";
        case SlotState::ReadError: return "read_error";
    }
    return "unknown";
}

const char* nameSourceName(NameSource source) {
    switch (source) {
        case NameSource::Metadata: return "metadata";
        case NameSource::ProjectName: return "project_name";
        case NameSource::Fallback: return "fallback";
    }
    return "unknown";
}

const char* metaStatusName(MetaStatus status) {
    switch (status) {
        case MetaStatus::Valid: return "valid";
        case MetaStatus::Empty: return "empty";
        case MetaStatus::ReadError: return "read_error";
        case MetaStatus::DecodeError: return "decode_error";
        case MetaStatus::ImageSizeMismatch: return "image_size_mismatch";
        case MetaStatus::AppDigestMismatch: return "app_digest_mismatch";
        case MetaStatus::ElfShaMismatch: return "elf_sha_mismatch";
    }
    return "unknown";
}

}  // namespace host
}  // namespace multifirm

#endif
