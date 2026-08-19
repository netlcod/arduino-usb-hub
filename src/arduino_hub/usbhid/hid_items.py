"""Low-level HID report descriptor item emitters.

Shared by the report descriptor generators: `hid_generator.py`
(build-time target descriptors) and `descriptor_reader.py`
(reconstruction of source descriptors from USB Tree Viewer caps).
Single source of truth for the variable-length item encoding
(short/long form selection).

Nothing here knows about mice, keyboards or gamepads — only about
HID report descriptor item bytes.
"""

from __future__ import annotations


def append_usage_page(buf: bytearray, page: int) -> None:
    """USAGE_PAGE (0x05 short / 0x06 long)."""
    if page <= 0xFF:
        buf.extend((0x05, page & 0xFF))
    else:
        buf.extend((0x06, page & 0xFF, (page >> 8) & 0xFF))


def append_usage(buf: bytearray, usage: int) -> None:
    """USAGE (0x09 short / 0x0A long)."""
    if usage <= 0xFF:
        buf.extend((0x09, usage & 0xFF))
    else:
        buf.extend((0x0A, usage & 0xFF, (usage >> 8) & 0xFF))


def append_logical(buf: bytearray, value: int) -> None:
    """LOGICAL_MINIMUM / LOGICAL_MAXIMUM item (signed form)."""
    if value < 0:
        if -128 <= value <= 127:
            buf.extend((0x15, value & 0xFF))
        elif -32768 <= value <= 32767:
            buf.extend((0x16, value & 0xFF, (value >> 8) & 0xFF))
        else:
            buf.extend((0x17, value & 0xFF, (value >> 8) & 0xFF,
                        (value >> 16) & 0xFF, (value >> 24) & 0xFF))
    else:
        if value <= 0xFF:
            buf.extend((0x25, value))
        elif value <= 0xFFFF:
            buf.extend((0x26, value & 0xFF, (value >> 8) & 0xFF))
        else:
            buf.extend((0x27, value & 0xFF, (value >> 8) & 0xFF,
                        (value >> 16) & 0xFF, (value >> 24) & 0xFF))


def append_report_count(buf: bytearray, count: int) -> None:
    """REPORT_COUNT (0x95 short / 0x96 long)."""
    if count <= 0xFF:
        buf.extend((0x95, count))
    else:
        buf.extend((0x96, count & 0xFF, (count >> 8) & 0xFF))
