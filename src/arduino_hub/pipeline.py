import logging
import re
import shutil
from pathlib import Path

from arduino_hub.cli.manager import ArduinoCLIManager
from arduino_hub.core.installer import ArduinoCoreInstaller
from arduino_hub.core.patcher import (
    PC_CLIENT_TARGET_DIR,
    apply_edits,
    build_manifest_patches,
    build_patch_edits,
    render_edits_diff,
    write_patch_manifest,
)
from arduino_hub.core.validation import has_errors, summarize, validate_identity
from arduino_hub.devices import load as load_device, save as save_device
from arduino_hub.exceptions import PatchError
from arduino_hub.targets import load_target
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


def _export_client_headers(base_dir: Path, out_dir: Path) -> None:
    """Copy the generated PC client headers to an external directory."""
    src_dir = base_dir / PC_CLIENT_TARGET_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in ("mouse_commands.h", "command_channel.h"):
        src = src_dir / name
        if not src.exists():
            raise PatchError(
                f"Cannot export client headers: {src} not found. "
                f"Run 'arduino-hub patch' first."
            )
        shutil.copy2(src, out_dir / name)
    logger.info("PC client headers exported: %s", out_dir)


def _patch_for_device(
    base_dir: Path,
    device_name: str,
    target_name: str,
    core_path: Path,
    client_out: Path | None = None,
    dry_run: bool = False,
) -> None:
    device = load_device(device_name, base_dir)
    target = load_target(target_name, base_dir)
    schema = load_command_schema(base_dir)

    # Pre-flight: refuse to touch the core on hard profile errors.
    issues = validate_identity(device)
    for issue in issues:
        logger.warning("validation %s", issue)
    if has_errors(issues):
        raise PatchError(
            "Profile validation failed:\n"
            + "\n".join(f"  - {i}" for i in issues if i.severity == "ERROR")
        )

    layout_warnings = source_layout_warnings(device)
    if layout_warnings:
        logger.warning("source layout: %s", "; ".join(layout_warnings))

    edits = build_patch_edits(base_dir, core_path, device, target, schema)

    if dry_run:
        diff = render_edits_diff(edits)
        logger.info("=== Dry run: no files were modified ===")
        logger.info("Validation: %s", summarize(issues))
        if diff:
            logger.info("%s", diff)
        else:
            logger.info("No file changes required — everything already up to date.")
        return

    apply_edits(edits)

    if target.command.enabled:
        hid_edit = next(
            (e for e in edits if e.path.name == "HID.cpp"), None
        )
        if hid_edit is None or "readReportPacket" not in hid_edit.updated:
            raise PatchError(
                f"Command channel patch did not apply: HID library not found "
                f"at {core_path / 'libraries' / 'HID' / 'src'}. Fix the core "
                f"install before flashing, otherwise the device flashes "
                f"without a command channel."
            )

    patches = build_manifest_patches(device, target, client_out, layout_warnings, issues)

    if client_out is not None:
        _export_client_headers(base_dir, client_out)

    write_patch_manifest(base_dir, device_name, target_name, patches)


def cmd_patch(
    base_dir: Path,
    device_name: str,
    target_name: str,
    cli_version: str,
    core_version: str,
    client_out: Path | None = None,
    dry_run: bool = False,
) -> None:
    logger.info("=== Step: Patch for device '%s', target '%s' ===", device_name, target_name)

    if dry_run:
        # Preview only: no toolchain downloads or installs. Requires an
        # environment prepared by 'arduino-hub setup'.
        core_path = ArduinoCoreInstaller.find_path(base_dir, core_version)
        if core_path is None:
            raise PatchError(
                f"AVR core {core_version} not found. Run 'arduino-hub setup' "
                f"first."
            )
        _patch_for_device(
            base_dir, device_name, target_name, core_path, client_out, True
        )
        return

    mgr = ArduinoCLIManager(base_dir, cli_version)
    executor = mgr.ensure_cli()

    installer = ArduinoCoreInstaller(executor, base_dir)
    core_path = installer.ensure_version(core_version)

    _patch_for_device(
        base_dir, device_name, target_name, core_path, client_out, False
    )

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
