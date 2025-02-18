#include <Mouse.h>
#include <usbhub.h>
#include "hidmouserptparser.h"


USB Usb;
HIDBoot < USB_HID_PROTOCOL_MOUSE > HidMouse( & Usb);
MouseRptParser Prs;

void onButtonUp(uint16_t id) {
  Mouse.release(id);
};

void onButtonDown(uint16_t id) {
  Mouse.press(id);
};

void onMouseMove(int8_t xMovement, int8_t yMovement, int8_t scrollValue) {
  Mouse.move(xMovement, yMovement, scrollValue);
};

void setup() {
  Mouse.begin();
  Usb.Init();
  HidMouse.SetReportParser(0, & Prs);
}

void loop() {
  Usb.Task();
}