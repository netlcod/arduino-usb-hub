"""Shared test helpers."""

from pathlib import Path

from arduino_hub.devices import load as load_device
from arduino_hub.usbhid.device_info import DeviceInfo

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def load_g305() -> DeviceInfo:
    """Load the committed G305 source profile fixture."""
    return load_device("g305", FIXTURES_DIR)
