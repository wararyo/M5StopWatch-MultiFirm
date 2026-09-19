// Internal ESP-IDF adapter shared by guest return and the future host API.
#pragma once
#include "../multifirm_layout.h"
#include "partition_table_bytes.h"
#include "esp_flash.h"
#include "esp_partition.h"
#include "esp_image_format.h"
#include "mbedtls/sha256.h"
#include "mbedtls/version.h"

namespace multifirm { namespace detail {
inline bool matchesPartition(const esp_partition_t* p, const layout::PartitionDef& d) {
    return p && p->type == d.type && p->subtype == d.subtype &&
        p->address == d.offset && p->size == d.size && !p->encrypted &&
        std::strncmp(p->label, d.label, sizeof(p->label)) == 0;
}

inline esp_err_t checkLayout() {
    uint8_t block[128];
    for (uint32_t offset = 0; offset < layout::kPartitionTableSize; offset += sizeof(block)) {
        const esp_err_t err = esp_flash_read(nullptr, block,
            layout::kPartitionTableOffset + offset, sizeof(block));
        if (err != ESP_OK) return err;
        for (size_t i = 0; i < sizeof(block); ++i) {
            const size_t pos = offset + i;
            const uint8_t expected = pos < sizeof(kPartitionTablePrefix) ?
                kPartitionTablePrefix[pos] : 0xFF;
            if (block[i] != expected) return ESP_ERR_INVALID_STATE;
        }
    }
    return ESP_OK;
}

struct VerifiedImage {
    uint32_t image_size = 0; // Includes appended SHA-256, not partition padding.
    uint8_t app_digest[32] = {};
};

inline esp_err_t readImage(const esp_partition_t* p, uint32_t offset, void* out, size_t n) {
    if (offset > p->size || n > p->size - offset) return ESP_ERR_IMAGE_INVALID;
    return esp_partition_read(p, offset, out, n);
}

class Sha256 {
public:
    Sha256() { mbedtls_sha256_init(&ctx_); }
    ~Sha256() { mbedtls_sha256_free(&ctx_); }
    Sha256(const Sha256&) = delete;
    Sha256& operator=(const Sha256&) = delete;
    int start() {
#if MBEDTLS_VERSION_MAJOR >= 3
        return mbedtls_sha256_starts(&ctx_, 0);
#else
        return mbedtls_sha256_starts_ret(&ctx_, 0);
#endif
    }
    int update(const uint8_t* data, size_t size) {
#if MBEDTLS_VERSION_MAJOR >= 3
        return mbedtls_sha256_update(&ctx_, data, size);
#else
        return mbedtls_sha256_update_ret(&ctx_, data, size);
#endif
    }
    int finish(uint8_t* digest) {
#if MBEDTLS_VERSION_MAJOR >= 3
        return mbedtls_sha256_finish(&ctx_, digest);
#else
        return mbedtls_sha256_finish_ret(&ctx_, digest);
#endif
    }
private:
    mbedtls_sha256_context ctx_;
};

inline uint32_t little32(const uint8_t* b) {
    return uint32_t(b[0]) | (uint32_t(b[1]) << 8) |
        (uint32_t(b[2]) << 16) | (uint32_t(b[3]) << 24);
}

inline esp_err_t verifyImage(const esp_partition_t* p, VerifiedImage& result) {
    result = VerifiedImage();
    if (!p || p->type != ESP_PARTITION_TYPE_APP || p->encrypted)
        return ESP_ERR_INVALID_ARG;
    // Walk lengths before IDF verification: even a malicious segment must not
    // make a verifier read outside this partition. Independently check XOR/SHA
    // because IDF can skip integrity checks when a debugger is attached.
    uint8_t header[24];
    esp_err_t err = readImage(p, 0, header, sizeof(header));
    if (err != ESP_OK) return err;
    if (header[0] != 0xE9 || header[1] == 0 || header[1] > 16 ||
        header[12] != 9 || header[13] != 0 || header[23] != 1)
        return ESP_ERR_IMAGE_INVALID;
    uint32_t offset = sizeof(header);
    uint8_t checksum = 0xEF;
    uint8_t block[512];
    for (unsigned segment = 0; segment < header[1]; ++segment) {
        uint8_t sh[8];
        err = readImage(p, offset, sh, sizeof(sh));
        if (err != ESP_OK) return err;
        offset += sizeof(sh);
        const uint32_t length = little32(sh + 4);
        if (length > p->size - offset) return ESP_ERR_IMAGE_INVALID;
        if (segment == 0) {
            uint8_t magic[4];
            if (length < 256) return ESP_ERR_IMAGE_INVALID;
            err = readImage(p, offset, magic, sizeof(magic));
            if (err != ESP_OK) return err;
            if (little32(magic) != 0xABCD5432) return ESP_ERR_IMAGE_INVALID;
        }
        for (uint32_t done = 0; done < length;) {
            const size_t n = length - done < sizeof(block) ? length - done : sizeof(block);
            err = readImage(p, offset + done, block, n);
            if (err != ESP_OK) return err;
            for (size_t i = 0; i < n; ++i) checksum ^= block[i];
            done += n;
        }
        offset += length;
    }
    const uint32_t padding = 15 - offset % 16;
    if (offset > p->size || p->size - offset < padding + 1 + 32)
        return ESP_ERR_IMAGE_INVALID;
    offset += padding;
    uint8_t storedChecksum;
    err = readImage(p, offset++, &storedChecksum, 1);
    if (err != ESP_OK) return err;
    if (storedChecksum != checksum) return ESP_ERR_IMAGE_INVALID;
    uint8_t storedDigest[32], computedDigest[32];
    err = readImage(p, offset, storedDigest, sizeof(storedDigest));
    if (err != ESP_OK) return err;
    Sha256 sha;
    if (sha.start() != 0) return ESP_FAIL;
    for (uint32_t done = 0; done < offset;) {
        const size_t n = offset - done < sizeof(block) ? offset - done : sizeof(block);
        err = readImage(p, done, block, n);
        if (err != ESP_OK) return err;
        if (sha.update(block, n) != 0) return ESP_FAIL;
        done += n;
    }
    if (sha.finish(computedDigest) != 0) return ESP_FAIL;
    if (std::memcmp(storedDigest, computedDigest, 32) != 0) return ESP_ERR_IMAGE_INVALID;

    esp_partition_pos_t pos = {};
    pos.offset = p->address;
    pos.size = p->size;
    esp_image_metadata_t metadata = {};
    err = esp_image_verify(ESP_IMAGE_VERIFY, &pos, &metadata);
    if (err != ESP_OK) return err;
    if (metadata.image_len != offset + 32) return ESP_ERR_IMAGE_INVALID;
    result.image_size = offset + 32;
    std::memcpy(result.app_digest, storedDigest, 32);
    return ESP_OK;
}
}}
