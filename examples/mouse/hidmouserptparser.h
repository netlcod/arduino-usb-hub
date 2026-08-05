#if !defined(__HIDMOUSERPTPARSER_H__)
#define __HIDMOUSERPTPARSER_H__

#include <hidboot.h>
#include <hid_mapper.h>

// Temporary debug: dump the first 20 reports as hex to Serial (USB CDC).
// Needs the unpatched core (CDC enabled) to run; set to 0 for patched builds.
#define HUB_DEBUG_DUMP 0

// Temporary diagnostic: each button press moves the cursor right by
// (button_bit * 8) px, so the movement distance reveals the button bit.
// The board blinks its LED 3 times at startup when this is enabled.
#define HUB_BUTTON_MARKER 0

void onButtonUp(uint16_t id);
void onButtonDown(uint16_t id);
void onMouseMove(int16_t xMovement, int16_t yMovement, int16_t scrollValue, int16_t panValue);

class MouseRptParser : public HIDReportParser {
public:
  void Parse(USBHID* hid, bool is_rpt_id, uint8_t len, uint8_t* buf);
};

#endif // __HIDMOUSERPTPARSER_H__
