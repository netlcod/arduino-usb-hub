import logging
import re
from pathlib import Path

from arduino_hub.usbhid.device_info import DeviceInfo

logger = logging.getLogger(__name__)

CDC_COMMENT_MARKER = "// CDC_GetInterface(&interfaces);"
CDC_DISABLED_FLAG = "-DCDC_DISABLED"


class USBPatcher:
    @staticmethod
    def patch_usb_core(core_path: Path) -> None:
        usb_core_file = core_path / "cores" / "arduino" / "USBCore.cpp"
        if not usb_core_file.exists():
            logger.error("USBCore.cpp not found: %s", usb_core_file)
            return

        content = usb_core_file.read_text(encoding="utf-8")

        if CDC_COMMENT_MARKER in content:
            logger.info("USB Core already patched (CDC disabled)")
            return

        content = re.sub(
            r"(CDC_GetInterface\(&interfaces\);)",
            r"// \1",
            content,
        )

        content = re.sub(
            r"(if \(CDC_ACM_INTERFACE == i\)\s*\n\s*)return CDC_Setup\(setup\);",
            r"\1// return CDC_Setup(setup);\n\t\treturn false;",
            content,
            flags=re.MULTILINE | re.DOTALL,
        )

        usb_core_file.write_text(content, encoding="utf-8")
        logger.info("USB Core patched (CDC disabled): %s", usb_core_file)

    @staticmethod
    def patch_boards_txt(core_path: Path, device: DeviceInfo) -> None:
        boards_file = core_path / "boards.txt"
        if not boards_file.exists():
            logger.error("boards.txt not found: %s", boards_file)
            return

        content = boards_file.read_text()

        vid = hex(device.vendor_id)
        pid = hex(device.product_id)

        if f"leonardo.build.vid={vid}" in content and f"leonardo.build.pid={pid}" in content:
            logger.info(
                "boards.txt already matches device %04X:%04X, skipping",
                device.vendor_id,
                device.product_id,
            )
            return

        content = re.sub(
            r"(leonardo\.build\.vid)\s*=\s*0x[0-9A-Fa-f]+",
            rf"\1={vid}",
            content,
        )
        content = re.sub(
            r"(leonardo\.build\.pid)\s*=\s*0x[0-9A-Fa-f]+",
            rf"\1={pid}",
            content,
        )
        content = re.sub(
            r'(leonardo\.build\.usb_product)\s*=\s*"([^"]*)"',
            rf'\1="{device.product_string}"',
            content,
        )

        if not re.search(
            r"^\s*leonardo\.build\.usb_manufacturer=",
            content,
            flags=re.MULTILINE,
        ):
            content = re.sub(
                r'(leonardo\.build\.usb_product=".*?")',
                rf'\1\nleonardo.build.usb_manufacturer="{device.manufacturer_string}"',
                content,
                flags=re.DOTALL,
            )
        else:
            content = re.sub(
                r'(leonardo\.build\.usb_manufacturer)\s*=\s*"([^"]*)"',
                rf'\1="{device.manufacturer_string}"',
                content,
            )

        if not re.search(
            rf"leonardo\.build\.extra_flags=\{{build\.usb_flags\}} {CDC_DISABLED_FLAG}",
            content,
            flags=re.MULTILINE,
        ):
            content = re.sub(
                r"(leonardo\.build\.extra_flags=\{build\.usb_flags\})",
                rf"\1 {CDC_DISABLED_FLAG}",
                content,
            )

        boards_file.write_text(content)
        logger.info(
            "boards.txt patched for %04X:%04X (%s)",
            device.vendor_id,
            device.product_id,
            device.product_string,
        )
