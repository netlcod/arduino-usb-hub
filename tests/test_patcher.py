"""Patcher tests: Mouse library patch (16-bit buttons), core HID command
channel patch, HubCommand/PC client installation, and idempotency.

Uses stub copies of the stock Arduino Mouse library (Mouse.h/Mouse.cpp)
and of the core HID library (HID.h/HID.cpp) so the tests do not depend
on the downloaded arduino-cli-data.
"""

import shutil
from pathlib import Path

from arduino_hub.core.patcher import (
    HID_CPP_MARKER,
    HID_H_MARKER,
    CommandChannelPatcher,
    LibraryPatcher,
)
from arduino_hub.targets import load_target
from arduino_hub.usbhid.command_schema import load_command_schema
from util import load_g305

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
    LibraryPatcher.patch_mouse_library(
        lib,
        load_g305(),
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
    source = load_g305()
    target = load_target("generic_16btn", REPO_ROOT)

    LibraryPatcher.patch_mouse_library(lib, source, target)
    first = {
        name: path.read_bytes()
        for name, path in (
            (f.name, f)
            for f in lib.iterdir()
            if f.suffix in (".h", ".cpp")
        )
    }

    LibraryPatcher.patch_mouse_library(lib, source, target)
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


# ── Core HID command channel patch ─────────────────────────────

STOCK_HID_H = """\
#include <stdint.h>
#include <Arduino.h>
#include "PluggableUSB.h"

#if defined(USBCON)

#define _USING_HID

#define HID_GET_REPORT        0x01
#define HID_GET_IDLE          0x02
#define HID_GET_PROTOCOL      0x03
#define HID_SET_REPORT        0x09
#define HID_SET_IDLE          0x0A
#define HID_SET_PROTOCOL      0x0B

#define HID_HID_DESCRIPTOR_TYPE         0x21
#define HID_REPORT_DESCRIPTOR_TYPE      0x22
#define HID_PHYSICAL_DESCRIPTOR_TYPE    0x23

#define HID_SUBCLASS_NONE 0
#define HID_SUBCLASS_BOOT_INTERFACE 1

#define HID_PROTOCOL_NONE 0
#define HID_PROTOCOL_KEYBOARD 1
#define HID_PROTOCOL_MOUSE 2

#define HID_BOOT_PROTOCOL	0
#define HID_REPORT_PROTOCOL	1

#define HID_REPORT_TYPE_INPUT   1
#define HID_REPORT_TYPE_OUTPUT  2
#define HID_REPORT_TYPE_FEATURE 3

typedef struct 
{
  InterfaceDescriptor hid;
  HIDDescDescriptor   desc;
  EndpointDescriptor  in;
} HIDDescriptor;

class HID_ : public PluggableUSBModule
{
public:
  HID_(void);
  int begin(void);
  int SendReport(uint8_t id, const void* data, int len);
  void AppendDescriptor(HIDSubDescriptor* node);

protected:
  // Implementation of the PluggableUSBModule
  int getInterface(uint8_t* interfaceCount);
  int getDescriptor(USBSetup& setup);
  bool setup(USBSetup& setup);
  uint8_t getShortName(char* name);

private:
  uint8_t epType[1];

  HIDSubDescriptor* rootNode;
  uint16_t descriptorSize;

  uint8_t protocol;
  uint8_t idle;
};

HID_& HID();

#endif // USBCON
"""

STOCK_HID_CPP = """\
#include "HID.h"

#if defined(USBCON)

HID_& HID()
{
	static HID_ obj;
	return obj;
}

int HID_::getInterface(uint8_t* interfaceCount)
{
	*interfaceCount += 1; // uses 1
	HIDDescriptor hidInterface = {
		D_INTERFACE(pluggedInterface, 1, USB_DEVICE_CLASS_HUMAN_INTERFACE, HID_SUBCLASS_BOOT_INTERFACE, HID_PROTOCOL_MOUSE),
		D_HIDREPORT(descriptorSize),
		D_ENDPOINT(USB_ENDPOINT_IN(pluggedEndpoint), USB_ENDPOINT_TYPE_INTERRUPT, USB_EP_SIZE, 0x01)
	};
	return USB_SendControl(0, &hidInterface, sizeof(hidInterface));
}

bool HID_::setup(USBSetup& setup)
{
	if (pluggedInterface != setup.wIndex) {
		return false;
	}

	uint8_t request = setup.bRequest;
	uint8_t requestType = setup.bmRequestType;

	if (requestType == REQUEST_DEVICETOHOST_CLASS_INTERFACE)
	{
		if (request == HID_GET_REPORT) {
			// TODO: HID_GetReport();
			return true;
		}
		if (request == HID_GET_PROTOCOL) {
			// TODO: Send8(protocol);
			return true;
		}
		if (request == HID_GET_IDLE) {
			// TODO: Send8(idle);
		}
	}

	if (requestType == REQUEST_HOSTTODEVICE_CLASS_INTERFACE)
	{
		if (request == HID_SET_PROTOCOL) {
			// The USB Host tells us if we are in boot or report mode.
			// This only works with a real boot compatible device.
			protocol = setup.wValueL;
			return true;
		}
		if (request == HID_SET_IDLE) {
			idle = setup.wValueL;
			return true;
		}
		if (request == HID_SET_REPORT)
		{
			//uint8_t reportID = setup.wValueL;
			//uint16_t length = setup.wLength;
			//uint8_t data[length];
			// Make sure to not read more data than USB_EP_SIZE.
			// You can read multiple times through a loop.
			// The first byte (may!) contain the reportID on a multreport.
			//USB_RecvControl(data, length);
		}
	}

	return false;
}

HID_::HID_(void) : PluggableUSBModule(1, 1, epType),
                   rootNode(NULL), descriptorSize(0),
                   protocol(HID_REPORT_PROTOCOL), idle(1)
{
	epType[0] = EP_TYPE_INTERRUPT_IN;
	PluggableUSB().plug(this);
}

int HID_::begin(void)
{
	return 0;
}

#endif /* if defined(USBCON) */
"""


def _make_core(tmp_path) -> Path:
    hid = tmp_path / "libraries" / "HID" / "src"
    hid.mkdir(parents=True)
    (hid / "HID.h").write_text(STOCK_HID_H, encoding="utf-8")
    (hid / "HID.cpp").write_text(STOCK_HID_CPP, encoding="utf-8")
    return hid


def test_patch_hid_command_core(tmp_path):
    hid = _make_core(tmp_path)
    CommandChannelPatcher.patch_hid_command_core(tmp_path)

    h = (hid / "HID.h").read_text(encoding="utf-8")
    assert HID_H_MARKER in h
    assert "int availableReportPackets();" in h
    assert "int readReportPacket(uint8_t* dst, uint8_t maxlen);" in h
    assert "_cmdRing[HID_COMMAND_QUEUE_SLOTS][HID_COMMAND_PAYLOAD_LEN]" in h
    # interrupt OUT: widened epType array, OUT endpoint member in the
    # descriptor struct, OUT read API declarations.
    assert "uint8_t epType[2];" in h
    assert "EndpointDescriptor  out;" in h
    assert "int availableOutReport();" in h
    assert "int readOutReport(uint8_t* dst, uint8_t maxlen);" in h

    cpp = (hid / "HID.cpp").read_text(encoding="utf-8")
    assert HID_CPP_MARKER in cpp
    # SET_REPORT: bounded copy, Output AND Feature accepted, tolerant of
    # the report id arriving either in wValueL or as the first data byte.
    assert "HID_REPORT_TYPE_OUTPUT ||" in cpp
    assert "HID_REPORT_TYPE_FEATURE" in cpp
    assert "USB_RecvControl(scratch, length)" in cpp
    # Control transfer failure must not enqueue garbage.
    assert "if (!USB_RecvControl(scratch, length))" in cpp
    assert "length <= HID_COMMAND_TOTAL_LEN" in cpp
    assert "scratch[0] != HID_COMMAND_REPORT_ID" in cpp
    # Windows sends the report id byte also in the data stage (wLength 16).
    assert "length == HID_COMMAND_TOTAL_LEN" in cpp
    assert "payloadLen <= HID_COMMAND_PAYLOAD_LEN" in cpp
    # Short data stages must not leak stale tail bytes from the slot.
    assert "memset(_cmdRing[_cmdHead], 0, HID_COMMAND_PAYLOAD_LEN)" in cpp
    assert "memcpy(_cmdRing[_cmdHead], payload, payloadLen)" in cpp
    assert "_cmdCount++" in cpp
    # GET_REPORT capability readback + stall otherwise. The capability
    # payload itself is a generated header (R3), not inline macros.
    assert "HID_CAPABILITY_REPORT_ID" in cpp
    assert "HID_CAPABILITY_BLOB" in cpp
    assert "sizeof(HID_CAPABILITY_BLOB)" in cpp
    assert '#include "hid_capability_blob.h"' in cpp
    assert "_capabilityReport" not in cpp
    # Constructor init + read API implementations.
    assert "_cmdHead = 0;" in cpp
    assert "memcpy(dst, _cmdRing[_cmdTail], len);" in cpp
    # Ring read is atomic against the SET_REPORT ISR.
    assert "cli();" in cpp
    assert "sei();" in cpp
    assert "_cmdCount--;" in cpp
    # interrupt OUT: second endpoint, interface numEndpoints, OUT read
    # implementation (report id as the first data byte).
    assert "EP_TYPE_INTERRUPT_OUT" in cpp
    assert "USB_ENDPOINT_OUT(pluggedEndpoint + 1)" in cpp
    assert "int HID_::availableOutReport(void)" in cpp
    assert "USB_Available(pluggedEndpoint + 1)" in cpp
    assert "USB_Recv(pluggedEndpoint + 1, scratch, HID_COMMAND_TOTAL_LEN)" in cpp
    assert "uint8_t scratch[HID_COMMAND_TOTAL_LEN] = {0};" in cpp
    assert "scratch[0] != HID_COMMAND_REPORT_ID" in cpp
    assert "memcpy(dst, scratch + 1, payloadLen);" in cpp


def test_patch_hid_command_core_idempotent(tmp_path):
    hid = _make_core(tmp_path)
    CommandChannelPatcher.patch_hid_command_core(tmp_path)
    first = {
        name: path.read_bytes() for name, path in (
            (f.name, f) for f in hid.iterdir() if f.suffix in (".h", ".cpp")
        )
    }
    CommandChannelPatcher.patch_hid_command_core(tmp_path)
    second = {
        name: path.read_bytes() for name, path in (
            (f.name, f) for f in hid.iterdir() if f.suffix in (".h", ".cpp")
        )
    }
    assert set(first) == set(second)
    for name in first:
        assert first[name] == second[name], f"{name} changed on re-patch"


def test_write_hid_command_config(tmp_path):
    hid = _make_core(tmp_path)
    schema = load_command_schema(REPO_ROOT)
    target = load_target("generic_5btn", REPO_ROOT)

    CommandChannelPatcher.write_hid_command_config(tmp_path, target, schema)

    config = (hid / "hid_command_config.h").read_text(encoding="utf-8")
    assert "#define HID_COMMAND_ENABLED 1" in config
    assert "#define HID_COMMAND_TRANSPORT 3" in config
    assert "#define HID_COMMAND_REPORT_ID 3" in config
    assert "#define HID_COMMAND_PAYLOAD_LEN 15" in config
    assert "#define HID_COMMAND_TOTAL_LEN 16" in config
    assert "#define HID_COMMAND_QUEUE_SLOTS 4" in config
    assert "#define HID_CAPABILITY_ENABLED 0" in config

    # Re-patching with a different target updates the config in place.
    target_16 = load_target("generic_16btn", REPO_ROOT)
    CommandChannelPatcher.write_hid_command_config(tmp_path, target_16, schema)
    config = (hid / "hid_command_config.h").read_text(encoding="utf-8")
    assert "#define HID_COMMAND_ENABLED 1" in config


def test_write_hid_capability_blob(tmp_path):
    """R3: the ready-made capability bytes live in a generated header;
    HID.cpp only serves HID_CAPABILITY_BLOB (single source of truth)."""
    hid = _make_core(tmp_path)
    schema = load_command_schema(REPO_ROOT)
    target = load_target("generic_5btn", REPO_ROOT)

    CommandChannelPatcher.write_hid_capability_blob(tmp_path, target, schema)

    blob = (hid / "hid_capability_blob.h").read_text(encoding="utf-8")
    assert "static const uint8_t HID_CAPABILITY_BLOB[] PROGMEM" in blob
    assert "0x48" in blob  # 'H'
    assert "0x43" in blob  # 'C'
    # transport code 3 (interrupt_out) is the first payload byte.
    assert "0x03" in blob
    # matches generate_capability_blob() bytes (feature: 0x01)
    target_feature = load_target("generic_16btn", REPO_ROOT)
    CommandChannelPatcher.write_hid_capability_blob(tmp_path, target_feature, schema)
    blob_feature = (hid / "hid_capability_blob.h").read_text(encoding="utf-8")
    assert "0x01" in blob_feature


def test_patch_hid_command_core_upgrades_stale_core(tmp_path):
    """R1: re-patching a core patched by an older patcher must upgrade
    the content (atomic ring read, SET_REPORT failure check +
    zero-padding, generated capability blob header), not skip it.

    Fixture: real patched HID.h/HID.cpp from a previous (v1) patch run.
    """
    import shutil

    stale = Path(__file__).resolve().parent / "fixtures" / "stale_core"
    hid = tmp_path / "libraries" / "HID" / "src"
    shutil.copytree(stale, hid)

    # The stale file predates the version marker and misses the fixes.
    stale_cpp = (hid / "HID.cpp").read_text(encoding="utf-8")
    assert "HUB_PATCH_VERSION" not in stale_cpp
    assert "cli();" not in stale_cpp
    assert "memset(_cmdRing[_cmdHead], 0" not in stale_cpp
    assert "if (!USB_RecvControl(scratch, length))" not in stale_cpp
    assert "_capabilityReport" in stale_cpp

    CommandChannelPatcher.patch_hid_command_core(tmp_path)

    cpp = (hid / "HID.cpp").read_text(encoding="utf-8")
    assert "// HUB_PATCH_VERSION 2" in cpp
    # Atomic ring read upgraded.
    assert "cli();" in cpp
    assert "sei();" in cpp
    # SET_REPORT failure check + zero-padding upgraded.
    assert "if (!USB_RecvControl(scratch, length))" in cpp
    assert "memset(_cmdRing[_cmdHead], 0, HID_COMMAND_PAYLOAD_LEN)" in cpp
    # Capability blob moved to the generated header.
    assert '_capabilityReport' not in cpp
    assert "HID_CAPABILITY_BLOB" in cpp
    assert '#include "hid_capability_blob.h"' in cpp
    assert (hid / "hid_capability_blob.h").exists() is False  # written by caller

    h = (hid / "HID.h").read_text(encoding="utf-8")
    assert "// HUB_PATCH_VERSION 2" in h
    assert HID_H_MARKER in h

    # Re-patch at the same version is a no-op (idempotent fast path).
    first = (hid / "HID.cpp").read_bytes()
    CommandChannelPatcher.patch_hid_command_core(tmp_path)
    assert (hid / "HID.cpp").read_bytes() == first


def test_install_hub_command_library_and_pc_headers(tmp_path):
    base = tmp_path
    (base / "libraries").mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        REPO_ROOT / "libraries" / "HubCommand",
        base / "libraries" / "HubCommand",
    )
    (base / "pc_client").mkdir(parents=True, exist_ok=True)
    schema = load_command_schema(REPO_ROOT)
    target = load_target("generic_5btn", REPO_ROOT)

    lib = LibraryPatcher.install_hub_command_library(base, schema)
    assert (lib / "src" / "CommandTransport.h").exists()
    assert (lib / "src" / "MouseCommandHandler.cpp").exists()
    commands = (lib / "src" / "mouse_commands.h").read_text(encoding="utf-8")
    assert "MOUSE_CMD_MOVE = 0x01" in commands

    LibraryPatcher.write_pc_client_headers(base, target, schema)
    gen = base / "pc_client" / "target"
    assert (gen / "mouse_commands.h").exists()
    assert (gen / "command_channel.h").exists()
    channel = (gen / "command_channel.h").read_text(encoding="utf-8")
    assert "#define HID_COMMAND_TRANSPORT 3" in channel
    assert "#define HID_COMMAND_TRANSPORT_INTERRUPT_OUT 3" in channel
    assert "#define HID_COMMAND_USAGE_PAGE 0xFF00" in channel
