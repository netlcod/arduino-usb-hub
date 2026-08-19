// DeviceCommandHandler — base class for device-specific command
// handlers. Handlers translate command packets into calls to the
// device API (Mouse/Keyboard/Gamepad); they never touch HID report
// encoding or the transport directly.

#ifndef HUB_DEVICE_COMMAND_HANDLER_H
#define HUB_DEVICE_COMMAND_HANDLER_H

class DeviceCommandHandler {
public:
  virtual void poll() = 0;
  virtual ~DeviceCommandHandler() {}
};

#endif  // HUB_DEVICE_COMMAND_HANDLER_H
