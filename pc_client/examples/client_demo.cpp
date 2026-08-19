// client_demo — exercise the command channel with visible feedback.
//
// Usage:
//   client_demo --list             print every HID device (VID/PID/usage page)
//   client_demo [VID] [PID]        draw a square, scroll and click;
//                                  defaults to 0x046D:0xC53F
//
// Every command prints ok/fail (+ hidapi error on failure). The cursor
// should visibly move in a square; on the device, HUB_CMD_MARKER makes
// the LED blink `opcode` times per received packet.

#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <thread>

#include "hidapi/hidapi.h"
#include "hubclient/mouse_client.hpp"

using namespace std::chrono_literals;

static void printHidError(hid_device* device) {
#ifdef _WIN32
  const wchar_t* err = hid_error(device);
  fwprintf(stderr, L" (%ls)\n", err);
#else
  const char* err = hid_error(device);
  fprintf(stderr, " (%s)\n", err);
#endif
}

static void run(const char* name, bool ok, hubclient::MouseClient& mouse) {
  std::printf("%-16s %s", name, ok ? "ok" : "FAIL");
  if (ok) {
    std::printf("\n");
  } else {
    printHidError(mouse.handle());
  }
}

static void wait() {
  std::this_thread::sleep_for(600ms);
}

int main(int argc, char** argv) {
  if (argc >= 2 && std::string(argv[1]) == "--list") {
    hid_device_info* info = hid_enumerate(0, 0);
    std::printf("%-6s %-6s %-9s %-6s %s\n", "VID", "PID", "UsagePage",
                "Usage", "Path");
    for (hid_device_info* it = info; it; it = it->next) {
#ifdef _WIN32
      std::printf("0x%04X 0x%04X 0x%04X   0x%04X  %ls\n", it->vendor_id,
                  it->product_id, it->usage_page, it->usage, it->path);
#else
      std::printf("0x%04X 0x%04X 0x%04X   0x%04X  %s\n", it->vendor_id,
                  it->product_id, it->usage_page, it->usage, it->path);
#endif
    }
    hid_free_enumeration(info);
    return 0;
  }

  uint16_t vid = 0x046D;
  uint16_t pid = 0xC53F;
  if (argc >= 3) {
    vid = static_cast<uint16_t>(std::strtoul(argv[1], nullptr, 0));
    pid = static_cast<uint16_t>(std::strtoul(argv[2], nullptr, 0));
  }

  hubclient::MouseClient mouse;
  if (!mouse.open(vid, pid)) {
    std::fprintf(stderr, "command channel not found (VID %04X PID %04X)\n",
                 vid, pid);
    std::fprintf(stderr, "run 'client_demo --list' to inspect HID devices\n");
    return 1;
  }
  std::printf("command channel open\n");

  // Visible square: 100 px per step, 600 ms pauses.
  run("move(100,0)", mouse.move(100, 0), mouse);
  wait();
  run("move(0,100)", mouse.move(0, 100), mouse);
  wait();
  run("move(-100,0)", mouse.move(-100, 0), mouse);
  wait();
  run("move(0,-100)", mouse.move(0, -100), mouse);
  wait();
  run("wheel(-5)", mouse.wheel(-5), mouse);
  wait();
  run("click(1)", mouse.click(1), mouse);

  mouse.close();
  std::printf("done\n");
  return 0;
}
