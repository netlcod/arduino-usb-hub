import json
import logging
import re
from pathlib import Path

from arduino_hub.exceptions import DeviceNotFoundError
from arduino_hub.usbhid.device_info import DeviceInfo, HIDReportDescriptor, HIDCollection, AxisSpec

logger = logging.getLogger(__name__)

DEVICES_DIR_NAME = "devices"


def _sanitize_filename(name: str) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    sanitized = re.sub(r"_+", "_", sanitized)
    return sanitized.strip("_").lower()


def _devices_dir(base_dir: Path) -> Path:
    return base_dir / DEVICES_DIR_NAME


def _fmt_hex(value: int) -> str:
    if value <= 0xFF:
        return f"0x{value:02X}"
    if value <= 0xFFFF:
        return f"0x{value:04X}"
    return f"0x{value:08X}"


def _parse_hex(value: str) -> int:
    return int(value, 16)


def _to_dict(device: DeviceInfo, name: str) -> dict:
    """Serialize a DeviceInfo to a JSON-friendly dict."""
    report_descriptors = []
    for rd in device.hid_report_descriptors:
        report_descriptors.append({
            "interface_number": rd.interface_number,
            "data_hex": rd.data.hex(),
            "collections": [
                {
                    "usage_page": _fmt_hex(c.usage_page),
                    "usage": _fmt_hex(c.usage),
                    "report_id": c.report_id,
                    "input_report_length": c.input_report_length,
                    "output_report_length": c.output_report_length,
                    "feature_report_length": c.feature_report_length,
                }
                for c in rd.collections
            ],
        })

    return {
        "name": name,
        # Device Descriptor
        "vendor_id": _fmt_hex(device.vendor_id),
        "product_id": _fmt_hex(device.product_id),
        "bcd_device": _fmt_hex(device.bcd_device),
        "usb_version": _fmt_hex(device.usb_version),
        "device_class": _fmt_hex(device.device_class),
        "device_subclass": _fmt_hex(device.device_subclass),
        "device_protocol": _fmt_hex(device.device_protocol),
        "ep0_max_packet_size": device.ep0_max_packet_size,
        # Configuration Descriptor
        "bm_attributes": _fmt_hex(device.bm_attributes),
        "max_power_ma": device.max_power_ma,
        # HID Interface
        "hid_interface_class": _fmt_hex(device.hid_interface_class),
        "hid_subclass": _fmt_hex(device.hid_subclass),
        "hid_protocol": _fmt_hex(device.hid_protocol),
        "bcd_hid": _fmt_hex(device.bcd_hid),
        "country_code": _fmt_hex(device.country_code),
        # Endpoint
        "ep_max_packet_size": device.ep_max_packet_size,
        "ep_interval_ms": device.ep_interval_ms,
        # HID Report
        "hid_report_descriptors": report_descriptors,
        "report_id": device.report_id,
        "report_length": device.report_length,
        "button_count": device.button_count,
        "axes": [
            {
                "usage_page": _fmt_hex(a.usage_page),
                "usage": _fmt_hex(a.usage),
                "bits": a.bits,
                "logical_min": a.logical_min,
                "logical_max": a.logical_max,
                "relative": a.relative,
                "data_index": a.data_index,
                "wire_order": a.wire_order,
            }
            for a in device.axes
        ],
        # Strings
        "manufacturer_string": device.manufacturer_string,
        "product_string": device.product_string,
        "serial_number": device.serial_number or "",
        "lang_id": _fmt_hex(device.lang_id),
    }


def _from_dict(data: dict) -> DeviceInfo:
    """Reconstruct a DeviceInfo from a JSON-friendly dict (new format)."""
    report_descriptors = []
    for rd in data.get("hid_report_descriptors", []):
        collections = [
            HIDCollection(
                usage_page=_parse_hex(c["usage_page"]),
                usage=_parse_hex(c["usage"]),
                report_id=c.get("report_id", 0),
                input_report_length=c.get("input_report_length", 0),
                output_report_length=c.get("output_report_length", 0),
                feature_report_length=c.get("feature_report_length", 0),
            )
            for c in rd.get("collections", [])
        ]
        report_descriptors.append(HIDReportDescriptor(
            interface_number=rd.get("interface_number", 0),
            data=bytes.fromhex(rd.get("data_hex", "")),
            collections=collections,
        ))

    axes = [
        AxisSpec(
            usage_page=_parse_hex(a.get("usage_page", "0x01")),
            usage=_parse_hex(a["usage"]),
            bits=a.get("bits", 8),
            logical_min=a.get("logical_min", -127),
            logical_max=a.get("logical_max", 127),
            relative=a.get("relative", True),
            data_index=a.get("data_index", 0),
            # Backward compat: pre-wire_order profiles used data_index as
            # the (possibly hand-swapped) wire sort key.
            wire_order=a.get("wire_order", a.get("data_index", 0)),
        )
        for a in data.get("axes", [])
    ]

    return DeviceInfo(
        name=data.get("name", ""),
        vendor_id=_parse_hex(data["vendor_id"]),
        product_id=_parse_hex(data["product_id"]),
        bcd_device=_parse_hex(data.get("bcd_device", "0x0100")),
        usb_version=_parse_hex(data.get("usb_version", "0x0200")),
        device_class=_parse_hex(data.get("device_class", "0x00")),
        device_subclass=_parse_hex(data.get("device_subclass", "0x00")),
        device_protocol=_parse_hex(data.get("device_protocol", "0x00")),
        ep0_max_packet_size=data.get("ep0_max_packet_size", 64),
        bm_attributes=_parse_hex(data.get("bm_attributes", "0x80")),
        max_power_ma=data.get("max_power_ma", 100),
        hid_interface_class=_parse_hex(data.get("hid_interface_class", "0x03")),
        hid_subclass=_parse_hex(data.get("hid_subclass", "0x01")),
        hid_protocol=_parse_hex(data.get("hid_protocol", "0x02")),
        bcd_hid=_parse_hex(data.get("bcd_hid", "0x0111")),
        country_code=_parse_hex(data.get("country_code", "0x00")),
        ep_max_packet_size=data.get("ep_max_packet_size", 16),
        ep_interval_ms=data.get("ep_interval_ms", 1),
        hid_report_descriptors=report_descriptors,
        report_id=data.get("report_id", 1),
        report_length=data.get("report_length", 4),
        button_count=data.get("button_count", 3),
        axes=axes,
        manufacturer_string=data.get("manufacturer_string", ""),
        product_string=data.get("product_string", ""),
        serial_number=data.get("serial_number") or None,
        lang_id=_parse_hex(data.get("lang_id", "0x0409")),
        path=b"",
    )


def _from_legacy_dict(data: dict) -> DeviceInfo:
    """Reconstruct a DeviceInfo from the old minimal format."""
    return DeviceInfo(
        name=data.get("name", ""),
        vendor_id=int(data["vendor_id"], 16),
        product_id=int(data["product_id"], 16),
        manufacturer_string=data.get("manufacturer_string", ""),
        product_string=data.get("product_string", ""),
        serial_number=data.get("serial_number"),
        path=b"",
    )


def save(device: DeviceInfo, name: str, base_dir: Path) -> Path:
    dir_path = _devices_dir(base_dir)
    dir_path.mkdir(parents=True, exist_ok=True)

    filename = _sanitize_filename(name)
    file_path = dir_path / f"{filename}.json"

    data = _to_dict(device, name)
    file_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    logger.info("Device saved: %s", file_path)
    return file_path


def load(name: str, base_dir: Path) -> DeviceInfo:
    sanitized = _sanitize_filename(name)

    candidates = list(_devices_dir(base_dir).glob(f"{sanitized}.json"))
    if not candidates:
        candidates = list(_devices_dir(base_dir).glob("*.json"))

    matches = [f for f in candidates if _sanitize_filename(f.stem) == sanitized]
    if not matches:
        raise DeviceNotFoundError(
            f"Device '{name}' not found in {_devices_dir(base_dir)}. "
            f"Run 'arduino-hub clone --name {name} --report <path>' first."
        )

    file_path = matches[0]
    data = json.loads(file_path.read_text(encoding="utf-8"))

    if "hid_report_descriptors" in data:
        return _from_dict(data)
    return _from_legacy_dict(data)


def list_devices(base_dir: Path) -> list[str]:
    dir_path = _devices_dir(base_dir)
    if not dir_path.exists():
        return []
    return sorted(f.stem for f in dir_path.glob("*.json"))
