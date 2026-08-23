class ArduinoHubError(Exception):
    """Base exception for all arduino-hub errors."""


class CLIDownloadError(ArduinoHubError):
    """Failed to download Arduino CLI."""


class CLIExtractError(ArduinoHubError):
    """Failed to extract Arduino CLI archive."""


class CLIExecutionError(ArduinoHubError):
    """Arduino CLI returned non-zero exit code."""

    def __init__(self, cmd: list[str], stdout: str, stderr: str):
        self.cmd = cmd
        self.stdout = stdout
        self.stderr = stderr
        message = f"CLI command failed: {' '.join(cmd)}"
        if stderr:
            message += f"\n{stderr}"
        super().__init__(message)


class CoreNotFoundError(ArduinoHubError):
    """AVR core path could not be found."""


class CoreInstallError(ArduinoHubError):
    """Failed to install AVR core."""


class PatchError(ArduinoHubError):
    """Patching failed."""


class DeviceNotFoundError(ArduinoHubError):
    """Device info not found in profiles/sources/ directory."""


class TargetNotFoundError(ArduinoHubError):
    """Target profile not found in profiles/targets/ directory."""


class InvalidTargetError(ArduinoHubError):
    """Target profile failed validation against its report layout."""


class CommandSchemaError(ArduinoHubError):
    """Command protocol schema is invalid or not found."""


class DeviceCloneError(ArduinoHubError):
    """Failed to clone HID device info."""


class NoHIDDevicesError(ArduinoHubError):
    """No HID devices found."""


class BuildError(ArduinoHubError):
    """Compilation or upload failed."""


class PipelineStepError(ArduinoHubError):
    """A pipeline step failed."""

    def __init__(self, step_name: str):
        self.step_name = step_name
        super().__init__(f"Step '{step_name}' failed")
