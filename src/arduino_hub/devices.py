import json
import logging
import re
from pathlib import Path

from arduino_hub.exceptions import DeviceNotFoundError
from arduino_hub.usbhid.device_info import DeviceInfo

logger = logging.getLogger(__name__)

DEVICES_DIR_NAME = "devices"


def _sanitize_filename(name: str) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    sanitized = re.sub(r"_+", "_", sanitized)
    return sanitized.strip("_").lower()


def _devices_dir(base_dir: Path) -> Path:
    return base_dir / DEVICES_DIR_NAME


def save(device: DeviceInfo, name: str, base_dir: Path) -> Path:
    dir_path = _devices_dir(base_dir)
    dir_path.mkdir(parents=True, exist_ok=True)

    filename = _sanitize_filename(name)
    file_path = dir_path / f"{filename}.json"

    data = {
        "name": name,
        "vendor_id": f"0x{device.vendor_id:04X}",
        "product_id": f"0x{device.product_id:04X}",
        "manufacturer_string": device.manufacturer_string,
        "product_string": device.product_string,
        "serial_number": device.serial_number or "",
    }

    file_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
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
            f"Run 'arduino-hub clone --name {name}' first."
        )

    file_path = matches[0]
    data = json.loads(file_path.read_text(encoding="utf-8"))

    return DeviceInfo(
        name=data["name"],
        vendor_id=int(data["vendor_id"], 16),
        product_id=int(data["product_id"], 16),
        manufacturer_string=data.get("manufacturer_string", ""),
        product_string=data.get("product_string", ""),
        serial_number=data.get("serial_number"),
        path=b"",
    )


def list_devices(base_dir: Path) -> list[str]:
    dir_path = _devices_dir(base_dir)
    if not dir_path.exists():
        return []
    return sorted(f.stem for f in dir_path.glob("*.json"))
