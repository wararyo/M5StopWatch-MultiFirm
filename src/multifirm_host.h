// Host-side slot discovery and boot API for the MultiFirm v1 layout.
#pragma once

#include "multifirm_layout.h"

#include <esp_err.h>
#include <esp_partition.h>
#include <string>
#include <vector>

namespace multifirm {
namespace host {

using Shutdown = void (*)(void*);

enum class SlotState { Empty, Invalid, Ready, ReadError };
enum class NameSource { Metadata, ProjectName, Fallback };
enum class MetaStatus {
    Valid,
    Empty,
    ReadError,
    DecodeError,
    ImageSizeMismatch,
    AppDigestMismatch,
    ElfShaMismatch,
};

struct SlotInfo {
    int index = 0;
    const esp_partition_t* partition = nullptr;
    SlotState state = SlotState::Invalid;
    std::string name;
    std::string project_name;
    std::string version;
    NameSource name_source = NameSource::Fallback;
    bool meta_valid = false;
    MetaStatus meta_status = MetaStatus::Empty;
    layout::MetaError meta_error = layout::MetaError::Empty;
    esp_err_t meta_read_error = ESP_OK;
    esp_err_t error = ESP_OK;
};

bool isMultiFirmLayout();
esp_err_t scanSlots(std::vector<SlotInfo>& slots);
esp_err_t inspectSlot(int index, SlotInfo& slot);
esp_err_t bootSlot(int index, Shutdown shutdown = nullptr, void* context = nullptr);

const char* slotStateName(SlotState state);
const char* nameSourceName(NameSource source);
const char* metaStatusName(MetaStatus status);

}  // namespace host
}  // namespace multifirm
