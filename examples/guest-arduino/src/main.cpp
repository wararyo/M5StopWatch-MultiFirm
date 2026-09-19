#include <Arduino.h>
#include <M5Unified.h>
#include <multifirm_guest.h>

extern void secondTranslationUnit();
static unsigned shutdownCount = 0;
static void shutdown(void* context) {
    ++*static_cast<unsigned*>(context);
    Serial.println("MultiFirm shutdown: speaker and vibration off");
    Serial.flush();
    M5.Speaker.end();
    M5.Power.setVibration(0);
}

void setup() {
    multifirm::guest::checkStartupEscape();
    Serial.begin(115200);
    auto cfg = M5.config();
    cfg.internal_imu = false;
    cfg.internal_rtc = false;
    cfg.internal_mic = false;
    M5.begin(cfg);
    M5.Display.setTextSize(2);
    M5.Display.println("MultiFirm Arduino\nA+B: hold 1.6s\nTouch cancels\nBoot with B: host");
    Serial.println("MultiFirm Arduino ready");
    secondTranslationUnit();
}

void loop() {
    M5.update();
    multifirm::guest::poll(M5.BtnA.isPressed(), M5.BtnB.isPressed(),
        M5.Touch.getCount() != 0, millis(), shutdown, &shutdownCount);
    delay(5);
}
