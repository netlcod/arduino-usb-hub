"""Device command protocol schema.

The JSON schema in `profiles/protocol/<device>.json` is the build-time
source of truth for the PC → Arduino command protocol: opcodes, argument
types, order and payload size. It is parsed at patch time (never at
runtime on the AVR) and used to generate shared definitions for the
device firmware and for the PC client.

Example (profiles/protocol/mouse.json):

    {
      "name": "mouse",
      "device_type": 1,
      "protocol_version": 1,
      "commands": [
        {"name": "MOVE", "opcode": 1, "args": [
            {"name": "dx", "type": "i16"},
            {"name": "dy", "type": "i16"}
        ]}
      ]
    }

Packet framing on the wire (payload only, the Report ID travels in the
SET_REPORT wValue field and is prepended by hidapi on the PC; Windows
additionally duplicates the id into the data stage — wLength 16 — and
the core handler strips it):

    [opcode u8][arg0][arg1]...
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from arduino_hub.exceptions import CommandSchemaError

logger = logging.getLogger(__name__)

_TYPE_SIZES = {
    "u8": 1,
    "i8": 1,
    "u16": 2,
    "i16": 2,
}

PROFILES_DIR_NAME = "profiles"
COMMANDS_DIR_NAME = "protocol"
DEFAULT_COMMAND_SCHEMA = "mouse"


@dataclass
class CommandArg:
    name: str = ""
    type: str = "u8"

    @property
    def size(self) -> int:
        if self.type not in _TYPE_SIZES:
            raise CommandSchemaError(f"Unknown argument type '{self.type}'")
        return _TYPE_SIZES[self.type]


@dataclass
class CommandDef:
    name: str = ""
    opcode: int = 0
    args: list[CommandArg] = field(default_factory=list)

    @property
    def packet_size(self) -> int:
        """Wire size of the packet payload: opcode + all arguments."""
        return 1 + sum(a.size for a in self.args)


@dataclass
class CommandSchema:
    name: str = ""
    device_type: int = 0
    protocol_version: int = 0
    commands: list[CommandDef] = field(default_factory=list)

    @property
    def max_packet_size(self) -> int:
        if not self.commands:
            return 0
        return max(c.packet_size for c in self.commands)

    def validate(self, max_payload: int) -> None:
        """Raise CommandSchemaError if the schema cannot fit the channel."""
        if not self.name:
            raise CommandSchemaError("Schema has no 'name'")
        opcodes: set[int] = set()
        for cmd in self.commands:
            if not cmd.name:
                raise CommandSchemaError("Command without a name")
            if cmd.opcode <= 0 or cmd.opcode > 0xFF:
                raise CommandSchemaError(
                    f"Command '{cmd.name}' opcode {cmd.opcode} out of range 1..255"
                )
            if cmd.opcode in opcodes:
                raise CommandSchemaError(
                    f"Duplicate opcode {cmd.opcode} for '{cmd.name}'"
                )
            opcodes.add(cmd.opcode)
            if cmd.packet_size > max_payload:
                raise CommandSchemaError(
                    f"Command '{cmd.name}' packet ({cmd.packet_size} bytes) "
                    f"exceeds channel payload ({max_payload} bytes)"
                )


def _parse_arg(data: dict) -> CommandArg:
    return CommandArg(name=data.get("name", ""), type=data.get("type", "u8"))


def _to_int(value, field: str, where: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as e:
        raise CommandSchemaError(
            f"{where}: field '{field}' must be an integer, got {value!r}"
        ) from e


def _parse_command(data: dict) -> CommandDef:
    return CommandDef(
        name=data.get("name", ""),
        opcode=_to_int(data.get("opcode", 0), "opcode", f"Command '{data.get('name', '')}'"),
        args=[_parse_arg(a) for a in data.get("args", [])],
    )


def from_dict(data: dict) -> CommandSchema:
    return CommandSchema(
        name=data.get("name", ""),
        device_type=_to_int(data.get("device_type", 0), "device_type", "Schema"),
        protocol_version=_to_int(
            data.get("protocol_version", 0), "protocol_version", "Schema"
        ),
        commands=[_parse_command(c) for c in data.get("commands", [])],
    )


def _commands_dir(base_dir: Path) -> Path:
    return base_dir / PROFILES_DIR_NAME / COMMANDS_DIR_NAME


def load_command_schema(base_dir: Path, name: str = DEFAULT_COMMAND_SCHEMA) -> CommandSchema:
    """Load a command protocol schema from profiles/protocol/<name>.json."""
    path = _commands_dir(base_dir) / f"{name}.json"
    if not path.exists():
        raise CommandSchemaError(
            f"Command schema '{name}' not found in {_commands_dir(base_dir)}. "
            f"Expected file: {path}"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    schema = from_dict(data)
    if schema.name != name:
        logger.warning(
            "Command schema file %s has name '%s', requested '%s'",
            path, schema.name, name,
        )
    return schema
