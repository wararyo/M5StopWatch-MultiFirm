#pragma once
#include <cstdint>

namespace multifirm { namespace guest {
static constexpr uint32_t kReturnHoldMs = 1600;

// Independent of IDF. Call regularly; elapsed time uses modulo-2^32 arithmetic.
class ReturnGesture {
public:
    explicit ReturnGesture(uint32_t holdMs = kReturnHoldMs) : holdMs_(holdMs) {}
    bool update(bool a, bool b, bool touched, uint32_t nowMs) {
        if (!a || !b || touched) {
            holding_ = fired_ = false;
            return false;
        }
        if (!holding_) {
            holding_ = true;
            started_ = nowMs;
        }
        if (!fired_ && uint32_t(nowMs - started_) >= holdMs_) {
            fired_ = true;
            return true;
        }
        return false;
    }
private:
    uint32_t holdMs_;
    uint32_t started_ = 0;
    bool holding_ = false;
    bool fired_ = false;
};
}}
