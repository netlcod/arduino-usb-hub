# Arduino USB Hub — HID Device Emulator

Clones an existing USB HID device (mouse, keyboard, gamepad) onto an
[Arduino Leonardo](https://docs.arduino.cc/hardware/leonardo/) with a
[USB Host Shield](https://docs.arduino.cc/retired/shields/arduino-usb-host-shield/).
The tool automates the entire pipeline: detecting the target device, patching the
Arduino AVR core, compiling, and flashing the firmware.

## How it works

1. **Clone** — select a connected HID device and save its descriptors (VID, PID,
   manufacturer, product strings) to a JSON file.
2. **Patch** — apply two modifications to the installed Arduino AVR core:
   - `USBCore.cpp` — comment out CDC (serial port) so Leonardo presents itself
     purely as an HID device.
   - `boards.txt` — inject the cloned VID, PID, and string descriptors.
3. **Compile** — build the sketch using `arduino-cli`.
4. **Flash** — patch, compile, and upload in one command.

After flashing, the Leonardo is no longer recognised as a serial port — it
appears to the host as the cloned device.

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
read descriptors, and saves everything to `devices/<name>.json`.

```bash
arduino-hub clone --name mydevice --report devices/reports/usb-report.txt
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

If the source device sends its X/Y axis data in a different order than the
report suggests (known for some Logitech receivers), swap the `data_index`
values of the X (`0x30`) and Y (`0x31`) entries in `devices/<name>.json`
manually. Re-running `clone` restores the reported order, so repeat the fix
after re-cloning.

### `patch`

Applies both patches to the installed AVR core:

- **USB Core patch** — disables CDC in `USBCore.cpp` (idempotent).
- **Board patch** — writes the device VID, PID, and string descriptors into
  `boards.txt` (skipped if already matching).

```bash
arduino-hub patch --device mydevice --target generic_3btn
```

`--target` selects the output profile from `targets/` (default `g305`):
`generic_3btn` presents a standard 3-button mouse, `g305` reproduces the
native 9-byte report. Patching also generates `hid_profile.h`/`hid_mapper.h`
into the Mouse library and writes a manifest to `.build/patches.json`.

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
| `--target` | `g305` | Target profile name from `targets/` |
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
forward mouse HID reports from the cloned device to the Leonardo's built-in HID
peripheral.

Copy and adapt it for your own device.

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
| Cursor moves right when moving the mouse down (axes swapped) | Source device sends X before Y in report data. Swap the `data_index` values for X/Y in `devices/<name>.json` and re-run `patch`/`flash` |

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
│       ├── devices.py         # load/save devices/<name>.json
│       ├── usbhid/            # HID enumeration (renamed to avoid collision with hidapi)
│       ├── cli/               # Arduino CLI downloader, executor, manager
│       └── core/              # Core installer, USB/boards patcher
├── examples/
│   └── mouse/                 # Reference Arduino sketch
├── arduino-cli-data/          # Downloaded cores & libraries (gitignored)
├── devices/                   # Cloned device descriptors (gitignored)
└── docs/
    └── usb-stack-audit.md     # USB stack compatibility analysis
```

## License

GNU General Public License v3.0. See [LICENSE](LICENSE).
