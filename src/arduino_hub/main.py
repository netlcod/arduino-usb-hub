import argparse
import logging
import sys
from pathlib import Path

from arduino_hub.exceptions import ArduinoHubError
from arduino_hub.logging_config import setup_logging
from arduino_hub.pipeline import (
    cmd_clone,
    cmd_compile,
    cmd_flash,
    cmd_patch,
    cmd_setup,
)

logger = logging.getLogger(__name__)


def _add_global_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--cli-version",
        default="1.5.1",
        help="Arduino CLI version (default: 1.5.1)",
    )
    p.add_argument(
        "--core-version",
        default="1.8.6",
        help="AVR core version (default: 1.8.6)",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="arduino-hub",
        description="Arduino HID device emulator tool",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug output"
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_setup = sub.add_parser("setup", help="Initialize Arduino CLI and AVR core")
    _add_global_opts(p_setup)

    p_clone = sub.add_parser("clone", help="Clone HID device descriptor")
    p_clone.add_argument(
        "--name", required=True, help="Device name for saving (e.g. 'g102')"
    )
    _add_global_opts(p_clone)

    p_patch = sub.add_parser("patch", help="Apply device descriptor to boards.txt")
    p_patch.add_argument(
        "--device", required=True, help="Device name (from clone)"
    )
    _add_global_opts(p_patch)

    p_compile = sub.add_parser("compile", help="Compile sketch")
    p_compile.add_argument(
        "--sketch", required=True, type=Path, help="Path to .ino sketch"
    )
    p_compile.add_argument(
        "--fqbn",
        default="arduino:avr:leonardo",
        help="Fully Qualified Board Name (default: arduino:avr:leonardo)",
    )
    _add_global_opts(p_compile)

    p_flash = sub.add_parser(
        "flash", help="Patch, compile and upload sketch to board"
    )
    p_flash.add_argument(
        "--device", required=True, help="Device name (from clone)"
    )
    p_flash.add_argument(
        "--sketch", required=True, type=Path, help="Path to .ino sketch"
    )
    p_flash.add_argument(
        "--port", required=True, help="COM port (e.g. COM6)"
    )
    p_flash.add_argument(
        "--fqbn",
        default="arduino:avr:leonardo",
        help="Fully Qualified Board Name (default: arduino:avr:leonardo)",
    )
    _add_global_opts(p_flash)

    args = parser.parse_args()
    setup_logging(verbose=args.verbose)
    base_dir = Path.cwd()

    try:
        if args.command == "setup":
            cmd_setup(base_dir, args.cli_version, args.core_version)
        elif args.command == "clone":
            cmd_clone(base_dir, args.name)
        elif args.command == "patch":
            cmd_patch(base_dir, args.device, args.cli_version, args.core_version)
        elif args.command == "compile":
            cmd_compile(
                base_dir,
                args.sketch,
                args.fqbn,
                args.cli_version,
                args.core_version,
            )
        elif args.command == "flash":
            cmd_flash(
                base_dir,
                args.device,
                args.sketch,
                args.port,
                args.fqbn,
                args.cli_version,
                args.core_version,
            )
    except ArduinoHubError as e:
        logger.error("%s", e)
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)


if __name__ == "__main__":
    main()
