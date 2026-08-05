"""Report descriptor generator tests.

Test 1: generic_3btn target must reproduce the stock Arduino Mouse
descriptor byte-for-byte (reference: user/libraries/Mouse/src/Mouse.cpp,
lines 26-57).

Test 2: g305 target must reproduce the 91-byte Logitech descriptor that
reconstruct_report_descriptor() produces for the G305 (and that is stored
in devices/g305.json).
"""

from pathlib import Path

from arduino_hub.devices import load as load_device
from arduino_hub.targets import load_target
from arduino_hub.usbhid.hid_generator import generate_report_descriptor

REPO_ROOT = Path(__file__).resolve().parents[1]

# Reference: Arduino Mouse descriptor (Mouse.cpp lines 26-57)
ARDUINO_MOUSE_DESCRIPTOR = bytes([
    0x05, 0x01, 0x09, 0x02, 0xa1, 0x01, 0x09, 0x01, 0xa1, 0x00, 0x85, 0x01,
    0x05, 0x09, 0x19, 0x01, 0x29, 0x03, 0x15, 0x00, 0x25, 0x01, 0x95, 0x03,
    0x75, 0x01, 0x81, 0x02, 0x95, 0x01, 0x75, 0x05, 0x81, 0x03,
    0x05, 0x01, 0x09, 0x30, 0x09, 0x31, 0x09, 0x38, 0x15, 0x81, 0x25, 0x7f,
    0x75, 0x08, 0x95, 0x03, 0x81, 0x06,
    0xc0, 0xc0,
])


def test_generic_3btn_matches_arduino_mouse_descriptor():
    target = load_target("generic_3btn", REPO_ROOT)
    descriptor = generate_report_descriptor(target)
    assert descriptor == ARDUINO_MOUSE_DESCRIPTOR


def test_g305_matches_91_byte_reference():
    target = load_target("g305", REPO_ROOT)
    descriptor = generate_report_descriptor(target)

    assert len(descriptor) == 91
    assert descriptor[:12] == bytes.fromhex("05010902a1010901a1008502")

    device = load_device("g305", REPO_ROOT)
    assert descriptor == device.hid_report_descriptors[0].data


def test_generic_3btn_report_length():
    target = load_target("generic_3btn", REPO_ROOT)
    descriptor = generate_report_descriptor(target)
    assert target.report_id == 1
    assert target.report_length == 4
    assert descriptor[10] == 0x85
    assert descriptor[11] == 0x01
