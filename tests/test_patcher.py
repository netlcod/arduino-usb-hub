"""Patcher tests: Mouse library patch (16-bit buttons) and idempotency.

Uses stub copies of the stock Arduino Mouse library (Mouse.h/Mouse.cpp) so
the tests do not depend on the downloaded arduino-cli-data.
"""

from pathlib import Path

from arduino_hub.core.patcher import USBPatcher
from arduino_hub.devices import load as load_device
from arduino_hub.targets import load_target

REPO_ROOT = Path(__file__).resolve().parents[1]

STOCK_MOUSE_H = """\
#ifndef MOUSE_h
#define MOUSE_h

#include "HID.h"

#if !defined(_USING_HID)

#warning "Using legacy HID core (non pluggable)"

#else

#define MOUSE_LEFT 1
#define MOUSE_RIGHT 2
#define MOUSE_MIDDLE 4
#define MOUSE_ALL (MOUSE_LEFT | MOUSE_RIGHT | MOUSE_MIDDLE)

class Mouse_
{
private:
  uint8_t _buttons;
  void buttons(uint8_t b);
public:
  Mouse_(void);
  void begin(void);
  void end(void);
  void click(uint8_t b = MOUSE_LEFT);
  void move(signed char x, signed char y, signed char wheel = 0);
  void press(uint8_t b = MOUSE_LEFT);
  void release(uint8_t b = MOUSE_LEFT);
  bool isPressed(uint8_t b = MOUSE_LEFT);
};
extern Mouse_ Mouse;

#endif
#endif
"""

STOCK_MOUSE_CPP = """\
#include "Mouse.h"

#if defined(_USING_HID)

static const uint8_t _hidReportDescriptor[] PROGMEM = {
  0x05, 0x01,
};

Mouse_::Mouse_(void) : _buttons(0)
{
    static HIDSubDescriptor node(_hidReportDescriptor, sizeof(_hidReportDescriptor));
    HID().AppendDescriptor(&node);
}

void Mouse_::begin(void)
{
}

void Mouse_::end(void)
{
}

void Mouse_::click(uint8_t b)
{
\t_buttons = b;
\tmove(0,0,0);
\t_buttons = 0;
\tmove(0,0,0);
}

void Mouse_::move(signed char x, signed char y, signed char wheel)
{
\tuint8_t m[4];
\tm[0] = _buttons;
\tm[1] = x;
\tm[2] = y;
\tm[3] = wheel;
\tHID().SendReport(1,m,4);
}

void Mouse_::buttons(uint8_t b)
{
\tif (b != _buttons)
\t{
\t\t_buttons = b;
\t\tmove(0,0,0);
\t}
}

void Mouse_::press(uint8_t b)
{
\tbuttons(_buttons | b);
}

void Mouse_::release(uint8_t b)
{
\tbuttons(_buttons & ~b);
}

bool Mouse_::isPressed(uint8_t b)
{
\tif ((b & _buttons) > 0)
\t\treturn true;
\treturn false;
}

Mouse_ Mouse;

#endif
"""


def _make_lib(tmp_path) -> Path:
    lib = tmp_path / "src"
    lib.mkdir(parents=True)
    (lib / "Mouse.h").write_text(STOCK_MOUSE_H, encoding="utf-8")
    (lib / "Mouse.cpp").write_text(STOCK_MOUSE_CPP, encoding="utf-8")
    return lib


def test_patch_mouse_library_widens_buttons(tmp_path):
    lib = _make_lib(tmp_path)
    USBPatcher.patch_mouse_library(
        lib,
        load_device("g305", REPO_ROOT),
        load_target("generic_16btn", REPO_ROOT),
    )

    h = (lib / "Mouse.h").read_text(encoding="utf-8")
    assert "uint16_t _buttons;" in h
    assert "void buttons(uint16_t b);" in h
    assert "void press(uint16_t b = MOUSE_LEFT);" in h
    assert "bool isPressed(uint16_t b = MOUSE_LEFT);" in h
    assert "uint8_t b" not in h

    cpp = (lib / "Mouse.cpp").read_text(encoding="utf-8")
    assert "Mouse_::click(uint16_t b)" in cpp
    assert "Mouse_::buttons(uint16_t b)" in cpp
    assert "Mouse_::press(uint16_t b)" in cpp
    assert "Mouse_::release(uint16_t b)" in cpp
    assert "bool Mouse_::isPressed(uint16_t b)" in cpp
    assert "uint8_t b)" not in cpp

    # Includes + descriptor + move() rewrites from Phase 1.
    assert '#include "hid_profile.h"' in cpp
    assert '#include "hid_mapper.h"' in cpp
    assert "uint8_t _hidReportDescriptor" not in cpp
    assert "static HIDSubDescriptor node(HID_DESCRIPTOR, sizeof(HID_DESCRIPTOR));" in cpp
    assert "encode_output(state, m)" in cpp
    assert "void Mouse_::move(int16_t x, int16_t y, int16_t wheel, int16_t pan)" in cpp
    assert "void move(int16_t x, int16_t y, int16_t wheel = 0, int16_t pan = 0);" in h

    assert (lib / "hid_profile.h").exists()
    assert (lib / "hid_mapper.h").exists()


def test_patch_mouse_library_idempotent(tmp_path):
    lib = _make_lib(tmp_path)
    source = load_device("g305", REPO_ROOT)
    target = load_target("generic_16btn", REPO_ROOT)

    USBPatcher.patch_mouse_library(lib, source, target)
    first = {
        name: path.read_bytes()
        for name, path in (
            (f.name, f)
            for f in lib.iterdir()
            if f.suffix in (".h", ".cpp")
        )
    }

    USBPatcher.patch_mouse_library(lib, source, target)
    second = {
        name: path.read_bytes()
        for name, path in (
            (f.name, f)
            for f in lib.iterdir()
            if f.suffix in (".h", ".cpp")
        )
    }

    assert set(first) == set(second)
    for name in first:
        assert first[name] == second[name], f"{name} changed on re-patch"
