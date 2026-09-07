// CommandTransport — transport abstraction over the patched core HID
// library. Hides from upper layers how a PC -> Arduino report arrived
// (Feature/Output SET_REPORT vs interrupt OUT endpoint). The core only
// stores raw reports; no command semantics live here.
//
// FeatureReportTransport and OutputReportTransport are separate classes
// for API clarity; today both read the same core ring (the transport
// choice is a descriptor/build-time decision). InterruptOutTransport
// reads the OUT endpoint through the core's availableOutReport()/
// readOutReport() helpers (which poll USB_Recv in loop()).
//
// The concrete transport is selected by hid_command_config.h
// (generated per target by arduino-hub), so switching transport never
// requires changes to the sketch. Each class is compiled only under
// the condition the factory selects it: C++ semantically checks inline
// member bodies at class-definition time even when never instantiated,
// and the patched core HID.h declares the corresponding API under the
// same guards — an ungated body would fail to compile on targets with
// the channel (or transport) off. NoneTransport is always available
// for the factory's disabled fallback.

#ifndef HUB_COMMAND_TRANSPORT_H
#define HUB_COMMAND_TRANSPORT_H

#include <stdint.h>

#include "HID.h"
#include "hid_command_config.h"

class CommandTransport {
public:
  virtual int available() = 0;
  virtual int read(uint8_t* dst, uint8_t maxlen) = 0;
  virtual ~CommandTransport() {}
};

#if HID_COMMAND_ENABLED

class FeatureReportTransport : public CommandTransport {
public:
  int available() override { return HID().availableReportPackets(); }
  int read(uint8_t* dst, uint8_t maxlen) override {
    return HID().readReportPacket(dst, maxlen);
  }
};

class OutputReportTransport : public CommandTransport {
public:
  int available() override { return HID().availableReportPackets(); }
  int read(uint8_t* dst, uint8_t maxlen) override {
    return HID().readReportPacket(dst, maxlen);
  }
};

#if HID_COMMAND_TRANSPORT == HID_COMMAND_TRANSPORT_INTERRUPT_OUT
class InterruptOutTransport : public CommandTransport {
public:
  int available() override { return HID().availableOutReport(); }
  int read(uint8_t* dst, uint8_t maxlen) override {
    return HID().readOutReport(dst, maxlen);
  }
};
#endif  // INTERRUPT_OUT

#endif  // HID_COMMAND_ENABLED

class NoneTransport : public CommandTransport {
public:
  int available() override { return 0; }
  int read(uint8_t* dst, uint8_t) override { return 0; }
};

inline CommandTransport& getCommandTransport() {
#if HID_COMMAND_ENABLED
  #if HID_COMMAND_TRANSPORT == HID_COMMAND_TRANSPORT_FEATURE
  static FeatureReportTransport transport;
  #elif HID_COMMAND_TRANSPORT == HID_COMMAND_TRANSPORT_OUTPUT
  static OutputReportTransport transport;
  #elif HID_COMMAND_TRANSPORT == HID_COMMAND_TRANSPORT_INTERRUPT_OUT
  static InterruptOutTransport transport;
  #else
  static NoneTransport transport;
  #endif
  return transport;
#else
  static NoneTransport transport;
  return transport;
#endif
}

#endif  // HUB_COMMAND_TRANSPORT_H
