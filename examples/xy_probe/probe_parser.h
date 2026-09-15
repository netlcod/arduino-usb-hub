#if !defined(__XY_PROBE_PARSER_H__)
#define __XY_PROBE_PARSER_H__

#include <usbhid.h>

// Raw-report dump for the X/Y order investigation (Phase 1, E5).
// See docs/plans/g305-xy-order-investigation.md. Prints "RPT <hex...>"
// lines to Serial (CDC build); only reports whose bytes change vs the
// previous one are printed, because the receiver streams zero reports
// at 1 kHz even when idle.
class ProbeParser : public HIDReportParser {
public:
  void Parse(USBHID* hid, bool is_rpt_id, uint8_t len, uint8_t* buf);
};

#endif // __XY_PROBE_PARSER_H__
