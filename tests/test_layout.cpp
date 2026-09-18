// Native test for src/multifirm_layout.h. Reads the Python-generated fixtures.
// usage: test_layout <tests/fixtures/meta>
#include "multifirm_layout.h"

#include <cstdio>
#include <fstream>
#include <iterator>
#include <sstream>
#include <string>
#include <vector>

namespace ml = multifirm::layout;

static int failures = 0;

#define CHECK(cond)                                                          \
    do {                                                                     \
        if (!(cond)) {                                                       \
            std::fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
            ++failures;                                                      \
        }                                                                    \
    } while (0)

static std::vector<uint8_t> readFile(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    return std::vector<uint8_t>(std::istreambuf_iterator<char>(in), {});
}

static std::string hex(const uint8_t* p, size_t n) {
    static const char* digits = "0123456789abcdef";
    std::string s;
    for (size_t i = 0; i < n; ++i) {
        s += digits[p[i] >> 4];
        s += digits[p[i] & 0xF];
    }
    return s;
}

static void testConstants() {
    const uint8_t vector[] = {'1', '2', '3', '4', '5', '6', '7', '8', '9'};
    CHECK(ml::crc32(vector, sizeof(vector)) == 0xCBF43926u);
    CHECK(ml::kPartitionCount == 11);
    // Contiguous, non-overlapping, inside the flash, nvs before multifirm_nvs.
    uint32_t end = 0x9000;
    int nvsIndex = -1, mfNvsIndex = -1;
    for (size_t i = 0; i < ml::kPartitionCount; ++i) {
        const auto& p = ml::kPartitions[i];
        CHECK(p.offset >= end);
        CHECK(p.offset % ml::kSectorSize == 0 && p.size % ml::kSectorSize == 0);
        end = p.offset + p.size;
        if (std::string(p.label) == "nvs") nvsIndex = static_cast<int>(i);
        if (std::string(p.label) == "multifirm_nvs") mfNvsIndex = static_cast<int>(i);
    }
    CHECK(end <= ml::kFlashSize);
    CHECK(nvsIndex >= 0 && mfNvsIndex > nvsIndex);
    CHECK(ml::findPartition("ota_0")->offset == ml::kHostOffset);
    CHECK(ml::findPartition("ota_0")->size == ml::kHostMaxSize);
    CHECK(ml::findPartition("multifirm_meta")->offset == ml::kMetaOffset);
    CHECK(ml::findPartition("multifirm_meta")->size == ml::kMetaSize);
    CHECK(ml::findPartition("multifirm_meta")->subtype == ml::kSubtypeMultifirmMeta);
    for (int slot = 1; slot <= ml::kGuestSlotCount; ++slot) {
        const std::string label = "ota_" + std::to_string(slot);
        CHECK(ml::guestSlotOffset(slot) == ml::findPartition(label.c_str())->offset);
        CHECK(ml::findPartition(label.c_str())->size == ml::kGuestMaxSize);
        CHECK(ml::metaSectorOffset(slot) == ml::kMetaOffset + (slot - 1) * ml::kSectorSize);
    }
    CHECK(ml::guestSlotOffset(0) == 0 && ml::guestSlotOffset(4) == 0);
    CHECK(ml::metaSectorOffset(0) == 0 && ml::metaSectorOffset(4) == 0);
    CHECK(ml::kMetaReservedOffset == 0x1F000);
}

static void testNames() {
    CHECK(ml::isDefaultProjectName("firmware"));
    CHECK(ml::isDefaultProjectName("arduino-lib-builder"));
    CHECK(ml::isDefaultProjectName(""));
    CHECK(!ml::isDefaultProjectName("VibeWatch"));
    CHECK(ml::isUsableProjectName("M5StopWatch-KantanPlay"));
    CHECK(!ml::isUsableProjectName("arduino-lib-builder"));
    char full[32];
    std::memset(full, 'A', sizeof(full));  // 32 bytes, no terminator
    CHECK(!ml::isUsableProjectName(full, sizeof(full)));
}

static void testFixtures(const std::string& dir) {
    std::ifstream cases(dir + "/cases.txt");
    CHECK(cases.good());
    std::string line;
    int count = 0;
    while (std::getline(cases, line)) {
        if (line.empty() || line[0] == '#') continue;
        std::istringstream in(line);
        std::string file, capacityText, expected;
        in >> file >> capacityText >> expected;
        const auto data = readFile(dir + "/" + file);
        const uint32_t capacity = static_cast<uint32_t>(std::stoul(capacityText, nullptr, 16));
        ml::MetaRecord rec{};
        const auto err = ml::decodeMeta(data.data(), data.size(), capacity, rec);
        if (expected != ml::metaErrorName(err)) {
            std::fprintf(stderr, "%s: expected %s, got %s\n", file.c_str(), expected.c_str(), ml::metaErrorName(err));
            ++failures;
            continue;
        }
        if (err == ml::MetaError::Ok) {
            std::string nameHex, elfHex, digestHex;
            uint32_t imageSize = 0;
            uint64_t installedAt = 0;
            in >> nameHex >> imageSize >> elfHex >> digestHex >> installedAt;
            CHECK(hex(reinterpret_cast<const uint8_t*>(rec.name), std::strlen(rec.name)) == nameHex);
            CHECK(rec.image_size == imageSize);
            CHECK(hex(rec.elf_sha256, 32) == elfHex);
            CHECK(hex(rec.app_digest, 32) == digestHex);
            CHECK(rec.installed_at == installedAt);
        }
        ++count;
    }
    CHECK(count >= 20);
    std::printf("fixtures: %d cases\n", count);
}

int main(int argc, char** argv) {
    const std::string dir = argc > 1 ? argv[1] : "tests/fixtures/meta";
    testConstants();
    testNames();
    testFixtures(dir);
    if (failures) {
        std::fprintf(stderr, "%d failure(s)\n", failures);
        return 1;
    }
    std::printf("test_layout: OK\n");
    return 0;
}
