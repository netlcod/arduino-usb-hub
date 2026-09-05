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
    IdentityPatcher,
    LibraryPatcher,
    apply_edits,
    build_patch_edits,
    render_edits_diff,
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

// Wire layout: len, dtype, bcdHID LOW, bcdHID HIGH, country, numDesc,
// descType, descLen LOW/HIGH.
#define D_HIDREPORT(length) { 9, 0x21, 0x01, 0x01, 0, 1, 0x22, lowByte(length), highByte(length) }

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
    # v3 identity parity: bInterval via macro, spec-correct class
    # request answers (no more empty packet / stall).
    assert "#ifndef HID_EP_INTERVAL" in cpp
    assert "#define HID_EP_INTERVAL 0x01" in cpp
    assert ", USB_EP_SIZE, HID_EP_INTERVAL)" in cpp
    assert ", USB_EP_SIZE, 0x01)" not in cpp
    assert "return USB_SendControl(0, &protocol, 1) > 0;" in cpp
    assert "return USB_SendControl(0, &idle, 1) > 0;" in cpp
    assert "// TODO: Send8(protocol);" not in cpp
    assert "// TODO: Send8(idle);" not in cpp


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
    assert "// HUB_PATCH_VERSION 4" in cpp
    # v4: SendReport ships id+payload as one USB packet (stale fixture
    # carries the stock two-packet form).
    assert "uint8_t buf[len + 1];" in cpp
    assert "USB_Send(pluggedEndpoint | TRANSFER_RELEASE, buf, len + 1)" in cpp
    assert "auto ret = USB_Send(pluggedEndpoint, &id, 1);" not in cpp
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
    # v3: interval macro + spec-correct class request answers applied
    # to the stale (v2-era) content too — including the already-present
    # OUT endpoint descriptor line.
    assert "#ifndef HID_EP_INTERVAL" in cpp
    assert ", USB_EP_SIZE, HID_EP_INTERVAL)" in cpp
    assert ", USB_EP_SIZE, 0x01)" not in cpp
    assert "return USB_SendControl(0, &protocol, 1) > 0;" in cpp
    assert "return USB_SendControl(0, &idle, 1) > 0;" in cpp

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

# ── Identity parity transforms (enumeration-identity-parity plan) ──

STOCK_USBCORE_CPP = """\
#define EP_SINGLE_64 0x32\t// EP0
#define EP_DOUBLE_64 0x36\t// Other endpoints
#define EP_SINGLE_16 0x12

static int _cmark;
static int _cend;
static bool SendControl(u8 d)
{
\tif (_cmark < _cend)
\t{
\t\tif (!WaitForINOrOUT())
\t\t\treturn false;
\t\tSend8(d);
\t\tif (!((_cmark + 1) & 0x3F))
\t\t\tClearIN();\t// Fifo is full, release this packet
\t}
\t_cmark++;
\treturn true;
}

static void core_init(void)
{
\tInitEP(0,EP_TYPE_CONTROL,EP_SINGLE_64);\t// init ep0
}

#ifdef CDC_ENABLED
const DeviceDescriptor USB_DeviceDescriptorIAD =
\tD_DEVICE(0xEF,0x02,0x01,64,USB_VID,USB_PID,0x100,IMANUFACTURER,IPRODUCT,ISERIAL,1);
#else // CDC_DISABLED
const DeviceDescriptor USB_DeviceDescriptorIAD =
\tD_DEVICE(0x00,0x00,0x00,64,USB_VID,USB_PID,0x100,IMANUFACTURER,IPRODUCT,ISERIAL,1);
#endif
"""

# Form produced by the pre-parity patcher (EP0 untouched everywhere,
# bcdDevice set).
OLD_PATCHED_USBCORE_CPP = """\
#define EP_SINGLE_64 0x32\t// EP0
#define EP_DOUBLE_64 0x36\t// Other endpoints
#define EP_SINGLE_16 0x12

static bool SendControl(u8 d)
{
\tif (!((_cmark + 1) & 0x3F))
\t\tClearIN();
}

static void core_init(void)
{
\tInitEP(0,EP_TYPE_CONTROL,EP_SINGLE_64);\t// init ep0
}
\tD_DEVICE(0xEF,0x02,0x01,64,USB_VID,USB_PID,0x4401,IMANUFACTURER,IPRODUCT,0,1);
\tD_DEVICE(0x00,0x00,0x00,64,USB_VID,USB_PID,0x4401,IMANUFACTURER,IPRODUCT,0,1);
"""

STOCK_USBCORE_H = """\
#define USB_CONFIG_BUS_POWERED                 0x80
#define USB_CONFIG_SELF_POWERED                0xC0
#define USB_CONFIG_REMOTE_WAKEUP               0x20

#ifndef USB_VERSION
#define USB_VERSION 0x200
#endif

#define D_DEVICE(_class,_subClass,_proto,_packetSize0,_vid,_pid,_version,_im,_ip,_is,_configs) \\
\t{ 18, 1, USB_VERSION, _class,_subClass,_proto,_packetSize0,_vid,_pid,_version,_im,_ip,_is,_configs }

#define D_CONFIG(_totalLength,_interfaces) \\
\t{ 9, 2, _totalLength,_interfaces, 1, 0, USB_CONFIG_BUS_POWERED | USB_CONFIG_REMOTE_WAKEUP, USB_CONFIG_POWER_MA(USB_CONFIG_POWER) }
"""

STOCK_BOARDS_TXT = """\
leonardo.name=Arduino Leonardo
leonardo.build.vid=0x2341
leonardo.build.pid=0x8036
leonardo.build.usb_product="Arduino Leonardo"
leonardo.build.extra_flags={build.usb_flags} -DCDC_DISABLED
"""

STOCK_D_HIDREPORT = (
    "#define D_HIDREPORT(length) "
    "{ 9, 0x21, 0x01, 0x01, 0, 1, 0x22, lowByte(length), highByte(length) }\n"
)

# What the pre-parity patcher produced: low byte written into the HIGH
# bcdHID position (wire value 0x1101 instead of 0x0111).
OLD_PATCHED_D_HIDREPORT = (
    "#define D_HIDREPORT(length) "
    "{ 9, 0x21, 0x01, 0x11, 0, 1, 0x22, lowByte(length), highByte(length) }\n"
)


def _identity_device(**overrides):
    from arduino_hub.usbhid.device_info import DeviceInfo

    fields = dict(
        name="generic_mouse",
        vendor_id=0x1234,
        product_id=0x5678,
        manufacturer_string="Acme",
        product_string="Universal Mouse",
        bcd_device=0x0201,
        usb_version=0x0200,
        ep0_max_packet_size=32,
        bm_attributes=0xA0,
        max_power_ma=98,
        bcd_hid=0x0111,
        country_code=0x00,
        ep_interval_ms=4,
        report_id=2,
        report_length=9,
        button_count=16,
    )
    fields.update(overrides)
    return DeviceInfo(**fields)


def test_usbcore_descriptor_patches_ep0_bcd_serial():
    device = _identity_device()
    out = IdentityPatcher._usbcore_descriptor_content(STOCK_USBCORE_CPP, device)
    expected = ",32,USB_VID,USB_PID,0x201,IMANUFACTURER,IPRODUCT,0,1)"
    assert out.count(expected) == 2  # both D_DEVICE branches
    assert "ISERIAL" not in out

    # EP0 hardware allocation routed through the overridable macro and
    # backed by the new bank-size constants.
    assert "#ifndef USB_EP0_MAX_PACKET" in out
    assert "#define EP_SINGLE_8 0x02" in out
    assert "#define EP_SINGLE_32 0x22" in out
    assert "InitEP(0,EP_TYPE_CONTROL,USB_EP0_ALLOC)" in out
    assert "InitEP(0,EP_TYPE_CONTROL,EP_SINGLE_64)" not in out

    # SendControl: bank-full release follows the configured size, not
    # the stock hardcoded 64-byte mask (root cause of the invisible
    # device with EP0=32).
    assert "if (!((_cmark + 1) % USB_EP0_MAX_PACKET))" in out
    assert "(_cmark + 1) & 0x3F" not in out

    # Idempotent.
    assert IdentityPatcher._usbcore_descriptor_content(out, device) == out


def test_usbcore_descriptor_migrates_old_patch_form():
    """Old-patched cores declare a custom bcdDevice but still run EP0
    from the 64-byte bank; the transform must fill in both halves."""
    device = _identity_device(bcd_device=0x4401, ep0_max_packet_size=8)
    out = IdentityPatcher._usbcore_descriptor_content(OLD_PATCHED_USBCORE_CPP, device)
    assert out.count(",8,USB_VID,USB_PID,0x4401,IMANUFACTURER,IPRODUCT,0,1)") == 2
    assert "InitEP(0,EP_TYPE_CONTROL,USB_EP0_ALLOC)" in out
    assert "USB_EP0_MAX_PACKET" in out
    assert "(_cmark + 1) % USB_EP0_MAX_PACKET" in out


def test_usbcore_ep0_alloc_idempotent_after_descriptor():
    """A core whose D_DEVICE is already at target values (e.g. patched
    by the buggy pre-fix build that declared EP0=32 without touching the
    hardware bank) must still get the USB_EP0_ALLOC fix."""
    device = _identity_device(bcd_device=0x4401, ep0_max_packet_size=32)
    broken = STOCK_USBCORE_CPP.replace("0x100", "0x4401").replace(
        ",64,USB_VID", ",32,USB_VID"
    ).replace("ISERIAL,1)", "0,1)")
    out = IdentityPatcher._usbcore_descriptor_content(broken, device)
    assert "InitEP(0,EP_TYPE_CONTROL,USB_EP0_ALLOC)" in out
    assert "(_cmark + 1) % USB_EP0_MAX_PACKET" in out
    assert IdentityPatcher._usbcore_descriptor_content(out, device) == out


def test_usbcore_descriptor_unexpected_form_raises():
    import pytest
    from arduino_hub.exceptions import PatchError

    with pytest.raises(PatchError):
        IdentityPatcher._usbcore_descriptor_content("D_DEVICE(broken);", _identity_device())


STOCK_SENDREPORT = """\
int HID_::SendReport(uint8_t id, const void* data, int len)
{
\tauto ret = USB_Send(pluggedEndpoint, &id, 1);
\tif (ret < 0) return ret;
\tauto ret2 = USB_Send(pluggedEndpoint | TRANSFER_RELEASE, data, len);
\tif (ret2 < 0) return ret2;
\treturn ret + ret2;
}
"""

STOCK_GET_CONFIGURATION = """\
\t\telse if (GET_CONFIGURATION == r)
\t\t{
\t\t\tSend8(1);
\t\t}
"""


def test_sendreport_collapsed_to_single_packet():
    """Audit tail: stock ships id and payload as two USB packets; the
    patch must collapse them into one (id followed by payload)."""
    out = CommandChannelPatcher._patch_hid_cpp(STOCK_SENDREPORT)
    assert "uint8_t buf[len + 1];" in out
    assert "USB_Send(pluggedEndpoint | TRANSFER_RELEASE, buf, len + 1)" in out
    assert "auto ret = USB_Send(pluggedEndpoint, &id, 1);" not in out
    # Idempotent.
    assert CommandChannelPatcher._patch_hid_cpp(out) == out


def test_usbcore_get_configuration_returns_live_value():
    """Audit tail: stock GET_CONFIGURATION always answers 1, even before
    SET_CONFIGURATION (spec: 0 while unconfigured)."""
    out = IdentityPatcher._usbcore_descriptor_content(
        STOCK_USBCORE_CPP + STOCK_GET_CONFIGURATION, _identity_device()
    )
    assert "Send8(_usbConfiguration);" in out
    assert "Send8(1);" not in out
    # Idempotent.
    assert IdentityPatcher._usbcore_descriptor_content(out, _identity_device()) == out


def test_hid_h_identity_writes_both_bcd_bytes_and_country():
    out = IdentityPatcher._hid_h_identity_content(
        STOCK_D_HIDREPORT, _identity_device(bcd_hid=0x0111, country_code=0x00)
    )
    # Wire layout: { len, dtype, LOW, HIGH, country, numDesc, ... }
    assert "{ 9, 0x21, 0x11, 0x01, 0x00, 1, 0x22," in out
    assert IdentityPatcher._hid_h_identity_content(out, _identity_device()) == out


def test_hid_h_identity_migrates_old_high_byte_patch():
    """The pre-parity patcher put the low byte into the HIGH position
    (wire 0x1101). The transform must migrate that form."""
    out = IdentityPatcher._hid_h_identity_content(
        OLD_PATCHED_D_HIDREPORT, _identity_device(bcd_hid=0x021A, country_code=0x05)
    )
    assert "{ 9, 0x21, 0x1A, 0x02, 0x05, 1, 0x22," in out


def test_usbc_h_attributes_guard():
    out = IdentityPatcher._usbc_h_attributes_content(STOCK_USBCORE_H)
    assert "#ifndef USB_CONFIG_ATTRIBUTES" in out
    assert "// HUB_PATCH_VERSION 1" in out
    assert (
        "#define USB_CONFIG_ATTRIBUTES (USB_CONFIG_BUS_POWERED | USB_CONFIG_REMOTE_WAKEUP)" in out
    )
    assert "USB_CONFIG_ATTRIBUTES, USB_CONFIG_POWER_MA(USB_CONFIG_POWER)" in out
    # Only inside the guard default, not in D_CONFIG anymore.
    body = out.split("#endif")[-1]
    assert "USB_CONFIG_BUS_POWERED | USB_CONFIG_REMOTE_WAKEUP" not in body

    # Idempotent.
    assert IdentityPatcher._usbc_h_attributes_content(out) == out


def test_boards_txt_carries_all_identity_flags():
    content = IdentityPatcher._boards_txt_content(STOCK_BOARDS_TXT, _identity_device())
    line = next(l for l in content.splitlines() if l.startswith("leonardo.build.extra_flags"))
    for flag in (
        "-DCDC_DISABLED",
        "-DUSB_EP_SIZE=16",
        "-DUSB_CONFIG_POWER=98",
        "-DUSB_VERSION=0x0200",
        "-DUSB_CONFIG_ATTRIBUTES=0xA0",
        "-DHID_EP_INTERVAL=0x04",
        "-DUSB_EP0_MAX_PACKET=32",
    ):
        assert flag in line, flag
    assert 'leonardo.build.vid=0x1234' in content
    assert 'leonardo.build.pid=0x5678' in content

    # Always-explicit: re-running with a changed interval updates flags
    # even though VID/PID are unchanged (no early return).
    updated = IdentityPatcher._boards_txt_content(content, _identity_device(ep_interval_ms=8))
    assert "-DHID_EP_INTERVAL=0x08" in updated


def test_build_patch_edits_apply_and_dry_run(tmp_path):
    """Full edit computation on a synthetic install; apply once -> all
    written; compute again -> nothing changed. render_edits_diff shows
    every change without touching the disk."""
    import shutil

    from arduino_hub.core.patcher import MOUSE_LIB_RELATIVE

    base = tmp_path
    core = base / "arduino-cli-data" / "packages" / "arduino" / "hardware" / "avr" / "1.8.6"
    hid_src = core / "libraries" / "HID" / "src"
    hid_src.mkdir(parents=True)
    (core / "cores" / "arduino").mkdir(parents=True)
    (hid_src / "HID.h").write_text(STOCK_HID_H, encoding="utf-8")
    (hid_src / "HID.cpp").write_text(STOCK_HID_CPP, encoding="utf-8")
    (core / "cores" / "arduino" / "USBCore.cpp").write_text(STOCK_USBCORE_CPP, encoding="utf-8")
    (core / "cores" / "arduino" / "USBCore.h").write_text(STOCK_USBCORE_H, encoding="utf-8")
    (core / "boards.txt").write_text(STOCK_BOARDS_TXT, encoding="utf-8")

    lib_src = base / MOUSE_LIB_RELATIVE
    lib_src.mkdir(parents=True)
    (lib_src / "Mouse.h").write_text(STOCK_MOUSE_H, encoding="utf-8")
    (lib_src / "Mouse.cpp").write_text(STOCK_MOUSE_CPP, encoding="utf-8")

    shutil.copytree(
        REPO_ROOT / "libraries" / "HubCommand", base / "libraries" / "HubCommand"
    )

    from arduino_hub.core.validation import validate_identity

    device = load_g305()
    target = load_target("generic_5btn", REPO_ROOT)
    schema = load_command_schema(REPO_ROOT)

    issues = validate_identity(device)
    assert not [i for i in issues if i.severity == "ERROR"]

    edits = build_patch_edits(base, core, device, target, schema)
    paths = {e.path.name for e in edits}
    assert {
        "boards.txt", "USBCore.cpp", "USBCore.h", "HID.h", "HID.cpp",
        "hid_command_config.h", "hid_capability_blob.h",
        "hid_profile.h", "hid_mapper.h", "Mouse.cpp", "Mouse.h",
        "MouseCommandHandler.cpp", "mouse_commands.h", "command_channel.h",
    } <= paths
    assert all(e.changed for e in edits)

    diff = render_edits_diff(edits)
    assert "boards.txt" in diff
    assert "-DUSB_CONFIG_ATTRIBUTES=0xA0" in diff
    assert "-DUSB_VERSION=0x0200" in diff
    assert "-DUSB_EP0_MAX_PACKET=32" in diff
    assert "+\tD_DEVICE(0x00,0x00,0x00,32,USB_VID,USB_PID,0x4401,IMANUFACTURER,IPRODUCT,0,1);" in diff
    assert "InitEP(0,EP_TYPE_CONTROL,USB_EP0_ALLOC);" in diff
    assert "+\t\tif (!((_cmark + 1) % USB_EP0_MAX_PACKET))" in diff
    assert "-\t\tif (!((_cmark + 1) & 0x3F))" in diff

    assert apply_edits(edits) == len(edits)

    # Second computation over the patched tree: everything up to date.
    edits2 = build_patch_edits(base, core, device, target, schema)
    unchanged = [e for e in edits2 if not e.changed]
    assert len(unchanged) == len(edits2), [e.path for e in edits2 if e.changed]
    assert render_edits_diff(edits2) == ""

    # No stray temp files left behind by atomic writes.
    strays = [p for p in base.rglob("*.tmp")]
    assert strays == []

    # Sanity on key patched artifacts.
    usbcpp = (core / "cores" / "arduino" / "USBCore.cpp").read_text(encoding="utf-8")
    assert ",32,USB_VID,USB_PID,0x4401,IMANUFACTURER,IPRODUCT,0,1)" in usbcpp
    assert "InitEP(0,EP_TYPE_CONTROL,USB_EP0_ALLOC)" in usbcpp
    assert "#define USB_EP0_ALLOC EP_SINGLE_32" in usbcpp
    assert "(_cmark + 1) % USB_EP0_MAX_PACKET" in usbcpp
    assert "& 0x3F" not in usbcpp
    hid_h = (hid_src / "HID.h").read_text(encoding="utf-8")
    assert "{ 9, 0x21, 0x11, 0x01, 0x00, 1, 0x22," in hid_h
