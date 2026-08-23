# 0. Чистое состояние (опционально — полный прогон с нуля)
Remove-Item -Recurse -Force .build, arduino-cli-data, pc_client\build, pc_client\target -ErrorAction SilentlyContinue

# 1. Окружение
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"

# 2. Санити-проверка кода
pytest

# 3. Скачивание arduino-cli в .build/tools/ + AVR core + библиотеки из arduino-cli.yaml
arduino-hub setup

# 4. Клонирование девайса из отчёта USB Tree Viewer
arduino-hub clone --name g305 --report profiles/sources/reports/logitech-g305-report.txt
⚠️ После шага 4 обязательно верните wire_order override в profiles/sources/g305.json: ось X (0x30) → wire_order 16, ось Y (0x31) → wire_order 17 (data_index не трогать). Причина: дескриптор приёмника (и Windows) декларирует Y первой, но за USB Host Shield приёмник шлёт X первой (G305-кверк, см. Gotchas в AGENTS.md). Если профиль g305.json уже правильный — шаг 4 пропустите.
# 5. Патч ядра + генерация заголовков (для другого репо добавьте --client-out <dir>)
arduino-hub patch --device g305 --target generic_5btn

# 6. Компиляция скетча
arduino-hub compile --sketch examples/mouse/mouse.ino

# 7. Прошивка (после неё CDC отключён; перед следующей прошивкой — reset-танец)
arduino-hub flash --device g305 --target generic_5btn --sketch examples/mouse/mouse.ino --port COM6

# 8. Сборка PC-клиента (hidapi подтянется через FetchContent)
cmake -B pc_client/build pc_client
cmake --build pc_client/build --config Release

# 9. Запуск клиентского примера
pc_client\build\Release\client_demo.exe --list          # все HID-устройства
pc_client\build\Release\client_demo.exe                 # дефолт 046D:C53F (G305 receiver)
pc_client\build\Release\client_demo.exe 0x046D 0xC53F       # явно