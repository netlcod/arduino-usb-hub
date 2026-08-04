import logging
from pathlib import Path

from arduino_hub.cli.executor import ArduinoCLIExecutor
from arduino_hub.exceptions import CoreNotFoundError

logger = logging.getLogger(__name__)

CORE_PACKAGE = "arduino:avr"
CORE_RELATIVE_PATH_TEMPLATE = Path("arduino-cli-data") / "packages" / "arduino" / "hardware" / "avr"


class ArduinoCoreInstaller:
    def __init__(self, executor: ArduinoCLIExecutor, base_dir: Path):
        self._executor = executor
        self._base_dir = base_dir

    @staticmethod
    def find_path(base_dir: Path, version: str) -> Path | None:
        avr_dir = base_dir / CORE_RELATIVE_PATH_TEMPLATE
        if not avr_dir.exists():
            return None

        for entry in avr_dir.iterdir():
            if entry.is_dir() and entry.name == version:
                return entry

        return None

    def ensure_version(self, version: str) -> Path:
        core_path = self.find_path(self._base_dir, version)
        if core_path is not None:
            logger.info("AVR core %s found: %s", version, core_path)
            return core_path

        logger.info("Installing AVR core %s ...", version)
        self._executor.core_install(f"{CORE_PACKAGE}@{version}")

        core_path = self.find_path(self._base_dir, version)
        if core_path is None:
            raise CoreNotFoundError(
                f"AVR core {version} not found after install. "
                f"Expected at: {self._base_dir / CORE_RELATIVE_PATH_TEMPLATE / version}"
            )
        logger.info("AVR core installed: %s", core_path)
        return core_path
