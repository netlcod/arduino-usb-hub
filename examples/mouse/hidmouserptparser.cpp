#include "hidmouserptparser.h"

#if HUB_DEBUG_DUMP
static uint8_t debugReports = 0;
#endif

static uint16_t prevButtons = 0;

void MouseRptParser::Parse(USBHID* hid __attribute__((unused)), bool is_rpt_id __attribute__((unused)), uint8_t len, uint8_t* buf) {
#if HUB_DEBUG_DUMP
  if (debugReports < 20) {
    Serial.print("RPT");
    for (uint8_t i = 0; i < len; i++) {
      Serial.print(' ');
      if (buf[i] < 0x10) Serial.print('0');
      Serial.print(buf[i], HEX);
    }
    Serial.println();
    debugReports++;
  }
#endif

  if (len == 0) return;

  // decode_input() strips the report ID byte itself (detected by length);
  // stripping it here too would corrupt reports where buttons == report id.
  MouseState state;
  if (!decode_input(buf, len, state)) return;

  uint16_t changed = prevButtons ^ state.buttons;
  for (uint16_t bit = 1; bit != 0; bit <<= 1) {
    if (changed & bit) {
      if (state.buttons & bit) {
        onButtonDown(bit);
#if HUB_BUTTON_MARKER
        onMouseMove((int16_t)(bit * 8), 0, 0, 0);
#endif
      } else {
        onButtonUp(bit);
      }
    }
  }
  prevButtons = state.buttons;

  if (state.x != 0 || state.y != 0 || state.wheel != 0 || state.pan != 0) {
    onMouseMove(state.x, state.y, state.wheel, state.pan);
  }
};
