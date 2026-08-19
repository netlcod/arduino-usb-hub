// MouseCommandHandler — device-specific command layer.
//
// Translates command packets (opcode + typed payload, layout generated
// from commands/mouse.json into mouse_commands.h) into the patched
// Mouse API. It does NOT know target-specific HID encoding: Mouse API
// calls go through the usual encode_output() path inside Mouse.cpp.
//
// Button arguments are logical button numbers (1..N); the handler maps
// them to button bits. This keeps the command protocol independent of
// the target layout (generic_3btn/5btn/16btn share the same protocol).

#ifndef HUB_MOUSE_COMMAND_HANDLER_H
#define HUB_MOUSE_COMMAND_HANDLER_H

#include "DeviceCommandHandler.h"
#include "mouse_commands.h"

class MouseCommandHandler : public DeviceCommandHandler {
public:
  void poll() override;
};

#endif  // HUB_MOUSE_COMMAND_HANDLER_H
