import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from arduino_hub.usbhid.device_info import DeviceInfo, TargetProfile
from arduino_hub.usbhid.hid_generator import (
    generate_hid_mapper_h,
    generate_hid_profile_h,
    generate_report_descriptor,
)

logger = logging.getLogger(__name__)

CDC_COMMENT_MARKER = "// CDC_GetInterface(&interfaces);"
CDC_DISABLED_FLAG = "-DCDC_DISABLED"
USB_EP_SIZE_FLAG = "-DUSB_EP_SIZE=16"
USB_CONFIG_POWER_FLAG = "-DUSB_CONFIG_POWER={max_power_ma}"

# HID interface policy: boot mouse (subclass 1 / protocol 2), applied
# to HID.cpp — deliberately not taken from the device JSON.
HID_POLICY_SUBCLASS = "HID_SUBCLASS_BOOT_INTERFACE"
HID_POLICY_PROTOCOL = "HID_PROTOCOL_MOUSE"

MOUSE_LIB_RELATIVE = Path("arduino-cli-data") / "user" / "libraries" / "Mouse" / "src"

_MOUSE_CPP_OLD_MOVE = (
    "void Mouse_::move(signed char x, signed char y, signed char wheel)\n"
    "{\n"
    "\tuint8_t m[4];\n"
    "\tm[0] = _buttons;\n"
    "\tm[1] = x;\n"
    "\tm[2] = y;\n"
    "\tm[3] = wheel;\n"
    "\tHID().SendReport(1,m,4);\n"
    "}"
)

_MOUSE_CPP_NEW_MOVE = (
    "void Mouse_::move(int16_t x, int16_t y, int16_t wheel, int16_t pan)\n"
    "{\n"
    "\tMouseState state = { _buttons, x, y, (int8_t)wheel, (int8_t)pan };\n"
    "\tuint8_t m[HID_REPORT_LENGTH] = {0};\n"
    "\tencode_output(state, m);\n"
    "\tHID().SendReport(HID_REPORT_ID, m, HID_REPORT_LENGTH);\n"
    "}"
)


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

        flags = (
            f"{CDC_DISABLED_FLAG} {USB_EP_SIZE_FLAG} "
            f"{USB_CONFIG_POWER_FLAG.format(max_power_ma=device.max_power_ma)}"
        )
        extra_flags_line = f"leonardo.build.extra_flags={{build.usb_flags}} {flags}"
        if not re.search(re.escape(extra_flags_line), content, flags=re.MULTILINE):
            content = re.sub(
                r"(leonardo\.build\.extra_flags=\{build\.usb_flags\})[^\r\n]*",
                extra_flags_line,
                content,
            )

        boards_file.write_text(content)
        logger.info(
            "boards.txt patched for %04X:%04X (%s)",
            device.vendor_id,
            device.product_id,
            device.product_string,
        )

    @staticmethod
    def patch_usbcore(core_path: Path, device: DeviceInfo) -> None:
        """Patch the device descriptor: bcdDevice and iSerialNumber."""
        usb_core_file = core_path / "cores" / "arduino" / "USBCore.cpp"
        if not usb_core_file.exists():
            logger.error("USBCore.cpp not found: %s", usb_core_file)
            return

        content = usb_core_file.read_text(encoding="utf-8")
        bcd = f"0x{device.bcd_device:X}"

        if f",{bcd},IMANUFACTURER,IPRODUCT,0,1)" in content:
            logger.info("USBCore.cpp already patched (bcdDevice=%s, iSerialNumber=0)", bcd)
            return

        content = re.sub(
            r"(D_DEVICE\([^)]*?,)0x100(,IMANUFACTURER,IPRODUCT,)ISERIAL(,1\))",
            lambda m: f"{m.group(1)}{bcd}{m.group(2)}0{m.group(3)}",
            content,
        )

        usb_core_file.write_text(content, encoding="utf-8")
        logger.info("USBCore.cpp patched: bcdDevice=%s, iSerialNumber=0", bcd)

    @staticmethod
    def patch_hid(core_path: Path, device: DeviceInfo) -> None:
        """Patch HID interface: bcdHID (HID.h) and subclass/protocol (HID.cpp)."""
        hid_dir = core_path / "libraries" / "HID" / "src"
        hid_h_file = hid_dir / "HID.h"
        hid_cpp_file = hid_dir / "HID.cpp"

        version_l = device.bcd_hid & 0xFF
        target_item = f"9, 0x21, 0x01, 0x{version_l:02X}, 0, 1, 0x22"
        if hid_h_file.exists():
            content = hid_h_file.read_text(encoding="utf-8")
            if re.search(rf"D_HIDREPORT\(length\) \{{ {re.escape(target_item)}", content):
                logger.info("HID.h already patched (bcdHID 0x%04X)", device.bcd_hid)
            else:
                content = re.sub(
                    r"(D_HIDREPORT\(length\) \{ 9, 0x21, 0x01,) 0x[0-9A-Fa-f]{2}(, 0, 1, 0x22)",
                    rf"\1 0x{version_l:02X}\2",
                    content,
                )
                hid_h_file.write_text(content, encoding="utf-8")
                logger.info("HID.h patched: bcdHID 0x%04X", device.bcd_hid)
        else:
            logger.error("HID.h not found: %s", hid_h_file)

        policy = f"{HID_POLICY_SUBCLASS}, {HID_POLICY_PROTOCOL}"
        if hid_cpp_file.exists():
            content = hid_cpp_file.read_text(encoding="utf-8")
            if policy in content:
                logger.info("HID.cpp already patched (boot mouse policy)")
            else:
                content = content.replace(
                    "HID_SUBCLASS_NONE, HID_PROTOCOL_NONE", policy
                )
                hid_cpp_file.write_text(content, encoding="utf-8")
                logger.info(
                    "HID.cpp patched: subclass/protocol = %s/%s",
                    HID_POLICY_SUBCLASS,
                    HID_POLICY_PROTOCOL,
                )
        else:
            logger.error("HID.cpp not found: %s", hid_cpp_file)

    @staticmethod
    def patch_mouse_library(
        lib_path: Path,
        source: DeviceInfo,
        target: TargetProfile,
    ) -> None:
        """Generate hid_profile.h/hid_mapper.h and patch the Mouse library.

        Mouse.cpp is only edited in place (includes + move()); it is never
        regenerated from scratch.
        """
        lib_path.mkdir(parents=True, exist_ok=True)

        descriptor = generate_report_descriptor(target)
        profile_file = lib_path / "hid_profile.h"
        mapper_file = lib_path / "hid_mapper.h"
        profile_file.write_text(
            generate_hid_profile_h(target, descriptor), encoding="utf-8"
        )
        mapper_file.write_text(
            generate_hid_mapper_h(source, target), encoding="utf-8"
        )
        logger.info(
            "Generated %s (descriptor %d bytes) and %s",
            profile_file.name,
            len(descriptor),
            mapper_file.name,
        )

        mouse_cpp_file = lib_path / "Mouse.cpp"
        if not mouse_cpp_file.exists():
            logger.error("Mouse.cpp not found: %s", mouse_cpp_file)
            return
        content = mouse_cpp_file.read_text(encoding="utf-8")

        if '#include "hid_profile.h"' not in content:
            content = content.replace(
                '#include "Mouse.h"',
                '#include "Mouse.h"\n#include "hid_profile.h"\n#include "hid_mapper.h"',
            )
            logger.info("Mouse.cpp: includes added")

        if "static HIDSubDescriptor node(HID_DESCRIPTOR" not in content:
            content = re.sub(
                r"static const uint8_t _hidReportDescriptor\[\] PROGMEM = \{.*?\};",
                "",
                content,
                count=1,
                flags=re.DOTALL,
            )
            content = content.replace(
                "static HIDSubDescriptor node(_hidReportDescriptor, sizeof(_hidReportDescriptor));",
                "static HIDSubDescriptor node(HID_DESCRIPTOR, sizeof(HID_DESCRIPTOR));",
            )
            logger.info("Mouse.cpp: descriptor replaced by HID_DESCRIPTOR")

        if "encode_output(state, m)" not in content:
            if _MOUSE_CPP_OLD_MOVE not in content:
                logger.warning("Mouse.cpp: move() body not found, patch skipped")
            else:
                content = content.replace(_MOUSE_CPP_OLD_MOVE, _MOUSE_CPP_NEW_MOVE)
                logger.info("Mouse.cpp: move() rewritten to encode->SendReport")

        if "Mouse_::buttons(uint16_t b)" not in content:
            content = content.replace("uint8_t b)", "uint16_t b)")
            logger.info("Mouse.cpp: buttons widened to uint16_t")

        mouse_cpp_file.write_text(content, encoding="utf-8")

        mouse_h_file = lib_path / "Mouse.h"
        if mouse_h_file.exists():
            content = mouse_h_file.read_text(encoding="utf-8")
            if "int16_t x, int16_t y, int16_t wheel" not in content:
                content = re.sub(
                    r"void move\(signed char x, signed char y, signed char wheel = 0\);",
                    "void move(int16_t x, int16_t y, int16_t wheel = 0, int16_t pan = 0);",
                    content,
                )
                mouse_h_file.write_text(content, encoding="utf-8")
                logger.info("Mouse.h: move() signature updated")

            if "uint16_t _buttons;" not in content:
                content = content.replace("uint8_t _buttons;", "uint16_t _buttons;")
                content = content.replace("uint8_t b", "uint16_t b")
                mouse_h_file.write_text(content, encoding="utf-8")
                logger.info("Mouse.h: buttons widened to uint16_t")
        else:
            logger.error("Mouse.h not found: %s", mouse_h_file)


def write_patch_manifest(
    base_dir: Path,
    device_name: str,
    target_name: str,
    patches: dict[str, str],
) -> None:
    """Write .build/patches.json describing the applied patch set."""
    build_dir = base_dir / ".build"
    build_dir.mkdir(parents=True, exist_ok=True)

    generated = {}
    for fname in ("hid_profile.h", "hid_mapper.h"):
        fpath = build_dir.parent / MOUSE_LIB_RELATIVE / fname
        if fpath.exists():
            generated[fname] = hashlib.sha256(fpath.read_bytes()).hexdigest()[:16]

    manifest = {
        "device": device_name,
        "target": target_name,
        "patched_at": datetime.now(timezone.utc).isoformat(),
        "patches": patches,
        "generated": generated,
    }
    manifest_file = build_dir / "patches.json"
    manifest_file.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    logger.info("Patch manifest written: %s", manifest_file)
