import logging
import re
from pathlib import Path

from arduino_hub.cli.manager import ArduinoCLIManager
from arduino_hub.core.installer import ArduinoCoreInstaller
from arduino_hub.core.patcher import (
    MOUSE_LIB_RELATIVE,
    CommandChannelPatcher,
    IdentityPatcher,
    LibraryPatcher,
    write_patch_manifest,
)
from arduino_hub.devices import load as load_device, save as save_device
from arduino_hub.exceptions import PatchError
from arduino_hub.targets import load_target
from arduino_hub.usbhid.command_generator import CMD_PAYLOAD_LEN
from arduino_hub.usbhid.command_schema import load_command_schema
from arduino_hub.usbhid.descriptor_reader import parse_report
from arduino_hub.usbhid.hid_generator import source_layout_warnings

logger = logging.getLogger(__name__)

_LIB_RE = re.compile(r'- name:\s*"(.*?)"')


def cmd_setup(
    base_dir: Path,
    cli_version: str,
    core_version: str,
) -> None:
    logger.info("=== Step: Setup ===")

    mgr = ArduinoCLIManager(base_dir, cli_version)
    executor = mgr.ensure_cli()

    installer = ArduinoCoreInstaller(executor, base_dir)
    installer.ensure_version(core_version)

    libraries = _read_libraries(base_dir)
    if libraries:
        logger.info("Installing libraries: %s", ", ".join(libraries))
        executor.lib_install_many(libraries)

    logger.info("Setup complete.")


def _read_libraries(base_dir: Path) -> list[str]:
    config_path = base_dir / "arduino-cli.yaml"
    if not config_path.exists():
        return []
    content = config_path.read_text(encoding="utf-8")
    return _LIB_RE.findall(content)


def cmd_clone(base_dir: Path, name: str, report: Path) -> None:
    logger.info("=== Step: Clone device '%s' ===", name)

    report = report.resolve()
    if not report.exists():
        logger.error("Report not found: %s", report)
        return

    device = parse_report(report)
    if device is None:
        logger.error("Failed to parse report: %s", report)
        return

    save_device(device, name, base_dir)

    logger.info(
        "Device '%s' cloned from report: %04X:%04X %s",
        name,
        device.vendor_id,
        device.product_id,
        device.product_string,
    )


def _patch_for_device(
    base_dir: Path,
    device_name: str,
    target_name: str,
    core_path: Path,
) -> None:
    device = load_device(device_name, base_dir)
    target = load_target(target_name, base_dir)
    schema = load_command_schema(base_dir)

    IdentityPatcher.patch_usb_core(core_path)
    IdentityPatcher.patch_boards_txt(core_path, device)
    IdentityPatcher.patch_usbcore(core_path, device)
    IdentityPatcher.patch_hid(core_path, device)

    CommandChannelPatcher.write_hid_command_config(core_path, target, schema)
    CommandChannelPatcher.write_hid_capability_blob(core_path, target, schema)
    CommandChannelPatcher.patch_hid_command_core(core_path)

    if target.command.enabled:
        hid_cpp = core_path / "libraries" / "HID" / "src" / "HID.cpp"
        if not hid_cpp.exists() or "readReportPacket" not in hid_cpp.read_text(
            encoding="utf-8"
        ):
            raise PatchError(
                f"Command channel patch did not apply: HID library not found "
                f"at {hid_cpp}. Fix the core install before flashing, "
                f"otherwise the device flashes without a command channel."
            )

    lib_path = base_dir / MOUSE_LIB_RELATIVE
    LibraryPatcher.patch_mouse_library(lib_path, device, target)

    LibraryPatcher.install_hub_command_library(base_dir, schema)
    LibraryPatcher.write_pc_client_headers(base_dir, target, schema)

    command_desc = ""
    if target.command.enabled:
        command_desc = (
            f"transport={target.command.transport}, "
            f"slots={target.command.queue_slots}, "
            f"payload={CMD_PAYLOAD_LEN}"
        )
    else:
        command_desc = "disabled"

    patches = {
        "boards.txt": f"VID/PID {device.vendor_id:04X}:{device.product_id:04X}, "
                      f"strings, CDC_DISABLED, USB_EP_SIZE=16, "
                      f"USB_CONFIG_POWER={device.max_power_ma}",
        "USBCore.cpp": f"bcdDevice 0x{device.bcd_device:X}, iSerialNumber 0",
        "HID.h": f"bcdHID 0x{device.bcd_hid:X}",
        "HID.cpp": "subclass 1 / protocol 2 (boot mouse policy)",
        "Mouse.cpp": "includes + move() decode->encode->SendReport",
        "Mouse.h": "move(int16_t x, int16_t y, int16_t wheel, int16_t pan), uint16_t buttons",
        "hid_command_config.h": command_desc,
        "hid_capability_blob.h": "generated capability payload (HID_CAPABILITY_BLOB)",
        "HID.cpp/HID.h command": "SET_REPORT(Output|Feature) ring buffer, GET_REPORT(Feature)",
        "HubCommand": "MouseCommandHandler + transports",
        "pc_client/generated": "mouse_commands.h + command_channel.h",
    }
    warnings = source_layout_warnings(device)
    if warnings:
        patches["source layout"] = "; ".join(warnings)

    write_patch_manifest(base_dir, device_name, target_name, patches)


def cmd_patch(
    base_dir: Path,
    device_name: str,
    target_name: str,
    cli_version: str,
    core_version: str,
) -> None:
    logger.info("=== Step: Patch for device '%s', target '%s' ===", device_name, target_name)

    mgr = ArduinoCLIManager(base_dir, cli_version)
    executor = mgr.ensure_cli()

    installer = ArduinoCoreInstaller(executor, base_dir)
    core_path = installer.ensure_version(core_version)

    _patch_for_device(base_dir, device_name, target_name, core_path)

    logger.info("Patch complete for '%s' (target '%s').", device_name, target_name)


def cmd_compile(
    base_dir: Path,
    sketch: Path,
    fqbn: str,
    cli_version: str,
    core_version: str,
) -> None:
    logger.info("=== Step: Compile ===")

    sketch = sketch.resolve()
    if not sketch.exists():
        logger.error("Sketch not found: %s", sketch)
        return

    mgr = ArduinoCLIManager(base_dir, cli_version)
    executor = mgr.ensure_cli()

    core_path = ArduinoCoreInstaller.find_path(base_dir, core_version)
    if core_path is None:
        logger.error(
            "AVR core %s not found. Run 'arduino-hub setup' first.",
            core_version,
        )
        return

    executor.compile(sketch, fqbn)
    logger.info("Compilation successful.")


def cmd_flash(
    base_dir: Path,
    device_name: str,
    target_name: str,
    sketch: Path,
    port: str,
    fqbn: str,
    cli_version: str,
    core_version: str,
) -> None:
    logger.info("=== Step: Flash ===")

    sketch = sketch.resolve()
    if not sketch.exists():
        logger.error("Sketch not found: %s", sketch)
        return

    mgr = ArduinoCLIManager(base_dir, cli_version)
    executor = mgr.ensure_cli()

    installer = ArduinoCoreInstaller(executor, base_dir)
    core_path = installer.ensure_version(core_version)

    _patch_for_device(base_dir, device_name, target_name, core_path)

    executor.compile(sketch, fqbn)
    executor.upload(sketch, port, fqbn)

    logger.info("Flash complete. Device '%s' on %s.", device_name, port)
