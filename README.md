# Arduino USB Hub — HID Device Emulator

Clones an existing USB HID device (mouse, keyboard, gamepad) onto an
[Arduino Leonardo](https://docs.arduino.cc/hardware/leonardo/) with a
[USB Host Shield](https://docs.arduino.cc/retired/shields/arduino-usb-host-shield/).
The tool automates the entire pipeline: detecting the target device, patching the
Arduino AVR core, compiling, and flashing the firmware.

Besides cloning, the firmware exposes an optional **PC → Arduino command
channel**: a second HID report on the *same* USB interface lets a PC
application control the mouse (move, click, scroll) through a C++ client
library (`pc_client/`).

## How it works

1. **Clone** — select a connected HID device and save its descriptors (VID, PID,
   manufacturer, product strings) to a JSON file.
2. **Patch** — apply modifications to the installed Arduino AVR core:
   - `USBCore.cpp` — comment out CDC (serial port) so Leonardo presents itself
     purely as an HID device.
   - `boards.txt` — inject the cloned VID, PID, and string descriptors.
   - `HID.h`/`HID.cpp` — bcdHID, boot-mouse interface policy and the
     command channel transport (a `SET_REPORT` ring, or an interrupt OUT
     endpoint).
   - Generate `hid_profile.h`/`hid_mapper.h` into the Mouse library and
     install the `HubCommand` library.
3. **Compile** — build the sketch using `arduino-cli`.
4. **Flash** — patch, compile, and upload in one command.

After flashing, the Leonardo is no longer recognised as a serial port — it
appears to the host as the cloned device.

## Architecture

```
┌─ physical mouse ─┐                    ┌────────── Leonardo ──────────┐
│ USB HID device    │                    │                              │
└────────┬──────────┘   USB Host Shield  │  decode_input()              │
         └───────────────►  (USBH 2.0)   │    (hid_mapper.h)            │
                            Usb.Task()   │       │                      │
                                         │       ▼                      │
                                         │  Mouse API (patched)         │
                                         │       │                      │
                                         │       ▼                      │
                                         │  encode_output()             │
                                         │    (hid_mapper.h)            │
                                         │       │                      │
                                         │       ▼                      │
                                         │  core HID ── HID Input ──────┼──► PC (clone)
                                         │       ▲                      │
                                         │       │  command channel     │
                                         │  MouseCommandHandler         │
                                         │       ▲                      │
                                         │  CommandTransport            │
                                         │       ▲                      │
                                         │  feature/output: SET_REPORT  │
                                         │  interrupt_out:  OUT endpoint │
                                         └───────┼──────────────────────┘
                                                 │  hid_write /
                                                 │  hid_send_feature_report
                                      PC MouseClient (hidapi) ──────────┘
```

- **Clone path** (left→right): the physical mouse's reports are read by the USB
  Host Shield, decoded by `decode_input()`, re-encoded by `encode_output()`,
  and sent to the PC as an HID Input report.
- **Command path** (right→left, optional): the PC drives the same `Mouse API`
  through the command channel — `feature`/`output` ride `SET_REPORT` into a
  ring, `interrupt_out` rides a dedicated interrupt OUT endpoint.

Both paths converge on `Mouse API → encode_output`, so host-shield replay and
PC commands are indistinguishable downstream.

## Requirements

- **Hardware**: Arduino Leonardo + USB Host Shield (Arduino Micro may work but
  is untested).
- **OS**: Windows (only platform supported).
- **Python**: 3.9 or newer.

## Installation

```bash
git clone https://github.com/netlcod/arduino-usb-hub.git
cd arduino-usb-hub
pip install .
```

The `arduino-cli` binary is downloaded automatically on first use.
Dependencies (`hidapi`, `requests`) are pulled from PyPI.

## Quick start

```bash
# 1. One-time setup (downloads arduino-cli, installs AVR core 1.8.6)
arduino-hub setup

# 2. Clone a connected HID device (interactive)
arduino-hub clone --name mymouse

# 3. Flash your sketch onto Leonardo
arduino-hub flash --device mymouse --sketch examples/mouse/mouse.ino --port COM6
```

Steps 1–2 are run once. To re-flash the same device later, just repeat step 3.

## Commands

### `setup`

Downloads `arduino-cli` and installs the AVR core. Idempotent — re-running
skips work that is already done.

```bash
arduino-hub setup
```

Optional flags:

| Flag | Default | Description |
|------|---------|-------------|
| `--cli-version` | `1.5.1` | Arduino CLI version to download |
| `--core-version` | `1.8.6` | AVR core version to install |

### `clone`

Lists all connected HID devices, lets you pick one interactively, opens it to
read descriptors, and saves everything to `profiles/sources/<name>.json`.

```bash
arduino-hub clone --name mydevice --report profiles/sources/reports/usb-report.txt
```

The saved JSON file contains:

```json
{
  "name": "mydevice",
  "vendor_id": "0x046D",
  "product_id": "0xC53F",
  "manufacturer_string": "Logitech",
  "product_string": "USB Receiver",
  "serial_number": ""
}
```

Some Logitech receivers emit their X/Y axis fields behind a USB Host
Shield in a different order than their own report descriptor declares
(the G305 receiver does: its descriptor — and Windows' own `InputCaps` —
list Y first, yet shield-side data carries X first; plugged straight
into a PC the same device behaves correctly). If cursor directions are
swapped after flashing, set the `wire_order` values of the X (`0x30`)
and Y (`0x31`) entries in `profiles/sources/<name>.json` to the observed
physical order (G305: X=`16`, Y=`17`; `data_index` stays as reported).
Re-running `clone` restores the reported order, so repeat the fix after
re-cloning; the patch run reports the override as a `source layout`
warning in `.build/patches.json`.

### `patch`

Applies all patches to the installed AVR core (idempotent):

- **USB Core patch** — disables CDC in `USBCore.cpp`.
- **Board patch** — writes the device VID, PID, and string descriptors into
  `boards.txt`.
- **HID patch** — bcdHID, boot-mouse interface policy, and the command
  channel transport in `HID.h`/`HID.cpp` (`SET_REPORT` ring, interrupt OUT
  endpoint, `GET_REPORT` capability).
- **Generated files** — `hid_profile.h`/`hid_mapper.h` (Mouse library),
  `hid_command_config.h` (core), `mouse_commands.h` (HubCommand library and
  `pc_client/target/`), HubCommand library install, `.build/patches.json`.

```bash
arduino-hub patch --device mydevice --target generic_3btn
# optionally export the PC client headers to an external project:
arduino-hub patch --device mydevice --target generic_3btn \
    --client-out ../my-project/pc_client/target
```

`--target` selects the output profile from `profiles/targets/` (default `generic_3btn`):

| Target | Presents to the PC | Command channel |
|--------|--------------------|-----------------|
| `generic_3btn` | Standard 3-button mouse (L/R/M + wheel), Arduino Mouse-compatible | off |
| `generic_5btn` | 5-button mouse — buttons 4/5 become XButton1 (Back) / XButton2 (Forward) | on, `interrupt_out` |
| `generic_16btn` | Full clone: 16 buttons, 16-bit X/Y, wheel, AC Pan, Report ID 2 (pass-through) | on, `feature` |

Patching also generates `hid_profile.h`/`hid_mapper.h` into the Mouse library
and writes a manifest to `.build/patches.json`. Re-patching with a different
`--target` swaps only the generated headers; all edits are idempotent.

Requires `setup` to have been run previously (core must be installed).

### `compile`

Compiles a sketch without uploading. Useful for checking that the sketch builds
before flashing.

```bash
arduino-hub compile --sketch examples/mouse/mouse.ino
arduino-hub compile --sketch ./mysketch/mysketch.ino --fqbn arduino:avr:leonardo
```

| Flag | Default | Description |
|------|---------|-------------|
| `--sketch` | *(required)* | Path to `.ino` file |
| `--fqbn` | `arduino:avr:leonardo` | Fully Qualified Board Name |

### `flash`

The all-in-one command: patches the core if needed, compiles the sketch, and
uploads it to the Leonardo.

```bash
arduino-hub flash --device mydevice --sketch examples/mouse/mouse.ino --port COM6
```

| Flag | Default | Description |
|------|---------|-------------|
| `--device` | *(required)* | Device name from `clone` |
| `--target` | `generic_3btn` | Target profile name from `profiles/targets/` |
| `--sketch` | *(required)* | Path to `.ino` file |
| `--port` | *(required)* | COM port (e.g. `COM6`) |
| `--fqbn` | `arduino:avr:leonardo` | Fully Qualified Board Name |

### Global flags

| Flag | Description |
|------|-------------|
| `-v`, `--verbose` | Enable debug-level logging |
| `--cli-version` | Override Arduino CLI version (default `1.5.1`) |
| `--core-version` | Override AVR core version (default `1.8.6`) |

## Hardware setup

```
USB HID Device  ──►  USB Host Shield  ──►  Leonardo  ──►  PC USB port
```

Connect the device you want to clone to the USB Host Shield. The Leonardo
connects to your PC via its native USB port.

## Example sketch

The `examples/mouse/` directory contains a reference sketch that uses the
[USB Host Shield Library 2.0](https://github.com/felis/USB_Host_Shield_2.0) to
read the cloned device's HID reports (report protocol) and forward them to the
Leonardo's built-in HID peripheral. The raw reports are translated by the
generated `hid_mapper.h` (`decode_input()` on the host side, `encode_output()`
in the Mouse library) into the target report format.

Copy and adapt it for your own device.

## PC → Arduino command channel

The firmware can expose a control channel that lets a PC application drive
the cloned mouse (move, click, scroll). The channel is **independent of the
cloned device's report format** and does not add a second USB interface —
it is an additional HID report (a vendor-defined collection, usage page
`0xFF00`) on the *same* interface as the mouse.

### Layering

```
Device (Leonardo):
  core HID (patched)            transport only:
    feature / output            SET_REPORT → fixed ring buffer
    interrupt_out               OUT endpoint → USB_Recv (polled in loop)
    (capability, optional)      GET_REPORT(Feature) readback
        ↓
  CommandTransport              hides how the report arrived (feature/output/interrupt_out)
        ↓
  MouseCommandHandler           binary opcodes → Mouse API (move/press/release/click)
        ↓
  Mouse API → encode_output()   target encoder from hid_mapper.h
        ↓
  HID Input report → PC

PC:
  MouseClient (mouse_client.hpp)
        ↓
  CommandProtocol (generated mouse_commands.h: opcodes + packet layout)
        ↓
  CommandTransport (transport.hpp: hid_send_feature_report | hid_write)
        ↓
  hidapi
```

The physical mouse flows through the same `Mouse API → encode_output` path,
so both sources (host shield replay and PC commands) converge in one place.

### Transports

Three delivery mechanisms exist; the choice is **compile-time** (set in the
target JSON — no runtime switching in the client). All three carry the same
15-byte payload with report ID 3.

| Transport | PC API | On the wire | Device receive path | Extra endpoint |
|-----------|--------|-------------|---------------------|----------------|
| `feature` | `hid_send_feature_report` | `SET_REPORT(Feature)`; report ID in `wValueL` (also duplicated in data stage) | `SET_REPORT` handler → ring | none |
| `output` | `hid_write` | `SET_REPORT(Output)`; report ID in `wValueL` (also duplicated in data stage) | `SET_REPORT` handler → ring | none |
| `interrupt_out` | `hid_write` | interrupt OUT transfer; report ID is the **first data byte** (`[0x03][payload 15]`) | `USB_Recv` polled in `loop()` | adds interrupt OUT endpoint |

`interrupt_out` is the primary channel: it adds a second endpoint (IN + OUT)
on the existing HID interface — closer to how a real bidirectional device is
wired — while `feature`/`output` keep a single IN endpoint and reuse the
control pipe. On the PC side `interrupt_out` uses the same
`OutputReportTransport` as `output`: `hid_write` automatically routes to the
OUT endpoint when one is present.

> A real mouse (e.g. the G305 receiver) is input-only — no OUT endpoint, no
> Output/Feature reports. Any PC → Arduino channel is therefore an *addition*
> to the clone's fingerprint; the transport is a deliberate, compile-time
> choice made per target.

### Fixed protocol constants

These are **not configurable** — they lock the contract between the firmware
and the PC client (defined in `src/arduino_hub/usbhid/command_generator.py`):

| Value | Meaning |
|-------|---------|
| Report ID `3` | command report (vendor collection, usage page `0xFF00`) |
| `15` | descriptor `REPORT_COUNT` (payload length, excluding the report ID byte) |
| `16` | hidapi buffer length on the PC: `[report_id][payload × 15]` (zero-padded) |
| Report ID `4` | capability report (optional, `GET_REPORT` Feature, payload 8) |

Windows quirk (feature/output transports): `HidD_SetFeature` /
`HidD_SetOutputReport` put the report ID in `wValueL` **and** send it again as
the first byte of the data stage, so the actual `SET_REPORT` arrives with
`wLength == 16` (report ID + 15-byte payload). The firmware handler strips the
duplicated report ID byte before copying the 15-byte payload into the ring.

For `interrupt_out` there is no `wValueL`: the report ID travels as the first
byte of the interrupt OUT packet (`[0x03][payload 15]` = 16 bytes), and
`readOutReport()` drops it after reading.

Every command is one packet: `[opcode u8][typed args]`. The opcodes and
payload layout are defined in `profiles/protocol/mouse.json` (build-time source of
truth) and generated into `mouse_commands.h` for both the firmware and the PC
client — they are validated against the 15-byte payload at patch time.

### Configuration

Everything is configured in the target profile, e.g.
`profiles/targets/generic_5btn.json`:

```json
"command": {
  "enabled": true,
  "transport": "interrupt_out",
  "queue_slots": 4
},
"capability": {
  "enabled": false
}
```

| Key | Values | Meaning |
|-----|--------|---------|
| `command.enabled` | `true` / `false` | enable/disable the whole channel (no reports added when off) |
| `command.transport` | `"feature"` / `"output"` / `"interrupt_out"` | delivery: `SET_REPORT(Feature)` via `hid_send_feature_report`, `SET_REPORT(Output)` via `hid_write`, or an interrupt OUT endpoint via `hid_write` |
| `command.queue_slots` | `4`–`8` | firmware ring buffer depth (drop-new when full) |
| `capability.enabled` | `true` / `false` | optional meta-level capability report (report ID 4, readable via `hid_get_feature_report`) |

After editing a target or `profiles/protocol/mouse.json`, re-run `arduino-hub patch`
(it regenerates the firmware descriptor **and** `pc_client/target/*`),
re-flash, and rebuild the PC client — both sides must come from the same
patch run.

### PC client (`pc_client/`)

C++ header-only client over [hidapi](https://github.com/libusb/hidapi)
(fetched automatically by CMake):

```bash
cmake -B pc_client/build pc_client
cmake --build pc_client/build --config Release
pc_client/build/Release/client_demo.exe            # defaults to 046D:C53F
pc_client/build/Release/client_demo.exe [VID] [PID]
```

`client_demo --list` prints every HID device (VID/PID/usage page) — the
command channel is the collection with usage page `0xFF00`. The demo moves
the cursor in a square, scrolls and clicks, printing `ok`/`fail` + hidapi
error for every command.

Your own program only needs the headers:

```cpp
#include "hubclient/mouse_client.hpp"
hubclient::MouseClient mouse;
if (!mouse.open(0x046D, 0xC53F)) return 1;
mouse.move(100, 0);
mouse.click(1);          // left click (logical button number 1..16)
mouse.wheel(-3);
```

## Warning: re-flashing after CDC is disabled

Once the patched firmware is uploaded, the Leonardo **no longer exposes a COM
port** to the host — it becomes a pure HID clone. To upload new firmware:

1. Press and **hold** the reset button on the Leonardo.
2. Run `arduino-hub flash ...` (or click Upload in Arduino IDE).
3. **Release** the reset button as soon as you see the upload begin (the IDE
   or `arduino-cli` will print `"Uploading..."` ).

The bootloader will create a temporary COM port during those few seconds.

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| `No HID devices found` | No HID devices connected, or `hidapi` backend is missing |
| `CLI command failed` | `arduino-cli` could not be downloaded, or the binary is corrupt. Delete `arduino-cli.exe` and re-run `setup` |
| `AVR core not found` | Run `arduino-hub setup` first |
| `Device 'X' not found` | Run `arduino-hub clone --name X` first |
| Upload fails with `can't open device` | Wrong COM port, or Leonardo is not in bootloader mode |
| Sketch compiles but upload fails on first attempt | The bootloader may need ~2 seconds after reset. Try running `flash` again |
| Cursor moves right when moving the mouse down (axes swapped) | Shield-side report data differs from the descriptor order (G305 quirk: descriptor/Windows list Y first, receiver sends X first behind the shield). Set the `wire_order` values for X/Y in `profiles/sources/<name>.json` to the physical order and re-run `patch`/`flash` |
| Right button does nothing, and the cursor freezes while it is held | Report ID byte was stripped twice (button byte `0x02` equals the report ID). Regenerate with a current `hid_mapper.h` — `decode_input()` detects the ID byte by report length |
| Side buttons do nothing in Windows | The 3-button target masks them out. Re-patch with `--target generic_5btn` (or `generic_16btn` for the full profile) and re-flash |
| `client_demo`: "command channel not found" | The firmware has no command channel (re-patch a target with `command.enabled: true` and re-flash), or the wrong VID/PID was passed |
| `client_demo` prints `fail` for every command | Run `client_demo --list` and check the `0xFF00` collection is present; the flashed firmware and the built client must come from the same `patch` run (re-patch → re-flash → rebuild client) |
| `client_demo` prints `ok` but the cursor does not move | Firmware receives packets but the handler does not run. Enable `HUB_CMD_MARKER` in `libraries/HubCommand/src/MouseCommandHandler.cpp`, re-flash, and check the LED blinks once per received packet (opcode = number of blinks) |

## Project structure

```
arduino-usb-hub/
├── pyproject.toml
├── arduino-cli.yaml           # config for arduino-cli binary
├── src/
│   └── arduino_hub/
│       ├── main.py            # CLI entry point (argparse)
│       ├── pipeline.py        # subcommand implementations
│       ├── exceptions.py      # custom exceptions
│       ├── logging_config.py
│       ├── devices.py         # load/save profiles/sources/<name>.json
│       ├── targets.py         # load/save profiles/targets/<name>.json (incl. command channel)
│       ├── usbhid/            # HID enumeration (renamed to avoid collision with hidapi)
│       │   ├── hid_items.py           # shared low-level HID descriptor item emitters
│       │   ├── hid_generator.py       # target report descriptor + hid_profile/mapper
│       │   ├── command_schema.py      # profiles/protocol/<name>.json schema (opcodes/payload)
│       │   └── command_generator.py   # command descriptor block, config + blob/opcode headers
│       ├── cli/               # Arduino CLI downloader, executor, manager
│       └── core/              # Core installer, Identity/CommandChannel/Library patchers
├── examples/
│   └── mouse/                 # Reference Arduino sketch (host replay + Cmd.poll())
├── profiles/
│   ├── sources/               # Cloned device descriptors (gitignored)
│   ├── targets/               # Output profiles (report layout + command channel wiring)
│   └── protocol/              # Command protocol schemas (mouse.json, future keyboard/gamepad)
├── libraries/
│   └── HubCommand/            # Device command channel library (copied to user libraries by patch)
├── pc_client/                 # C++ PC client (hidapi) + target headers written by patch (gitignored)
├── arduino-cli-data/          # Downloaded cores & libraries (gitignored)
└── docs/
    ├── usb-stack-audit.md        # USB stack compatibility analysis
    └── plans/                    # design notes (hid-clone-architecture,
                                  # interrupt-out-command-channel)
```

## License

GNU General Public License v3.0. See [LICENSE](LICENSE).
