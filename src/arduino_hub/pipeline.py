import logging
import re
from pathlib import Path

from arduino_hub.cli.manager import ArduinoCLIManager
from arduino_hub.core.installer import ArduinoCoreInstaller
from arduino_hub.core.patcher import (
    MOUSE_LIB_RELATIVE,
    USBPatcher,
    write_patch_manifest,
)
from arduino_hub.devices import load as load_device, save as save_device
from arduino_hub.targets import load_target
from arduino_hub.usbhid.descriptor_reader import parse_report

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

    USBPatcher.patch_usb_core(core_path)
    USBPatcher.patch_boards_txt(core_path, device)
    USBPatcher.patch_usbcore(core_path, device)
    USBPatcher.patch_hid(core_path, device)

    lib_path = base_dir / MOUSE_LIB_RELATIVE
    USBPatcher.patch_mouse_library(lib_path, device, target)

    write_patch_manifest(
        base_dir,
        device_name,
        target_name,
        {
            "boards.txt": f"VID/PID {device.vendor_id:04X}:{device.product_id:04X}, "
                          f"strings, CDC_DISABLED, USB_EP_SIZE=16, "
                          f"USB_CONFIG_POWER={device.max_power_ma}",
            "USBCore.cpp": f"bcdDevice 0x{device.bcd_device:X}, iSerialNumber 0",
            "HID.h": f"bcdHID 0x{device.bcd_hid:X}",
            "HID.cpp": "subclass 1 / protocol 2 (boot mouse policy)",
            "Mouse.cpp": "includes + move() decode->encode->SendReport",
            "Mouse.h": "move(int16_t x, int16_t y, int16_t wheel, int16_t pan), uint16_t buttons",
        },
    )


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
