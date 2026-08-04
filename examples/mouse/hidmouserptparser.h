#if!defined(__HIDMOUSERPTPARSER_H__)
#define __HIDMOUSERPTPARSER_H__

#include <hidboot.h>

#define CHECK_BIT(var, pos)((var) & pos)
#define MOUSE_LEFT 1
#define MOUSE_RIGHT 2
#define MOUSE_MIDDLE 4
#define MOUSE_PREV 8
#define MOUSE_NEXT 16

struct CUSTOMMOUSEINFO {
  uint8_t buttons;
  int8_t dX;
  int8_t dY;
  int8_t dZ;
};

void onButtonUp(uint16_t id);
void onButtonDown(uint16_t id);
void onMouseMove(int8_t xMovement, int8_t yMovement, int8_t scrollValue);

class MouseRptParser: public MouseReportParser {
  union {
    CUSTOMMOUSEINFO mouseInfo;
    uint8_t bInfo[sizeof(CUSTOMMOUSEINFO)];
  }
  prevState;

  public:
    void Parse(USBHID * hid, bool is_rpt_id, uint8_t len, uint8_t * buf);
};

#endif //__HIDMOUSERPTPARSER_H__