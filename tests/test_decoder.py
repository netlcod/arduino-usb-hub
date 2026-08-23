"""Decoder tests for the generated hid_mapper.h.

The generated C++ header's field offsets (SRC_*_OFF) are parsed back and
used to decode raw G305 reports, mirroring the generated decode_input().
"""

import re
from pathlib import Path

from arduino_hub.targets import load_target
from arduino_hub.usbhid.hid_generator import generate_hid_mapper_h
from util import load_g305

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "g305_reports.bin"

_CONST_RE = re.compile(r"^#define\s+(SRC|TGT)_(\w+)\s+(\d+)$", re.MULTILINE)


def _constants(header: str) -> dict[str, int]:
    return {
        f"{m.group(1)}_{m.group(2)}": int(m.group(3))
        for m in _CONST_RE.finditer(header)
    }


def _read_bits(data: bytes, bit_off: int, bit_len: int) -> int:
    value = 0
    for i in range(bit_len):
        if data[(bit_off + i) >> 3] & (1 << ((bit_off + i) & 7)):
            value |= 1 << i
    return value


def _sign_extend(value: int, bit_len: int) -> int:
    if bit_len < 16:
        if value & (1 << (bit_len - 1)):
            value |= ~((1 << bit_len) - 1)
    if value >= 0x8000:
        value -= 0x10000
    return value


def _decode_report(header: str, report: bytes) -> dict[str, int]:
    c = _constants(header)
    src = bytearray(report)
    if len(src) == c["SRC_REPORT_LEN"] and src[0] == c["SRC_REPORT_ID"]:
        src = src[1:]
    assert len(src) >= c["SRC_PAYLOAD_LEN"]

    out = {"buttons": _read_bits(src, 0, c["SRC_BUTTONS_DATA_BITS"])}
    for name in ("x", "y", "wheel", "pan"):
        off = c[f"SRC_{name.upper()}_OFF"]
        bits = c[f"SRC_{name.upper()}_BITS"]
        out[name] = _sign_extend(_read_bits(src, off, bits), bits)
    return out


def test_generated_constants_match_g305_layout():
    source = load_g305()
    target = load_target("generic_3btn", REPO_ROOT)
    header = generate_hid_mapper_h(source, target)
    c = _constants(header)

    # NOTE: the descriptor — and Windows' own HID caps in the USB Tree
    # Viewer dump — list Y (DataIndex 16) before X (17), and the real
    # receiver works correctly plugged straight into a PC. Behind the USB
    # Host Shield, however, it emits X first (verified empirically:
    # moving right produced cursor-down until calibrated). The profile
    # keeps data_index as reported and swaps wire_order in
    # profiles/sources/g305.json.
    assert c["SRC_REPORT_ID"] == 2
    assert c["SRC_REPORT_LEN"] == 9
    assert c["SRC_PAYLOAD_LEN"] == 8
    assert c["SRC_BUTTONS_DATA_BITS"] == 16
    assert c["SRC_X_OFF"] == 16 and c["SRC_X_BITS"] == 16
    assert c["SRC_Y_OFF"] == 32 and c["SRC_Y_BITS"] == 16
    assert c["SRC_WHEEL_OFF"] == 48 and c["SRC_WHEEL_BITS"] == 8
    assert c["SRC_PAN_OFF"] == 56 and c["SRC_PAN_BITS"] == 8

    assert c["TGT_REPORT_ID"] == 1
    assert c["TGT_REPORT_LEN"] == 4
    assert c["TGT_BUTTONS_DATA_BITS"] == 3
    assert c["TGT_X_OFF"] == 8 and c["TGT_X_BITS"] == 8
    assert c["TGT_Y_OFF"] == 16 and c["TGT_Y_BITS"] == 8
    assert c["TGT_WHEEL_OFF"] == 24 and c["TGT_WHEEL_BITS"] == 8


def test_decode_spec_report():
    # decode(02 05 00 10 00 20 00 01 00) == {buttons:5, x:16, y:32, wheel:1}
    source = load_g305()
    target = load_target("generic_3btn", REPO_ROOT)
    header = generate_hid_mapper_h(source, target)

    state = _decode_report(header, bytes.fromhex("02 05 00 10 00 20 00 01 00"))
    assert state["buttons"] == 5
    assert state["x"] == 16
    assert state["y"] == 32
    assert state["wheel"] == 1
    assert state["pan"] == 0


def test_decode_fixture():
    source = load_g305()
    target = load_target("generic_3btn", REPO_ROOT)
    header = generate_hid_mapper_h(source, target)

    # Field order in fixture reports: [ID] [buttons LE 16] [X LE 16] [Y LE 16]
    # [wheel] [pan] (X before Y, verified on hardware).
    expected = [
        (5, 16, 32, 1, 0),      # 02 05 00 10 00 20 00 01 00
        (0, 0, 0, 0, 0),        # no buttons, no movement
        (1, -2, 0, -1, 0),      # left button, x=-2, wheel=-1
        (7, 480, 576, 0, 0),    # left+right+middle, 16-bit deltas
        (0, -32767, 32767, 127, -127),  # full-range extremes
        (2, 0, 0, 0, 0),        # right button only: buttons byte is 0x02,
                                # must NOT be mistaken for a report ID byte
    ]

    data = FIXTURE.read_bytes()
    assert len(data) % 9 == 0
    reports = [data[i:i + 9] for i in range(0, len(data), 9)]
    assert len(reports) == len(expected)

    for report, (buttons, x, y, wheel, pan) in zip(reports, expected):
        state = _decode_report(header, report)
        assert state == {
            "buttons": buttons, "x": x, "y": y, "wheel": wheel, "pan": pan,
        }, f"failed for report {report.hex()}"
