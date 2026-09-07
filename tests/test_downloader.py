"""ArduinoCLIDownloader tests (no network)."""

from pathlib import Path

from arduino_hub.cli.downloader import ArduinoCLIDownloader


def test_ensure_binary_creates_missing_tools_dir(tmp_path, monkeypatch):
    """Fresh clones have no .build/tools — ensure_binary must create it
    before _download/_extract write into it."""
    tools = tmp_path / ".build" / "tools"
    assert not tools.exists()

    def fake_download(dest: Path, version: str) -> None:
        dest.write_bytes(b"")

    def fake_extract(archive: Path, dest_dir: Path) -> Path:
        binary = dest_dir / ArduinoCLIDownloader._binary_name()
        binary.write_bytes(b"fake")
        return binary

    monkeypatch.setattr(ArduinoCLIDownloader, "_download", staticmethod(fake_download))
    monkeypatch.setattr(ArduinoCLIDownloader, "_extract", staticmethod(fake_extract))
    monkeypatch.setattr(
        ArduinoCLIDownloader, "_verify_sha256", staticmethod(lambda a, v: None)
    )

    binary = ArduinoCLIDownloader.ensure_binary(tools, "1.5.1")

    assert binary.exists()
    assert binary == tools / "arduino-cli.exe"
    # The version marker records what was downloaded.
    assert (tools / "arduino-cli.version").read_text(encoding="utf-8").strip() == "1.5.1"


def test_ensure_binary_reuses_existing(tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "arduino-cli.exe").write_bytes(b"fake")
    assert ArduinoCLIDownloader.ensure_binary(tools, "1.5.1").exists()


def test_sha256_mismatch_rejected(tmp_path, monkeypatch):
    """A corrupted/mismatched archive must fail loudly before extract."""
    import pytest

    from arduino_hub.exceptions import CLIDownloadError

    archive = tmp_path / ArduinoCLIDownloader._archive_name("1.5.1")
    archive.write_bytes(b"corrupted")

    def fail_extract(archive: Path, dest_dir: Path) -> Path:
        raise AssertionError("extract must not run on a checksum mismatch")

    monkeypatch.setattr(ArduinoCLIDownloader, "_extract", staticmethod(fail_extract))
    with pytest.raises(CLIDownloadError):
        ArduinoCLIDownloader.ensure_binary(tmp_path, "1.5.1")


def test_unpinned_version_warns_but_proceeds(tmp_path, monkeypatch):
    """Versions without a pin are extracted with a warning, not an error."""
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / ArduinoCLIDownloader._archive_name("9.9.9")).write_bytes(b"bogus")

    def fake_extract(archive: Path, dest_dir: Path) -> Path:
        binary = dest_dir / ArduinoCLIDownloader._binary_name()
        binary.write_bytes(b"fake")
        return binary

    monkeypatch.setattr(ArduinoCLIDownloader, "_extract", staticmethod(fake_extract))
    binary = ArduinoCLIDownloader.ensure_binary(tools, "9.9.9")
    assert binary.exists()
    assert (tools / "arduino-cli.version").read_text(encoding="utf-8").strip() == "9.9.9"
