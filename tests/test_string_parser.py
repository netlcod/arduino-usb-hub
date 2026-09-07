"""Unit tests for the USB Tree Viewer string-descriptor parsing.

The index-based mapping (iManufacturer/iProduct/iSerialNumber →
String Descriptor N) is the entry point of the serial-parity feature:
a regex drift against a different USB Tree Viewer version would
silently drop the serial, so the parser is pinned here with synthetic
report excerpts (the full G305 dump stays in profiles/sources/reports/,
which is gitignored user data).
"""

import pytest

from arduino_hub.usbhid.descriptor_reader import ReportParser

_DEV_HEADER = (
    "    ---------------------- Device Descriptor ----------------------\n"
    "bcdUSB                   : 0x200 (USB Version 2.0)\n"
    "idVendor                 : 0x046D (Logitech Inc.)\n"
    "idProduct                : 0xC53F\n"
    "{IM}\n"
    "{IP}\n"
    "{IS}\n"
    "    -------------------- String Descriptors -------------------\n"
    "             ------ String Descriptor 0 ------\n"
    "Language ID[0]           : 0x0409 (English - United States)\n"
    "             ------ String Descriptor 1 ------\n"
    'Language 0x0409          : "Acme"\n'
    "             ------ String Descriptor 2 ------\n"
    'Language 0x0409          : "Gadget"\n'
)


def _report(im="{IM}", ip="{IP}", iserial="{IS}", extra=""):
    return _DEV_HEADER.format(
        IM=im, IP=ip, IS=iserial,
    ) + extra


def test_index_mapped_strings_with_serial():
    text = _report(
        im="iManufacturer            : 0x01 (String Descriptor 1)",
        ip="iProduct                 : 0x02 (String Descriptor 2)",
        iserial="iSerialNumber            : 0x03 (String Descriptor 3)",
        extra=(
            "             ------ String Descriptor 3 ------\n"
            'Language 0x0409          : "SN12345678"\n'
        ),
    )
    strings = ReportParser(text).parse_strings()
    assert strings["manufacturer_string"] == "Acme"
    assert strings["product_string"] == "Gadget"
    assert strings["serial_number"] == "SN12345678"
    assert strings["lang_id"] == 0x0409


def test_no_serial_when_iserial_zero():
    text = _report(
        im="iManufacturer            : 0x01 (String Descriptor 1)",
        ip="iProduct                 : 0x02 (String Descriptor 2)",
        iserial="iSerialNumber            : 0x00 (No String Descriptor)",
    )
    strings = ReportParser(text).parse_strings()
    assert strings["serial_number"] is None
    assert strings["manufacturer_string"] == "Acme"


def test_truncated_serial_report_warns(caplog):
    """iSerialNumber declared non-zero but the descriptor block is
    missing from the report: no serial in the profile + a warning
    (never silent)."""
    text = _report(
        im="iManufacturer            : 0x01 (String Descriptor 1)",
        ip="iProduct                 : 0x02 (String Descriptor 2)",
        iserial="iSerialNumber            : 0x03 (String Descriptor 3)",
    )
    with caplog.at_level("WARNING"):
        strings = ReportParser(text).parse_strings()
    assert "serial_number" not in strings
    assert any("iSerialNumber=3" in r.message for r in caplog.records)


def test_positional_fallback_without_indices():
    """Reports that do not declare string indices still yield the two
    strings in positional order (legacy behaviour)."""
    text = _report(
        im="",
        ip="",
        iserial="iSerialNumber            : 0x00 (No String Descriptor)",
    )
    strings = ReportParser(text).parse_strings()
    assert strings["manufacturer_string"] == "Acme"
    assert strings["product_string"] == "Gadget"
    assert strings["serial_number"] is None


def test_unused_descriptor_index_is_ignored():
    """A config string (index 4) must not leak into manufacturer/product
    slots — indices decide, position does not."""
    text = _report(
        im="iManufacturer            : 0x01 (String Descriptor 1)",
        ip="iProduct                 : 0x02 (String Descriptor 2)",
        iserial="iSerialNumber            : 0x00 (No String Descriptor)",
        extra=(
            "             ------ String Descriptor 4 ------\n"
            'Language 0x0409          : "RQR44.01_B0005"\n'
        ),
    )
    strings = ReportParser(text).parse_strings()
    assert strings["manufacturer_string"] == "Acme"
    assert strings["product_string"] == "Gadget"
    assert "RQR44.01_B0005" not in strings.values()
