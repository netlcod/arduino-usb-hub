"""Target profiles: output HID report layouts for the Leonardo.

Target profiles live in targets/<name>.json and describe the report
the Leonardo will present to the PC (unlike source profiles in
devices/<name>.json, which describe what the receiver sends).
"""

import json
import logging
from pathlib import Path

from arduino_hub.exceptions import TargetNotFoundError
from arduino_hub.usbhid.device_info import AxisSpec, TargetProfile

logger = logging.getLogger(__name__)

TARGETS_DIR_NAME = "targets"


def _targets_dir(base_dir: Path) -> Path:
    return base_dir / TARGETS_DIR_NAME


def _fmt_hex(value: int) -> str:
    if value <= 0xFF:
        return f"0x{value:02X}"
    if value <= 0xFFFF:
        return f"0x{value:04X}"
    return f"0x{value:08X}"


def _parse_hex(value: str) -> int:
    return int(value, 16)


def _to_dict(profile: TargetProfile) -> dict:
    return {
        "name": profile.name,
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
    return TargetProfile(
        name=data.get("name", ""),
        report_id=data.get("report_id", 1),
        report_length=data.get("report_length", 4),
        buttons=data.get("buttons", 3),
        layout=data.get("layout", "per_axis"),
        axes=axes,
    )


def load_target(name: str, base_dir: Path) -> TargetProfile:
    """Load a target profile from targets/<name>.json."""
    path = _targets_dir(base_dir) / f"{name}.json"
    if not path.exists():
        raise TargetNotFoundError(
            f"Target profile '{name}' not found in {_targets_dir(base_dir)}. "
            f"Expected file: {path}"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    return _from_dict(data)


def list_targets(base_dir: Path) -> list[str]:
    dir_path = _targets_dir(base_dir)
    if not dir_path.exists():
        return []
    return sorted(f.stem for f in dir_path.glob("*.json"))
