import os
import subprocess
import platform
import requests
import zipfile


class ArduinoCLI:
    def __init__(self):
        self.arduino_cli_url = "https://downloads.arduino.cc/arduino-cli/arduino-cli_latest"
        self.arduino_cli_archive_path = None
        self.arduino_cli_path_exec = None
        self.arduino_cli_config_path = os.path.join(os.getcwd(), "arduino-cli.yaml")
        self.arduino_data_path = os.path.join(os.getcwd(), "arduino-cli-data")

    def download_arduino_cli(self):
        print("Скачивание Arduino CLI...")
        system = platform.system().lower()
        if system == "windows":
            arduino_cli_url = self.arduino_cli_url + "_Windows_64bit.zip"
        elif system == "linux":
            arduino_cli_url = self.arduino_cli_url + "_Linux_64bit.tar.gz"
        elif system == "darwin":
            arduino_cli_url = self.arduino_cli_url + "_macOS_64bit.tar.gz"
        else:
            raise Exception("Неподдерживаемая операционная система")

        self.arduino_cli_archive_path = os.path.join(os.getcwd(), arduino_cli_url.split("/")[-1])
        if not os.path.exists(self.arduino_cli_archive_path):
            response = requests.get(arduino_cli_url, stream=True)
            if response.status_code == 200:
                with open(self.arduino_cli_archive_path, "wb") as f:
                    f.write(response.content)
                print("Arduino CLI успешно скачан.")
            else:
                raise Exception("Не удалось скачать Arduino CLI")
        else:
            print("Arduino CLI уже скачан.")

    def extract_arduino_cli(self):
        print("Распаковка Arduino CLI...")
        system = platform.system().lower()
        if system == "windows":
            executable_name = "arduino-cli.exe"
        elif system in ["linux", "darwin"]:
            executable_name = "arduino-cli"
        else:
            raise Exception("Неподдерживаемая операционная система")

        self.arduino_cli_path_exec = os.path.join(os.getcwd(), executable_name)
        with zipfile.ZipFile(self.arduino_cli_archive_path, "r") as zip_ref:
            for file_info in zip_ref.infolist():
                if file_info.filename == executable_name:
                    zip_ref.extract(file_info, path=os.getcwd())
                    break
        print("Arduino CLI успешно распакован.")

    def install_avr_core(self):
        """Устанавливает AVR core."""
        print("Установка AVR core...")
        try:
            subprocess.run(
                [
                    self.arduino_cli_path_exec,
                    "--config-file",
                    self.arduino_cli_config_path,
                    "core",
                    "install",
                    "arduino:avr",
                ],
                check=True,
            )
            print("AVR core успешно установлен.")
        except subprocess.CalledProcessError as e:
            print(f"Ошибка при установке AVR core: {e}")
            raise

    def install_libraries(self, libraries):
        if not libraries:
            print("Не указаны библиотеки для установки.")
            return
        try:
            for lib in libraries:
                subprocess.run(
                    [
                        self.arduino_cli_path_exec,
                        "--config-file",
                        self.arduino_cli_config_path,
                        "lib",
                        "install",
                        lib,
                    ],
                    check=True,
                )
            print("Библиотеки успешно установлены.")
        except subprocess.CalledProcessError as e:
            print(f"Ошибка при установке библиотек: {e}")
            raise

    def compile_sketch(self, sketch_path):
        print("Компиляция скетча...")
        if not os.path.exists(sketch_path):
            raise Exception(f"Файл скетча не найден: {sketch_path}")

        try:
            subprocess.run(
                [
                    self.arduino_cli_path_exec,
                    "--config-file",
                    self.arduino_cli_config_path,
                    "compile",
                    "--fqbn",
                    "arduino:avr:leonardo",
                    sketch_path,
                ],
                check=True,
            )
            print("Скетч успешно скомпилирован.")
        except subprocess.CalledProcessError as e:
            print(f"Ошибка при компиляции скетча: {e}")
            raise

    def upload_sketch(self, sketch_path, port):
        print("Загрузка скетча на Arduino...")
        try:
            subprocess.run(
                [
                    self.arduino_cli_path_exec,
                    "--config-file",
                    self.arduino_cli_config_path,
                    "upload",
                    "-p",
                    port,
                    "--fqbn",
                    "arduino:avr:leonardo",
                    sketch_path,
                ],
                check=True,
            )
            print("Скетч успешно загружен.")
        except subprocess.CalledProcessError as e:
            print(f"Ошибка при загрузке скетча: {e}")
            raise
