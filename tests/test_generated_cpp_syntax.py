"""Host-side semantic check of the generated C++ headers.

The Python mirror tests (test_decoder/test_encode) share assumptions
with the generator; a bug in a generated function *body* would pass
them unnoticed. This test feeds the generated hid_mapper.h /
hid_profile.h / mouse_commands.h / command_channel.h to a host C++
compiler (-fsyntax-only) and calls the generated entry points, so any
type or body error fails here. Skipped when no host compiler exists
(the AVR toolchain is NOT required — only a plain host g++).

hid_profile.h includes <avr/pgmspace.h>; a two-line stub provides the
single macro it uses (PROGMEM).
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from arduino_hub.targets import load_target
from arduino_hub.usbhid.command_generator import (
    generate_commands_h,
    generate_hid_command_config_h,
)
from arduino_hub.usbhid.command_schema import load_command_schema
from arduino_hub.usbhid.device_info import CapabilityProfile, CommandProfile
from arduino_hub.usbhid.hid_generator import (
    generate_hid_mapper_h,
    generate_hid_profile_h,
    generate_report_descriptor,
)
from util import load_g305

REPO_ROOT = Path(__file__).resolve().parents[1]
CXX = shutil.which("g++") or shutil.which("c++")

_PGMSPACE_STUB = """\
#pragma once
// Host-side stub for <avr/pgmspace.h>: the generated headers only need
// the PROGMEM attribute, which is a no-op on a host.
#define PROGMEM
"""

_MAIN_CPP = """\
#include "command_channel.h"
#include "hid_mapper.h"
#include "hid_profile.h"
#include "mouse_commands.h"

int check_report_path() {
  unsigned char src[32] = {0};
  MouseState state;
  if (decode_input(src, (unsigned char)sizeof(src), state)) {
    unsigned char out[HID_REPORT_LENGTH] = {0};
    encode_output(state, out);
  }
  return 0;
}

int check_command_path() {
  unsigned char pkt[HID_COMMAND_PAYLOAD_LEN] = {0};
  pkt[MOUSE_CMD_OPCODE_OFF] = MOUSE_CMD_MOVE;
  pkt[MOUSE_CMD_MOVE_DX_OFF] = 1;
  pkt[MOUSE_CMD_MOVE_DX_OFF + 1] = 2;
  pkt[MOUSE_CMD_MOVE_DY_OFF] = 3;
  pkt[MOUSE_CMD_BUTTON_DOWN_BUTTON_OFF] = 1;
  pkt[MOUSE_CMD_WHEEL_DELTA_OFF] = 200;
  (void)pkt;
  return MOUSE_CMD_MAX_PACKET > 0 ? 0 : 1;
}
"""


@pytest.mark.skipif(CXX is None, reason="host C++ compiler not available")
@pytest.mark.parametrize("target_name", ["generic_3btn", "generic_5btn", "generic_16btn"])
def test_generated_headers_are_valid_cpp(tmp_path, target_name):
    source = load_g305()
    target = load_target(target_name, REPO_ROOT)
    schema = load_command_schema(REPO_ROOT)

    (tmp_path / "avr").mkdir()
    (tmp_path / "avr" / "pgmspace.h").write_text(_PGMSPACE_STUB, encoding="utf-8")
    (tmp_path / "hid_profile.h").write_text(
        generate_hid_profile_h(target, generate_report_descriptor(target)),
        encoding="utf-8",
    )
    (tmp_path / "hid_mapper.h").write_text(
        generate_hid_mapper_h(source, target), encoding="utf-8"
    )
    (tmp_path / "mouse_commands.h").write_text(
        generate_commands_h(schema), encoding="utf-8"
    )
    (tmp_path / "command_channel.h").write_text(
        generate_hid_command_config_h(target.command, target.capability, schema),
        encoding="utf-8",
    )
    main = tmp_path / "check_main.cpp"
    main.write_text(_MAIN_CPP, encoding="utf-8")

    result = subprocess.run(
        [CXX, "-fsyntax-only", "-Wall", "-Werror=return-type", "-I", str(tmp_path), str(main)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"generated C++ for {target_name} failed to compile:\n{result.stderr}"
    )

# --- CommandTransport.h compile matrix ---------------------------------
# Regression: transport class bodies are semantically checked at
# class-definition time even when never instantiated, and the patched
# core HID.h declares the command API under the same #if guards as the
# factory uses. Each config below must compile; an ungated body fails
# exactly the configs where the API is not declared.

_HID_STUB = """\
#pragma once
#include <stdint.h>
// Mirrors the patched core HID.h declarations (same #if guards as the
// patcher emits) so CommandTransport.h can be host-compiled. The real
// HID.h includes hid_command_config.h itself — the stub must too,
// otherwise the guards below see undefined (0) macros.
#include "hid_command_config.h"
struct HIDSubDescriptor {
  const void* data;
  int length;
  HIDSubDescriptor* next;
};
class HID_ {
public:
  int SendReport(unsigned char id, const void* data, int len);
  void AppendDescriptor(HIDSubDescriptor* node);
#if HID_COMMAND_ENABLED
  int availableReportPackets();
  int readReportPacket(unsigned char* dst, unsigned char maxlen);
#endif
#if HID_COMMAND_ENABLED && HID_COMMAND_TRANSPORT == HID_COMMAND_TRANSPORT_INTERRUPT_OUT
  int availableOutReport();
  int readOutReport(unsigned char* dst, unsigned char maxlen);
#endif
};
HID_& HID();
"""

_TRANSPORT_MAIN = """\
#include "CommandTransport.h"

int check_factory() {
  return getCommandTransport().available();
}
"""


@pytest.mark.skipif(CXX is None, reason="host C++ compiler not available")
@pytest.mark.parametrize(
    "config_name, command",
    [
        ("disabled", CommandProfile(enabled=False, transport="feature", queue_slots=4)),
        ("feature", CommandProfile(enabled=True, transport="feature", queue_slots=4)),
        ("output", CommandProfile(enabled=True, transport="output", queue_slots=8)),
        ("interrupt_out", CommandProfile(enabled=True, transport="interrupt_out", queue_slots=4)),
    ],
)
def test_command_transport_compiles_for_every_config(tmp_path, config_name, command):
    schema = load_command_schema(REPO_ROOT)
    src = REPO_ROOT / "libraries" / "HubCommand" / "src" / "CommandTransport.h"
    (tmp_path / "CommandTransport.h").write_text(
        src.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "HID.h").write_text(_HID_STUB, encoding="utf-8")
    (tmp_path / "hid_command_config.h").write_text(
        generate_hid_command_config_h(command, CapabilityProfile(enabled=False), schema),
        encoding="utf-8",
    )
    main = tmp_path / "transport_main.cpp"
    main.write_text(_TRANSPORT_MAIN, encoding="utf-8")

    result = subprocess.run(
        [CXX, "-fsyntax-only", "-Wall", "-I", str(tmp_path), str(main)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"CommandTransport.h failed for config '{config_name}':\n{result.stderr}"
    )
