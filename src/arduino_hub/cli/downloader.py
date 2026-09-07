from __future__ import annotations

import hashlib
import logging
import zipfile
from pathlib import Path

import requests

from arduino_hub.exceptions import CLIDownloadError, CLIExtractError

logger = logging.getLogger(__name__)

# SHA256 of the release archives, keyed by version. Anchored against the
# copy downloaded over TLS from github.com (accepted trust root for this
# tool); the pin turns a silent mirror/CDN swap into a loud failure.
# Versions without a pin are downloaded with a warning, not an error.
CLI_SHA256: dict[str, str] = {
    "1.5.1": "FABE42E0EB04D00E776A66178299FF95A46C623DBC260F997E58FD514853DD40",
}

_VERSION_MARKER = "arduino-cli.version"


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
    def _verify_sha256(archive: Path, version: str) -> None:
        expected = CLI_SHA256.get(version)
        if expected is None:
            logger.warning(
                "No SHA256 pin for arduino-cli %s; skipping checksum "
                "verification (add it to CLI_SHA256 to enforce)",
                version,
            )
            return
        actual = hashlib.sha256(archive.read_bytes()).hexdigest().upper()
        if actual != expected.upper():
            # Integrity failure of the download, not of extraction —
            # hence CLIDownloadError, not CLIExtractError.
            raise CLIDownloadError(
                f"SHA256 mismatch for {archive}: expected {expected}, "
                f"got {actual}. Delete the file and re-run 'arduino-hub "
                f"setup'; if it persists, verify the release on github.com."
            )
        logger.info("SHA256 verified: %s", archive.name)

    @staticmethod
    def ensure_binary(base_dir: Path, version: str) -> Path:
        base_dir.mkdir(parents=True, exist_ok=True)
        binary = base_dir / ArduinoCLIDownloader._binary_name()
        if binary.exists():
            # The binary's version cannot be checked without running it;
            # the marker written at download time is the honest signal.
            marker = base_dir / _VERSION_MARKER
            downloaded_version = (
                marker.read_text(encoding="utf-8").strip()
                if marker.exists() else None
            )
            if downloaded_version is not None and downloaded_version != version:
                logger.warning(
                    "arduino-cli on disk was downloaded as %s but %s was "
                    "requested; delete %s to force the requested version",
                    downloaded_version,
                    version,
                    binary,
                )
            logger.info("Arduino CLI binary found: %s", binary)
            return binary

        archive_path = base_dir / ArduinoCLIDownloader._archive_name(version)
        if not archive_path.exists():
            ArduinoCLIDownloader._download(archive_path, version)
        ArduinoCLIDownloader._verify_sha256(archive_path, version)

        binary = ArduinoCLIDownloader._extract(archive_path, base_dir)
        (base_dir / _VERSION_MARKER).write_text(version, encoding="utf-8")
        return binary

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
