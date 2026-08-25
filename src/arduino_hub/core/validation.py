"""Identity profile validation.

Pre-flight checks over a cloned DeviceInfo before any core file is
touched. The clone flow derives everything from the device's own
descriptors, so these rules mostly guard *hand-edited*
``profiles/sources/*.json`` (profiles are edited manually — e.g. the
data_index/wire_order swap) against values that would break compilation
or produce an implausible identity.
"""

from dataclasses import dataclass

from arduino_hub.usbhid.device_info import DeviceInfo

ERROR = "ERROR"
WARN = "WARN"

# Subset of usb.ids: well-known peripheral vendors. Used only for a
# WARN-level coherence check between idVendor and the manufacturer
# string; unknown VIDs never produce issues.
KNOWN_VENDORS: dict[int, list[str]] = {
    0x046D: ["logitech"],
    0x045E: ["microsoft"],
    0x1532: ["razer"],
    0x1038: ["steelseries"],
    0x1B1C: ["corsair"],
    0x0738: ["mad catz", "madcatz"],
    0x044F: ["thrustmaster"],
    0x046A: ["cherry"],
    0x04D9: ["holtek"],
    0x093A: ["pixart"],
    0x0461: ["primax"],
    0x413C: ["dell"],
    0x09DA: ["a4tech"],
    0x05AC: ["apple"],
    0x2341: ["arduino"],
    0x1B4F: ["sparkfun"],
    0x0458: ["kye", "genius"],
    0x062A: ["saitek"],
    0x1A81: ["holtek"],
    0x248A: ["maxxter"],
    0x18F8: ["maxxter"],
    0x0C45: ["sonix"],
    0x25A7: ["areson"],
    0x1EA7: ["sharkoon", "2the"],
    0x2101: ["action star"],
}

# These strings mean "no real manufacturer info"; the vendor check is
# skipped for them.
_UNKNOWN_MANUFACTURERS = {"", "unknown", "н/д"}

_MAX_STRING_LEN = 126  # USB string descriptor limit (UTF-16 chars)
_EP0_SIZES = {8, 16, 32, 64}


@dataclass(frozen=True)
class ValidationIssue:
    severity: str  # ERROR | WARN
    field: str
    message: str

    def __str__(self) -> str:
        return f"{self.severity}: [{self.field}] {self.message}"


def has_errors(issues: list[ValidationIssue]) -> bool:
    return any(i.severity == ERROR for i in issues)


def summarize(issues: list[ValidationIssue]) -> str:
    """Compact single-line summary for the patch manifest."""
    if not issues:
        return "ok"
    return "; ".join(str(i) for i in issues)


def _check_string(issues: list[ValidationIssue], field: str, value: str) -> None:
    if not value.strip():
        issues.append(
            ValidationIssue(ERROR, field, f"{field} string is empty")
        )
        return
    if '"' in value or "\\" in value:
        issues.append(
            ValidationIssue(
                ERROR,
                field,
                f'{field} contains \'"/\\\' which breaks -D compiler flags: {value!r}',
            )
        )
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        issues.append(
            ValidationIssue(
                ERROR, field, f"{field} contains control characters: {value!r}"
            )
        )
    if len(value) > _MAX_STRING_LEN:
        issues.append(
            ValidationIssue(
                WARN,
                field,
                f"{field} longer than {_MAX_STRING_LEN} chars ({len(value)}); "
                f"USB string descriptors cannot exceed that",
            )
        )


def validate_identity(device: DeviceInfo) -> list[ValidationIssue]:
    """Validate identity fields of a source profile.

    Returns all found issues sorted ERROR-first. An empty list means the
    profile passed clean.
    """
    issues: list[ValidationIssue] = []

    # Strings must survive boards.txt -D flags verbatim.
    _check_string(issues, "manufacturer", device.manufacturer_string)
    _check_string(issues, "product", device.product_string)

    if device.ep0_max_packet_size not in _EP0_SIZES:
        issues.append(
            ValidationIssue(
                WARN,
                "ep0_max_packet_size",
                f"bMaxPacketSize0={device.ep0_max_packet_size} is not one of "
                f"8/16/32/64; the descriptor will carry it verbatim but hosts "
                f"may reject enumeration",
            )
        )

    if not 1 <= device.max_power_ma <= 500:
        issues.append(
            ValidationIssue(
                WARN,
                "max_power_ma",
                f"bMaxPower={device.max_power_ma} mA outside the legal "
                f"1..500 range for bus-powered devices",
            )
        )
    elif device.max_power_ma > 200:
        issues.append(
            ValidationIssue(
                WARN,
                "max_power_ma",
                f"bMaxPower={device.max_power_ma} mA is unrealistic for a HID "
                f"peripheral (typical mice report 50..100 mA)",
            )
        )

    if device.usb_version > 0x0200:
        issues.append(
            ValidationIssue(
                WARN,
                "usb_version",
                f"bcdUSB=0x{device.usb_version:04X}: ATmega32U4 is Full Speed "
                f"only; anything above 0x0200 cannot be honoured on the wire",
            )
        )

    if device.device_class != 0x00:
        issues.append(
            ValidationIssue(
                WARN,
                "device_class",
                f"bDeviceClass=0x{device.device_class:02X} is not reproduced "
                f"by the patcher (CDC-disabled core always reports 0x00 "
                f"class-at-interface)",
            )
        )

    if device.bm_attributes & 0x40:
        issues.append(
            ValidationIssue(
                WARN,
                "bm_attributes",
                f"bmAttributes=0x{device.bm_attributes:02X} declares "
                f"self-powered; the patched core always reports bus-powered "
                f"for bit 7 and copies this value verbatim otherwise",
            )
        )

    if device.ep_interval_ms < 1:
        issues.append(
            ValidationIssue(
                WARN,
                "ep_interval_ms",
                f"bInterval={device.ep_interval_ms} < 1 ms is invalid USB; "
                f"clamped to 1 by the patcher",
            )
        )

    # Vendor coherence: only meaningful for hand-edited profiles; a live
    # clone takes both VID and strings from the same device.
    names = KNOWN_VENDORS.get(device.vendor_id)
    manufacturer = device.manufacturer_string.lower().strip()
    if names and manufacturer not in _UNKNOWN_MANUFACTURERS:
        if not any(alias in manufacturer for alias in names):
            issues.append(
                ValidationIssue(
                    WARN,
                    "vendor_id",
                    f"VID 0x{device.vendor_id:04X} belongs to "
                    f"{', '.join(sorted(set(names)))}, but manufacturer string "
                    f"is {device.manufacturer_string!r}",
                )
            )

    errors_first = sorted(issues, key=lambda i: i.severity != ERROR)
    return errors_first
