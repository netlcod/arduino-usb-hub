import logging
import subprocess
from pathlib import Path

from arduino_hub.exceptions import CLIExecutionError

logger = logging.getLogger(__name__)


class ArduinoCLIExecutor:
    def __init__(self, executable: Path, config_file: Path):
        self._executable = executable
        self._config_file = config_file

    def core_install(self, core_ref: str) -> None:
        self._run("core", "install", core_ref)

    def compile(self, sketch: Path, fqbn: str) -> None:
        self._run("compile", "--fqbn", fqbn, str(sketch))

    def upload(self, sketch: Path, port: str, fqbn: str) -> None:
        self._run("upload", "-p", port, "--fqbn", fqbn, str(sketch))

    def lib_install(self, lib: str) -> None:
        self._run("lib", "install", lib)

    def lib_install_many(self, libraries: list[str]) -> None:
        for lib in libraries:
            self.lib_install(lib)

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        cmd = [
            str(self._executable),
            "--config-file",
            str(self._config_file),
            *args,
        ]
        logger.debug("Running: %s", " ".join(cmd))

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        if result.stdout:
            logger.debug("stdout: %s", result.stdout.strip())
        if result.stderr:
            logger.debug("stderr: %s", result.stderr.strip())

        if result.returncode != 0:
            raise CLIExecutionError(cmd, result.stdout, result.stderr)

        logger.info("CLI command succeeded: %s", args[0])
        return result
