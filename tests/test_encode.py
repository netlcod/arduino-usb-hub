"""Encode tests: mirror encode_output() from the generated hid_mapper.h.

The generator's field offsets (TGT_*_OFF) are parsed back from the header
and used to encode a MouseState into a target report, mirroring the
generated encode_output() (write_bits + clamp_i16). Phase 1 left encode
untested; these tests pin the report bytes for every target profile.
"""

import re
from pathlib import Path

from arduino_hub.devices import load as load_device
from arduino_hub.targets import load_target
from arduino_hub.usbhid.hid_generator import USAGE_FIELD, generate_hid_mapper_h

REPO_ROOT = Path(__file__).resolve().parents[1]

_CONST_RE = re.compile(r"^#define\s+(TGT)_(\w+)\s+(\d+)$", re.MULTILINE)


def _constants(header: str) -> dict[str, int]:
    return {
        f"{m.group(1)}_{m.group(2)}": int(m.group(3))
        for m in _CONST_RE.finditer(header)
    }


def _write_bits(report: bytearray, bit_off: int, bit_len: int, value: int) -> None:
    for i in range(bit_len):
        byte = (bit_off + i) >> 3
        mask = 1 << ((bit_off + i) & 7)
        if value & (1 << i):
            report[byte] |= mask
        else:
            report[byte] &= ~mask


def _encode(header, target, buttons, x, y, wheel, pan) -> bytes:
    c = _constants(header)
    report = bytearray(c["TGT_REPORT_LEN"])
    _write_bits(
        report,
        0,
        c["TGT_BUTTONS_DATA_BITS"],
        buttons & ((1 << c["TGT_BUTTONS_DATA_BITS"]) - 1),
    )
    for name, value in (("x", x), ("y", y), ("wheel", wheel), ("pan", pan)):
        prefix = f"TGT_{name.upper()}"
        if f"{prefix}_OFF" not in c:
            continue
        off = c[f"{prefix}_OFF"]
        bits = c[f"{prefix}_BITS"]
        axis = next(a for a in target.axes if USAGE_FIELD.get(a.usage) == name)
        clamped = max(axis.logical_min, min(axis.logical_max, value))
        _write_bits(report, off, bits, clamped & ((1 << bits) - 1))
    return bytes(report)


def _mapper(source_name: str, target_name: str):
    return generate_hid_mapper_h(
        load_device(source_name, REPO_ROOT),
        load_target(target_name, REPO_ROOT),
    )


def test_generic_16btn_encode_layout():
    header = _mapper("g305", "generic_16btn")
    c = _constants(header)
    assert c["TGT_REPORT_ID"] == 2
    assert c["TGT_REPORT_LEN"] == 8
    assert c["TGT_BUTTONS_DATA_BITS"] == 16
    # Pass-through layout: [btn LE16][Y LE16][X LE16][wheel][pan]
    assert c["TGT_Y_OFF"] == 16 and c["TGT_Y_BITS"] == 16
    assert c["TGT_X_OFF"] == 32 and c["TGT_X_BITS"] == 16
    assert c["TGT_WHEEL_OFF"] == 48 and c["TGT_WHEEL_BITS"] == 8
    assert c["TGT_PAN_OFF"] == 56 and c["TGT_PAN_BITS"] == 8


def test_generic_16btn_encode_report():
    target = load_target("generic_16btn", REPO_ROOT)
    header = _mapper("g305", "generic_16btn")
    report = _encode(header, target, 0xFFFF, 1234, -5678, 127, -127)
    assert report == bytes.fromhex("ff ff d2 e9 d2 04 7f 81")


def test_generic_16btn_encode_clamps():
    target = load_target("generic_16btn", REPO_ROOT)
    header = _mapper("g305", "generic_16btn")
    report = _encode(header, target, 0, 40000, -40000, 200, -200)
    # 16-bit axes clamp to -32767..32767; wheel/pan clamp to -127..127.
    assert report == bytes.fromhex("00 00 01 80 ff 7f 7f 81")


def test_generic_5btn_encode_mask():
    target = load_target("generic_5btn", REPO_ROOT)
    header = _mapper("g305", "generic_5btn")
    c = _constants(header)
    assert c["TGT_BUTTONS_DATA_BITS"] == 5

    # All 5 buttons (bits 0-4, incl. XButton1=0x08, XButton2=0x10).
    assert _encode(header, target, 0x1F, 10, 0, 0, 0) == bytes.fromhex("1f 0a 00 00")
    # Bit 5 (0x20) is beyond the 5-bit field and must be masked out.
    assert _encode(header, target, 0x20, 0, 0, 0, 0) == bytes.fromhex("00 00 00 00")


def test_generic_3btn_encode_mask():
    target = load_target("generic_3btn", REPO_ROOT)
    header = _mapper("g305", "generic_3btn")
    c = _constants(header)
    assert c["TGT_BUTTONS_DATA_BITS"] == 3

    # Left|right|middle fit in 3 bits; bit 3 (0x08) must be masked out.
    assert _encode(header, target, 0x07, -1, 1, -1, 0) == bytes.fromhex("07 ff 01 ff")
    assert _encode(header, target, 0x08, 0, 0, 0, 0) == bytes.fromhex("00 00 00 00")
