import logging
import zipfile
from pathlib import Path

import requests

from arduino_hub.exceptions import CLIDownloadError, CLIExtractError

logger = logging.getLogger(__name__)


class ArduinoCLIDownloader:
    @staticmethod
    def _archive_name(version: str) -> str:
        return f"arduino-cli_{version}_Windows_64bit.zip"

    @staticmethod
    def _binary_name() -> str:
        return "arduino-cli.exe"

    @staticmethod
    def _download_url(version: str) -> str:
        archive = ArduinoCLIDownloader._archive_name(version)
        return f"https://github.com/arduino/arduino-cli/releases/download/v{version}/{archive}"

    @staticmethod
    def ensure_binary(base_dir: Path, version: str) -> Path:
        binary = base_dir / ArduinoCLIDownloader._binary_name()
        if binary.exists():
            logger.info("Arduino CLI binary found: %s", binary)
            return binary

        archive_path = base_dir / ArduinoCLIDownloader._archive_name(version)
        if not archive_path.exists():
            ArduinoCLIDownloader._download(archive_path, version)

        return ArduinoCLIDownloader._extract(archive_path, base_dir)

    @staticmethod
    def _download(dest: Path, version: str) -> None:
        url = ArduinoCLIDownloader._download_url(version)
        logger.info("Downloading Arduino CLI v%s ...", version)
        logger.debug("URL: %s", url)

        try:
            response = requests.get(url, stream=True, timeout=120)
        except requests.RequestException as e:
            raise CLIDownloadError(f"Download request failed: {e}") from e

        if response.status_code != 200:
            raise CLIDownloadError(
                f"Failed to download Arduino CLI v{version}. "
                f"HTTP {response.status_code}"
            )

        with open(dest, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        logger.info("Downloaded: %s", dest)

    @staticmethod
    def _extract(archive: Path, dest_dir: Path) -> Path:
        binary_name = ArduinoCLIDownloader._binary_name()
        logger.info("Extracting %s ...", archive)

        try:
            with zipfile.ZipFile(archive, "r") as zf:
                found = False
                for info in zf.infolist():
                    if info.filename == binary_name:
                        zf.extract(info, path=dest_dir)
                        found = True
                        break
                if not found:
                    raise CLIExtractError(
                        f"{binary_name} not found in archive: {archive}"
                    )
        except zipfile.BadZipFile as e:
            raise CLIExtractError(f"Corrupted archive: {archive}") from e

        binary = dest_dir / binary_name
        logger.info("Extracted: %s", binary)
        return binary
