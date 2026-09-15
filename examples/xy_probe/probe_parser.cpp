#include "probe_parser.h"

namespace {
constexpr uint8_t kMaxReports = 60;
constexpr uint8_t kMaxReportLen = 16;
uint8_t dumped = 0;
uint8_t prev[kMaxReportLen] = {0};
uint8_t prevLen = 0;
}  // namespace

void ProbeParser::Parse(USBHID* hid __attribute__((unused)),
                        bool is_rpt_id __attribute__((unused)),
                        uint8_t len, uint8_t* buf) {
  if (dumped >= kMaxReports || len == 0) return;

  bool differs = len != prevLen;
  for (uint8_t i = 0; i < len && !differs; i++)
    if (buf[i] != prev[i]) differs = true;
  if (!differs) return;

  Serial.print("RPT");
  for (uint8_t i = 0; i < len; i++) {
    Serial.print(' ');
    if (buf[i] < 0x10) Serial.print('0');
    Serial.print(buf[i], HEX);
  }
  Serial.println();
  dumped++;

  prevLen = len;
  for (uint8_t i = 0; i < len && i < kMaxReportLen; i++) prev[i] = buf[i];
}
