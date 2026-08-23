// Capability report (meta-level interface, optional).
//
// Layout of the static payload served by the device on
// GET_REPORT(Feature, report id 4):
//   [0..1] magic 'H' 'C'
//   [2]    transport          (1 = feature, 2 = output, 3 = interrupt_out)
//   [3]    command report id  (3)
//   [4]    payload length     (15)
//   [5]    queue slots
//   [6]    device type        (1 = mouse)
//   [7]    command protocol version
//
// Compiled out (returns false) when the channel was patched with
// capability disabled (HID_CAPABILITY_ENABLED == 0).

#pragma once

#include <cstdint>

#include <hidapi.h>

#include "command_channel.h"

namespace hubclient {

struct Capability {
  uint8_t transport = 0;
  uint8_t commandReportId = 0;
  uint8_t payloadLen = 0;
  uint8_t queueSlots = 0;
  uint8_t deviceType = 0;
  uint8_t protocolVersion = 0;

  bool valid() const {
    return (transport == HID_COMMAND_TRANSPORT_FEATURE ||
           transport == HID_COMMAND_TRANSPORT_OUTPUT ||
           transport == HID_COMMAND_TRANSPORT_INTERRUPT_OUT) &&
           commandReportId == HID_COMMAND_REPORT_ID &&
           payloadLen == HID_COMMAND_PAYLOAD_LEN;
  }
};

inline bool readCapability(hid_device* device, Capability& out) {
#if HID_CAPABILITY_ENABLED
  uint8_t buf[1 + HID_CAPABILITY_PAYLOAD_LEN] = {0};
  buf[0] = HID_CAPABILITY_REPORT_ID;
  if (hid_get_feature_report(device, buf, sizeof(buf)) < 0) return false;
  if (buf[1] != 'H' || buf[2] != 'C') return false;
  out.transport = buf[3];
  out.commandReportId = buf[4];
  out.payloadLen = buf[5];
  out.queueSlots = buf[6];
  out.deviceType = buf[7];
  out.protocolVersion = buf[8];
  return true;
#else
  (void)device;
  (void)out;
  return false;
#endif
}

}  // namespace hubclient
