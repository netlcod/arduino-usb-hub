// MouseCommandHandler implementation.
//
// Packet framing (payload only; the Report ID travels in SET_REPORT
// wValue and is stripped by the host driver):
//   [opcode u8][args...]
// All packets are fixed-length (HID_COMMAND_PAYLOAD_LEN) on the wire;
// unused trailing bytes are zero-padded by the client.

#include "MouseCommandHandler.h"

#include <Mouse.h>

#include "CommandTransport.h"
#include "hid_command_config.h"

#define HUB_MAX_BUTTONS 16

// Diagnostic: blink LED_BUILTIN once per received packet, `opcode` times
// (MOVE=1 ... WHEEL=5). 0 = off (production). The library sources are
// re-copied to user libraries on every `arduino-hub patch`, so edit this
// file in libraries/HubCommand/ and re-flash.
#define HUB_CMD_MARKER 0

#if HID_COMMAND_ENABLED

static inline int16_t read_i16(const uint8_t* p) {
  return (int16_t)((uint16_t)p[0] | ((uint16_t)p[1] << 8));
}

static inline bool validButton(uint8_t button) {
  return button >= 1 && button <= HUB_MAX_BUTTONS;
}

#if HUB_CMD_MARKER
static void markerBlink(uint8_t count) {
  pinMode(LED_BUILTIN, OUTPUT);
  for (uint8_t i = 0; i < count; i++) {
    digitalWrite(LED_BUILTIN, HIGH);
    delay(100);
    digitalWrite(LED_BUILTIN, LOW);
    delay(100);
  }
  delay(400);
}
#endif

void MouseCommandHandler::poll() {
  CommandTransport& transport = getCommandTransport();
  uint8_t pkt[HID_COMMAND_PAYLOAD_LEN];

  while (transport.available() > 0) {
    int n = transport.read(pkt, sizeof(pkt));
    if (n < 1) {
      continue;
    }
#if HUB_CMD_MARKER
    markerBlink(pkt[0]);
#endif
    switch (pkt[0]) {
      case MOUSE_CMD_MOVE:
        // args: dx i16, dy i16 (offsets from mouse_commands.h)
        if (n >= MOUSE_CMD_MOVE_LEN) {
          Mouse.move(read_i16(&pkt[MOUSE_CMD_MOVE_DX_OFF]),
                     read_i16(&pkt[MOUSE_CMD_MOVE_DY_OFF]), 0, 0);
        }
        break;
      case MOUSE_CMD_BUTTON_DOWN:
        // arg: button u8 (logical 1..16)
        if (n >= MOUSE_CMD_BUTTON_DOWN_LEN &&
            validButton(pkt[MOUSE_CMD_BUTTON_DOWN_BUTTON_OFF])) {
          Mouse.press((uint16_t)(1U << (pkt[MOUSE_CMD_BUTTON_DOWN_BUTTON_OFF] - 1)));
        }
        break;
      case MOUSE_CMD_BUTTON_UP:
        if (n >= MOUSE_CMD_BUTTON_UP_LEN &&
            validButton(pkt[MOUSE_CMD_BUTTON_UP_BUTTON_OFF])) {
          Mouse.release((uint16_t)(1U << (pkt[MOUSE_CMD_BUTTON_UP_BUTTON_OFF] - 1)));
        }
        break;
      case MOUSE_CMD_CLICK:
        if (n >= MOUSE_CMD_CLICK_LEN &&
            validButton(pkt[MOUSE_CMD_CLICK_BUTTON_OFF])) {
          Mouse.click((uint16_t)(1U << (pkt[MOUSE_CMD_CLICK_BUTTON_OFF] - 1)));
        }
        break;
      case MOUSE_CMD_WHEEL:
        // arg: delta i8
        if (n >= MOUSE_CMD_WHEEL_LEN) {
          Mouse.move(0, 0, (int8_t)pkt[MOUSE_CMD_WHEEL_DELTA_OFF], 0);
        }
        break;
      default:
        break;
    }
  }
}

#else  // HID_COMMAND_ENABLED

void MouseCommandHandler::poll() {
}

#endif  // HID_COMMAND_ENABLED
