"""Target profile validation (R11) and source wire-order consistency."""

from pathlib import Path

import pytest

from arduino_hub.devices import load as load_device
from arduino_hub.exceptions import (
    InvalidProfileError,
    InvalidTargetError,
    TargetNotFoundError,
)
from arduino_hub.targets import load_target
from arduino_hub.usbhid.device_info import AxisSpec, DeviceInfo
from arduino_hub.usbhid.hid_generator import source_layout_warnings
from util import load_g305

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_valid_targets_pass_validation():
    for name in ("generic_3btn", "generic_5btn", "generic_16btn"):
        load_target(name, REPO_ROOT)  # must not raise


def test_target_wrong_report_length_rejected(tmp_path):
    bad = {
        "name": "bad",
        "report_id": 1,
        "report_length": 7,  # 3btn layout needs 4
        "buttons": 3,
        "layout": "arduino",
        "axes": [
            {"usage": "0x30", "bits": 8},
            {"usage": "0x31", "bits": 8},
            {"usage": "0x38", "bits": 8},
        ],
    }
    targets = tmp_path / "profiles" / "targets"
    targets.mkdir(parents=True)
    (targets / "bad.json").write_text(
        __import__("json").dumps(bad), encoding="utf-8"
    )
    with pytest.raises(InvalidTargetError):
        load_target("bad", tmp_path)


def test_target_bad_buttons_rejected(tmp_path):
    bad = {
        "name": "bad",
        "report_id": 1,
        "report_length": 4,
        "buttons": 17,  # MouseState buttons are uint16_t
        "layout": "arduino",
        "axes": [
            {"usage": "0x30", "bits": 8},
            {"usage": "0x31", "bits": 8},
            {"usage": "0x38", "bits": 8},
        ],
    }
    targets = tmp_path / "profiles" / "targets"
    targets.mkdir(parents=True)
    (targets / "bad.json").write_text(
        __import__("json").dumps(bad), encoding="utf-8"
    )
    with pytest.raises(InvalidTargetError):
        load_target("bad", tmp_path)


def test_target_unknown_device_kind_rejected(tmp_path):
    bad = {
        "name": "bad",
        "device_kind": "joystick",
        "report_id": 1,
        "report_length": 4,
        "buttons": 3,
        "layout": "arduino",
        "axes": [
            {"usage": "0x30", "bits": 8},
            {"usage": "0x31", "bits": 8},
            {"usage": "0x38", "bits": 8},
        ],
    }
    targets = tmp_path / "profiles" / "targets"
    targets.mkdir(parents=True)
    (targets / "bad.json").write_text(
        __import__("json").dumps(bad), encoding="utf-8"
    )
    with pytest.raises(InvalidTargetError):
        load_target("bad", tmp_path)


def test_g305_wire_order_override_is_visible():
    """The G305 quirk (wire order != descriptor order) must surface as a
    warning, not stay silent."""
    device = load_g305()
    warnings = source_layout_warnings(device)
    assert any("differs from descriptor order" in w for w in warnings)


def test_no_warning_when_orders_agree():
    """A profile whose wire order matches the descriptor order is clean."""
    device = load_g305()
    for a in device.axes:
        a.wire_order = a.data_index
    assert source_layout_warnings(device) == []


def test_wire_order_backward_compat(tmp_path):
    """Pre-wire_order profiles derive the wire sort key from data_index."""
    source = {
        "name": "old",
        "vendor_id": "0x046D",
        "product_id": "0xC53F",
        "hid_report_descriptors": [],
        "report_id": 2,
        "report_length": 9,
        "button_count": 16,
        "axes": [
            # swapped data_index: the old hand-fix for the X/Y quirk
            {"usage": "0x30", "bits": 16, "data_index": 16},
            {"usage": "0x31", "bits": 16, "data_index": 17},
            {"usage": "0x38", "bits": 8, "data_index": 18},
            {"usage": "0x0238", "bits": 8, "data_index": 19},
        ],
    }
    devices = tmp_path / "profiles" / "sources"
    devices.mkdir(parents=True)
    (devices / "old.json").write_text(
        __import__("json").dumps(source), encoding="utf-8"
    )
    device = load_device("old", tmp_path)
    assert [a.wire_order for a in device.axes] == [16, 17, 18, 19]
    # data_index order (16,17,18,19) == wire order -> no warning
    assert source_layout_warnings(device) == []


def test_duplicate_wire_order_rejected(tmp_path):
    """Duplicate wire_order values must fail loudly at generation time."""
    from arduino_hub.usbhid.hid_generator import generate_hid_mapper_h

    device = DeviceInfo(
        name="dup",
        report_id=2,
        report_length=9,
        button_count=16,
        axes=[
            AxisSpec(usage=0x30, bits=16, wire_order=0),
            AxisSpec(usage=0x31, bits=16, wire_order=0),
            AxisSpec(usage=0x38, bits=8, wire_order=1),
            AxisSpec(usage=0x0238, bits=8, wire_order=2),
        ],
    )
    with pytest.raises(InvalidProfileError):
        generate_hid_mapper_h(device, load_target("generic_3btn", REPO_ROOT))

# ── Identity profile validation (enumeration-identity-parity plan) ──

from arduino_hub.core.validation import (
    ERROR,
    WARN,
    has_errors,
    validate_identity,
)


def _device(**overrides):
    from arduino_hub.usbhid.device_info import DeviceInfo

    fields = dict(
        name="generic_mouse",
        vendor_id=0x1234,
        product_id=0x5678,
        manufacturer_string="Acme",
        product_string="Universal Mouse",
        bcd_device=0x0201,
        usb_version=0x0200,
        device_class=0x00,
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


def _fields(issues):
    return {i.field for i in issues}


def test_clean_profile_has_no_issues():
    assert validate_identity(_device()) == []


def test_empty_strings_are_errors():
    issues = validate_identity(_device(manufacturer_string="", product_string=" "))
    assert has_errors(issues)
    assert {"manufacturer", "product"} <= _fields(issues)


def test_quote_and_backslash_break_compilation():
    issues = validate_identity(_device(product_string='Weird"Mouse'))
    assert has_errors(issues)
    issues = validate_identity(_device(product_string="Back\\slash"))
    assert has_errors(issues)


def test_control_chars_rejected():
    issues = validate_identity(_device(product_string="Bad\x01Mouse"))
    assert has_errors(issues)
    assert any("control characters" in i.message for i in issues)


def test_overlong_string_warns_not_errors():
    issues = validate_identity(_device(product_string="M" * 200))
    assert not has_errors(issues)
    assert any("longer than 126" in i.message for i in issues)


def test_serial_string_rules():
    # Valid serial: clean.
    assert validate_identity(_device(serial_number="SN12345")) == []
    # Empty/absent serial: fine (iSerialNumber stays 0).
    assert validate_identity(_device(serial_number=None)) == []
    assert validate_identity(_device(serial_number="")) == []
    # Quote/backslash would break the generated hub_serial_string.h literal.
    issues = validate_identity(_device(serial_number='Bad"Serial'))
    assert has_errors(issues)
    assert "serial" in _fields(issues)
    issues = validate_identity(_device(serial_number="Back\\slash"))
    assert has_errors(issues)
    # Whitespace-only serial is effectively empty → ERROR.
    issues = validate_identity(_device(serial_number="   "))
    assert has_errors(issues)
    # Overlong serial: WARN only.
    issues = validate_identity(_device(serial_number="S" * 200))
    assert not has_errors(issues)
    assert any(i.field == "serial" and "longer than 126" in i.message for i in issues)


def test_ep0_outside_standard_sizes_errors():
    # A non-standard EP0 size bricks enumeration with no CDC fallback,
    # so it must fail fast before patch (ERROR, not WARN).
    issues = validate_identity(_device(ep0_max_packet_size=48))
    assert has_errors(issues)
    assert "ep0_max_packet_size" in _fields(issues)
    assert validate_identity(_device(ep0_max_packet_size=32)) == []


def test_ep_size_outside_standard_sizes_errors():
    # Interrupt EP banks map only {8, 16, 32, 64} (USB_EP_ALLOC).
    issues = validate_identity(_device(ep_max_packet_size=24))
    assert has_errors(issues)
    assert "ep_max_packet_size" in _fields(issues)
    for size in (8, 16, 32, 64):
        assert validate_identity(_device(ep_max_packet_size=size)) == []


def test_power_rules():
    out_of_range = validate_identity(_device(max_power_ma=600))
    assert any(i.field == "max_power_ma" and "1..500" in i.message for i in out_of_range)
    unrealistic = validate_identity(_device(max_power_ma=300))
    assert any("unrealistic" in i.message for i in unrealistic)
    assert validate_identity(_device(max_power_ma=100)) == []


def test_bcdusb_above_full_speed_warns():
    issues = validate_identity(_device(usb_version=0x0210))
    assert any("Full Speed" in i.message for i in issues)
    assert validate_identity(_device(usb_version=0x0110)) == []


def test_nonzero_device_class_warns():
    issues = validate_identity(_device(device_class=0xFF))
    assert any(i.field == "device_class" for i in issues)


def test_self_powered_bit_warns():
    issues = validate_identity(_device(bm_attributes=0xC0))
    assert any("self-powered" in i.message for i in issues)


def test_interval_below_one_ms_warns():
    issues = validate_identity(_device(ep_interval_ms=0))
    assert any(i.field == "ep_interval_ms" for i in issues)


def test_vendor_coherence_check():
    # Logitech VID with a foreign manufacturer string -> WARN.
    issues = validate_identity(
        _device(vendor_id=0x046D, manufacturer_string="Microsoft")
    )
    assert any(i.field == "vendor_id" for i in issues)

    # Matching vendor string is clean.
    assert validate_identity(
        _device(vendor_id=0x046D, manufacturer_string="Logitech")
    ) == []

    # Unknown VID never triggers.
    assert validate_identity(_device(vendor_id=0xFACE)) == []

    # Placeholder manufacturer strings are skipped.
    assert validate_identity(
        _device(vendor_id=0x046D, manufacturer_string="Unknown")
    ) == []


def test_legacy_minimal_profile_raises_invalid_profile(tmp_path):
    """A profile in the old minimal format (no hid_report_descriptors)
    is not 'not found' — it exists but is unusable."""
    import json

    from arduino_hub.devices import load as load_device
    from arduino_hub.exceptions import (
        DeviceNotFoundError,
        InvalidProfileError,
    )

    src = tmp_path / "profiles" / "sources"
    src.mkdir(parents=True)
    (src / "oldmouse.json").write_text(
        json.dumps(
            {
                "name": "oldmouse",
                "vendor_id": "0x046D",
                "product_id": "0xC53F",
                "manufacturer_string": "Logitech",
                "product_string": "USB Receiver",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(InvalidProfileError):
        load_device("oldmouse", tmp_path)
    # And a genuinely missing device still raises DeviceNotFoundError.
    with pytest.raises(DeviceNotFoundError):
        load_device("nosuchdevice", tmp_path)


def test_errors_sorted_first_and_g305_is_clean():
    from util import load_g305

    issues = validate_identity(load_g305())
    assert issues == []
