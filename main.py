import os
import re
import hid
import json
from arduino_cli import ArduinoCLI


def hid_device_info():
    devices = hid.enumerate()
    for i, device in enumerate(devices):
        print(
            f"{i}: {device['manufacturer_string']} {device['product_string']} (VID: {device['vendor_id']:04X}, PID: {device['product_id']:04X})"
        )

    device_index = int(input("Введите индекс устройства:"))
    if device_index < 0 or device_index >= len(devices):
        print("Неверный индекс устройства.")
        return

    device_info = devices[device_index]
    print("Информация о устройстве:")
    for key, value in device_info.items():
        print(f"{key}: {value}")

    device = hid.device()
    device.open_path(device_info["path"])

    try:
        manufacturer = device.get_manufacturer_string()
        product = device.get_product_string()
        serial = device.get_serial_number_string()
        print(f"Manufacturer String: {manufacturer}")
        print(f"Product String: {product}")
        print(f"Serial Number String: {serial}")
    finally:
        device.close()

    return device_info


def patch_arduino_usb(arduino_path):
    usb_core_path = os.path.join(arduino_path, "cores", "arduino", "USBCore.cpp")
    with open(usb_core_path, "r", encoding="utf-8") as file:
        data = file.read()

    if not re.search(r"// CDC_GetInterface\(&interfaces\);", data, flags=re.MULTILINE):
        data = re.sub(r"(CDC_GetInterface\(&interfaces\);)", r"// \1", data, flags=re.MULTILINE)

        data = re.sub(
            r"(if \(CDC_ACM_INTERFACE == i\)\s*\n\s*)return CDC_Setup\(setup\);",
            r"\1// return CDC_Setup(setup);\n\t\treturn false;",
            data,
            flags=re.MULTILINE | re.DOTALL,
        )

    with open(usb_core_path, "w", encoding="utf-8") as file:
        file.write(data)


def patch_arduino_boards(arduino_path, mouse_info):
    boards_path = os.path.join(arduino_path, "boards.txt")
    with open(boards_path, "r") as file:
        data = file.read()

    vid = hex(mouse_info["vendor_id"])
    pid = hex(mouse_info["product_id"])
    product = mouse_info["product_string"]
    manufacturer = mouse_info["manufacturer_string"]

    data = re.sub(r"(leonardo\.build\.vid)\s*=\s*0x[0-9A-Fa-f]+", rf"\1={vid}", data)
    data = re.sub(r"(leonardo\.build\.pid)\s*=\s*0x[0-9A-Fa-f]+", rf"\1={pid}", data)
    data = re.sub(r'(leonardo\.build\.usb_product)\s*=\s*"([^"]*)"', rf'\1="{product}"', data)
    if not re.search(r"^\s*leonardo\.build\.usb_manufacturer=", data, flags=re.MULTILINE):
        data = re.sub(
            r'(leonardo\.build\.usb_product=".*?")',
            rf'\1\nleonardo.build.usb_manufacturer="{manufacturer}"',
            data,
            flags=re.DOTALL,
        )

    if not re.search(r"leonardo\.build\.extra_flags=\{build\.usb_flags\} -DCDC_DISABLED", data, flags=re.MULTILINE):
        data = re.sub(r"(leonardo\.build\.extra_flags=\{build\.usb_flags\})", r"\1 -DCDC_DISABLED", data)

    with open(boards_path, "w") as file:
        file.writelines(data)


if __name__ == "__main__":
    config = {}
    with open(("config.json"), encoding="utf-8") as file:
        data = json.load(file)
        config.update(data)

    arduinoCLI = ArduinoCLI()

    arduinoCLI.download_arduino_cli()
    arduinoCLI.extract_arduino_cli()
    arduinoCLI.install_avr_core()

    arduino_path = os.path.join(os.getcwd(), "arduino-cli-data", "packages", "arduino", "hardware", "avr", "1.8.6")
    patch_arduino_usb(arduino_path)
    mouse_info = hid_device_info()
    patch_arduino_boards(arduino_path, mouse_info)

    sketch_path = config["SKETCH"]
    arduinoCLI.compile_sketch(sketch_path)
    arduinoCLI.upload_sketch(sketch_path, config["PORT"])
