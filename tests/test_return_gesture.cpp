#include "return_gesture.h"
#include <cassert>
#include <cstdint>

int main() {
    using multifirm::guest::ReturnGesture;
    ReturnGesture g;
    assert(!g.update(true, false, false, 0));
    assert(!g.update(true, true, false, 100));
    assert(!g.update(true, true, false, 1699));
    assert(g.update(true, true, false, 1700));
    assert(!g.update(true, true, false, 9000));
    assert(!g.update(false, true, false, 9001));
    assert(!g.update(true, true, false, 9100));
    assert(!g.update(true, true, true, 10699));
    assert(!g.update(true, true, false, 10700));
    assert(g.update(true, true, false, 12300));
    assert(!g.update(true, false, false, 12301));
    assert(!g.update(true, true, false, UINT32_MAX - 799));
    assert(!g.update(true, true, false, 799));
    assert(g.update(true, true, false, 800));
    ReturnGesture custom(20);
    assert(!custom.update(true, true, false, 0));
    assert(!custom.update(true, true, false, 19));
    assert(custom.update(true, true, false, 20));
    ReturnGesture immediate(0);
    assert(immediate.update(true, true, false, 0));
    assert(!immediate.update(true, true, false, 1));
}
