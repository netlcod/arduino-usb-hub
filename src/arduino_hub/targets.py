"""Target profiles: output HID report layouts for the Leonardo.

Target profiles live in profiles/targets/<name>.json and describe the
report the Leonardo will present to the PC (unlike source profiles in
profiles/sources/<name>.json, which describe what the receiver sends).
"""

import json
import logging
from pathlib import Path

from arduino_hub.exceptions import InvalidTargetError, TargetNotFoundError
from arduino_hub.usbhid.device_info import (
    AxisSpec,
    CapabilityProfile,
    CommandProfile,
    TargetProfile,
)
from arduino_hub.usbhid.hid_generator import compute_report_layout

logger = logging.getLogger(__name__)

TARGETS_DIR_NAME = "targets"
PROFILES_DIR_NAME = "profiles"

DEVICE_KINDS = ("mouse", "keyboard", "gamepad")
MAX_BUTTONS = 16  # MouseState buttons are uint16_t


def validate_target(profile: TargetProfile) -> None:
    """Validate a target profile against its own report layout.

    Catches silent descriptor/report desync (e.g. a hand-edited
    report_length or button count that no longer matches the axes).
    """
    if profile.device_kind not in DEVICE_KINDS:
        raise InvalidTargetError(
            f"Target '{profile.name}' has unknown device_kind "
            f"'{profile.device_kind}' (expected one of {DEVICE_KINDS})"
        )
    if profile.report_id < 1:
        raise InvalidTargetError(
            f"Target '{profile.name}' report_id must be >= 1, got {profile.report_id}"
        )
    if not (1 <= profile.buttons <= MAX_BUTTONS):
        raise InvalidTargetError(
            f"Target '{profile.name}' buttons {profile.buttons} out of range "
            f"1..{MAX_BUTTONS}"
        )
    usages = [a.usage for a in profile.axes]
    if len(usages) != len(set(usages)):
        raise InvalidTargetError(
            f"Target '{profile.name}' has duplicate axis usages: "
            f"{[hex(u) for u in sorted(usages)]}"
        )
    for a in profile.axes:
        if not (1 <= a.bits <= 16):
            raise InvalidTargetError(
                f"Target '{profile.name}' axis 0x{a.usage:04X} has {a.bits} bits "
                f"(expected 1..16)"
            )

    layout = compute_report_layout(
        profile.report_id, profile.report_length, profile.buttons, profile.axes
    )
    if profile.report_length != layout.payload_len:
        raise InvalidTargetError(
            f"Target '{profile.name}' report_length {profile.report_length} does "
            f"not match the computed report payload {layout.payload_len} "
            f"(buttons {profile.buttons}, axes {[a.bits for a in profile.axes]})"
        )


def _targets_dir(base_dir: Path) -> Path:
    return base_dir / PROFILES_DIR_NAME / TARGETS_DIR_NAME


def _fmt_hex(value: int) -> str:
    if value <= 0xFF:
        return f"0x{value:02X}"
    if value <= 0xFFFF:
        return f"0x{value:04X}"
    return f"0x{value:08X}"


def _parse_hex(value: str) -> int:
    return int(value, 16)


def _to_dict(profile: TargetProfile) -> dict:
    data = {
        "name": profile.name,
        "device_kind": profile.device_kind,
        "report_id": profile.report_id,
        "report_length": profile.report_length,
        "buttons": profile.buttons,
        "layout": profile.layout,
        "axes": [
            {
                "usage_page": _fmt_hex(a.usage_page),
                "usage": _fmt_hex(a.usage),
                "bits": a.bits,
                "logical_min": a.logical_min,
                "logical_max": a.logical_max,
                "relative": a.relative,
            }
            for a in profile.axes
        ],
    }
    data["command"] = {
        "enabled": profile.command.enabled,
        "transport": profile.command.transport,
        "queue_slots": profile.command.queue_slots,
    }
    data["capability"] = {"enabled": profile.capability.enabled}
    return data


def _from_dict(data: dict) -> TargetProfile:
    axes = [
        AxisSpec(
            usage_page=_parse_hex(a.get("usage_page", "0x01")),
            usage=_parse_hex(a["usage"]),
            bits=a.get("bits", 8),
            logical_min=a.get("logical_min", -127),
            logical_max=a.get("logical_max", 127),
            relative=a.get("relative", True),
        )
        for a in data.get("axes", [])
    ]
    cmd = data.get("command") or {}
    cap = data.get("capability") or {}
    return TargetProfile(
        name=data.get("name", ""),
        device_kind=data.get("device_kind", "mouse"),
        report_id=data.get("report_id", 1),
        report_length=data.get("report_length", 4),
        buttons=data.get("buttons", 3),
        layout=data.get("layout", "per_axis"),
        axes=axes,
        command=CommandProfile(
            enabled=bool(cmd.get("enabled", False)),
            transport=cmd.get("transport", "feature"),
            queue_slots=int(cmd.get("queue_slots", 4)),
        ),
        capability=CapabilityProfile(enabled=bool(cap.get("enabled", False))),
    )


def load_target(name: str, base_dir: Path) -> TargetProfile:
    """Load a target profile from profiles/targets/<name>.json."""
    path = _targets_dir(base_dir) / f"{name}.json"
    if not path.exists():
        raise TargetNotFoundError(
            f"Target profile '{name}' not found in {_targets_dir(base_dir)}. "
            f"Expected file: {path}"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    profile = _from_dict(data)
    validate_target(profile)
    return profile


def list_targets(base_dir: Path) -> list[str]:
    dir_path = _targets_dir(base_dir)
    if not dir_path.exists():
        return []
    return sorted(f.stem for f in dir_path.glob("*.json"))
