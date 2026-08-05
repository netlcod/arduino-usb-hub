#include <Mouse.h>
#include <usbhub.h>
#include "hidmouserptparser.h"

USB Usb;
HIDBoot<USB_HID_PROTOCOL_MOUSE> HidMouse(&Usb, true); // report protocol
MouseRptParser Prs;

void onButtonUp(uint16_t id) {
  Mouse.release(id);
};

void onButtonDown(uint16_t id) {
  Mouse.press(id);
};

void onMouseMove(int16_t xMovement, int16_t yMovement, int16_t scrollValue, int16_t panValue) {
  Mouse.move(xMovement, yMovement, scrollValue, panValue);
};

void setup() {
#if HUB_DEBUG_DUMP
  Serial.begin(115200);
#endif
#if HUB_BUTTON_MARKER
  pinMode(LED_BUILTIN, OUTPUT);
  for (uint8_t i = 0; i < 3; i++) {
    digitalWrite(LED_BUILTIN, HIGH);
    delay(200);
    digitalWrite(LED_BUILTIN, LOW);
    delay(200);
  }
#endif
  Mouse.begin();
  Usb.Init();
  HidMouse.SetReportParser(0, &Prs);
}

void loop() {
  Usb.Task();
}
