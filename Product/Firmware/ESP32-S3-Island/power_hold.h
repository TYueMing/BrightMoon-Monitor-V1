#pragma once
#include <stdint.h>

// Pure state machine: ignore the boot hold, debounce edges, fire once per press.
// Unsigned subtraction also works across the millis() wrap-around.
class PowerHold {
 public:
  bool update(bool pressed, uint32_t now) {
    if (!initialized_) { initialized_ = true; raw_ = pressed; edge_ = now; }
    if (pressed != raw_) { raw_ = pressed; edge_ = now; }
    if (now - edge_ < 40) return false;
    if (!raw_) { armed_ = true; down_ = false; fired_ = false; return false; }
    if (!armed_) return false;
    if (!down_) { down_ = true; start_ = now; }
    if (!fired_ && now - start_ >= 3000) { fired_ = true; return true; }
    return false;
  }
 private:
  bool initialized_ = false, raw_ = false, armed_ = false, down_ = false, fired_ = false;
  uint32_t edge_ = 0, start_ = 0;
};
