
# Arduino USB Hub - Arduino HID Device Emulator

This repository contains a Python script that facilitates the emulation of a HID (Human Interface Device) using an [Arduino Leonardo](https://docs.arduino.cc/hardware/leonardo/) and [USB Host Shield](https://docs.arduino.cc/retired/shields/arduino-usb-host-shield/). The script automates the process of modifying the Arduino core and board configuration to emulate a specific HID device based on the device's VID (Vendor ID) and PID (Product ID).

## Features

- **HID Device Selection**: The script lists all connected HID devices and allows you to select one by its index to clone.
- **Arduino Core Patching**: Automatically patches the `USBCore.cpp` file to disable CDC (Communication Device Class) functionality, which is necessary for HID emulation.
- **Board Configuration Patching**: Modifies the `boards.txt` file to update the VID, PID, product name, and manufacturer string to match the selected HID device.
- **Arduino CLI Integration**: Uses the Arduino CLI to download and install necessary cores and libraries, compile the sketch, and upload it to the Arduino board.

## Prerequisites

- Arduino Leonardo
- USB Host Shield
- Python 3.x
- Arduino CLI
- `hidapi` Python library

## Installation

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/netlcod/arduino-usb-hub.git
   cd arduino-usb-hub
   ```

2. **Install Python Dependencies**:
   ```bash
   pip install hidapi
   ```

3. **Install Arduino CLI**:
   The script will automatically download and install the Arduino CLI if it is not already installed. However, you can manually install it by following the instructions on the [Arduino CLI GitHub page](https://github.com/arduino/arduino-cli).

4. **Install Required Arduino Libraries**:
   The script will automatically install the necessary Arduino libraries (`Mouse`, `Keyboard`, `USB Host Shield Library 2.0`). If you want to install them manually, you can do so using the Arduino CLI or by editing `arduino-cli.yaml`.

## Usage

1. **USB HID Device** -> **USB Host Shield** -> **Leonardo** -> **Computer USB port**.

2. **Run the Script**:
   ```bash
   python main.py
   ```

3. **Select the HID Device**:
   The script will list all connected HID devices. Enter the index of the device you want to emulate.

4. **Compile and Upload the Sketch**:
   The script will automatically compile and upload the `.ino` sketch to your Arduino board. Make sure the `sketch_path` variable in the script points to the correct location of your `.ino` file.

5. **Verify the Emulation**:
   Once the sketch is uploaded, your Arduino board should emulate the selected HID device.

## Configuration

Specify the path to `.ino` and COM-Port in the `config.json`.

## Warning!

**Take precautions when working with the board.**

After successful flashing, the possibility to flash the Leonardo via COM port is disabled. 
In order to be able to update the firmware, it is necessary to reset Leonardo. To do this, press and hold the reset button on the Leonardo, then press the download sketch (any) button in the Arduino IDE. Release the reset button only after the message “Loading...” appears in the program status bar. In this case the bootloader will start, creating a new virtual serial/COM port on the computer.

## Troubleshooting

- **Device Not Found**: Ensure that the HID device is connected and recognized by your operating system.
- **Compilation Errors**: Make sure all required libraries are installed and the `config.json` file is correct.
- **Upload Errors**: Verify that the correct COM port is specified and that the Arduino board is properly connected.

## Acknowledgments

- [Arduino](https://www.arduino.cc/) for the Arduino platform.
- [cython-hidapi](https://github.com/trezor/cython-hidapi) for the Cython interface to [HIDAPI](https://github.com/libusb/hidapi) library.
- [USB Host Library Rev. 2.0](https://github.com/felis/USB_Host_Shield_2.0) for the library allowing to work with USB Host Shield.

## License

This project is licensed under the GNU Lesser General Public License v3.0. See the [LICENSE](LICENSE) file for details.
