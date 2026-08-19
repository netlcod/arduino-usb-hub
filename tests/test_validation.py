"""Target profile validation (R11) and source wire-order consistency."""

from pathlib import Path

import pytest

from arduino_hub.devices import load as load_device
from arduino_hub.exceptions import InvalidTargetError, TargetNotFoundError
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
    targets = tmp_path / "targets"
    targets.mkdir()
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
    targets = tmp_path / "targets"
    targets.mkdir()
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
    targets = tmp_path / "targets"
    targets.mkdir()
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
    devices = tmp_path / "devices"
    devices.mkdir()
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
    with pytest.raises(ValueError):
        generate_hid_mapper_h(device, load_target("generic_3btn", REPO_ROOT))
