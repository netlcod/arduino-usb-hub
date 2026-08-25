import difflib
import hashlib
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from arduino_hub.exceptions import PatchError
from arduino_hub.core.validation import ValidationIssue, summarize
from arduino_hub.usbhid.command_generator import (
    CMD_PAYLOAD_LEN,
    commands_header_name,
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
USB_VERSION_FLAG = "-DUSB_VERSION=0x{usb_version:04X}"
USB_CONFIG_ATTRIBUTES_FLAG = "-DUSB_CONFIG_ATTRIBUTES=0x{bm_attributes:02X}"
HID_EP_INTERVAL_FLAG = "-DHID_EP_INTERVAL=0x{ep_interval_ms:02X}"
USB_EP0_MAX_PACKET_FLAG = "-DUSB_EP0_MAX_PACKET={ep0_max_packet_size}"

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
#
# v3: identity parity additions — HID_EP_INTERVAL macro in D_ENDPOINT,
# spec-correct GET_IDLE/GET_PROTOCOL answers (USB_SendControl instead
# of TODO stubs).
HID_H_PATCH_VERSION = 2
HID_CPP_PATCH_VERSION = 3
USBCORE_H_MARKER = "#ifndef USB_CONFIG_ATTRIBUTES"
USBCORE_H_VERSION_MARKER = "// HUB_PATCH_VERSION 1"
_PATCH_VERSION_RE = re.compile(r"// HUB_PATCH_VERSION (\d+)")

_D_DEVICE_RE = re.compile(
    r"(D_DEVICE\(0x[0-9A-Fa-f]{2},0x[0-9A-Fa-f]{2},0x[0-9A-Fa-f]{2},)"
    r"\d+"
    r"(,USB_VID,USB_PID,)0x[0-9A-Fa-f]+"
    r"(,IMANUFACTURER,IPRODUCT,)(?:ISERIAL|0)"
    r"(,1\))"
)
_D_HIDREPORT_RE = re.compile(
    r"(D_HIDREPORT\(length\) \{ 9, 0x21, )"
    r"0x[0-9A-Fa-f]{2}"
    r"(, )"
    r"0x[0-9A-Fa-f]{2}"
    r"(, )"
    r"(?:0x[0-9A-Fa-f]{2}|\d+)"
    r"(, 1, 0x22)"
)


def _get_patch_version(content: str) -> int:
    m = _PATCH_VERSION_RE.search(content)
    return int(m.group(1)) if m else 0


def _atomic_write_text(path: Path, content: str) -> None:
    """Write via a sibling temp file + os.replace.

    A crash mid-write can never leave a truncated core/library file
    behind (the AVR core is restored only by a full re-setup).
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


@dataclass
class FileEdit:
    """A single computed file change (not yet written)."""

    path: Path
    description: str
    original: str
    updated: str

    @property
    def changed(self) -> bool:
        return self.original != self.updated


def _read_or_empty(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def apply_edits(edits: list[FileEdit]) -> int:
    """Persist changed edits atomically. Returns number of files written."""
    written = 0
    for edit in edits:
        if not edit.changed:
            continue
        edit.path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(edit.path, edit.updated)
        logger.info("Patched %s (%s)", edit.path, edit.description)
        written += 1
    unchanged = len(edits) - written
    logger.info("Patch summary: %d file(s) changed, %d already up to date", written, unchanged)
    return written


def render_edits_diff(edits: list[FileEdit]) -> str:
    """Unified diffs for all changed edits (dry-run output)."""
    chunks = []
    for edit in edits:
        if not edit.changed:
            continue
        diff = difflib.unified_diff(
            edit.original.splitlines(keepends=True),
            edit.updated.splitlines(keepends=True),
            fromfile=f"{edit.path} (current)",
            tofile=f"{edit.path} (after patch)",
        )
        chunks.append(f"# {edit.description}\n" + "".join(diff))
    return "\n".join(chunks)


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
    """USB identity / fingerprint patches (device-specific).

    Every edit is available twice: a pure ``*_content`` transform
    (str -> str, raises PatchError on structural surprises) and a thin
    public wrapper that applies it to disk atomically.
    :func:`build_patch_edits` reuses the same transforms for dry-run.
    """

    @staticmethod
    def _usb_core_disable_cdc(content: str) -> str:
        if CDC_COMMENT_MARKER in content:
            return content
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
        return content

    @staticmethod
    def patch_usb_core(core_path: Path) -> None:
        _apply_transform(
            core_path / "cores" / "arduino" / "USBCore.cpp",
            IdentityPatcher._usb_core_disable_cdc,
            "CDC disabled",
        )

    @staticmethod
    def _boards_txt_content(content: str, device: DeviceInfo) -> str:
        """Rewrite the leonardo identity block; always explicit.

        Values are rebuilt on every run even when they match the stock
        core (the always-explicit principle), so a profile edit is
        picked up without depending on VID/PID change detection.
        """
        vid = f"0x{device.vendor_id:04X}"
        pid = f"0x{device.product_id:04X}"

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

        interval_ms = max(1, int(device.ep_interval_ms))
        flags = " ".join(
            [
                CDC_DISABLED_FLAG,
                USB_EP_SIZE_FLAG,
                USB_CONFIG_POWER_FLAG.format(max_power_ma=device.max_power_ma),
                USB_VERSION_FLAG.format(usb_version=device.usb_version),
                USB_CONFIG_ATTRIBUTES_FLAG.format(bm_attributes=device.bm_attributes),
                HID_EP_INTERVAL_FLAG.format(ep_interval_ms=interval_ms),
                USB_EP0_MAX_PACKET_FLAG.format(
                    ep0_max_packet_size=int(device.ep0_max_packet_size)
                ),
            ]
        )
        extra_flags_line = f"leonardo.build.extra_flags={{build.usb_flags}} {flags}"
        if not re.search(re.escape(extra_flags_line), content, flags=re.MULTILINE):
            content, count = re.subn(
                r"(leonardo\.build\.extra_flags=\{build\.usb_flags\})[^\r\n]*",
                extra_flags_line,
                content,
            )
            if count == 0:
                raise PatchError(
                    "boards.txt: 'leonardo.build.extra_flags={build.usb_flags}' "
                    "line not found. Re-run 'arduino-hub setup' to restore the "
                    "stock core."
                )
        return content

    @staticmethod
    def patch_boards_txt(core_path: Path, device: DeviceInfo) -> None:
        _apply_transform(
            core_path / "boards.txt",
            lambda c: IdentityPatcher._boards_txt_content(c, device),
            f"VID/PID {device.vendor_id:04X}:{device.product_id:04X}, "
            f"strings, identity flags",
        )

    @staticmethod
    def _usbcore_ep0_alloc_content(content: str) -> str:
        """Make EP0 bank size configurable AND make control transfers work.

        Two coupled fixes, each independently idempotent:

        1. Allocation: InitEP(0,...) routes through USB_EP0_ALLOC so the
           hardware FIFO matches the declared bMaxPacketSize0.

        2. SendControl bank-full release (root cause of the invisible-
           device failure): stock code releases the FIFO every *64*
           bytes via a hardcoded ``(_cmark + 1) & 0x3F`` mask. With a
           32-byte bank, byte 33 is written into an already-full FIFO
           and the next WaitForINOrOUT() spins forever inside the
           control ISR — device freezes mid-enumeration (config
           descriptor is 41 bytes: 18-byte device descriptor reads
           fine, config kills it). Releasing every USB_EP0_MAX_PACKET
           bytes keeps all chunk sizes legal (config 41 = 32+9, HID
           report 77 = 32+32+13).
        """
        if "USB_EP0_ALLOC" not in content:
            anchor = "#define EP_SINGLE_16 0x12"
            if anchor not in content:
                raise PatchError(
                    "USBCore.cpp: EP_SINGLE_16 define not found; cannot insert "
                    "the USB_EP0_ALLOC block. Re-run 'arduino-hub setup'."
                )
            additions = (
                "#define EP_SINGLE_8 0x02\n"
                "#define EP_SINGLE_32 0x22\n"
                "// HUB_PATCH: EP0 hardware allocation follows the declared\n"
                "// bMaxPacketSize0 (boards.txt -> -DUSB_EP0_MAX_PACKET=N).\n"
                "#ifndef USB_EP0_MAX_PACKET\n"
                "#define USB_EP0_MAX_PACKET 64\n"
                "#endif\n"
                "#if USB_EP0_MAX_PACKET == 8\n"
                "#define USB_EP0_ALLOC EP_SINGLE_8\n"
                "#elif USB_EP0_MAX_PACKET == 16\n"
                "#define USB_EP0_ALLOC EP_SINGLE_16\n"
                "#elif USB_EP0_MAX_PACKET == 32\n"
                "#define USB_EP0_ALLOC EP_SINGLE_32\n"
                "#else\n"
                "#define USB_EP0_ALLOC EP_SINGLE_64\n"
                "#endif\n"
            )
            content = content.replace(anchor, anchor + "\n" + additions, 1)

            old_call = "InitEP(0,EP_TYPE_CONTROL,EP_SINGLE_64)"
            if old_call not in content:
                raise PatchError(
                    "USBCore.cpp: InitEP(0,...) call not found; cannot route EP0 "
                    "allocation through USB_EP0_ALLOC. Re-run 'arduino-hub setup'."
                )
            content = content.replace(
                old_call, "InitEP(0,EP_TYPE_CONTROL,USB_EP0_ALLOC)", 1
            )

        if "% USB_EP0_MAX_PACKET" not in content:
            old_mask = "if (!((_cmark + 1) & 0x3F))"
            if old_mask not in content:
                raise PatchError(
                    "USBCore.cpp: stock SendControl bank-release line not "
                    "found; cannot route it through USB_EP0_MAX_PACKET. "
                    "Re-run 'arduino-hub setup'."
                )
            content = content.replace(
                old_mask,
                "if (!((_cmark + 1) % USB_EP0_MAX_PACKET))",
                1,
            )

        return content

    @staticmethod
    def _usbcore_descriptor_content(content: str, device: DeviceInfo) -> str:
        """Patch the device descriptor: EP0 size, bcdDevice, iSerialNumber.

        Both D_DEVICE branches (CDC_ENABLED / CDC_DISABLED) are
        rewritten; the pipeline always disables CDC first, so only the
        second one compiles. Handles stock (ISERIAL/0x100/64) and
        previously-patched forms alike. The declared EP0 size must be
        backed by a matching hardware allocation — see
        :meth:`_usbcore_ep0_alloc_content`.
        """
        ep0 = int(device.ep0_max_packet_size)
        bcd = f"0x{device.bcd_device:X}"

        expected = f",{ep0},USB_VID,USB_PID,{bcd},IMANUFACTURER,IPRODUCT,0,1)"
        if expected not in content:
            def repl(m: re.Match[str]) -> str:
                return (
                    f"{m.group(1)}{ep0}{m.group(2)}{bcd}{m.group(3)}0{m.group(4)}"
                )

            updated, count = _D_DEVICE_RE.subn(repl, content)
            if count == 0:
                raise PatchError(
                    "USBCore.cpp: D_DEVICE(...) not found in a recognizable form; "
                    "cannot patch EP0/bcdDevice/iSerialNumber. Re-run "
                    "'arduino-hub setup' to restore the stock core."
                )
            content = updated
            logger.debug("USBCore.cpp: %d D_DEVICE occurrence(s) rewritten", count)

        return IdentityPatcher._usbcore_ep0_alloc_content(content)

    @staticmethod
    def patch_usbcore(core_path: Path, device: DeviceInfo) -> None:
        _apply_transform(
            core_path / "cores" / "arduino" / "USBCore.cpp",
            lambda c: IdentityPatcher._usbcore_descriptor_content(c, device),
            f"EP0={device.ep0_max_packet_size}, bcdDevice=0x{device.bcd_device:X}, iSerialNumber 0",
        )

    @staticmethod
    def _usbc_h_attributes_content(content: str) -> str:
        """Make D_CONFIG bmAttributes overridable via -DUSB_CONFIG_ATTRIBUTES."""
        if USBCORE_H_MARKER in content:
            if "USB_CONFIG_ATTRIBUTES, USB_CONFIG_POWER_MA(USB_CONFIG_POWER)" not in content:
                raise PatchError(
                    "USBCore.h: attributes guard is present but the D_CONFIG "
                    "body is not routed through USB_CONFIG_ATTRIBUTES; the "
                    "core is in an unexpected state. Re-run "
                    "'arduino-hub setup'."
                )
            return content
        guard = (
            f"{USBCORE_H_VERSION_MARKER}\n"
            "// Configuration attributes are overridable per cloned\n"
            "// device: boards.txt passes -DUSB_CONFIG_ATTRIBUTES=0xNN.\n"
            "#ifndef USB_CONFIG_ATTRIBUTES\n"
            "#define USB_CONFIG_ATTRIBUTES "
            "(USB_CONFIG_BUS_POWERED | USB_CONFIG_REMOTE_WAKEUP)\n"
            "#endif\n"
        )
        anchor = "#define D_CONFIG(_totalLength,_interfaces) \\"
        if anchor not in content:
            raise PatchError(
                "USBCore.h: D_CONFIG macro not found; cannot insert the "
                "attributes guard. Re-run 'arduino-hub setup'."
            )
        content = content.replace(anchor, guard + "\n" + anchor, 1)

        body_old = (
            "USB_CONFIG_BUS_POWERED | USB_CONFIG_REMOTE_WAKEUP, "
            "USB_CONFIG_POWER_MA(USB_CONFIG_POWER)"
        )
        if body_old not in content:
            raise PatchError(
                "USBCore.h: D_CONFIG body does not match the expected "
                "stock form; cannot route bmAttributes through "
                "USB_CONFIG_ATTRIBUTES."
            )
        return content.replace(
            body_old,
            "USB_CONFIG_ATTRIBUTES, USB_CONFIG_POWER_MA(USB_CONFIG_POWER)",
            1,
        )

    @staticmethod
    def patch_usbc_h(core_path: Path) -> None:
        _apply_transform(
            core_path / "cores" / "arduino" / "USBCore.h",
            IdentityPatcher._usbc_h_attributes_content,
            "bmAttributes overridable (USB_CONFIG_ATTRIBUTES)",
        )

    @staticmethod
    def _hid_h_identity_content(content: str, device: DeviceInfo) -> str:
        """Rewrite the D_HIDREPORT literal: bcdHID (both bytes) + country.

        The macro follows the HID spec wire layout — byte 2 is the LOW
        bcdHID byte and byte 4 is bCountryCode (the neighbouring
        HIDDescDescriptor struct mislabels them as addr/versionH).
        Earlier patcher versions wrote the low byte into the HIGH
        position, producing 0x1101 on the wire instead of 0x0111; this
        transform also migrates such cores.
        """
        low = device.bcd_hid & 0xFF
        high = (device.bcd_hid >> 8) & 0xFF
        country = device.country_code & 0xFF

        expected = (
            f"D_HIDREPORT(length) {{ 9, 0x21, 0x{low:02X}, 0x{high:02X}, "
            f"0x{country:02X}, 1, 0x22"
        )
        if expected in content:
            return content

        def repl(m: re.Match[str]) -> str:
            return (
                f"{m.group(1)}0x{low:02X}"
                f"{m.group(2)}0x{high:02X}"
                f"{m.group(3)}0x{country:02X}"
                f"{m.group(4)}"
            )

        updated, count = _D_HIDREPORT_RE.subn(repl, content)
        if count == 0:
            raise PatchError(
                "HID.h: D_HIDREPORT(...) not found in a recognizable form; "
                "cannot patch bcdHID/country code. Re-run 'arduino-hub setup' "
                "to restore the stock core."
            )
        return updated

    @staticmethod
    def _hid_cpp_policy_content(content: str) -> str:
        policy = f"{HID_POLICY_SUBCLASS}, {HID_POLICY_PROTOCOL}"
        if policy in content:
            return content
        old = "HID_SUBCLASS_NONE, HID_PROTOCOL_NONE"
        if old not in content:
            raise PatchError(
                "HID.cpp: interface descriptor policy anchor not found; "
                "cannot apply the boot mouse policy. Re-run "
                "'arduino-hub setup'."
            )
        return content.replace(old, policy)

    @staticmethod
    def patch_hid(core_path: Path, device: DeviceInfo) -> None:
        """Patch HID interface: bcdHID+country (HID.h), boot policy (HID.cpp)."""
        hid_dir = core_path / "libraries" / "HID" / "src"
        _apply_transform(
            hid_dir / "HID.h",
            lambda c: IdentityPatcher._hid_h_identity_content(c, device),
            f"bcdHID 0x{device.bcd_hid:04X}, country 0x{device.country_code:02X}",
        )
        _apply_transform(
            hid_dir / "HID.cpp",
            IdentityPatcher._hid_cpp_policy_content,
            "subclass 1 / protocol 2 (boot mouse policy)",
        )


def _apply_transform(path: Path, transform, description: str) -> None:
    """Read a file, apply a content transform, write back if changed."""
    if not path.exists():
        raise PatchError(f"{path} not found. Re-run 'arduino-hub setup'.")
    original = path.read_text(encoding="utf-8")
    updated = transform(original)
    if updated != original:
        _atomic_write_text(path, updated)
        logger.info("%s patched: %s", path.name, description)
    else:
        logger.info("%s already up to date (%s)", path.name, description)

class LibraryPatcher:
    """Mouse library + HubCommand install + PC client headers."""

    @staticmethod
    def _profile_and_mapper_texts(
        source: DeviceInfo, target: TargetProfile
    ) -> tuple[str, str]:
        descriptor = generate_report_descriptor(target) + generate_command_descriptor(
            target.command, target.capability
        )
        profile_text = generate_hid_profile_h(target, descriptor)
        mapper_text = generate_hid_mapper_h(source, target)
        return profile_text, mapper_text

    @staticmethod
    def _mouse_cpp_content(content: str) -> str:
        """Marker-guarded Mouse.cpp edits: includes, descriptor, move()."""
        if '#include "hid_profile.h"' not in content:
            content = content.replace(
                '#include "Mouse.h"',
                '#include "Mouse.h"\n#include "hid_profile.h"\n#include "hid_mapper.h"',
            )

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

        if "encode_output(state, m)" not in content:
            if _MOUSE_CPP_OLD_MOVE not in content:
                logger.warning("Mouse.cpp: move() body not found, patch skipped")
            else:
                content = content.replace(_MOUSE_CPP_OLD_MOVE, _MOUSE_CPP_NEW_MOVE)

        if "Mouse_::buttons(uint16_t b)" not in content:
            content = content.replace("uint8_t b)", "uint16_t b)")

        return content

    @staticmethod
    def _mouse_h_content(content: str) -> str:
        """Marker-guarded Mouse.h edits: move() signature, uint16_t buttons."""
        if "int16_t x, int16_t y, int16_t wheel" not in content:
            content = re.sub(
                r"void move\(signed char x, signed char y, signed char wheel = 0\);",
                "void move(int16_t x, int16_t y, int16_t wheel = 0, int16_t pan = 0);",
                content,
            )
        if "uint16_t _buttons;" not in content:
            content = content.replace("uint8_t _buttons;", "uint16_t _buttons;")
            content = content.replace("uint8_t b", "uint16_t b")
        return content

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

        profile_text, mapper_text = LibraryPatcher._profile_and_mapper_texts(
            source, target
        )
        profile_file = lib_path / "hid_profile.h"
        mapper_file = lib_path / "hid_mapper.h"
        if (
            not profile_file.exists()
            or profile_file.read_text(encoding="utf-8") != profile_text
        ):
            _atomic_write_text(profile_file, profile_text)
        if _read_or_empty(mapper_file) != mapper_text:
            _atomic_write_text(mapper_file, mapper_text)
        logger.info(
            "Generated %s and %s", profile_file.name, mapper_file.name
        )

        mouse_cpp_file = lib_path / "Mouse.cpp"
        if not mouse_cpp_file.exists():
            raise PatchError(f"Mouse.cpp not found: {mouse_cpp_file}")
        _apply_transform(
            mouse_cpp_file,
            lambda c: LibraryPatcher._mouse_cpp_content(c),
            "includes + HID_DESCRIPTOR + encode->SendReport + uint16_t buttons",
        )

        mouse_h_file = lib_path / "Mouse.h"
        if mouse_h_file.exists():
            _apply_transform(
                mouse_h_file,
                LibraryPatcher._mouse_h_content,
                "move(int16_t ...) signature, uint16_t buttons",
            )
        else:
            raise PatchError(f"Mouse.h not found: {mouse_h_file}")

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
        _atomic_write_text(
            dst_dir / "src" / commands_header_name(schema),
            generate_commands_h(schema),
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
        _atomic_write_text(
            gen_dir / commands_header_name(schema),
            generate_commands_h(schema),
        )
        _atomic_write_text(
            gen_dir / "command_channel.h",
            generate_hid_command_config_h(target.command, target.capability, schema),
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
            raise PatchError(f"HID library not found: {hid_dir}")
        config_file = hid_dir / _HID_COMMAND_CONFIG_NAME
        _atomic_write_text(
            config_file,
            generate_hid_command_config_h(target.command, target.capability, schema),
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
            raise PatchError(f"HID library not found: {hid_dir}")
        blob_file = hid_dir / _HID_CAPABILITY_BLOB_NAME
        _atomic_write_text(
            blob_file,
            generate_hid_capability_blob_h(target.command, schema),
        )
        logger.info("Generated %s", blob_file.name)

    @staticmethod
    def _updated_hid_h(content: str) -> tuple[str, bool]:
        """Version-gated HID.h transform: (new content, changed)."""
        if _get_patch_version(content) >= HID_H_PATCH_VERSION:
            return content, False
        return CommandChannelPatcher._patch_hid_h(content), True

    @staticmethod
    def _updated_hid_cpp(content: str) -> tuple[str, bool]:
        """Version-gated HID.cpp transform: (new content, changed)."""
        if _get_patch_version(content) >= HID_CPP_PATCH_VERSION:
            return content, False
        return CommandChannelPatcher._patch_hid_cpp(content), True

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

        _apply_transform(
            hid_h_file,
            CommandChannelPatcher._updated_hid_h_content,
            "command channel declarations",
        )
        _apply_transform(
            hid_cpp_file,
            CommandChannelPatcher._updated_hid_cpp_content,
            "command channel core (SET_REPORT ring, capability, interval, class requests)",
        )

    @staticmethod
    def _updated_hid_h_content(content: str) -> str:
        updated, _ = CommandChannelPatcher._updated_hid_h(content)
        return updated

    @staticmethod
    def _updated_hid_cpp_content(content: str) -> str:
        updated, _ = CommandChannelPatcher._updated_hid_cpp(content)
        return updated

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

        # ── v3 identity parity ────────────────────────────────────
        # Interrupt endpoint polling interval (bInterval) comes from
        # boards.txt (-DHID_EP_INTERVAL=0xNN); the define must exist
        # even without the flag so the core stays self-contained.
        if "#ifndef HID_EP_INTERVAL" not in content:
            content = content.replace(
                '#include "HID.h"',
                '#include "HID.h"\n'
                "\n"
                "// Interrupt endpoint polling interval (bInterval),\n"
                "// overridable per cloned device via boards.txt\n"
                "// -DHID_EP_INTERVAL=0xNN.\n"
                "#ifndef HID_EP_INTERVAL\n"
                "#define HID_EP_INTERVAL 0x01\n"
                "#endif",
                1,
            )

        # Every interrupt-endpoint descriptor (IN and, once added below,
        # OUT) references the macro instead of a hardcoded 1 ms.
        content = content.replace(
            "USB_ENDPOINT_TYPE_INTERRUPT, USB_EP_SIZE, 0x01)",
            "USB_ENDPOINT_TYPE_INTERRUPT, USB_EP_SIZE, HID_EP_INTERVAL)",
        )

        # Spec-correct answers for the remaining class requests: hosts
        # that probe them currently get an empty packet (GET_PROTOCOL)
        # or a stall (GET_IDLE).
        if "USB_SendControl(0, &protocol, 1)" not in content:
            content = content.replace(
                "\t\tif (request == HID_GET_PROTOCOL) {\n"
                "\t\t\t// TODO: Send8(protocol);\n"
                "\t\t\treturn true;\n"
                "\t\t}",
                "\t\tif (request == HID_GET_PROTOCOL) {\n"
                "\t\t\t// Spec answer: report the active protocol\n"
                "\t\t\t// (boot=0 / report=1).\n"
                "\t\t\treturn USB_SendControl(0, &protocol, 1) > 0;\n"
                "\t\t}",
                1,
            )
        if "USB_SendControl(0, &idle, 1)" not in content:
            content = content.replace(
                "\t\tif (request == HID_GET_IDLE) {\n"
                "\t\t\t// TODO: Send8(idle);\n"
                "\t\t}",
                "\t\tif (request == HID_GET_IDLE) {\n"
                "\t\t\t// Spec answer: the stored idle rate instead of a\n"
                "\t\t\t// stall.\n"
                "\t\t\treturn USB_SendControl(0, &idle, 1) > 0;\n"
                "\t\t}",
                1,
            )
        # ──────────────────────────────────────────────────────────

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
                "\t\tD_ENDPOINT(USB_ENDPOINT_IN(pluggedEndpoint), USB_ENDPOINT_TYPE_INTERRUPT, USB_EP_SIZE, HID_EP_INTERVAL)\n"
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
                "\t\tD_ENDPOINT(USB_ENDPOINT_IN(pluggedEndpoint), USB_ENDPOINT_TYPE_INTERRUPT, USB_EP_SIZE, HID_EP_INTERVAL)\n"
                "#if HID_COMMAND_ENABLED && HID_COMMAND_TRANSPORT == HID_COMMAND_TRANSPORT_INTERRUPT_OUT\n"
                "\t\t,\n"
                "\t\tD_ENDPOINT(USB_ENDPOINT_OUT(pluggedEndpoint + 1), USB_ENDPOINT_TYPE_INTERRUPT, USB_EP_SIZE, HID_EP_INTERVAL)\n"
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

def build_patch_edits(
    base_dir: Path,
    core_path: Path,
    device: DeviceInfo,
    target: TargetProfile,
    schema: CommandSchema,
) -> list[FileEdit]:
    """Compute every file change of a patch run entirely in memory.

    Reads the current state of each target file and applies the same
    transforms the public patch_* wrappers use, so ``apply_edits`` and
    dry-run diffs always agree with a real run.
    """
    edits: list[FileEdit] = []

    def add_transform(path: Path, description: str, transform) -> None:
        original = _read_or_empty(path)
        edits.append(
            FileEdit(
                path=path,
                description=description,
                original=original,
                updated=transform(original),
            )
        )

    def add_content(path: Path, description: str, updated: str) -> None:
        edits.append(
            FileEdit(
                path=path,
                description=description,
                original=_read_or_empty(path),
                updated=updated,
            )
        )

    hid_src = core_path / "libraries" / "HID" / "src"

    # ── Core identity ────────────────────────────────────────────
    add_transform(
        core_path / "boards.txt",
        f"VID/PID {device.vendor_id:04X}:{device.product_id:04X}, strings, "
        f"CDC off, EP_SIZE=16, power/bcdUSB/bmAttributes/bInterval flags",
        lambda c: IdentityPatcher._boards_txt_content(c, device),
    )
    add_transform(
        core_path / "cores" / "arduino" / "USBCore.cpp",
        f"EP0={device.ep0_max_packet_size}, bcdDevice=0x{device.bcd_device:X}, "
        f"iSerialNumber 0, CDC disabled",
        lambda c: IdentityPatcher._usbcore_descriptor_content(
            IdentityPatcher._usb_core_disable_cdc(c), device
        ),
    )
    add_transform(
        core_path / "cores" / "arduino" / "USBCore.h",
        "bmAttributes overridable (USB_CONFIG_ATTRIBUTES guard)",
        IdentityPatcher._usbc_h_attributes_content,
    )

    def _hid_h(c: str) -> str:
        c = IdentityPatcher._hid_h_identity_content(c, device)
        c, _ = CommandChannelPatcher._updated_hid_h(c)
        return c

    add_transform(hid_src / "HID.h", "bcdHID/country + command channel declarations", _hid_h)

    def _hid_cpp(c: str) -> str:
        c = IdentityPatcher._hid_cpp_policy_content(c)
        c, _ = CommandChannelPatcher._updated_hid_cpp(c)
        return c

    add_transform(
        hid_src / "HID.cpp",
        "boot mouse policy + command channel core + interval/class-request parity",
        _hid_cpp,
    )

    # ── Generated core headers (rewritten on every run) ──────────
    config_text = generate_hid_command_config_h(target.command, target.capability, schema)
    blob_text = generate_hid_capability_blob_h(target.command, schema)
    command_desc = (
        f"command={target.command.enabled}, transport={target.command.transport}, "
        f"slots={target.command.queue_slots}, capability={target.capability.enabled}"
    )
    add_content(hid_src / _HID_COMMAND_CONFIG_NAME, command_desc, config_text)
    add_content(
        hid_src / _HID_CAPABILITY_BLOB_NAME,
        "generated capability payload (HID_CAPABILITY_BLOB)",
        blob_text,
    )

    # ── Mouse library ────────────────────────────────────────────
    lib_path = base_dir / MOUSE_LIB_RELATIVE
    profile_text, mapper_text = LibraryPatcher._profile_and_mapper_texts(device, target)
    descriptor_len = len(generate_report_descriptor(target)) + len(
        generate_command_descriptor(target.command, target.capability)
    )
    add_content(
        lib_path / "hid_profile.h",
        f"target report descriptor ({descriptor_len} bytes incl. vendor collection)",
        profile_text,
    )
    add_content(lib_path / "hid_mapper.h", "decode_input/encode_output", mapper_text)

    mouse_cpp = lib_path / "Mouse.cpp"
    if not mouse_cpp.exists():
        raise PatchError(f"Mouse.cpp not found: {mouse_cpp}")
    add_transform(
        mouse_cpp,
        "includes + HID_DESCRIPTOR + encode->SendReport + uint16_t buttons",
        LibraryPatcher._mouse_cpp_content,
    )
    mouse_h = lib_path / "Mouse.h"
    if not mouse_h.exists():
        raise PatchError(f"Mouse.h not found: {mouse_h}")
    add_transform(mouse_h, "move(int16_t ...) signature, uint16_t buttons", LibraryPatcher._mouse_h_content)

    # ── HubCommand library (copy from repo + generated opcodes) ──
    src_root = base_dir / HUB_COMMAND_SOURCE_DIR
    dst_root = base_dir / HUB_COMMAND_RELATIVE
    if not src_root.is_dir():
        raise PatchError(f"HubCommand source library not found: {src_root}")
    for src_file in sorted(src_root.rglob("*")):
        if src_file.is_file():
            rel = src_file.relative_to(src_root)
            add_content(
                dst_root / rel,
                f"install HubCommand/{rel.as_posix()}",
                src_file.read_text(encoding="utf-8"),
            )
    add_content(
        dst_root / "src" / commands_header_name(schema),
        "generated opcodes + wire-format macros",
        generate_commands_h(schema),
    )

    # ── PC client headers ────────────────────────────────────────
    client_dir = base_dir / PC_CLIENT_TARGET_DIR
    add_content(
        client_dir / commands_header_name(schema),
        "generated opcodes + wire-format macros (PC side)",
        generate_commands_h(schema),
    )
    add_content(client_dir / "command_channel.h", "command channel wiring (PC side)", config_text)

    return edits


def build_manifest_patches(
    device: DeviceInfo,
    target: TargetProfile,
    client_out: Path | None,
    layout_warnings: list[str],
    validation_issues: list[ValidationIssue],
) -> dict[str, str]:
    """Human-readable patch summary recorded in .build/patches.json."""
    if target.command.enabled:
        command_desc = (
            f"transport={target.command.transport}, "
            f"slots={target.command.queue_slots}, "
            f"payload={CMD_PAYLOAD_LEN}"
        )
    else:
        command_desc = "disabled"

    patches = {
        "boards.txt": (
            f"VID/PID {device.vendor_id:04X}:{device.product_id:04X}, strings, "
            f"CDC_DISABLED, USB_EP_SIZE=16, USB_CONFIG_POWER={device.max_power_ma}, "
            f"USB_VERSION=0x{device.usb_version:04X}, "
            f"USB_CONFIG_ATTRIBUTES=0x{device.bm_attributes:02X}, "
            f"HID_EP_INTERVAL=0x{max(1, device.ep_interval_ms):02X}, "
            f"USB_EP0_MAX_PACKET={int(device.ep0_max_packet_size)}"
        ),
        "USBCore.cpp": (
            f"EP0={device.ep0_max_packet_size} (descriptor + USB_EP0_ALLOC), "
            f"bcdDevice 0x{device.bcd_device:X}, iSerialNumber 0, CDC disabled"
        ),
        "USBCore.h": f"bmAttributes overridable (USB_CONFIG_ATTRIBUTES=0x{device.bm_attributes:02X})",
        "HID.h": (
            f"bcdHID 0x{device.bcd_hid:04X} (both bytes), "
            f"country 0x{device.country_code:02X}"
        ),
        "HID.cpp": (
            "subclass 1 / protocol 2 (boot mouse policy), bInterval via "
            "HID_EP_INTERVAL, GET_IDLE/GET_PROTOCOL spec answers"
        ),
        "Mouse.cpp": "includes + move() decode->encode->SendReport",
        "Mouse.h": "move(int16_t x, int16_t y, int16_t wheel, int16_t pan), uint16_t buttons",
        "hid_command_config.h": command_desc,
        "hid_capability_blob.h": "generated capability payload (HID_CAPABILITY_BLOB)",
        "HID.cpp/HID.h command": "SET_REPORT(Output|Feature) ring buffer, GET_REPORT(Feature)",
        "HubCommand": "MouseCommandHandler + transports",
        "pc_client/target": "mouse_commands.h + command_channel.h",
    }
    if client_out is not None:
        patches["client export"] = str(client_out)
    if layout_warnings:
        patches["source layout"] = "; ".join(layout_warnings)
    patches["validation"] = summarize(validation_issues)
    return patches


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
    _atomic_write_text(
        manifest_file,
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    )
    logger.info("Patch manifest written: %s", manifest_file)
