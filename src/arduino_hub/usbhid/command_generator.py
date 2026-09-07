"""Command channel generators.

Produces:
- `generate_command_descriptor(command, capability)` — HID report
  descriptor block (vendor-defined Application collection) appended to
  the target's HID_DESCRIPTOR when the command channel is enabled;
- `generate_hid_command_config_h(command, capability, schema)` —
  `hid_command_config.h` for the patched Arduino core HID library:
  fixed protocol constants plus the per-target wiring;
- `generate_mouse_commands_h(schema)` — shared opcode/layout definitions
  generated from the command schema JSON (device + PC client);
- `generate_capability_blob(command, schema)` — the static capability
  report payload served by GET_REPORT(Feature).

Fixed protocol constants (see the audit/plan: these values must not
be confused):

    Report ID (command)                 = 3
    payload (descriptor REPORT_COUNT)   = 15
    hidapi buffer length                = 16  ([ID][payload x 15])

Note on SET_REPORT wLength: the descriptor REPORT_COUNT is 15, but
Windows carries the control transfer with wLength == 16 — HidD_*
duplicates the report id byte into the data stage. The core SET_REPORT
handler accepts both and strips the duplicated id (see HID.cpp patch).

Capability (meta-level interface, optional):

    Report ID (capability)              = 4
    descriptor payload (REPORT_COUNT)   = 8
    hidapi buffer length                = 9
"""

from __future__ import annotations

import logging

from arduino_hub.exceptions import CommandSchemaError
from arduino_hub.usbhid.command_schema import CommandSchema
from arduino_hub.usbhid.device_info import CapabilityProfile, CommandProfile

logger = logging.getLogger(__name__)

# ── Fixed command channel constants ────────────────────────────
CMD_REPORT_ID = 3
CMD_PAYLOAD_LEN = 15
CMD_TOTAL_LEN = 16          # hidapi buffer: [report id][payload]
CAP_REPORT_ID = 4
CAP_PAYLOAD_LEN = 8
CAP_TOTAL_LEN = 9           # hidapi buffer: [report id][payload]

CMD_USAGE_PAGE = 0xFF00     # vendor-defined
CMD_USAGE = 0x01            # application collection usage
CMD_WRITE_USAGE = 0x02      # command write report usage
CAP_READ_USAGE = 0x03       # capability read report usage

TRANSPORT_FEATURE = "feature"
TRANSPORT_OUTPUT = "output"
TRANSPORT_INTERRUPT_OUT = "interrupt_out"
TRANSPORT_CODE_FEATURE = 1
TRANSPORT_CODE_OUTPUT = 2
TRANSPORT_CODE_INTERRUPT_OUT = 3
TRANSPORT_CODES = {
    TRANSPORT_FEATURE: TRANSPORT_CODE_FEATURE,
    TRANSPORT_OUTPUT: TRANSPORT_CODE_OUTPUT,
    TRANSPORT_INTERRUPT_OUT: TRANSPORT_CODE_INTERRUPT_OUT,
}

DEFAULT_QUEUE_SLOTS = 4
MIN_QUEUE_SLOTS = 4
MAX_QUEUE_SLOTS = 8

CAPABILITY_MAGIC = b"HC"


def validate_command_profile(command: CommandProfile) -> None:
    if command.transport not in TRANSPORT_CODES:
        raise CommandSchemaError(
            f"Unknown command transport '{command.transport}' "
            f"(expected 'feature', 'output' or 'interrupt_out')"
        )
    if not (MIN_QUEUE_SLOTS <= command.queue_slots <= MAX_QUEUE_SLOTS):
        raise CommandSchemaError(
            f"queue_slots {command.queue_slots} out of range "
            f"{MIN_QUEUE_SLOTS}..{MAX_QUEUE_SLOTS}"
        )


# ── Report descriptor block ────────────────────────────────────


def generate_command_descriptor(
    command: CommandProfile,
    capability: CapabilityProfile,
) -> bytes:
    """Vendor-defined command collection block (empty when disabled).

    Kept as a separate top-level collection so the mouse collection
    stays byte-identical to target-only builds.
    """
    if not command.enabled:
        return b""

    buf = bytearray()
    # USAGE_PAGE (Vendor-Defined 0xFF00), USAGE, COLLECTION (Application)
    buf.extend((0x06, CMD_USAGE_PAGE & 0xFF, (CMD_USAGE_PAGE >> 8) & 0xFF))
    buf.extend((0x09, CMD_USAGE))
    buf.extend((0xA1, 0x01))

    # Command write report (Feature or Output by transport). interrupt_out
    # uses an Output report (0x91): hid_write routes to the interrupt OUT
    # endpoint when one is present.
    buf.extend((0x85, CMD_REPORT_ID))
    buf.extend((0x09, CMD_WRITE_USAGE))
    buf.extend((0x15, 0x00))                # LOGICAL_MINIMUM (0)
    buf.extend((0x26, 0xFF, 0x00))          # LOGICAL_MAXIMUM (255)
    buf.extend((0x75, 0x08))                # REPORT_SIZE (8)
    buf.extend((0x95, CMD_PAYLOAD_LEN))     # REPORT_COUNT (15)
    if command.transport == TRANSPORT_FEATURE:
        buf.extend((0xB1, 0x02))            # FEATURE (Data,Var,Abs)
    else:
        buf.extend((0x91, 0x02))            # OUTPUT (Data,Var,Abs)

    # Optional capability report (read-only, GET_REPORT Feature).
    if capability.enabled:
        buf.extend((0x85, CAP_REPORT_ID))
        buf.extend((0x09, CAP_READ_USAGE))
        buf.extend((0x15, 0x00))
        buf.extend((0x26, 0xFF, 0x00))
        buf.extend((0x75, 0x08))
        buf.extend((0x95, CAP_PAYLOAD_LEN))
        buf.extend((0xB1, 0x02))            # FEATURE (Data,Var,Abs)

    buf.extend((0xC0,))                     # END_COLLECTION
    return bytes(buf)


# ── hid_command_config.h (patched core HID library) ─────────────


def generate_hid_command_config_h(
    command: CommandProfile,
    capability: CapabilityProfile,
    schema: CommandSchema,
) -> str:
    validate_command_profile(command)
    schema.validate(CMD_PAYLOAD_LEN)
    transport = (
        TRANSPORT_CODES[command.transport] if command.enabled else 0
    )
    capability_enabled = 1 if (command.enabled and capability.enabled) else 0
    lines = [
        "#ifndef HID_COMMAND_CONFIG_H",
        "#define HID_COMMAND_CONFIG_H",
        "",
        "// Generated by arduino-hub — do not edit.",
        "#define HID_COMMAND_ENABLED %d" % (1 if command.enabled else 0),
        "#define HID_COMMAND_TRANSPORT_FEATURE %d" % TRANSPORT_CODE_FEATURE,
        "#define HID_COMMAND_TRANSPORT_OUTPUT %d" % TRANSPORT_CODE_OUTPUT,
        "#define HID_COMMAND_TRANSPORT_INTERRUPT_OUT %d" % TRANSPORT_CODE_INTERRUPT_OUT,
        "#define HID_COMMAND_TRANSPORT %d" % transport,
        "",
        f"#define HID_COMMAND_REPORT_ID {CMD_REPORT_ID}",
        f"#define HID_COMMAND_PAYLOAD_LEN {CMD_PAYLOAD_LEN}",
        f"#define HID_COMMAND_TOTAL_LEN {CMD_TOTAL_LEN}",
        f"#define HID_COMMAND_QUEUE_SLOTS {command.queue_slots}",
        f"#define HID_COMMAND_USAGE_PAGE 0x{CMD_USAGE_PAGE:04X}",
        f"#define HID_COMMAND_USAGE 0x{CMD_USAGE:02X}",
        "",
        "#define HID_CAPABILITY_ENABLED %d" % capability_enabled,
        f"#define HID_CAPABILITY_REPORT_ID {CAP_REPORT_ID}",
        f"#define HID_CAPABILITY_PAYLOAD_LEN {CAP_PAYLOAD_LEN}",
        f"#define HID_CAPABILITY_DEVICE_TYPE {schema.device_type}",
        f"#define HID_CAPABILITY_PROTOCOL_VERSION {schema.protocol_version}",
        "",
        "#endif",
        "",
    ]
    return "\n".join(lines)


# ── Capability blob (GET_REPORT Feature payload) ────────────────


def generate_capability_blob(
    command: CommandProfile,
    schema: CommandSchema,
) -> bytes:
    """[ 'H' 'C' ][transport][cmd_report_id][payload_len][queue_slots][device_type][protocol_version]"""
    return bytes([
        CAPABILITY_MAGIC[0],
        CAPABILITY_MAGIC[1],
        TRANSPORT_CODES[command.transport],
        CMD_REPORT_ID,
        CMD_PAYLOAD_LEN,
        command.queue_slots,
        schema.device_type & 0xFF,
        schema.protocol_version & 0xFF,
    ])


def generate_hid_capability_blob_h(
    command: CommandProfile,
    schema: CommandSchema,
) -> str:
    """`hid_capability_blob.h`: the ready-made capability report payload.

    Single source of truth: the bytes come from `generate_capability_blob`.
    The patched core HID.cpp only #includes this header and serves
    HID_CAPABILITY_BLOB on GET_REPORT(Feature); it never describes the
    blob structure itself.
    """
    blob = generate_capability_blob(command, schema)
    lines = [
        "#ifndef HID_CAPABILITY_BLOB_H",
        "#define HID_CAPABILITY_BLOB_H",
        "",
        "// Generated by arduino-hub — do not edit.",
        "#include <stdint.h>",
        "#include <avr/pgmspace.h>",
        "",
        "static const uint8_t HID_CAPABILITY_BLOB[] PROGMEM = {",
    ]
    for i in range(0, len(blob), 8):
        chunk = ", ".join(f"0x{b:02X}" for b in blob[i:i + 8])
        lines.append(f"  {chunk},")
    lines += ["};", "", "#endif", ""]
    return "\n".join(lines)


# ── Shared opcode/layout header (device + PC client) ────────────


def _enum_prefix(schema: CommandSchema) -> str:
    return f"{schema.name.upper()}_CMD_"


def commands_header_name(schema: CommandSchema) -> str:
    """Generated header file name, derived from the schema name."""
    return f"{schema.name}_commands.h"


def generate_commands_h(schema: CommandSchema) -> str:
    """Shared opcode/layout header generated from the schema JSON.

    Wire-format definitions only (opcodes + argument offsets/sizes):
    both the device handler and the PC client build packets from these
    macros instead of hardcoding byte offsets.
    """
    schema.validate(CMD_PAYLOAD_LEN)
    prefix = _enum_prefix(schema)
    lines = [
        "#ifndef %sH" % prefix,
        "#define %sH" % prefix,
        "",
        f"// Generated from profiles/protocol/{schema.name}.json by arduino-hub — do not edit.",
        f"#define {prefix}DEVICE_TYPE {schema.device_type}",
        f"#define {prefix}PROTOCOL_VERSION {schema.protocol_version}",
        "",
        "enum %sOpcode {" % (schema.name.title().replace("_", "")),
    ]
    for cmd in schema.commands:
        args = ", ".join(a.type for a in cmd.args)
        lines.append(f"  {prefix}{cmd.name.upper()} = 0x{cmd.opcode:02X},  // 1 + {cmd.packet_size - 1} bytes: [{args}]")
    lines += [
        "};",
        "",
        f"#define {prefix}MAX_PACKET {schema.max_packet_size}",
        "",
        "// Wire layout (single source of truth for device + PC client):",
        "// packet = [opcode u8][arg0][arg1]...",
        f"#define {prefix}OPCODE_OFF 0",
        f"#define {prefix}OPCODE_SIZE 1",
    ]
    for cmd in schema.commands:
        tag = f"{prefix}{cmd.name.upper()}"
        offset = 1
        for arg in cmd.args:
            lines.append(f"#define {tag}_{arg.name.upper()}_OFF {offset}")
            lines.append(f"#define {tag}_{arg.name.upper()}_SIZE {arg.size}")
            offset += arg.size
        lines.append(f"#define {tag}_LEN {cmd.packet_size}")
    lines += ["", "#endif", ""]
    return "\n".join(lines)
