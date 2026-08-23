import hashlib
import json
import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from arduino_hub.usbhid.command_generator import (
    CMD_PAYLOAD_LEN,
    commands_header_name,
    generate_capability_blob,
    generate_command_descriptor,
    generate_commands_h,
    generate_hid_capability_blob_h,
    generate_hid_command_config_h,
    validate_command_profile,
)
from arduino_hub.usbhid.command_schema import CommandSchema
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
HUB_COMMAND_RELATIVE = Path("arduino-cli-data") / "user" / "libraries" / "HubCommand"
HUB_COMMAND_SOURCE_DIR = Path("libraries") / "HubCommand"
PC_CLIENT_TARGET_DIR = Path("pc_client") / "target"

_HID_COMMAND_CONFIG_NAME = "hid_command_config.h"
_HID_CAPABILITY_BLOB_NAME = "hid_capability_blob.h"

# Patch versioning for the command channel core edits (R1). The marker
# lets a patched core distinguish "already at current version" from
# "patched by an older patcher", so content upgrades (e.g. the atomic
# ring read) are re-applied instead of silently skipped.
HID_H_PATCH_VERSION = 2
HID_CPP_PATCH_VERSION = 2
_PATCH_VERSION_RE = re.compile(r"// HUB_PATCH_VERSION (\d+)")


def _get_patch_version(content: str) -> int:
    m = _PATCH_VERSION_RE.search(content)
    return int(m.group(1)) if m else 0

HID_CPP_MARKER = "int HID_::availableReportPackets"
HID_CPP_OUT_MARKER = "int HID_::availableOutReport"
HID_CPP_EP_OUT_MARKER = "EP_TYPE_INTERRUPT_OUT"
HID_CPP_GETIFACE_MARKER = "USB_ENDPOINT_OUT(pluggedEndpoint + 1)"
HID_H_MARKER = '#include "hid_command_config.h"'
HID_H_EPTYPE_MARKER = "uint8_t epType[2]"
HID_H_DESC_MARKER = "EndpointDescriptor  out;"
HID_H_OUT_API_MARKER = "int availableOutReport();"

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



class IdentityPatcher:
    """USB identity / fingerprint patches (device-specific)."""

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

class LibraryPatcher:
    """Mouse library + HubCommand install + PC client headers."""

    @staticmethod
    def patch_mouse_library(
        lib_path: Path,
        source: DeviceInfo,
        target: TargetProfile,
    ) -> None:
        """Generate hid_profile.h/hid_mapper.h and patch the Mouse library.

        The generated HID_DESCRIPTOR is the target-specific mouse block
        plus, when the command channel is enabled, the vendor-defined
        command collection (write report + optional capability report).

        Mouse.cpp is only edited in place (includes + move()); it is never
        regenerated from scratch.
        """
        lib_path.mkdir(parents=True, exist_ok=True)

        descriptor = generate_report_descriptor(target) + generate_command_descriptor(
            target.command, target.capability
        )
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

    # ── PC → Arduino command channel ─────────────────────────────

    def install_hub_command_library(
        base_dir: Path,
        schema: CommandSchema,
    ) -> Path:
        """Copy the HubCommand device library and generate opcode defs."""
        src_dir = base_dir / HUB_COMMAND_SOURCE_DIR
        if not src_dir.exists():
            logger.error("HubCommand source library not found: %s", src_dir)
            return Path()
        dst_dir = base_dir / HUB_COMMAND_RELATIVE
        shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
        (dst_dir / "src" / commands_header_name(schema)).write_text(
            generate_commands_h(schema), encoding="utf-8"
        )
        logger.info("HubCommand library installed: %s", dst_dir)
        return dst_dir

    @staticmethod
    def write_pc_client_headers(
        base_dir: Path,
        target: TargetProfile,
        schema: CommandSchema,
    ) -> None:
        """Generate the shared headers for the PC C++ client."""
        gen_dir = base_dir / PC_CLIENT_TARGET_DIR
        gen_dir.mkdir(parents=True, exist_ok=True)
        (gen_dir / commands_header_name(schema)).write_text(
            generate_commands_h(schema), encoding="utf-8"
        )
        (gen_dir / "command_channel.h").write_text(
            generate_hid_command_config_h(
                target.command, target.capability, schema
            ),
            encoding="utf-8",
        )
        logger.info("PC client headers written: %s", gen_dir)

class CommandChannelPatcher:
    """PC -> Arduino command channel: config, capability blob, core patch."""

    @staticmethod
    def write_hid_command_config(
        core_path: Path,
        target: TargetProfile,
        schema: CommandSchema,
    ) -> None:
        """Generate hid_command_config.h into the core HID library.

        Written on every patch run: the file carries the per-target
        wiring (enable/transport/queue slots), the code patch in
        HID.cpp/.h is applied once and compiled out via #if guards
        when the channel is disabled.
        """
        validate_command_profile(target.command)
        schema.validate(CMD_PAYLOAD_LEN)
        hid_dir = core_path / "libraries" / "HID" / "src"
        if not hid_dir.exists():
            logger.error("HID library not found: %s", hid_dir)
            return
        config_file = hid_dir / _HID_COMMAND_CONFIG_NAME
        config_file.write_text(
            generate_hid_command_config_h(
                target.command, target.capability, schema
            ),
            encoding="utf-8",
        )
        logger.info(
            "Generated %s (command=%s, transport=%s, slots=%d, capability=%s)",
            config_file.name,
            target.command.enabled,
            target.command.transport if target.command.enabled else "off",
            target.command.queue_slots,
            target.capability.enabled and target.command.enabled,
        )

    @staticmethod
    def write_hid_capability_blob(
        core_path: Path,
        target: TargetProfile,
        schema: CommandSchema,
    ) -> None:
        """Generate hid_capability_blob.h into the core HID library.

        The ready-made capability payload (bytes from
        generate_capability_blob) lives in this header; the patched
        HID.cpp only #includes it and serves HID_CAPABILITY_BLOB on
        GET_REPORT(Feature).
        """
        hid_dir = core_path / "libraries" / "HID" / "src"
        if not hid_dir.exists():
            logger.error("HID library not found: %s", hid_dir)
            return
        blob_file = hid_dir / _HID_CAPABILITY_BLOB_NAME
        blob_file.write_text(
            generate_hid_capability_blob_h(target.command, schema),
            encoding="utf-8",
        )
        logger.info("Generated %s", blob_file.name)

    @staticmethod
    def patch_hid_command_core(core_path: Path) -> None:
        """Transport-only command channel in the core HID library.

        Applies versioned, idempotent edits to HID.h/HID.cpp:
        - HID.h: config include, epType[2], HIDDescriptor OUT endpoint,
          ring receive API + members, interrupt-OUT read API;
        - HID.cpp: SET_REPORT(Output|Feature) → bounded copy into the
          ring (drop-new when full), GET_REPORT(Feature) capability
          readback, read API implementations, and — only when
          transport=interrupt_out — a second (OUT) endpoint so hid_write
          routes to the interrupt OUT endpoint instead of SET_REPORT.

        Each file carries a `// HUB_PATCH_VERSION N` marker; content
        produced by an older patcher is upgraded in place (see
        `_upgrade_hid_cpp`). No command semantics here: the core only
        buffers raw reports.
        """
        hid_dir = core_path / "libraries" / "HID" / "src"
        hid_h_file = hid_dir / "HID.h"
        hid_cpp_file = hid_dir / "HID.cpp"

        if hid_h_file.exists():
            content = hid_h_file.read_text(encoding="utf-8")
            if _get_patch_version(content) >= HID_H_PATCH_VERSION:
                logger.info(
                    "HID.h already patched at v%d, skipping", HID_H_PATCH_VERSION
                )
            else:
                content = CommandChannelPatcher._patch_hid_h(content)
                hid_h_file.write_text(content, encoding="utf-8")
        else:
            logger.error("HID.h not found: %s", hid_h_file)

        if hid_cpp_file.exists():
            content = hid_cpp_file.read_text(encoding="utf-8")
            if _get_patch_version(content) >= HID_CPP_PATCH_VERSION:
                logger.info(
                    "HID.cpp already patched at v%d, skipping", HID_CPP_PATCH_VERSION
                )
            else:
                content = CommandChannelPatcher._patch_hid_cpp(content)
                hid_cpp_file.write_text(content, encoding="utf-8")
        else:
            logger.error("HID.cpp not found: %s", hid_cpp_file)

    @staticmethod
    def _stamp_version(content: str, anchor: str, version: int) -> str:
        """Insert or bump the `// HUB_PATCH_VERSION N` marker."""
        marker = f"// HUB_PATCH_VERSION {version}"
        if _PATCH_VERSION_RE.search(content):
            return _PATCH_VERSION_RE.sub(marker, content, count=1)
        return content.replace(anchor, anchor + marker + "\n", 1)

    @staticmethod
    def _patch_hid_h(content: str) -> str:
        """Apply all HID.h command-channel edits + version marker."""
        if HID_H_MARKER not in content:
            content = content.replace(
                '#include "PluggableUSB.h"\n',
                '#include "PluggableUSB.h"\n'
                '#include "hid_command_config.h"\n',
                1,
            )
            logger.info("HID.h: hid_command_config.h include added")

        if HID_H_EPTYPE_MARKER not in content:
            content = content.replace(
                "  uint8_t epType[1];\n",
                "  uint8_t epType[2];\n",
                1,
            )
            logger.info("HID.h: epType widened to [2]")

        if HID_H_DESC_MARKER not in content:
            content = content.replace(
                "typedef struct \n"
                "{\n"
                "  InterfaceDescriptor hid;\n"
                "  HIDDescDescriptor   desc;\n"
                "  EndpointDescriptor  in;\n"
                "} HIDDescriptor;\n",
                "typedef struct \n"
                "{\n"
                "  InterfaceDescriptor hid;\n"
                "  HIDDescDescriptor   desc;\n"
                "  EndpointDescriptor  in;\n"
                "#if HID_COMMAND_ENABLED && HID_COMMAND_TRANSPORT == HID_COMMAND_TRANSPORT_INTERRUPT_OUT\n"
                "  EndpointDescriptor  out;\n"
                "#endif\n"
                "} HIDDescriptor;\n",
                1,
            )
            logger.info("HID.h: HIDDescriptor OUT endpoint added")

        if "int availableReportPackets();" not in content:
            content = content.replace(
                "  int SendReport(uint8_t id, const void* data, int len);\n"
                "  void AppendDescriptor(HIDSubDescriptor* node);\n",
                "  int SendReport(uint8_t id, const void* data, int len);\n"
                "  void AppendDescriptor(HIDSubDescriptor* node);\n"
                "\n"
                "#if HID_COMMAND_ENABLED\n"
                "  // PC -> Arduino command channel (transport only).\n"
                "  int availableReportPackets();\n"
                "  int readReportPacket(uint8_t* dst, uint8_t maxlen);\n"
                "#endif\n",
                1,
            )
            logger.info("HID.h: ring receive API added")

        if HID_H_OUT_API_MARKER not in content:
            content = content.replace(
                "  int readReportPacket(uint8_t* dst, uint8_t maxlen);\n",
                "  int readReportPacket(uint8_t* dst, uint8_t maxlen);\n"
                "#if HID_COMMAND_TRANSPORT == HID_COMMAND_TRANSPORT_INTERRUPT_OUT\n"
                "  int availableOutReport();\n"
                "  int readOutReport(uint8_t* dst, uint8_t maxlen);\n"
                "#endif\n",
                1,
            )
            logger.info("HID.h: interrupt-OUT read API added")

        if "_cmdRing[HID_COMMAND_QUEUE_SLOTS]" not in content:
            content = content.replace(
                "  uint8_t protocol;\n"
                "  uint8_t idle;\n"
                "};\n",
                "  uint8_t protocol;\n"
                "  uint8_t idle;\n"
                "\n"
                "#if HID_COMMAND_ENABLED\n"
                "  uint8_t _cmdRing[HID_COMMAND_QUEUE_SLOTS][HID_COMMAND_PAYLOAD_LEN];\n"
                "  volatile uint8_t _cmdHead;\n"
                "  volatile uint8_t _cmdTail;\n"
                "  volatile uint8_t _cmdCount;\n"
                "#endif\n"
                "};\n",
                1,
            )
            logger.info("HID.h: ring buffer members added")

        return CommandChannelPatcher._stamp_version(content, "#define HID_h\n", HID_H_PATCH_VERSION)

    @staticmethod
    def _upgrade_hid_cpp(content: str) -> str:
        """Upgrade HID.cpp content patched by an older patcher (v1).

        Older cores miss: the atomic ring read (cli/sei), the
        SET_REPORT zero-padding and control-transfer failure check, and
        the generated capability blob header (a macro-based blob was
        baked into HID.cpp instead).
        """
        # Atomic ring read: check-copy-decrement against the control ISR.
        if "// _cmdCount is shared with the SET_REPORT control ISR" not in content:
            old = (
                "int HID_::readReportPacket(uint8_t* dst, uint8_t maxlen)\n"
                "{\n"
                "\tif (_cmdCount == 0)\n"
                "\t\treturn 0;\n"
                "\tuint8_t len = HID_COMMAND_PAYLOAD_LEN;\n"
                "\tif (len > maxlen)\n"
                "\t\tlen = maxlen;\n"
                "\tmemcpy(dst, _cmdRing[_cmdTail], len);\n"
                "\t_cmdTail = (_cmdTail + 1) % HID_COMMAND_QUEUE_SLOTS;\n"
                "\t_cmdCount--;\n"
                "\treturn len;\n"
                "}\n"
            )
            new = (
                "int HID_::readReportPacket(uint8_t* dst, uint8_t maxlen)\n"
                "{\n"
                "\t// _cmdCount is shared with the SET_REPORT control ISR, so\n"
                "\t// the check-copy-decrement must be atomic against it.\n"
                "\tcli();\n"
                "\tif (_cmdCount == 0) {\n"
                "\t\tsei();\n"
                "\t\treturn 0;\n"
                "\t}\n"
                "\tuint8_t len = HID_COMMAND_PAYLOAD_LEN;\n"
                "\tif (len > maxlen)\n"
                "\t\tlen = maxlen;\n"
                "\tmemcpy(dst, _cmdRing[_cmdTail], len);\n"
                "\t_cmdTail = (_cmdTail + 1) % HID_COMMAND_QUEUE_SLOTS;\n"
                "\t_cmdCount--;\n"
                "\tsei();\n"
                "\treturn len;\n"
                "}\n"
            )
            if old in content:
                content = content.replace(old, new, 1)
                logger.info("HID.cpp: ring read upgraded (atomic cli/sei)")

        # SET_REPORT: a failed control transfer must not enqueue garbage.
        if "if (!USB_RecvControl(scratch, length))" not in content:
            old = "\t\t\t\t\tUSB_RecvControl(scratch, length);\n"
            new = (
                "\t\t\t\t\tif (!USB_RecvControl(scratch, length))\n"
                "\t\t\t\t\t\treturn false;\n"
            )
            if old in content:
                content = content.replace(old, new, 1)
                logger.info("HID.cpp: SET_REPORT failure check added")

        # SET_REPORT: zero-pad the ring slot before the copy.
        if "memset(_cmdRing[_cmdHead], 0" not in content:
            old = "\t\t\t\t\t\tmemcpy(_cmdRing[_cmdHead], payload, payloadLen);\n"
            new = (
                "\t\t\t\t\t\tmemset(_cmdRing[_cmdHead], 0, HID_COMMAND_PAYLOAD_LEN);\n"
                "\t\t\t\t\t\tmemcpy(_cmdRing[_cmdHead], payload, payloadLen);\n"
            )
            if old in content:
                content = content.replace(old, new, 1)
                logger.info("HID.cpp: SET_REPORT slot zero-padding added")

        # Capability: macro-based blob → generated hid_capability_blob.h.
        if "_capabilityReport[] PROGMEM" in content:
            old_blob = re.compile(
                r"#if HID_COMMAND_ENABLED && HID_CAPABILITY_ENABLED\n"
                r"static const uint8_t _capabilityReport\[\] PROGMEM = \{[^}]*\};\n"
                r"#endif\n",
                re.DOTALL,
            )
            content = old_blob.sub(
                "#if HID_COMMAND_ENABLED && HID_CAPABILITY_ENABLED\n"
                '#include "hid_capability_blob.h"\n'
                "#endif\n",
                content,
                count=1,
            )
            content = content.replace("_capabilityReport", "HID_CAPABILITY_BLOB")
            logger.info("HID.cpp: capability blob moved to generated header")

        return content

    @staticmethod
    def _patch_hid_cpp(content: str) -> str:
        """Apply all HID.cpp command-channel edits + version marker."""
        content = CommandChannelPatcher._upgrade_hid_cpp(content)

        # Capability blob: serve the generated header, never inline the
        # structure (R3: single source of truth in command_generator).
        if '#include "hid_capability_blob.h"' not in content:
            content = content.replace(
                "HID_& HID()\n{",
                "#if HID_COMMAND_ENABLED && HID_CAPABILITY_ENABLED\n"
                '#include "hid_capability_blob.h"\n'
                "#endif\n"
                "\n"
                "HID_& HID()\n{",
                1,
            )
            logger.info("HID.cpp: capability blob header included")

        if "setup.wValueL == HID_CAPABILITY_REPORT_ID" not in content:
            content = content.replace(
                "\t\tif (request == HID_GET_REPORT) {\n"
                "\t\t\t// TODO: HID_GetReport();\n"
                "\t\t\treturn true;\n"
                "\t\t}",
                "\t\tif (request == HID_GET_REPORT) {\n"
                "#if HID_COMMAND_ENABLED && HID_CAPABILITY_ENABLED\n"
                "\t\t\tif (setup.wValueH == HID_REPORT_TYPE_FEATURE &&\n"
                "\t\t\t    setup.wValueL == HID_CAPABILITY_REPORT_ID) {\n"
                "\t\t\t\treturn USB_SendControl(TRANSFER_PGM, HID_CAPABILITY_BLOB,\n"
                "\t\t\t\t                        sizeof(HID_CAPABILITY_BLOB)) > 0;\n"
                "\t\t\t}\n"
                "#endif\n"
                "\t\t\t// Unsupported report: stall instead of sending an\n"
                "\t\t\t// empty zero-length packet (previous behaviour).\n"
                "\t\t\treturn false;\n"
                "\t\t}",
                1,
            )
            logger.info("HID.cpp: GET_REPORT capability readback added")

        if "USB_RecvControl(scratch, length)" not in content:
            content = content.replace(
                "\t\tif (request == HID_SET_REPORT)\n"
                "\t\t{\n"
                "\t\t\t//uint8_t reportID = setup.wValueL;\n"
                "\t\t\t//uint16_t length = setup.wLength;\n"
                "\t\t\t//uint8_t data[length];\n"
                "\t\t\t// Make sure to not read more data than USB_EP_SIZE.\n"
                "\t\t\t// You can read multiple times through a loop.\n"
                "\t\t\t// The first byte (may!) contain the reportID on a multreport.\n"
                "\t\t\t//USB_RecvControl(data, length);\n"
                "\t\t}",
                "\t\tif (request == HID_SET_REPORT)\n"
                "\t\t{\n"
                "#if HID_COMMAND_ENABLED\n"
                "\t\t\t// Command channel: accept Output and Feature reports carrying\n"
                "\t\t\t// the command report. Windows sends the full report buffer\n"
                "\t\t\t// (including the report id byte) as the data stage, so\n"
                "\t\t\t// wLength == TOTAL_LEN (16), not PAYLOAD_LEN (15). Only a\n"
                "\t\t\t// bounded copy into the ring happens here; commands are\n"
                "\t\t\t// consumed from loop() via readReportPacket().\n"
                "\t\t\tif (setup.wValueH == HID_REPORT_TYPE_OUTPUT ||\n"
                "\t\t\t    setup.wValueH == HID_REPORT_TYPE_FEATURE)\n"
                "\t\t\t{\n"
                "\t\t\t\tuint16_t length = setup.wLength;\n"
                "\t\t\t\tif (length > 0 && length <= HID_COMMAND_TOTAL_LEN) {\n"
                "\t\t\t\t\tuint8_t scratch[HID_COMMAND_TOTAL_LEN];\n"
                "\t\t\t\t\tif (!USB_RecvControl(scratch, length))\n"
                "\t\t\t\t\t\treturn false;\n"
                "\t\t\t\t\tconst uint8_t* payload = scratch;\n"
                "\t\t\t\t\tuint8_t payloadLen = length;\n"
                "\t\t\t\t\tuint8_t reportId = setup.wValueL;\n"
                "\t\t\t\t\tif (reportId == 0) {\n"
                "\t\t\t\t\t\t// Report id carried in the first data byte.\n"
                "\t\t\t\t\t\tif (scratch[0] != HID_COMMAND_REPORT_ID) {\n"
                "\t\t\t\t\t\t\treturn false;\n"
                "\t\t\t\t\t\t}\n"
                "\t\t\t\t\t\tpayload = scratch + 1;\n"
                "\t\t\t\t\t\tpayloadLen = length - 1;\n"
                "\t\t\t\t\t} else if (reportId == HID_COMMAND_REPORT_ID &&\n"
                "\t\t\t\t\t           length == HID_COMMAND_TOTAL_LEN) {\n"
                "\t\t\t\t\t\t// Report id duplicated in the data stage.\n"
                "\t\t\t\t\t\tif (scratch[0] == HID_COMMAND_REPORT_ID) {\n"
                "\t\t\t\t\t\t\tpayload = scratch + 1;\n"
                "\t\t\t\t\t\t\tpayloadLen = length - 1;\n"
                "\t\t\t\t\t\t} else {\n"
                "\t\t\t\t\t\t\tpayloadLen = HID_COMMAND_PAYLOAD_LEN;\n"
                "\t\t\t\t\t\t}\n"
                "\t\t\t\t\t} else if (reportId != HID_COMMAND_REPORT_ID) {\n"
                "\t\t\t\t\t\treturn false;\n"
                "\t\t\t\t\t}\n"
                "\t\t\t\t\tif (payloadLen <= HID_COMMAND_PAYLOAD_LEN) {\n"
                "\t\t\t\t\t\tif (_cmdCount < HID_COMMAND_QUEUE_SLOTS) {\n"
                "\t\t\t\t\t\t\t// Zero-pad the slot: a data stage shorter than the\n"
                "\t\t\t\t\t\t\t// full payload would otherwise leave stale bytes\n"
                "\t\t\t\t\t\t\t// from the slot's previous occupant at the tail.\n"
                "\t\t\t\t\t\t\tmemset(_cmdRing[_cmdHead], 0, HID_COMMAND_PAYLOAD_LEN);\n"
                "\t\t\t\t\t\t\tmemcpy(_cmdRing[_cmdHead], payload, payloadLen);\n"
                "\t\t\t\t\t\t\t_cmdHead = (_cmdHead + 1) % HID_COMMAND_QUEUE_SLOTS;\n"
                "\t\t\t\t\t\t\t_cmdCount++;\n"
                "\t\t\t\t\t\t}\n"
                "\t\t\t\t\t\treturn true;\n"
                "\t\t\t\t\t}\n"
                "\t\t\t\t}\n"
                "\t\t\t\treturn false;\n"
                "\t\t\t}\n"
                "#endif\n"
                "\t\t}",
                1,
            )
            logger.info("HID.cpp: SET_REPORT bounded ring copy added")

        # Constructor: claim a second endpoint (OUT) only for
        # interrupt_out; ring buffer init is transport-independent.
        # Split into granular edits so re-patching over an already
        # ring-init'ed core (older patcher) still adds the endpoint.
        if "PluggableUSBModule(1, 1, epType)" in content:
            content = content.replace(
                "HID_::HID_(void) : PluggableUSBModule(1, 1, epType),\n",
                "HID_::HID_(void) : PluggableUSBModule(\n"
                "#if HID_COMMAND_ENABLED && HID_COMMAND_TRANSPORT == HID_COMMAND_TRANSPORT_INTERRUPT_OUT\n"
                "    2,\n"
                "#else\n"
                "    1,\n"
                "#endif\n"
                "    1, epType),\n",
                1,
            )
            logger.info("HID.cpp: constructor endpoint count patched")

        if "_cmdHead = 0;" not in content:
            content = content.replace(
                "\tepType[0] = EP_TYPE_INTERRUPT_IN;\n"
                "\tPluggableUSB().plug(this);\n",
                "\tepType[0] = EP_TYPE_INTERRUPT_IN;\n"
                "#if HID_COMMAND_ENABLED\n"
                "\t_cmdHead = 0;\n"
                "\t_cmdTail = 0;\n"
                "\t_cmdCount = 0;\n"
                "#endif\n"
                "\tPluggableUSB().plug(this);\n",
                1,
            )
            logger.info("HID.cpp: constructor ring buffer init added")

        if HID_CPP_EP_OUT_MARKER not in content:
            content = content.replace(
                "\tepType[0] = EP_TYPE_INTERRUPT_IN;\n",
                "\tepType[0] = EP_TYPE_INTERRUPT_IN;\n"
                "#if HID_COMMAND_ENABLED && HID_COMMAND_TRANSPORT == HID_COMMAND_TRANSPORT_INTERRUPT_OUT\n"
                "\tepType[1] = EP_TYPE_INTERRUPT_OUT;\n"
                "#endif\n",
                1,
            )
            logger.info("HID.cpp: constructor OUT endpoint added")

        # Interface descriptor: two endpoints (IN + OUT) only for
        # interrupt_out.
        if HID_CPP_GETIFACE_MARKER not in content:
            content = content.replace(
                "int HID_::getInterface(uint8_t* interfaceCount)\n"
                "{\n"
                "\t*interfaceCount += 1; // uses 1\n"
                "\tHIDDescriptor hidInterface = {\n"
                "\t\tD_INTERFACE(pluggedInterface, 1, USB_DEVICE_CLASS_HUMAN_INTERFACE, HID_SUBCLASS_BOOT_INTERFACE, HID_PROTOCOL_MOUSE),\n"
                "\t\tD_HIDREPORT(descriptorSize),\n"
                "\t\tD_ENDPOINT(USB_ENDPOINT_IN(pluggedEndpoint), USB_ENDPOINT_TYPE_INTERRUPT, USB_EP_SIZE, 0x01)\n"
                "\t};\n"
                "\treturn USB_SendControl(0, &hidInterface, sizeof(hidInterface));\n"
                "}\n",
                "int HID_::getInterface(uint8_t* interfaceCount)\n"
                "{\n"
                "\t*interfaceCount += 1; // uses 1\n"
                "\tHIDDescriptor hidInterface = {\n"
                "\t\tD_INTERFACE(pluggedInterface,\n"
                "#if HID_COMMAND_ENABLED && HID_COMMAND_TRANSPORT == HID_COMMAND_TRANSPORT_INTERRUPT_OUT\n"
                "\t\t            2,\n"
                "#else\n"
                "\t\t            1,\n"
                "#endif\n"
                "\t\t            USB_DEVICE_CLASS_HUMAN_INTERFACE, HID_SUBCLASS_BOOT_INTERFACE, HID_PROTOCOL_MOUSE),\n"
                "\t\tD_HIDREPORT(descriptorSize),\n"
                "\t\tD_ENDPOINT(USB_ENDPOINT_IN(pluggedEndpoint), USB_ENDPOINT_TYPE_INTERRUPT, USB_EP_SIZE, 0x01)\n"
                "#if HID_COMMAND_ENABLED && HID_COMMAND_TRANSPORT == HID_COMMAND_TRANSPORT_INTERRUPT_OUT\n"
                "\t\t,\n"
                "\t\tD_ENDPOINT(USB_ENDPOINT_OUT(pluggedEndpoint + 1), USB_ENDPOINT_TYPE_INTERRUPT, USB_EP_SIZE, 0x01)\n"
                "#endif\n"
                "\t};\n"
                "\treturn USB_SendControl(0, &hidInterface, sizeof(hidInterface));\n"
                "}\n",
                1,
            )
            logger.info("HID.cpp: getInterface OUT endpoint added")

        # Ring read implementation (SET_REPORT transports).
        if HID_CPP_MARKER not in content:
            content = content.replace(
                "#endif /* if defined(USBCON) */",
                "#if HID_COMMAND_ENABLED\n"
                "\n"
                "int HID_::availableReportPackets(void)\n"
                "{\n"
                "\treturn _cmdCount;\n"
                "}\n"
                "\n"
                "int HID_::readReportPacket(uint8_t* dst, uint8_t maxlen)\n"
                "{\n"
                "\t// _cmdCount is shared with the SET_REPORT control ISR, so\n"
                "\t// the check-copy-decrement must be atomic against it.\n"
                "\tcli();\n"
                "\tif (_cmdCount == 0) {\n"
                "\t\tsei();\n"
                "\t\treturn 0;\n"
                "\t}\n"
                "\tuint8_t len = HID_COMMAND_PAYLOAD_LEN;\n"
                "\tif (len > maxlen)\n"
                "\t\tlen = maxlen;\n"
                "\tmemcpy(dst, _cmdRing[_cmdTail], len);\n"
                "\t_cmdTail = (_cmdTail + 1) % HID_COMMAND_QUEUE_SLOTS;\n"
                "\t_cmdCount--;\n"
                "\tsei();\n"
                "\treturn len;\n"
                "}\n"
                "\n"
                "#endif\n"
                "\n"
                "#endif /* if defined(USBCON) */",
                1,
            )
            logger.info("HID.cpp: ring read API implemented")

        # Interrupt OUT read implementation. The report id travels as
        # the FIRST data byte on the wire (unlike SET_REPORT where it
        # sits in wValueL): USB_Recv reads 16 bytes [0x03][payload 15].
        if HID_CPP_OUT_MARKER not in content:
            content = content.replace(
                "#endif /* if defined(USBCON) */",
                "#if HID_COMMAND_ENABLED && HID_COMMAND_TRANSPORT == HID_COMMAND_TRANSPORT_INTERRUPT_OUT\n"
                "\n"
                "int HID_::availableOutReport(void)\n"
                "{\n"
                "\treturn USB_Available(pluggedEndpoint + 1);\n"
                "}\n"
                "\n"
                "int HID_::readOutReport(uint8_t* dst, uint8_t maxlen)\n"
                "{\n"
                "\t// Zero-init so a short packet cannot leak stale tail bytes.\n"
                "\tuint8_t scratch[HID_COMMAND_TOTAL_LEN] = {0};\n"
                "\tint n = USB_Recv(pluggedEndpoint + 1, scratch, HID_COMMAND_TOTAL_LEN);\n"
                "\tif (n <= 0)\n"
                "\t\treturn 0;\n"
                "\tif (scratch[0] != HID_COMMAND_REPORT_ID)\n"
                "\t\treturn 0;\n"
                "\tuint8_t payloadLen = (uint8_t)n - 1;\n"
                "\tif (payloadLen > HID_COMMAND_PAYLOAD_LEN)\n"
                "\t\tpayloadLen = HID_COMMAND_PAYLOAD_LEN;\n"
                "\tif (payloadLen > maxlen)\n"
                "\t\tpayloadLen = maxlen;\n"
                "\tmemcpy(dst, scratch + 1, payloadLen);\n"
                "\treturn payloadLen;\n"
                "}\n"
                "\n"
                "#endif\n"
                "\n"
                "#endif /* if defined(USBCON) */",
                1,
            )
            logger.info("HID.cpp: interrupt-OUT read API implemented")

        return CommandChannelPatcher._stamp_version(
            content, '#include "HID.h"\n', HID_CPP_PATCH_VERSION
        )

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
