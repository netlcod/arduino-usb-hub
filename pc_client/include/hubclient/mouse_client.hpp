// MouseClient — high-level PC side of the command channel.
//
// Opens the vendor-defined HID collection (usage page 0xFF00) of the
// target device and sends binary mouse commands. Opcode/payload layout
// comes from generated/mouse_commands.h (generated from
// commands/mouse.json by arduino-hub), channel wiring defaults from
// generated/command_channel.h; when the firmware has the capability
// report enabled, the values read at runtime override the defaults.

#pragma once

#include <cstdint>
#include <memory>

#include "hidapi/hidapi.h"

#include "capability.hpp"
#include "command_channel.h"
#include "mouse_commands.h"
#include "transport.hpp"

namespace hubclient {

class MouseClient {
public:
  MouseClient() = default;
  ~MouseClient() { close(); }

  MouseClient(const MouseClient&) = delete;
  MouseClient& operator=(const MouseClient&) = delete;

  // Open the command channel of the first device with VID/PID whose
  // enumeration reports the vendor-defined usage page 0xFF00.
  // hidapi 0.15: hid_enumerate(vid, pid) — the serial filter argument
  // was removed; the usage page filter stays manual.
  bool open(uint16_t vid, uint16_t pid) {
    close();
    hid_device_info* info = hid_enumerate(vid, pid);
    const char* path = nullptr;
    for (hid_device_info* it = info; it; it = it->next) {
      if (it->vendor_id == vid && it->product_id == pid &&
          it->usage_page == HID_COMMAND_USAGE_PAGE) {
        path = it->path;
        break;
      }
    }
    if (path == nullptr) {
      hid_free_enumeration(info);
      return false;
    }
    device_ = hid_open_path(path);
    hid_free_enumeration(info);
    if (device_ == nullptr) return false;

    uint8_t reportId = HID_COMMAND_REPORT_ID;
    uint8_t payloadLen = HID_COMMAND_PAYLOAD_LEN;
    Capability cap;
    if (readCapability(device_, cap) && cap.valid()) {
      reportId = cap.commandReportId;
      payloadLen = cap.payloadLen;
    }

#if HID_COMMAND_TRANSPORT == HID_COMMAND_TRANSPORT_FEATURE
    transport_.reset(
        new FeatureReportTransport(device_, reportId, payloadLen));
#elif HID_COMMAND_TRANSPORT == HID_COMMAND_TRANSPORT_OUTPUT
    transport_.reset(
        new OutputReportTransport(device_, reportId, payloadLen));
#elif HID_COMMAND_TRANSPORT == HID_COMMAND_TRANSPORT_INTERRUPT_OUT
    // interrupt OUT endpoint: hid_write routes to the OUT endpoint when
    // the device exposes one, so the same OutputReportTransport is used.
    transport_.reset(
        new OutputReportTransport(device_, reportId, payloadLen));
#else
    close();
    return false;
#endif
    return true;
  }

  void close() {
    transport_.reset();
    if (device_ != nullptr) {
      hid_close(device_);
      device_ = nullptr;
    }
  }

  bool isOpen() const { return device_ != nullptr; }

  // Raw hidapi handle, for error reporting and advanced use.
  hid_device* handle() const { return device_; }

  bool move(int16_t dx, int16_t dy) {
    // Packet layout from the generated mouse_commands.h (single source
    // of truth shared with the firmware).
    uint8_t pkt[MOUSE_CMD_MAX_PACKET] = {0};
    pkt[MOUSE_CMD_OPCODE_OFF] = MOUSE_CMD_MOVE;
    pkt[MOUSE_CMD_MOVE_DX_OFF] = (uint8_t)(dx & 0xFF);
    pkt[MOUSE_CMD_MOVE_DX_OFF + 1] = (uint8_t)((dx >> 8) & 0xFF);
    pkt[MOUSE_CMD_MOVE_DY_OFF] = (uint8_t)(dy & 0xFF);
    pkt[MOUSE_CMD_MOVE_DY_OFF + 1] = (uint8_t)((dy >> 8) & 0xFF);
    return write(pkt, MOUSE_CMD_MOVE_LEN);
  }

  bool buttonDown(uint8_t button) {
    return buttonCommand(MOUSE_CMD_BUTTON_DOWN, button);
  }

  bool buttonUp(uint8_t button) {
    return buttonCommand(MOUSE_CMD_BUTTON_UP, button);
  }

  bool click(uint8_t button) { return buttonCommand(MOUSE_CMD_CLICK, button); }

  bool wheel(int8_t delta) {
    uint8_t pkt[MOUSE_CMD_MAX_PACKET] = {0};
    pkt[MOUSE_CMD_OPCODE_OFF] = MOUSE_CMD_WHEEL;
    pkt[MOUSE_CMD_WHEEL_DELTA_OFF] = (uint8_t)delta;
    return write(pkt, MOUSE_CMD_WHEEL_LEN);
  }

private:
  bool buttonCommand(uint8_t opcode, uint8_t button) {
    // All button commands (BUTTON_DOWN/UP/CLICK) share one layout —
    // [opcode u8][button u8] — guaranteed by the schema.
    uint8_t pkt[MOUSE_CMD_MAX_PACKET] = {0};
    pkt[MOUSE_CMD_OPCODE_OFF] = opcode;
    pkt[MOUSE_CMD_BUTTON_DOWN_BUTTON_OFF] = button;
    return write(pkt, MOUSE_CMD_BUTTON_DOWN_LEN);
  }

  bool write(const uint8_t* pkt, std::size_t len) {
    return transport_ != nullptr && transport_->write(pkt, len);
  }

  hid_device* device_ = nullptr;
  std::unique_ptr<CommandTransport> transport_;
};

}  // namespace hubclient
