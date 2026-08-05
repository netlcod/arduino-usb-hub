"""
Parser for USB Device Tree Viewer reports.

Reads a full report .txt file and extracts:
- Device Descriptor
- Configuration Descriptor
- Interface Descriptors (finds the mouse one by bInterfaceProtocol=0x02)
- HID Descriptor
- Endpoint Descriptor
- String Descriptors
- HID Caps (ButtonCaps, ValueCaps)
- Reconstructs the raw HID Report Descriptor from Caps

Usage:
    python -m arduino_hub.usbhid.descriptor_reader devices/reports/logitech-g305-report.txt
"""

from __future__ import annotations

import logging
from pathlib import Path
import re
from dataclasses import asdict
from typing import Any

from arduino_hub.usbhid.device_info import DeviceInfo, HIDReportDescriptor, HIDCollection

logger = logging.getLogger(__name__)

# ── Low-level report line parsers ───────────────────────────────

_HEX_RE = re.compile(r"0x[0-9A-Fa-f]+")


def _parse_hex_field(text: str, key: str) -> int | None:
    """Extract a hex value like 'bcdUSB : 0x200' → 0x200."""
    pattern = re.escape(key) + r"\s*:\s*(0x[0-9A-Fa-f]+)"
    m = re.search(pattern, text)
    if m:
        return int(m.group(1), 16)
    return None


def _parse_dec_field(text: str, key: str) -> int | None:
    """Extract a decimal value like 'Number of Buttons : 16' → 16."""
    pattern = re.escape(key) + r"\s*:\s*(\d+)"
    m = re.search(pattern, text)
    if m:
        return int(m.group(1))
    return None


def _parse_string_field(text: str, key: str) -> str | None:
    """Extract a quoted string like Language 0x0409 : \"Logitech\"."""
    pattern = re.escape(key) + r'\s*:\s*"([^"]*)"'
    m = re.search(pattern, text)
    if m:
        return m.group(1)
    return None


def _parse_pipe_field(text: str, pipe_index: int) -> dict[str, int] | None:
    """Parse a Pipe[N] line like 'Pipe[0]: EndpointID=1 Direction=IN ... wMaxPacketSize=0x0C bInterval=1'."""
    pattern = rf"Pipe\[{pipe_index}\]\s*:.*?EndpointID=(\d+).*?Direction=(IN|OUT).*?Type=(\w+).*?wMaxPacketSize=(0x[0-9A-Fa-f]+).*?bInterval=(\d+)"
    m = re.search(pattern, text)
    if m:
        return {
            "endpoint_id": int(m.group(1)),
            "direction": m.group(2),
            "type": m.group(3),
            "max_packet_size": int(m.group(4), 16),
            "interval": int(m.group(5)),
        }
    return None


# ── HID Caps data structures ────────────────────────────────────


class ParsedButtonCaps:
    usage_page: int = 0
    report_id: int = 0
    is_variable: bool = True
    is_absolute: bool = True
    is_range: bool = False
    usage_min: int = 0
    usage_max: int = 0
    data_index_min: int = 0
    data_index_max: int = 0


class ParsedValueCaps:
    usage_page: int = 0
    usage: int = 0
    report_id: int = 0
    is_variable: bool = True
    is_relative: bool = False
    is_absolute: bool = True
    bit_size: int = 8
    report_count: int = 1
    logical_min: int = 0
    logical_max: int = 0
    data_index: int = 0


# ── Main parser ─────────────────────────────────────────────────

class ReportParser:
    """Parses a USB Device Tree Viewer report .txt file."""

    def __init__(self, text: str):
        self.text = text

    # ── Section extractors ──────────────────────────────────

    def _section(self, header: str, end_marker: str | None = None) -> str:
        """Extract a section of the report between header and end_marker."""
        start = self.text.find(header)
        if start < 0:
            return ""
        if end_marker:
            end = self.text.find(end_marker, start + len(header))
            if end < 0:
                return self.text[start:]
            return self.text[start:end]
        return self.text[start:]

    def _all_sections(self, header: str) -> list[str]:
        """Find all sections starting with header (non-overlapping)."""
        result = []
        idx = 0
        while True:
            idx = self.text.find(header, idx)
            if idx < 0:
                break
            result.append(self.text[idx:])
            idx += len(header)
        return result

    # ── Summary ─────────────────────────────────────────────

    def parse_summary(self) -> dict[str, Any]:
        section = self._section("========================== Summary =========================",
                                "======================== USB Device =========================")
        result: dict[str, Any] = {}
        if not section:
            return result

        v = _parse_hex_field(section, "Vendor ID")
        if v is not None:
            result["vendor_id"] = v
        v = _parse_hex_field(section, "Product ID")
        if v is not None:
            result["product_id"] = v
        s = _parse_string_field(section, "Manufacturer String")
        if s:
            result["manufacturer_string"] = s
        s = _parse_string_field(section, "Product String")
        if s:
            result["product_string"] = s
        # "Demanded Current" in mA
        m = re.search(r"Demanded Current\s*:\s*(\d+)\s*mA", section)
        if m:
            result["max_power_ma"] = int(m.group(1))
        # Self powered
        m = re.search(r"Self powered\s*:\s*(yes|no)", section)
        if m:
            result["self_powered"] = (m.group(1) == "yes")
        # Serial
        m = re.search(r"Serial\s*:\s*(---|.+)$", section, re.MULTILINE)
        if m and m.group(1).strip() == "---":
            result["serial_number"] = None

        return result

    # ── Device Descriptor ───────────────────────────────────

    def parse_device_descriptor(self) -> dict[str, Any]:
        section = self._section("---------------------- Device Descriptor ----------------------",
                                "------------------ Configuration Descriptor -------------------")
        if not section:
            return {}
        return {
            "usb_version": _parse_hex_field(section, "bcdUSB") or 0x0200,
            "device_class": _parse_hex_field(section, "bDeviceClass") or 0,
            "device_subclass": _parse_hex_field(section, "bDeviceSubClass") or 0,
            "device_protocol": _parse_hex_field(section, "bDeviceProtocol") or 0,
            "ep0_max_packet_size": _parse_hex_field(section, "bMaxPacketSize0") or 64,
            "vendor_id": _parse_hex_field(section, "idVendor") or 0,
            "product_id": _parse_hex_field(section, "idProduct") or 0,
            "bcd_device": _parse_hex_field(section, "bcdDevice") or 0x0100,
        }

    # ── Configuration Descriptor ────────────────────────────

    def parse_config_descriptor(self) -> dict[str, Any]:
        section = self._section("------------------ Configuration Descriptor -------------------",
                                "---------------- Interface Descriptor -----------------")
        if not section:
            return {}
        result: dict[str, Any] = {}
        v = _parse_hex_field(section, "bmAttributes")
        if v is not None:
            result["bm_attributes"] = v
        m = re.search(r"MaxPower\s*:\s*(0x[0-9A-Fa-f]+)\s*\((\d+)\s*mA\)", section)
        if m:
            result["max_power_ma"] = int(m.group(2))
        return result

    # ── Mouse Interface Descriptor ──────────────────────────

    def find_mouse_interface(self) -> str:
        """Find the block for the mouse HID interface (bInterfaceProtocol=0x02)."""
        blocks = re.split(r"---------------- Interface Descriptor ----------------", self.text)
        for block in blocks:
            if re.search(r"bInterfaceProtocol\s*:\s*0x02\s*\(Mouse\)", block):
                return block
        return ""

    def parse_mouse_interface(self) -> dict[str, Any]:
        block = self.find_mouse_interface()
        if not block:
            return {}
        return {
            "hid_subclass": _parse_hex_field(block, "bInterfaceSubClass") or 1,
            "hid_protocol": _parse_hex_field(block, "bInterfaceProtocol") or 2,
        }

    # ── HID Descriptor (for mouse interface) ────────────────

    def find_mouse_hid_descriptor(self) -> str:
        """HID descriptor section that follows the mouse interface."""
        blocks = re.split(r"------------------- HID Descriptor --------------------", self.text)
        for i, block in enumerate(blocks):
            if re.search(r"bInterfaceProtocol\s*:\s*0x02\s*\(Mouse\)", block):
                return block
        # Fallback: find HID desc that comes after mouse interface
        mouse_block = self.find_mouse_interface()
        if mouse_block:
            # Search for HID descriptor in the text after mouse interface
            idx = self.text.find(mouse_block)
            if idx >= 0:
                after_mouse = self.text[idx + len(mouse_block):]
                m = re.search(r"------------------- HID Descriptor --------------------(.*?)"
                              r"----------------- Endpoint Descriptor -----------------",
                              after_mouse, re.DOTALL)
                if m:
                    return m.group(1)
        return ""

    def parse_hid_descriptor(self) -> dict[str, Any]:
        block = self.find_mouse_hid_descriptor()
        if not block:
            return {}
        result: dict[str, Any] = {}
        v = _parse_hex_field(block, "bcdHID")
        if v is not None:
            result["bcd_hid"] = v
        v = _parse_hex_field(block, "bCountryCode")
        if v is not None:
            result["country_code"] = v
        return result

    # ── Endpoint Descriptor (for mouse interface) ───────────

    def find_mouse_endpoint(self) -> str:
        mouse_block = self.find_mouse_interface()
        if not mouse_block:
            return ""
        idx = self.text.find(mouse_block)
        if idx < 0:
            return ""
        after = self.text[idx + len(mouse_block):]
        m = re.search(r"----------------- Endpoint Descriptor -----------------(.*?)"
                      r"(?=---------------- Interface Descriptor|------------------- HID Descriptor|$)",
                      after, re.DOTALL)
        return m.group(1) if m else ""

    def parse_endpoint(self) -> dict[str, Any]:
        block = self.find_mouse_endpoint()
        if not block:
            return {}
        result: dict[str, Any] = {}
        v = _parse_hex_field(block, "wMaxPacketSize")
        if v is not None:
            result["ep_max_packet_size"] = v
        v = _parse_hex_field(block, "bInterval")
        if v is not None:
            result["ep_interval_ms"] = v
        return result

    # ── String Descriptors ──────────────────────────────────

    def parse_strings(self) -> dict[str, Any]:
        section = self._section("-------------------- String Descriptors -------------------",
                                "+++++++++++++++++ Device Information ++++++++++++++++++")
        if not section:
            return {}

        result: dict[str, Any] = {}
        m = re.search(r"Language ID\[0\]\s*:\s*(0x[0-9A-Fa-f]+)", section)
        if m:
            result["lang_id"] = int(m.group(1), 16)

        # iManufacturer
        s = _parse_string_field(section, 'Language 0x0409')
        # Actually we need to find the right string. Look for iManufacturer reference then nearby string.
        # Simpler: just find all "Language 0x0409" strings in order.
        strings = re.findall(r'Language 0x0409\s*:\s*"([^"]*)"', section)
        # String 1 = manufacturer, String 2 = product
        if len(strings) >= 1:
            result["manufacturer_string"] = strings[0]
        if len(strings) >= 2:
            result["product_string"] = strings[1]

        # Serial: check iSerialNumber in device descriptor
        dev_sec = self._section("Device Descriptor", "Configuration Descriptor")
        if dev_sec:
            m = re.search(r"iSerialNumber\s*:\s*0x00\s*\(No String Descriptor\)", dev_sec)
            if m:
                result["serial_number"] = None

        return result

    # ── HID Caps (mouse collection) ─────────────────────────

    def find_mouse_caps_section(self) -> str:
        """Find the HID caps section for the mouse."""
        sections = re.split(r"\+\+\+\+\+\+\+\+\+\+ Device Information \+\+\+\+\+\+\+\+\+\+", self.text)
        for sec in sections:
            # Must contain mouse HID info
            if re.search(r"HID-совместимая мышь|HID.compliant.mouse|UsagePage\s*:\s*0x0001.*\n.*Usage\s*:\s*0x0002\s*\(Mouse\)", sec, re.DOTALL):
                return sec
        return ""

    def parse_button_caps(self, caps_section: str) -> list[ParsedButtonCaps]:
        result = []
        blocks = re.split(r"-{5,}\s*Input ButtonCaps\[", caps_section)
        for block in blocks[1:]:
            caps = ParsedButtonCaps()
            v = _parse_hex_field(block, "UsagePage")
            if v is not None:
                caps.usage_page = v
            v = _parse_hex_field(block, "ReportID")
            if v is not None:
                caps.report_id = v
            # Bit 1: Variable vs Array
            m = re.search(r"Bit 1\s*:\s*(\d)\s*\((Variable|Array)\)", block)
            if m:
                caps.is_variable = (m.group(2) == "Variable")
            # Bit 2: Absolute vs Relative
            m = re.search(r"Bit 2\s*:\s*(\d)\s*\((Absolute|Relative)\)", block)
            if m:
                caps.is_absolute = (m.group(2) == "Absolute")
            # IsRange
            m = re.search(r"IsRange\s*:\s*0x0([01])\s*\((yes|no)\)", block)
            if m:
                caps.is_range = (m.group(2) == "yes")
            v = _parse_hex_field(block, "UsageMin")
            if v is not None:
                caps.usage_min = v
            v = _parse_hex_field(block, "UsageMax")
            if v is not None:
                caps.usage_max = v
            v = _parse_dec_field(block, "DataIndexMin")
            if v is not None:
                caps.data_index_min = v
            v = _parse_dec_field(block, "DataIndexMax")
            if v is not None:
                caps.data_index_max = v
            result.append(caps)
        return result

    def parse_value_caps(self, caps_section: str) -> list[ParsedValueCaps]:
        result = []
        blocks = re.split(r"-{5,}\s*InputCaps\[", caps_section)
        for block in blocks[1:]:
            caps = ParsedValueCaps()
            v = _parse_hex_field(block, "UsagePage")
            if v is not None:
                caps.usage_page = v
            m = re.search(r"\bUsage\b\s*:\s*(0x[0-9A-Fa-f]+)", block)
            if m:
                caps.usage = int(m.group(1), 16)
            v = _parse_hex_field(block, "ReportID")
            if v is not None:
                caps.report_id = v
            m = re.search(r"Bit 1\s*:\s*(\d)\s*\((Variable|Array)\)", block)
            if m:
                caps.is_variable = (m.group(2) == "Variable")
            m = re.search(r"Bit 2\s*:\s*(\d)\s*\((Absolute|Relative)\)", block)
            if m:
                caps.is_relative = (m.group(2) == "Relative")
                caps.is_absolute = (m.group(2) == "Absolute")
            v = _parse_hex_field(block, "BitSize")
            if v is not None:
                caps.bit_size = v
            v = _parse_hex_field(block, "ReportCount")
            if v is not None:
                caps.report_count = v
            # LogicalMin: hex value may be 0xFFFFFF81, extract the integer from parentheses
            m = re.search(r"LogicalMin\s*:\s*(0x[0-9A-Fa-f]+)\s*\((-?\d+)\)", block)
            if m:
                caps.logical_min = int(m.group(2))
            m = re.search(r"LogicalMax\s*:\s*(0x[0-9A-Fa-f]+)\s*\((\+?\d+)\)", block)
            if m:
                caps.logical_max = int(m.group(2))
            v = _parse_dec_field(block, "DataIndex")
            if v is not None:
                caps.data_index = v
            result.append(caps)
        # Sort by DataIndex to match order in the descriptor
        result.sort(key=lambda c: c.data_index)
        return result

    def parse_mouse_caps(self) -> dict[str, Any]:
        section = self.find_mouse_caps_section()
        if not section:
            return {}

        result: dict[str, Any] = {}
        v = _parse_hex_field(section, "UsagePage")
        if v is not None:
            result["usage_page"] = v
        v = _parse_hex_field(section, "Usage")
        if v is not None:
            result["usage"] = v
        v = _parse_hex_field(section, "InputReportByteLength")
        if v is not None:
            result["report_length"] = v
        v = _parse_dec_field(section, "NumberLinkCollectionNodes")
        if v is not None:
            result["num_collections"] = v
        v = _parse_dec_field(section, "Number of Buttons")
        if v is not None:
            result["button_count"] = v

        result["button_caps"] = self.parse_button_caps(section)
        result["value_caps"] = self.parse_value_caps(section)

        # Extract ReportID from first button or value cap
        for bc in result["button_caps"]:
            result["report_id"] = bc.report_id
            break
        if "report_id" not in result:
            for vc in result["value_caps"]:
                result["report_id"] = vc.report_id
                break

        return result

    # ── HID Report Descriptor reconstruction ────────────────

    def reconstruct_report_descriptor(self, caps: dict[str, Any]) -> bytes:
        """Reconstruct raw HID Report Descriptor from parsed caps."""
        buf = bytearray()

        button_caps: list[ParsedButtonCaps] = caps.get("button_caps", [])
        value_caps: list[ParsedValueCaps] = caps.get("value_caps", [])
        report_id = caps.get("report_id", 1)

        # ── Header ──
        # USAGE_PAGE (Generic Desktop) 0x01
        buf.extend((0x05, 0x01))
        # USAGE (Mouse) 0x02
        buf.extend((0x09, 0x02))
        # COLLECTION (Application)
        buf.extend((0xA1, 0x01))
        #   USAGE (Pointer)
        buf.extend((0x09, 0x01))
        #   COLLECTION (Physical)
        buf.extend((0xA1, 0x00))
        #   REPORT_ID
        buf.extend((0x85, report_id & 0xFF))

        # ── Buttons ──
        if button_caps:
            bc = button_caps[0]
            num_buttons = bc.usage_max - bc.usage_min + 1

            # USAGE_PAGE (Button)
            buf.extend((0x05, 0x09))
            # USAGE_MINIMUM
            if bc.usage_min <= 0xFF:
                buf.extend((0x19, bc.usage_min))
            else:
                buf.extend((0x1A, bc.usage_min & 0xFF, (bc.usage_min >> 8) & 0xFF))
            # USAGE_MAXIMUM
            if bc.usage_max <= 0xFF:
                buf.extend((0x29, bc.usage_max))
            else:
                buf.extend((0x2A, bc.usage_max & 0xFF, (bc.usage_max >> 8) & 0xFF))
            # LOGICAL_MINIMUM (0)
            buf.extend((0x15, 0x00))
            # LOGICAL_MAXIMUM (1)
            buf.extend((0x25, 0x01))
            # REPORT_SIZE (1)
            buf.extend((0x75, 0x01))
            # REPORT_COUNT (num_buttons)
            if num_buttons <= 0xFF:
                buf.extend((0x95, num_buttons))
            else:
                buf.extend((0x96, num_buttons & 0xFF, (num_buttons >> 8) & 0xFF))
            # INPUT (Data | Variable | Absolute)
            flags = 0x02  # Data | Variable | Absolute
            if not bc.is_variable:
                flags &= ~0x02  # Clear Variable → Array
            if bc.is_absolute:
                flags &= ~0x04  # Absolute bit clear in HID spec? No - Absolute=0 in BitField but 0 in flags
            # Actually: BitField: Bit1=Variable(=1), Bit2=Absolute(=0)
            # HID INPUT flags: bit 0=Data(1), bit 1=Var(1), bit 2=Rel(0 for Abs)
            flags = 0x02  # Data | Variable | Abs

            buf.extend((0x81, flags))

            # Padding to byte boundary
            remainder = num_buttons % 8
            if remainder != 0:
                # We append padding as a Const INPUT
                padding_bits = 8 - remainder
                buf.extend((0x95, 0x01))      # REPORT_COUNT 1
                buf.extend((0x75, padding_bits))  # REPORT_SIZE
                buf.extend((0x81, 0x03))      # INPUT (Cnst | Var | Abs)

        # ── Value axes ──
        for vc in value_caps:
            # USAGE_PAGE
            if vc.usage_page <= 0xFF:
                buf.extend((0x05, vc.usage_page & 0xFF))
            else:
                buf.extend((0x06, vc.usage_page & 0xFF, (vc.usage_page >> 8) & 0xFF))

            # USAGE
            if vc.usage <= 0xFF:
                buf.extend((0x09, vc.usage & 0xFF))
            else:
                buf.extend((0x0A, vc.usage & 0xFF, (vc.usage >> 8) & 0xFF))

            # LOGICAL_MINIMUM
            _append_logical(buf, vc.logical_min)
            # LOGICAL_MAXIMUM
            _append_logical(buf, vc.logical_max)

            # REPORT_SIZE
            buf.extend((0x75, vc.bit_size & 0xFF))
            # REPORT_COUNT
            if vc.report_count <= 0xFF:
                buf.extend((0x95, vc.report_count & 0xFF))
            else:
                buf.extend((0x96, vc.report_count & 0xFF, (vc.report_count >> 8) & 0xFF))

            # INPUT flags
            flags = 0x02  # Data | Variable
            if vc.is_relative:
                flags |= 0x04  # Relative
            buf.extend((0x81, flags))

        # ── Footer ──
        buf.extend((0xC0,))  # END_COLLECTION (Physical)
        buf.extend((0xC0,))  # END_COLLECTION (Application)

        return bytes(buf)

    # ── Full build ──────────────────────────────────────────

    def build_device_info(self) -> DeviceInfo:
        """Parse the report and build complete DeviceInfo."""
        # Parse all sections
        summary = self.parse_summary()
        dev_desc = self.parse_device_descriptor()
        cfg_desc = self.parse_config_descriptor()
        iface = self.parse_mouse_interface()
        hid_desc = self.parse_hid_descriptor()
        ep = self.parse_endpoint()
        strings = self.parse_strings()
        mouse_caps = self.parse_mouse_caps()

        # Reconstruct raw HID Report Descriptor
        raw_report = self.reconstruct_report_descriptor(mouse_caps)

        # Build DeviceInfo
        info = DeviceInfo(
            vendor_id=dev_desc.get("vendor_id") or summary.get("vendor_id") or 0,
            product_id=dev_desc.get("product_id") or summary.get("product_id") or 0,
            bcd_device=dev_desc.get("bcd_device") or 0x4401,
            usb_version=dev_desc.get("usb_version") or 0x0200,
            device_class=dev_desc.get("device_class") or 0,
            device_subclass=dev_desc.get("device_subclass") or 0,
            device_protocol=dev_desc.get("device_protocol") or 0,
            ep0_max_packet_size=dev_desc.get("ep0_max_packet_size") or 64,
            bm_attributes=cfg_desc.get("bm_attributes") or 0xA0,
            max_power_ma=(cfg_desc.get("max_power_ma")
                          or summary.get("max_power_ma") or 100),
            hid_subclass=iface.get("hid_subclass") or 1,
            hid_protocol=iface.get("hid_protocol") or 2,
            bcd_hid=hid_desc.get("bcd_hid") or 0x0111,
            country_code=hid_desc.get("country_code") or 0,
            ep_max_packet_size=ep.get("ep_max_packet_size") or 16,
            ep_interval_ms=ep.get("ep_interval_ms") or 1,
            report_id=mouse_caps.get("report_id") or 1,
            report_length=mouse_caps.get("report_length") or 4,
            button_count=mouse_caps.get("button_count") or 3,
            manufacturer_string=(
                strings.get("manufacturer_string")
                or summary.get("manufacturer_string") or ""
            ),
            product_string=(
                strings.get("product_string")
                or summary.get("product_string") or ""
            ),
            serial_number=strings.get("serial_number", summary.get("serial_number", "")),
            lang_id=strings.get("lang_id") or 0x0409,
        )

        # Attach HID report descriptors
        if raw_report:
            info.hid_report_descriptors.append(
                HIDReportDescriptor(
                    interface_number=0,
                    data=raw_report,
                    collections=[
                        HIDCollection(
                            usage_page=mouse_caps.get("usage_page") or 0x0001,
                            usage=mouse_caps.get("usage") or 0x0002,
                            report_id=info.report_id,
                            input_report_length=info.report_length,
                        )
                    ],
                )
            )

        info.name = info.product_string or f"{info.vendor_id:04X}:{info.product_id:04X}"
        return info


# ── Helper: logical min/max encoding ─────────────────────────────


def _append_logical(buf: bytearray, value: int):
    """Append LOGICAL_MINIMUM or LOGICAL_MAXIMUM item to buffer."""
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


# ── CLI ──────────────────────────────────────────────────────────


def parse_report(report_path: str | Path) -> DeviceInfo | None:
    """Parse a USB Device Tree Viewer report file into DeviceInfo.

    Returns None if the report cannot be read or the mouse interface
    is not found.
    """
    path = Path(report_path)
    if not path.exists():
        logger.error("Report not found: %s", path)
        return None

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()

    parser = ReportParser(text)
    info = parser.build_device_info()

    if not info.product_string and not info.vendor_id:
        logger.error("No usable device data found in report: %s", path)
        return None

    return info


def read_and_print(report_path: str) -> DeviceInfo | None:
    """Parse a USB Device Tree Viewer report and return DeviceInfo."""
    info = parse_report(report_path)
    if info is None:
        return None

    print(f"=== {info.name} ({info.vendor_id:04X}:{info.product_id:04X}) ===")
    print(f"Manufacturer   : {info.manufacturer_string}")
    print(f"Product        : {info.product_string}")
    print(f"Serial         : {info.serial_number or '(none)'}")
    print()
    print(f"bcdUSB         : 0x{info.usb_version:04X}")
    print(f"bcdDevice      : 0x{info.bcd_device:04X}")
    print(f"Device Class   : 0x{info.device_class:02X}")
    print(f"EP0 MaxPacket  : {info.ep0_max_packet_size}")
    print()
    print(f"bmAttributes   : 0x{info.bm_attributes:02X}")
    print(f"MaxPower       : {info.max_power_ma} mA")
    print()
    print(f"HID Subclass   : {info.hid_subclass} ({'Boot' if info.hid_subclass == 1 else 'None'})")
    print(f"HID Protocol   : {info.hid_protocol} ({'Mouse' if info.hid_protocol == 2 else 'None'})")
    print(f"bcdHID         : 0x{info.bcd_hid:04X}")
    print(f"Country Code   : {info.country_code}")
    print()
    print(f"EP MaxPacket   : {info.ep_max_packet_size}")
    print(f"bInterval      : {info.ep_interval_ms} ms")
    print()
    print(f"Report ID      : {info.report_id}")
    print(f"Report Length  : {info.report_length} bytes")
    print(f"Button Count   : {info.button_count}")
    print()

    for rd in info.hid_report_descriptors:
        print(f"--- HID Report Descriptor ({len(rd.data)} bytes) ---")
        hex_str = " ".join(f"{b:02X}" for b in rd.data[:64])
        if len(rd.data) > 64:
            hex_str += " ..."
        print(f"  {hex_str}")
        if len(rd.data) > 64:
            # Print continuation lines
            for i in range(64, len(rd.data), 64):
                chunk = rd.data[i:i + 64]
                hex_chunk = " ".join(f"{b:02X}" for b in chunk)
                print(f"  {hex_chunk}")

    return info


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if len(sys.argv) >= 2:
        report_path = sys.argv[1]
    else:
        report_path = "devices/reports/logitech-g305-report.txt"

    read_and_print(report_path)
