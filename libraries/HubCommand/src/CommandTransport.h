// CommandTransport — transport abstraction over the patched core HID
// library. Hides from upper layers how a PC -> Arduino report arrived
// (Output vs Feature SET_REPORT). The core only stores raw reports in
// a fixed ring buffer; no command semantics live here.
//
// FeatureReportTransport and OutputReportTransport are separate classes
// for API clarity; today both read the same core ring (the transport
// choice is a descriptor/build-time decision). InterruptOutTransport
// polls the interrupt OUT endpoint directly via USB_Recv.
//
// The concrete transport is selected by hid_command_config.h
// (generated per target by arduino-hub), so switching transport never
// requires changes to the sketch.

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

class FeatureReportTransport : public CommandTransport {
public:
#if HID_COMMAND_ENABLED
  int available() override { return HID().availableReportPackets(); }
  int read(uint8_t* dst, uint8_t maxlen) override {
    return HID().readReportPacket(dst, maxlen);
  }
#else
  int available() override { return 0; }
  int read(uint8_t* dst, uint8_t) override { return 0; }
#endif
};

class OutputReportTransport : public CommandTransport {
public:
#if HID_COMMAND_ENABLED
  int available() override { return HID().availableReportPackets(); }
  int read(uint8_t* dst, uint8_t maxlen) override {
    return HID().readReportPacket(dst, maxlen);
  }
#else
  int available() override { return 0; }
  int read(uint8_t* dst, uint8_t) override { return 0; }
#endif
};

class InterruptOutTransport : public CommandTransport {
public:
#if HID_COMMAND_ENABLED && HID_COMMAND_TRANSPORT == HID_COMMAND_TRANSPORT_INTERRUPT_OUT
  int available() override { return HID().availableOutReport(); }
  int read(uint8_t* dst, uint8_t maxlen) override {
    return HID().readOutReport(dst, maxlen);
  }
#else
  int available() override { return 0; }
  int read(uint8_t* dst, uint8_t) override { return 0; }
#endif
};

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
