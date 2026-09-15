// E5 probe: report protocol without SET_PROTOCOL.
// docs/plans/g305-xy-order-investigation.md, Phase 1.
//
// Same receiver, same report protocol as the main example (HIDBoot
// variant), but a different activation path: HIDComposite claims the
// boot-mouse interface directly and sends only setConf + SetIdle — no
// SET_PROTOCOL. If the X/Y byte order in this dump differs from the
// HIDBoot dump (Phase 0-A), the activation transactions drive the
// receiver's legacy layout (hypotheses F1/F3/F4).
//
// Bitness note: X/Y on the wire are 16-bit regardless of the claim
// path — the report descriptor defines them; this probe changes only
// which control transactions run at activation.
//
// Build (Serial needs CDC): remove -DCDC_DISABLED from boards.txt,
// compile/upload directly with arduino-cli, open the monitor, then
// follow the READY instructions. Restore by re-running 'arduino-hub
// patch' (idempotent) before the next normal flash.
//
// Movement procedure (Phase 0-A template):
//   right 5 s -> pause -> up 5 s -> wheel -> buttons L/R/M/S1/S2.
// Analysis: which byte positions carry the X delta vs the HIDBoot run.

#include <usbhub.h>
#include <hidcomposite.h>
#include <hidboot.h>  // USB_HID_PROTOCOL_* constants
#include "probe_parser.h"

USB Usb;

class MouseComposite : public HIDComposite {
public:
  MouseComposite(USB* p) : HIDComposite(p) {}

protected:
  bool SelectInterface(uint8_t iface, uint8_t proto) override {
    (void)iface;
    // Claim only the boot-mouse interface (protocol 2); the keyboard
    // (1) and vendor (0) interfaces of the composite stay unclaimed.
    return proto == USB_HID_PROTOCOL_MOUSE;
  }
};

MouseComposite HidMouse(&Usb);
ProbeParser Prs;

void setup() {
  Serial.begin(115200);
  while (!Serial) {
    // Leonardo CDC: wait for the monitor so the first reports are not
    // lost before capture starts.
  }
  Serial.println("xy_probe E5: HIDComposite (report protocol, no SET_PROTOCOL)");

  if (Usb.Init() == -1) Serial.println("OSC did not start.");
  delay(200);

  if (!HidMouse.SetReportParser(0, &Prs)) Serial.println("SetReportParser failed");

  Serial.println("READY: right 5s -> pause -> up 5s -> wheel -> buttons");
}

void loop() {
  Usb.Task();
}
