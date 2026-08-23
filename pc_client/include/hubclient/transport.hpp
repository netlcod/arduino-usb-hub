// CommandTransport (PC side) — hides how a command packet is delivered
// over hidapi. Buffers are payload-only; the transport prepends the
// Report ID byte and zero-pads to the full report length.
//
// hidapi conventions fixed by the command channel contract:
//   hidapi buffer length      = 1 + payload length  ([report id][payload])
//   SET_REPORT data stage     = payload length only (no report id byte)

#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>

#include <hidapi.h>

#include "command_channel.h"

namespace hubclient {

class CommandTransport {
public:
  virtual ~CommandTransport() = default;

  // Send a command payload (without the Report ID byte). The payload is
  // zero-padded to the full report payload length on the wire.
  virtual bool write(const uint8_t* payload, std::size_t len) = 0;
};

class FeatureReportTransport : public CommandTransport {
public:
  FeatureReportTransport(hid_device* device, uint8_t reportId,
                         uint8_t payloadLen)
      : device_(device), reportId_(reportId), payloadLen_(payloadLen) {}

  bool write(const uint8_t* payload, std::size_t len) override {
    uint8_t buf[HID_COMMAND_TOTAL_LEN] = {0};
    buf[0] = reportId_;
    if (len > payloadLen_) len = payloadLen_;
    std::memcpy(buf + 1, payload, len);
    return hid_send_feature_report(device_, buf, payloadLen_ + 1) ==
           payloadLen_ + 1;
  }

private:
  hid_device* device_;
  uint8_t reportId_;
  uint8_t payloadLen_;
};

class OutputReportTransport : public CommandTransport {
public:
  OutputReportTransport(hid_device* device, uint8_t reportId,
                        uint8_t payloadLen)
      : device_(device), reportId_(reportId), payloadLen_(payloadLen) {}

  bool write(const uint8_t* payload, std::size_t len) override {
    uint8_t buf[HID_COMMAND_TOTAL_LEN] = {0};
    buf[0] = reportId_;
    if (len > payloadLen_) len = payloadLen_;
    std::memcpy(buf + 1, payload, len);
    return hid_write(device_, buf, payloadLen_ + 1) == payloadLen_ + 1;
  }

private:
  hid_device* device_;
  uint8_t reportId_;
  uint8_t payloadLen_;
};

}  // namespace hubclient
