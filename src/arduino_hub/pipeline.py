import logging
import re
from pathlib import Path

from arduino_hub.cli.manager import ArduinoCLIManager
from arduino_hub.core.installer import ArduinoCoreInstaller
from arduino_hub.core.patcher import USBPatcher
from arduino_hub.devices import load as load_device, save as save_device
from arduino_hub.usbhid.enumerator import HIDEnumerator

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


def cmd_clone(base_dir: Path, name: str) -> None:
    logger.info("=== Step: Clone device '%s' ===", name)

    devices = HIDEnumerator.enumerate()
    if not devices:
        logger.error("No HID devices found. Check USB connections.")
        return

    device = HIDEnumerator.select_interactive(devices)
    save_device(device, name, base_dir)

    logger.info(
        "Device '%s' cloned: %04X:%04X %s",
        name,
        device.vendor_id,
        device.product_id,
        device.product_string,
    )


def cmd_patch(
    base_dir: Path,
    device_name: str,
    cli_version: str,
    core_version: str,
) -> None:
    logger.info("=== Step: Patch for device '%s' ===", device_name)

    device = load_device(device_name, base_dir)

    mgr = ArduinoCLIManager(base_dir, cli_version)
    executor = mgr.ensure_cli()

    installer = ArduinoCoreInstaller(executor, base_dir)
    core_path = installer.ensure_version(core_version)

    USBPatcher.patch_usb_core(core_path)
    USBPatcher.patch_boards_txt(core_path, device)

    logger.info("Patch complete for '%s'.", device_name)


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

    USBPatcher.patch_usb_core(core_path)

    device = load_device(device_name, base_dir)
    USBPatcher.patch_boards_txt(core_path, device)

    executor.compile(sketch, fqbn)
    executor.upload(sketch, port, fqbn)

    logger.info("Flash complete. Device '%s' on %s.", device_name, port)
