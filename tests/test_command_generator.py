"""Command channel generator tests.

Locks the four fixed protocol values from the architecture contract:

    Report ID (command)         = 3
    descriptor payload (count)  = 15
    SET_REPORT wLength          = 15   (data stage, no report id byte)
    hidapi buffer length        = 16   ([ID][payload x 15])

Capability (meta-level, optional): report id 4, payload 8, buffer 9.
"""

from pathlib import Path

import pytest

from arduino_hub.exceptions import CommandSchemaError
from arduino_hub.targets import load_target
from arduino_hub.usbhid.command_generator import (
    CAP_PAYLOAD_LEN,
    CAP_REPORT_ID,
    CMD_PAYLOAD_LEN,
    CMD_REPORT_ID,
    CMD_TOTAL_LEN,
    generate_capability_blob,
    generate_command_descriptor,
    generate_commands_h,
    generate_hid_capability_blob_h,
    generate_hid_command_config_h,
    validate_command_profile,
)
from arduino_hub.usbhid.command_schema import (
    load_command_schema,
    from_dict as schema_from_dict,
)
from arduino_hub.usbhid.device_info import CapabilityProfile, CommandProfile

REPO_ROOT = Path(__file__).resolve().parents[1]

FEATURE_CMD = CommandProfile(enabled=True, transport="feature", queue_slots=4)
OUTPUT_CMD = CommandProfile(enabled=True, transport="output", queue_slots=8)
INTERRUPT_OUT_CMD = CommandProfile(enabled=True, transport="interrupt_out", queue_slots=4)
DISABLED_CMD = CommandProfile(enabled=False, transport="feature", queue_slots=4)
NO_CAPABILITY = CapabilityProfile(enabled=False)
WITH_CAPABILITY = CapabilityProfile(enabled=True)

MOUSE_SCHEMA = load_command_schema(REPO_ROOT)


# ── Fixed four-values contract ─────────────────────────────────


def test_fixed_protocol_constants():
    assert CMD_REPORT_ID == 3
    assert CMD_PAYLOAD_LEN == 15
    assert CMD_TOTAL_LEN == 16
    assert CAP_REPORT_ID == 4
    assert CAP_PAYLOAD_LEN == 8
    # hidapi buffer = [report id][payload]
    assert CMD_TOTAL_LEN == CMD_PAYLOAD_LEN + 1


def test_descriptor_payload_count_is_15():
    # The REPORT_COUNT byte (after 0x95) in the command write block is
    # the SET_REPORT data stage length: 15, not 16.
    descriptor = generate_command_descriptor(FEATURE_CMD, NO_CAPABILITY)
    assert descriptor[18] == 0x95
    assert descriptor[19] == 15
    assert descriptor[19] == CMD_PAYLOAD_LEN
    assert 16 not in descriptor  # 16 never appears in the command block


# ── Descriptor generation ──────────────────────────────────────


def test_feature_transport_descriptor():
    descriptor = generate_command_descriptor(FEATURE_CMD, NO_CAPABILITY)
    assert descriptor == bytes([
        0x06, 0x00, 0xFF,        # USAGE_PAGE (Vendor-Defined 0xFF00)
        0x09, 0x01,              # USAGE
        0xA1, 0x01,              # COLLECTION (Application)
        0x85, 0x03,              # REPORT_ID (3)
        0x09, 0x02,              # USAGE
        0x15, 0x00,              # LOGICAL_MINIMUM (0)
        0x26, 0xFF, 0x00,        # LOGICAL_MAXIMUM (255)
        0x75, 0x08,              # REPORT_SIZE (8)
        0x95, 0x0F,              # REPORT_COUNT (15)
        0xB1, 0x02,              # FEATURE (Data,Var,Abs)
        0xC0,                    # END_COLLECTION
    ])


def test_output_transport_descriptor():
    descriptor = generate_command_descriptor(OUTPUT_CMD, NO_CAPABILITY)
    assert descriptor[20:22] == bytes([0x91, 0x02])  # OUTPUT (Data,Var,Abs)
    assert 0xB1 not in descriptor


def test_interrupt_out_transport_descriptor():
    # interrupt_out uses an Output report (0x91): identical to output.
    descriptor = generate_command_descriptor(INTERRUPT_OUT_CMD, NO_CAPABILITY)
    assert descriptor[20:22] == bytes([0x91, 0x02])
    assert descriptor == generate_command_descriptor(OUTPUT_CMD, NO_CAPABILITY)


def test_capability_block_appended():
    base = generate_command_descriptor(FEATURE_CMD, NO_CAPABILITY)
    assert base[-1] == 0xC0  # END_COLLECTION
    cap_block = bytes([
        0x85, 0x04,              # REPORT_ID (4)
        0x09, 0x03,              # USAGE
        0x15, 0x00,
        0x26, 0xFF, 0x00,
        0x75, 0x08,
        0x95, 0x08,              # REPORT_COUNT (8)
        0xB1, 0x02,              # FEATURE
    ])
    descriptor = generate_command_descriptor(FEATURE_CMD, WITH_CAPABILITY)
    assert descriptor == base[:-1] + cap_block + b"\xC0"


def test_disabled_channel_descriptor_is_empty():
    assert generate_command_descriptor(DISABLED_CMD, NO_CAPABILITY) == b""
    # Capability without the channel is ignored.
    assert generate_command_descriptor(DISABLED_CMD, WITH_CAPABILITY) == b""


def test_capability_independent_of_transport():
    # Capability is a meta-level Feature report: present with both
    # transports, and the command block type differs by transport.
    feature = generate_command_descriptor(FEATURE_CMD, WITH_CAPABILITY)
    output = generate_command_descriptor(OUTPUT_CMD, WITH_CAPABILITY)
    assert feature[20:22] == bytes([0xB1, 0x02])
    assert output[20:22] == bytes([0x91, 0x02])
    assert feature[22:] == output[22:]  # capability block identical


def test_mouse_profile_integrates_command_block():
    target = load_target("generic_5btn", REPO_ROOT)
    assert target.command.enabled
    assert target.command.transport == "interrupt_out"
    assert target.command.queue_slots == 4
    assert not target.capability.enabled

    disabled = load_target("generic_3btn", REPO_ROOT)
    assert not disabled.command.enabled


# ── Config header ──────────────────────────────────────────────


def test_config_header_feature():
    header = generate_hid_command_config_h(
        FEATURE_CMD, NO_CAPABILITY, MOUSE_SCHEMA
    )
    assert "#define HID_COMMAND_ENABLED 1" in header
    assert "#define HID_COMMAND_TRANSPORT_FEATURE 1" in header
    assert "#define HID_COMMAND_TRANSPORT_OUTPUT 2" in header
    assert "#define HID_COMMAND_TRANSPORT_INTERRUPT_OUT 3" in header
    assert "#define HID_COMMAND_TRANSPORT 1" in header
    assert "#define HID_COMMAND_REPORT_ID 3" in header
    assert "#define HID_COMMAND_PAYLOAD_LEN 15" in header
    assert "#define HID_COMMAND_TOTAL_LEN 16" in header
    assert "#define HID_COMMAND_QUEUE_SLOTS 4" in header
    assert "#define HID_CAPABILITY_ENABLED 0" in header


def test_config_header_interrupt_out():
    header = generate_hid_command_config_h(
        INTERRUPT_OUT_CMD, NO_CAPABILITY, MOUSE_SCHEMA
    )
    assert "#define HID_COMMAND_TRANSPORT 3" in header
    assert "#define HID_COMMAND_TRANSPORT_INTERRUPT_OUT 3" in header


def test_config_header_disabled():
    header = generate_hid_command_config_h(
        DISABLED_CMD, WITH_CAPABILITY, MOUSE_SCHEMA
    )
    assert "#define HID_COMMAND_ENABLED 0" in header
    assert "#define HID_COMMAND_TRANSPORT 0" in header
    # Capability without the command channel is not effective.
    assert "#define HID_CAPABILITY_ENABLED 0" in header


def test_config_header_output_and_capability():
    header = generate_hid_command_config_h(
        OUTPUT_CMD, WITH_CAPABILITY, MOUSE_SCHEMA
    )
    assert "#define HID_COMMAND_TRANSPORT 2" in header
    assert "#define HID_COMMAND_QUEUE_SLOTS 8" in header
    assert "#define HID_CAPABILITY_ENABLED 1" in header
    assert "#define HID_CAPABILITY_REPORT_ID 4" in header
    assert "#define HID_CAPABILITY_PAYLOAD_LEN 8" in header


def test_config_header_validates_queue_slots():
    with pytest.raises(CommandSchemaError):
        validate_command_profile(
            CommandProfile(enabled=True, transport="feature", queue_slots=2)
        )
    with pytest.raises(CommandSchemaError):
        validate_command_profile(
            CommandProfile(enabled=True, transport="feature", queue_slots=9)
        )
    with pytest.raises(CommandSchemaError):
        validate_command_profile(
            CommandProfile(enabled=True, transport="bulk", queue_slots=4)
        )


# ── Capability blob ────────────────────────────────────────────


def test_capability_blob_layout():
    blob = generate_capability_blob(FEATURE_CMD, MOUSE_SCHEMA)
    assert blob == bytes([0x48, 0x43, 1, 3, 15, 4, 1, 1])
    assert len(blob) == CAP_PAYLOAD_LEN

    blob_out = generate_capability_blob(OUTPUT_CMD, MOUSE_SCHEMA)
    assert blob_out[2] == 2  # transport code output
    assert blob_out[5] == 8  # queue slots

    blob_int = generate_capability_blob(INTERRUPT_OUT_CMD, MOUSE_SCHEMA)
    assert blob_int[2] == 3  # transport code interrupt_out
    assert blob_int[5] == 4  # queue slots


# ── Shared opcode header (device + PC client) ──────────────────


def test_mouse_commands_header():
    header = generate_commands_h(MOUSE_SCHEMA)
    assert "MOUSE_CMD_DEVICE_TYPE 1" in header
    assert "MOUSE_CMD_PROTOCOL_VERSION 1" in header
    assert "MOUSE_CMD_MOVE = 0x01" in header
    assert "MOUSE_CMD_BUTTON_DOWN = 0x02" in header
    assert "MOUSE_CMD_BUTTON_UP = 0x03" in header
    assert "MOUSE_CMD_CLICK = 0x04" in header
    assert "MOUSE_CMD_WHEEL = 0x05" in header
    assert "MOUSE_CMD_MAX_PACKET 5" in header


def test_generated_wire_format_offsets():
    """R4: argument offsets/sizes and packet lengths are generated from
    the schema — one source of truth for device and PC client."""
    header = generate_commands_h(MOUSE_SCHEMA)
    assert "MOUSE_CMD_OPCODE_OFF 0" in header
    assert "MOUSE_CMD_OPCODE_SIZE 1" in header
    # MOVE: [opcode u8][dx i16][dy i16]
    assert "MOUSE_CMD_MOVE_DX_OFF 1" in header
    assert "MOUSE_CMD_MOVE_DX_SIZE 2" in header
    assert "MOUSE_CMD_MOVE_DY_OFF 3" in header
    assert "MOUSE_CMD_MOVE_DY_SIZE 2" in header
    assert "MOUSE_CMD_MOVE_LEN 5" in header
    # Single-arg commands: [opcode u8][arg]
    assert "MOUSE_CMD_BUTTON_DOWN_BUTTON_OFF 1" in header
    assert "MOUSE_CMD_BUTTON_DOWN_BUTTON_SIZE 1" in header
    assert "MOUSE_CMD_BUTTON_DOWN_LEN 2" in header
    assert "MOUSE_CMD_WHEEL_DELTA_OFF 1" in header
    assert "MOUSE_CMD_WHEEL_DELTA_SIZE 1" in header
    assert "MOUSE_CMD_WHEEL_LEN 2" in header


def test_capability_blob_header():
    """R3: the generated header carries the exact generate_capability_blob
    bytes; HID.cpp only #includes it."""
    header = generate_hid_capability_blob_h(FEATURE_CMD, MOUSE_SCHEMA)
    assert "static const uint8_t HID_CAPABILITY_BLOB[] PROGMEM" in header
    assert "0x48, 0x43, 0x01, 0x03, 0x0F, 0x04, 0x01, 0x01" in header

    blob = generate_capability_blob(FEATURE_CMD, MOUSE_SCHEMA)
    joined = ", ".join(f"0x{b:02X}" for b in blob)
    assert joined in header


# ── Schema validation ──────────────────────────────────────────


def test_schema_packet_sizes_fit_channel():
    MOUSE_SCHEMA.validate(CMD_PAYLOAD_LEN)
    assert MOUSE_SCHEMA.max_packet_size == 5  # MOVE: 1 + 4


def test_schema_duplicate_opcode_rejected():
    schema = schema_from_dict({
        "name": "mouse",
        "device_type": 1,
        "protocol_version": 1,
        "commands": [
            {"name": "A", "opcode": 1, "args": []},
            {"name": "B", "opcode": 1, "args": []},
        ],
    })
    with pytest.raises(CommandSchemaError):
        schema.validate(CMD_PAYLOAD_LEN)


def test_schema_oversized_packet_rejected():
    schema = schema_from_dict({
        "name": "mouse",
        "device_type": 1,
        "protocol_version": 1,
        "commands": [
            {"name": "HUGE", "opcode": 1, "args": [
                {"name": "a", "type": "u8"} for _ in range(15)
            ]},
        ],
    })
    with pytest.raises(CommandSchemaError):
        schema.validate(CMD_PAYLOAD_LEN)


def test_schema_unknown_type_rejected():
    schema = schema_from_dict({
        "name": "mouse",
        "device_type": 1,
        "protocol_version": 1,
        "commands": [
            {"name": "BAD", "opcode": 1, "args": [{"name": "x", "type": "i64"}]},
        ],
    })
    with pytest.raises(CommandSchemaError):
        schema.validate(CMD_PAYLOAD_LEN)
