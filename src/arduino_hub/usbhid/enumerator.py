import logging

import hid

from arduino_hub.exceptions import DeviceCloneError, NoHIDDevicesError
from arduino_hub.usbhid.device_info import DeviceInfo

logger = logging.getLogger(__name__)


class HIDEnumerator:
    @staticmethod
    def enumerate() -> list[DeviceInfo]:
        raw = hid.enumerate()
        if not raw:
            return []

        devices: list[DeviceInfo] = []
        for dev in raw:
            devices.append(DeviceInfo(
                vendor_id=dev["vendor_id"],
                product_id=dev["product_id"],
                manufacturer_string=dev.get("manufacturer_string", ""),
                product_string=dev.get("product_string", ""),
                serial_number=dev.get("serial_number"),
                path=dev["path"],
            ))
        return devices

    @staticmethod
    def select_interactive(devices: list[DeviceInfo]) -> DeviceInfo:
        if not devices:
            raise NoHIDDevicesError("No HID devices found")

        for i, dev in enumerate(devices):
            print(
                f"{i}: {dev.manufacturer_string} {dev.product_string} "
                f"(VID: {dev.vendor_id:04X}, PID: {dev.product_id:04X})"
            )

        try:
            idx = int(input("Enter device index: "))
        except ValueError:
            raise DeviceCloneError("Invalid index (not a number)")

        if idx < 0 or idx >= len(devices):
            raise DeviceCloneError(f"Index out of range [0, {len(devices) - 1}]")

        return HIDEnumerator._open_and_read_strings(devices[idx])

    @staticmethod
    def _open_and_read_strings(device: DeviceInfo) -> DeviceInfo:
        try:
            dev = hid.device()
            dev.open_path(device.path)
            try:
                manufacturer = dev.get_manufacturer_string()
                product = dev.get_product_string()
                serial = dev.get_serial_number_string()
            finally:
                dev.close()
        except OSError as e:
            raise DeviceCloneError(f"Failed to open device: {e}") from e

        logger.info("Manufacturer: %s", manufacturer)
        logger.info("Product: %s", product)
        logger.info("Serial: %s", serial)

        device.manufacturer_string = manufacturer
        device.product_string = product
        device.serial_number = serial
        device.name = product or f"{device.vendor_id:04X}:{device.product_id:04X}"
        return device

    @staticmethod
    def open_by_vid_pid(vendor_id: int, product_id: int) -> DeviceInfo | None:
        raw = hid.enumerate(vendor_id, product_id)
        if not raw:
            return None
        dev_info = DeviceInfo(
            vendor_id=vendor_id,
            product_id=product_id,
            manufacturer_string=raw[0].get("manufacturer_string", ""),
            product_string=raw[0].get("product_string", ""),
            serial_number=raw[0].get("serial_number"),
            path=raw[0]["path"],
        )
        return HIDEnumerator._open_and_read_strings(dev_info)
