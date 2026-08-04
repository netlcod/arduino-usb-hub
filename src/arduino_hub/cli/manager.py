import logging
from pathlib import Path

from arduino_hub.cli.downloader import ArduinoCLIDownloader
from arduino_hub.cli.executor import ArduinoCLIExecutor

logger = logging.getLogger(__name__)


class ArduinoCLIManager:
    def __init__(
        self,
        base_dir: Path,
        cli_version: str = "1.5.1",
    ):
        self._base_dir = base_dir
        self._cli_version = cli_version
        self._config_file = base_dir / "arduino-cli.yaml"

    @property
    def config_file(self) -> Path:
        return self._config_file

    def ensure_cli(self) -> ArduinoCLIExecutor:
        binary = ArduinoCLIDownloader.ensure_binary(
            self._base_dir, self._cli_version
        )
        return ArduinoCLIExecutor(binary, self._config_file)
