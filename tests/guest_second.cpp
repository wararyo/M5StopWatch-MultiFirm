#include "multifirm_guest.h"
bool pollFromSecond(bool a, bool b, uint32_t ms) {
    return multifirm::guest::poll(a, b, false, ms);
}
