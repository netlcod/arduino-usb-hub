#include "hidmouserptparser.h"

void MouseRptParser::Parse(USBHID * hid __attribute__((unused)), bool is_rpt_id __attribute__((unused)), uint8_t len __attribute__((unused)), uint8_t * buf) {
  CUSTOMMOUSEINFO * pmi = (CUSTOMMOUSEINFO * ) buf;

  if (CHECK_BIT(prevState.mouseInfo.buttons, MOUSE_LEFT) != CHECK_BIT(pmi -> buttons, MOUSE_LEFT)) {
    if (CHECK_BIT(pmi -> buttons, MOUSE_LEFT)) {
      onButtonDown(MOUSE_LEFT);
    } else {
      onButtonUp(MOUSE_LEFT);
    }
  }

  if (CHECK_BIT(prevState.mouseInfo.buttons, MOUSE_RIGHT) != CHECK_BIT(pmi -> buttons, MOUSE_RIGHT)) {
    if (CHECK_BIT(pmi -> buttons, MOUSE_RIGHT)) {
      onButtonDown(MOUSE_RIGHT);
    } else {
      onButtonUp(MOUSE_RIGHT);
    }
  }

  if (CHECK_BIT(prevState.mouseInfo.buttons, MOUSE_MIDDLE) != CHECK_BIT(pmi -> buttons, MOUSE_MIDDLE)) {
    if (CHECK_BIT(pmi -> buttons, MOUSE_MIDDLE)) {
      onButtonDown(MOUSE_MIDDLE);
    } else {
      onButtonUp(MOUSE_MIDDLE);
    }
  }

  if (CHECK_BIT(prevState.mouseInfo.buttons, MOUSE_PREV) != CHECK_BIT(pmi -> buttons, MOUSE_PREV)) {
    if (CHECK_BIT(pmi -> buttons, MOUSE_PREV)) {
      onButtonDown(MOUSE_PREV);
    } else {
      onButtonUp(MOUSE_PREV);
    }
  }

  if (CHECK_BIT(prevState.mouseInfo.buttons, MOUSE_NEXT) != CHECK_BIT(pmi -> buttons, MOUSE_NEXT)) {
    if (CHECK_BIT(pmi -> buttons, MOUSE_NEXT)) {
      onButtonDown(MOUSE_NEXT);
    } else {
      onButtonUp(MOUSE_NEXT);
    }
  }

  int8_t xMovement = pmi -> dX;
  int8_t yMovement = pmi -> dY;
  int8_t scrollValue = pmi -> dZ;

  if (xMovement > 127) {
    xMovement -= 256;
  }
  if (yMovement > 127) {
    yMovement -= 256;
  }

  if (xMovement != 0 || yMovement != 0 || scrollValue != 0) {
    onMouseMove(xMovement, yMovement, scrollValue);
  }

  prevState.bInfo[0] = buf[0];
};