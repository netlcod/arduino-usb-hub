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

    binary = ArduinoCLIDownloader.ensure_binary(tools, "1.5.1")

    assert binary.exists()
    assert binary == tools / "arduino-cli.exe"


def test_ensure_binary_reuses_existing(tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "arduino-cli.exe").write_bytes(b"fake")
    assert ArduinoCLIDownloader.ensure_binary(tools, "1.5.1").exists()
