# Arduino Hub: полный туториал по пайплайну

Исчерпывающее описание текущего пайплайна `arduino-hub`: от установки до прошивки
Leonardo, клонирующего реальное USB HID-устройство, с командным каналом
PC → Arduino.

Смежные документы: [usb-stack-audit.md](usb-stack-audit.md) — аудит USB-стека
AVR Core 1.8.6 (низкоуровневые детали); `docs/plans/` — планы по фичам.

---

## 1. Что делает инструмент

`arduino-hub` превращает Arduino Leonardo (ATmega32U4) в клон произвольной
USB-мыши:

1. **clone** — разбирает отчёт USB Device Tree Viewer реального устройства и
   сохраняет его дескрипторы в `profiles/sources/<name>.json`.
2. **patch** — патчит установленный AVR core 1.8.6 и библиотеки в
   `arduino-cli-data/`, чтобы прошивка представляла себя как клонируемое
   устройство (VID/PID/строки/дескрипторы) и выдавала целевой HID-репорт.
   Плюс встраивает командный канал PC → Arduino (feature/output/interrupt_out).
3. **compile / flash** — компилирует sketch и заливает.

Вся конфигурация — это JSON-профили: `profiles/sources/` (что клонируем),
`profiles/targets/` (что отдаём), `profiles/protocol/` (протокол командного канала).

## 2. Установка и окружение

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
# или: pip install arduino_hub
```

Рабочая директория инструмента — **текущая** (`Path.cwd()`). Все артефакты
создаются рядом с ней:

| Путь | Что это |
|------|---------|
| `.build/tools/arduino-cli.exe` | Arduino CLI (автозагрузка, только Windows) |
| `arduino-cli-data/` | Данные CLI: `packages/arduino/hardware/avr/<ver>/` (ядро), `user/libraries/` (патченные библиотеки) |
| `profiles/sources/<name>.json` | Клонированные профили устройств |
| `profiles/targets/<name>.json` | Целевые профили вывода |
| `profiles/protocol/<name>.json` | Схема командного протокола |
| `.build/patches.json` | Манифест последнего `patch` |
| `pc_client/target/` | Заголовки для клиента (пишутся `patch`, gitignored) |
| `examples/mouse/` | Референсный sketch: `Usb.Task()` + `Cmd.poll()` в loop() |

## 3. Быстрый старт

```bash
# 1. Установить Arduino CLI + AVR core 1.8.6 + библиотеки из arduino-cli.yaml
arduino-hub setup

# 2. Клонировать устройство из отчёта USB Device Tree Viewer
arduino-hub clone --name g305 --report profiles/sources/reports/g305.txt

# 3. Патчить ядро под устройство и целевой профиль
arduino-hub patch --device g305 --target generic_5btn

# 4. Скомпилировать sketch
arduino-hub compile --sketch examples/mouse/mouse.ino

# 5. Патч + компиляция + загрузка
arduino-hub flash --device g305 --target generic_5btn \
    --sketch examples/mouse/mouse.ino --port COM6
```

Все шаги **идемпотентны**: повторный запуск проверяет маркеры/состояние и
пропускает уже выполненное.

> **ВАЖНО про CDC**: после прошивки CDC отключён — Leonardo выглядит как
> клонированное HID-устройство без COM-порта. Для повторной прошивки:
> зажмите RESET, запустите `arduino-hub flash`, отпустите RESET в момент
> начала загрузки (подробнее в README, раздел Warning).

---

## 4. Пайплайн по шагам

### 4.1 `setup`

`pipeline.py: cmd_setup`:

1. `ArduinoCLIManager.ensure_cli()` — скачивает `arduino-cli.exe` нужной версии
   (`--cli-version`, по умолчанию 1.5.1), если бинарник отсутствует.
2. `ArduinoCoreInstaller.ensure_version()` — ставит AVR core
   (`--core-version`, по умолчанию 1.8.6). Распаковка в
   `arduino-cli-data/packages/arduino/hardware/avr/`.
3. Читает `arduino-cli.yaml` и через регулярное выражение `- name: "..."` ищет
   список библиотек, ставит их через `lib install` (например, USB Host Shield).

### 4.2 `clone`

`pipeline.py: cmd_clone` → `descriptor_reader.parse_report()`:

- принимает текстовый отчёт **USB Device Tree Viewer** (меню Device → Export
  To Text);
- извлекает: VID/PID, bcdDevice, классы, bcdHID, строки производителя/
  продукта, страну, endpoint (размер пакета/интервал), raw HID Report
  Descriptor (hex) и список коллекций;
- реконструирует из дескриптора репорт: report_id, длину, число кнопок,
  оси (usage page/usage/разрядность/логические мин-макс/relative),
  data_index (смещение поля в физическом репорте);
- сохраняет всё в `profiles/sources/<name>.json`.

Клонирование служит **источником** для `hid_mapper.h` (декодер исходного
репорта) и для `boards.txt`/`USBCore.cpp`/`HID.h` (дескрипторы устройства).

**Quirks**:
- G305 шлёт X раньше Y, хотя в репорте Y указан первым — в `profiles/sources/g305.json`
  `data_index` у осей переставлен вручную (X=16, Y=17). Повторный `clone`
  вернёт порядок отчёта — перестановку нужно восстанавливать вручную.
- Репорт может быть многоотчётным (Report ID ≠ 1). Код на устройстве
  определяет ID репорта **по длине**, а не по значению: при зажатой правой
  кнопке первый байт `0x02 == SRC_REPORT_ID`, поэтому `decode_input()` проверяет
  `len == SRC_REPORT_LEN && src[0] == SRC_REPORT_ID` (двойное срезание ломало
  правую кнопку).

### 4.3 `patch`

`pipeline.py: _patch_for_device` — сердце пайплайна. Порядок операций:

1. Загрузка профилей: `profiles/sources/<name>.json`, `profiles/targets/<target>.json`,
   `profiles/protocol/mouse.json` (схема нужна всегда — из неё генерируются
   `mouse_commands.h` и поля capability).
2. **Валидация профиля** (`core/validation.py: validate_identity`) — до любых
   записей: ERROR (кавычки/бэкслеш/контрол-символы в строках, пустые строки)
   ломают `-D` флаги и останавливают patch; WARN (длины >126,
   VID↔производитель по `KNOWN_VENDORS` — актуально для ручных правок профиля,
   диапазоны power/ep0/bcdUSB/интервал) логируются и попадают в манифест.
3. Все правки вычисляются **в памяти** как список `FileEdit`
   (`build_patch_edits()`); затем либо пишутся атомарно
   (`apply_edits()`: tmp + `os.replace`), либо печатаются диффом
   (`--dry-run`, `render_edits_diff()`). Состав правок:
4. `IdentityPatcher` — identity-паритетность с source-профилем (принцип
   «всегда явно»: значения пересобираются на каждом patch, даже совпадающие
   со стоком):
   - отключение CDC в `USBCore.cpp`;
   - `boards.txt`: `build.vid/pid`, `usb_product/manufacturer`,
     `extra_flags` = `{build.usb_flags} -DCDC_DISABLED -DUSB_EP_SIZE=16
     -DUSB_CONFIG_POWER=<mA> -DUSB_VERSION=0x<bcdUSB>
     -DUSB_CONFIG_ATTRIBUTES=0x<bmAttributes> -DHID_EP_INTERVAL=0x<bInterval>`;
     `USB_EP_SIZE=16` — чтобы interrupt-пакеты были по 16 байт
     (совместимость с hidapi-буфером `[ID][payload]`);
   - `USBCore.cpp`: в обеих ветках `D_DEVICE(...)` — EP0 maxPacketSize ←
     устройства, bcdDevice ← устройства, iSerialNumber → 0;
   - `USBCore.h`: guard `#ifndef USB_CONFIG_ATTRIBUTES` вокруг атрибутов
     конфигурации в `D_CONFIG` (версия `// HUB_PATCH_VERSION 1`);
   - `HID.h`: литерал `D_HIDREPORT` переписывается целиком — **оба байта**
     bcdHID ← устройства + bCountryCode ← устройства;
   - `HID.cpp`: subclass/protocol интерфейса → boot mouse policy
     (`HID_SUBCLASS_BOOT_INTERFACE, HID_PROTOCOL_MOUSE`; намеренно не из JSON);
5. `CommandChannelPatcher.patch_hid_command_core()` (v3) — маркер-охраняемые
   правки HID.h/HID.cpp командного канала **плюс**: `HID_EP_INTERVAL` define +
   его использование в обоих `D_ENDPOINT` (IN и interrupt OUT), корректные
   ответы GET_IDLE/GET_PROTOCOL (`USB_SendControl(0, &idle|&protocol, 1)`)
   вместо stall/пустого пакета. Если канал включён, а правка не легла —
   пайплайн падает с `PatchError`.
6. `write_hid_command_config()` / `write_hid_capability_blob()` — генерация
   `hid_command_config.h` / `hid_capability_blob.h` в ядро (переписываются
   на каждом patch — пер-таргетная проводка).
7. `LibraryPatcher.patch_mouse_library()` — генерирует `hid_profile.h`
   (таргетный HID-дескриптор + vendor-коллекция канала) и `hid_mapper.h`,
   маркер-охраняемо правит Mouse.cpp/h. Смена `--target` регенерирует только
   заголовки.
8. `install_hub_command_library()` + `write_pc_client_headers()` — копия
   библиотеки, `mouse_commands.h`, заголовки PC-клиента.
9. `write_patch_manifest()` — `.build/patches.json`: device/target/время +
   описание патчей (включая `validation`) + sha256 генератов.

### 4.4 `compile`

`pipeline.py: cmd_compile`:

- резолвит путь к `.ino`, проверяет существование;
- `ArduinoCoreInstaller.find_path()` — ищет ядро нужной версии в
  `arduino-cli-data/packages/arduino/hardware/avr/` (без хардкода путей);
- `executor.compile(sketch, fqbn)` — `arduino-cli compile` с FQBN
  (`--fqbn`, по умолчанию `arduino:avr:leonardo`).

Компилируется и патченное ядро, и sketch — поэтому сломанный патч падает
здесь, громко и до прошивки.

### 4.5 `flash`

`pipeline.py: cmd_flash` = `_patch_for_device` + `compile` + `upload`.
Нужен `--port` (COM-порт, например `COM6`). После загрузки CDC отключён —
для повторной прошивки смотрите процедуру с RESET в разделе 3.

---

## 5. Конфигурационные файлы

### 5.1 `profiles/sources/<name>.json`

Профиль клонированного устройства (см. `profiles/sources/g305.json`):

| Поле | Назначение |
|------|------------|
| `vendor_id` / `product_id` | VID/PID → `boards.txt` (build.vid/build.pid) |
| `bcd_device` | Версия устройства → `D_DEVICE` в `USBCore.cpp` |
| `max_power_ma` | → `-DUSB_CONFIG_POWER=` |
| `bcd_hid` | → младший байт HID-дескриптора в `HID.h` |
| `manufacturer_string` / `product_string` | → `boards.txt` usb_manufacturer/usb_product |
| `report_id` / `report_length` / `button_count` | Метаданные исходного репорта |
| `axes[]` | Оси источника: usage, bits, logical min/max, relative, `data_index` (смещение в физическом репорте) |
| `hid_report_descriptors[]` | Raw-дескрипторы (справочно для аудита) |

### 5.2 `profiles/targets/<name>.json`

Профиль **вывода** — какой репорт Leonardo отдаёт (см. `generic_3btn.json`,
`generic_5btn.json`, `generic_16btn.json`):

| Поле | Назначение |
|------|------------|
| `report_id` | ID репорта мыши (1 для Arduino-совместимых, 2 для полного клона) |
| `report_length` | Длина репорта в байтах |
| `buttons` | Число кнопок (3 / 5 / 16) |
| `layout` | `arduino` — кнопки разом, затем оси (Arduino Mouse-совместимо); `per_axis` — сборка как у Logitech (каждая ось со своими Usage/Count) |
| `axes[]` | Оси вывода: usage_page/usage, bits, logical min/max, relative |
| `command.enabled` / `command.transport` / `command.queue_slots` | Проводка канала: `feature` / `output` / `interrupt_out`; слоты кольца 4–8 |
| `capability.enabled` | Опциональный GET_REPORT(Feature) capability-репорт (id 4, payload 8) |

Что выбрать: `generic_3btn` — совместимость с Arduino Mouse API (канал off);
`generic_5btn` — боковые кнопки (Back/Forward), interrupt_out;
`generic_16btn` — полный клон (16 кнопок, 16-бит X/Y, Wheel, AC Pan, ReportID 2,
команды через feature).

**После правки `profiles/targets/*.json` или `profiles/protocol/mouse.json` обязательно
перепатчить** (`arduino-hub patch`) — сгенерированные заголовки устройства и
`pc_client/target/*` должны приходить из одного прогона с прошивкой.

### 5.3 `profiles/protocol/mouse.json`

Source of truth протокола канала:

```json
{
  "name": "mouse",
  "device_type": 1,
  "protocol_version": 1,
  "commands": [
    { "name": "MOVE",       "opcode": 1, "args": [{"name": "dx", "type": "i16"}, {"name": "dy", "type": "i16"}] },
    { "name": "BUTTON_DOWN", "opcode": 2, "args": [{"name": "button", "type": "u8"}] },
    { "name": "BUTTON_UP",  "opcode": 3, "args": [{"name": "button", "type": "u8"}] },
    { "name": "CLICK",      "opcode": 4, "args": [{"name": "button", "type": "u8"}] },
    { "name": "WHEEL",      "opcode": 5, "args": [{"name": "delta", "type": "i8"}] }
  ]
}
```

- Типы аргументов: `u8`, `i8`, `u16`, `i16` (little-endian).
- Из схемы генерируется `mouse_commands.h` (опкоды + размеры пакетов) для
  устройства **и** PC-клиента; валидируется против payload 15 на каждом patch.
- Номера кнопок — **логические 1..16**, мапятся на биты в
  MouseCommandHandler (протокол не зависит от таргета: работает для 3/5/16).
- Невалидные значения (не-инт в `opcode`/`device_type`/`protocol_version`)
  дают `CommandSchemaError`, а не сырой `ValueError`.

### 5.4 `arduino-cli.yaml`

Показывает, какие библиотеки ставит `setup` (например, USB Host Shield):

```yaml
libraries:
  - name: "USB Host Shield 2.0"
```

---

## 6. Командный канал PC → Arduino

### 6.1 Фиксированные константы (не конфигурируются)

| Константа | Значение | Где живёт |
|-----------|----------|-----------|
| Command Report ID | 3 | `command_generator.py: CMD_REPORT_ID` |
| Payload (descriptor REPORT_COUNT) | 15 | `CMD_PAYLOAD_LEN` |
| hidapi-буфер | 16 (`[ID][payload×15]`) | `CMD_TOTAL_LEN` |
| Capability Report ID | 4 | `CAP_REPORT_ID` |
| Capability payload | 8 (буфер 9) | `CAP_PAYLOAD_LEN` |
| Usage Page / Usage | 0xFF00 / 0x01 | `CMD_USAGE_PAGE/USAGE` |

### 6.2 Проводка в ядре (патч HID.h/HID.cpp)

Патч `patch_hid_command_core` добавляет:

- `hid_command_config.h` include в HID.h; `epType[2]`;
- член `EndpointDescriptor out;` в `HIDDescriptor` и read-API
  (`availableReportPackets`/`readReportPacket`, а при interrupt_out — ещё
  `availableOutReport`/`readOutReport`);
- кольцевой буфер в классе HID_:
  `_cmdRing[QUEUE_SLOTS][PAYLOAD_LEN]`, `_cmdHead/_cmdTail/_cmdCount`;
- в `HID.cpp`:
  - `_capabilityReport[] PROGMEM` (магик `'H' 'C'` + транспорт, id, payload,
    слоты, device type, версия протокола) — только при `capability.enabled`;
  - GET_REPORT(Feature, id 4) отдаёт capability; неподдерживаемые репорты —
    stall (`return false`) вместо пустого ZLP;
  - SET_REPORT(Output|Feature, id 3): bounded copy в кольцо. Разбор формата:
    - `reportId == 0` — id в первом байте данных, payload = `length - 1`;
    - `reportId == 3 && length == 16` — Windows дублирует id байтом в data
      stage: если `scratch[0] == 3`, срезаем первый байт;
    - иначе payloadLen = 15;
    - несоответствие id → `return false`;
    - **проваленный `USB_RecvControl` → `return false`** (никакого мусора
      в кольце);
    - слот **обнуляется перед записью** (`memset`), чтобы короткий data stage
      не оставил stale-хвост от предыдущего пакета;
    - drop-new при полном кольце;
  - конструктор: число endpoint'ов (2 только для interrupt_out), инициализация
    кольца, `epType[1] = EP_TYPE_INTERRUPT_OUT`;
  - `getInterface()`: 2 endpoint'а (IN + OUT) только для interrupt_out;
  - `readReportPacket()` — из кольца, под `cli()/sei()` (атомарность против
    ISR-инкремента `_cmdCount`), возвращает 15 байт;
  - `readOutReport()` — interrupt OUT: `USB_Recv(pluggedEndpoint+1, ...)`,
    id = первый байт (16 = `[0x03][payload]`), срезается; буфер zero-init.

Важно: **ядро только буферизует** — никакой семантики команд в
control-ISR/передачах. Обработка — только в `loop()` через `Cmd.poll()`.
Никогда не вызывайте Mouse API из обработчика SET_REPORT.

### 6.3 Транспорты

| transport | Wire-механика | Платформы |
|-----------|---------------|-----------|
| `feature` | `HidD_SetFeature`/`hid_get_feature_report` → SET_REPORT(Feature) через control EP0 | Windows + Linux |
| `output` | `HidD_SetOutputReport` → SET_REPORT(Output) через EP0 | **Windows only**: на Linux `hid_write` — raw `write()` на hidraw-fd, требует interrupt OUT endpoint, без него падает |
| `interrupt_out` | interrupt OUT endpoint (второй EP на том же HID-интерфейсе), поллится `USB_Recv` в loop() | Windows + Linux |

Правило выбора: `feature` — кросс-платформенный; `interrupt_out` — когда
устройство должно опрашивать команды из `loop()`; `output` — только Windows.
Устройство само подбирается клиентом через `hid_enumerate` + фильтр usage
page 0xFF00 (vendor-коллекция живёт на том же HID-интерфейсе — второго
интерфейса нет).

### 6.4 Протокол (payload, 15 байт)

| Opcode | Команда | Аргументы | Замечания |
|--------|---------|-----------|-----------|
| 1 | MOVE | dx i16, dy i16 | относительное движение |
| 2 | BUTTON_DOWN | button u8 (1..16) | `Mouse.press(1 << (b-1))` |
| 3 | BUTTON_UP | button u8 | `Mouse.release(...)` |
| 4 | CLICK | button u8 | `Mouse.click(...)` |
| 5 | WHEEL | delta i8 | `Mouse.move(0,0,delta,0)` |

Хвост пакета дополняется нулями (клиент) и/или нулевой-падит ядро — обработчик
видит фиксированные 15 байт, проверки длины (`n >= 2`, `n >= 5`) всегда
проходят на корректных данных.

### 6.5 Сборка PC-клиента

```bash
cmake -B pc_client/build pc_client
cmake --build pc_client/build --config Release
```

- hidapi 0.15 через FetchContent; POST_BUILD копирует `hidapi.dll` рядом с exe.
- `hid_enumerate(vid, pid)` без serial-аргумента; выбор топ-коллекции —
  vendor (0xFF00).
- `pc_client/include/hubclient/`: `transport.hpp` (feature/output/
  interrupt_out), `capability.hpp` (GET_REPORT(Feature) — `valid()` проверяет
  транспорт **и** `commandReportId == HID_COMMAND_REPORT_ID`,
  `payloadLen == HID_COMMAND_PAYLOAD_LEN`), `mouse_client.hpp`,
  `examples/client_demo.cpp`.

### 6.6 Диагностика

- `HUB_CMD_MARKER` в `libraries/HubCommand/src/MouseCommandHandler.cpp` —
  мигает LED_BUILTIN `opcode` раз на пакет (0 = выкл; на каждый `patch`
  библиотека копируется заново, правьте источник и перепатчивайте).
- `client_demo --list` — VID/PID/usage всех HID-устройств; каждая команда
  печатает ok/fail + hidapi-ошибку.
- `arduino-hub -v ...` — debug-логи.

---

## 7. Как устроена прошивка (артефакты в `user/libraries/`)

### `Mouse/src/hid_profile.h`

Таргетный HID Report Descriptor (`HID_DESCRIPTOR`, `HID_REPORT_ID`,
`HID_REPORT_LENGTH`) — mouse-коллекция из `profiles/targets/<t>.json` + vendor-
коллекция канала (при `command.enabled`). Mouse-коллекция байт-в-байт
совпадает со сборками без канала.

### `Mouse/src/hid_mapper.h`

`decode_input(src, dst)` — исходный репорт (9 байт G305) → внутреннее
`MouseState` (по `data_index`); `encode_output(state, dst)` — внутреннее
состояние → таргетный репорт (16 кнопок, X/Y 16-бит, wheel, pan).
`SRC_*`/`TGT_*` константы — из `profiles/sources/` и `profiles/targets/`.

### `HubCommand/`

- `CommandTransport` (feature/output/interrupt_out/None) поверх
  `HID().readReportPacket`/`readOutReport`;
- `DeviceCommandHandler`, `MouseCommandHandler` (опкоды → Mouse API);
- `mouse_commands.h` — генерируется из `profiles/protocol/mouse.json`.

### `Mouse.cpp` / `Mouse.h`

Патченные один раз (маркер-охраняемо): `move(int16_t,...)` → encode →
`SendReport(HID_REPORT_ID, ...)`; кнопки `uint16_t` (`press(bit)` принимает
биты до `0x8000`; `encode_output` маскирует до `TGT_BUTTONS_DATA_BITS`).

### `examples/mouse/mouse.ino`

Референс для Leonardo + USB Host Shield:

```cpp
Usb.Task();   // поллинг USB Host Shield (приём пакетов от оригинального приёмника)
Cmd.poll();   // обработка командного канала из loop()
```

Скетч-макросы (например, дефайны конфигурации) **должны лежать в
`hidmouserptparser.h`**, а не в `.ino` — `.ino` это отдельная единица
трансляции, её `#define` невидимы для `hidmouserptparser.cpp`.

---

## 8. Справочник CLI

| Команда | Опции | Действие |
|---------|-------|----------|
| `setup` | `--cli-version`, `--core-version` | Скачать CLI, установить AVR core и библиотеки |
| `clone` | `--name`, `--report` | Парсинг отчёта → `profiles/sources/<name>.json` |
| `patch` | `--device`, `--target` (default `generic_3btn`), `--client-out <dir>`, `--dry-run` | Полный набор правок ядра + генерация заголовков + валидация + манифест; `--client-out` экспортирует заголовки PC-клиента во внешний каталог; `--dry-run` печатает unified diff всех правок и отчёт валидации, ничего не пишет и не скачивает инструментарий (нужен установленный `setup`; код 1 при ERROR-валидации) |
| `compile` | `--sketch`, `--fqbn` (default `arduino:avr:leonardo`) | Компиляция sketch'а |
| `flash` | `--device`, `--target`, `--sketch`, `--port`, `--fqbn` | Патч + компиляция + загрузка |

Глобально: `-v/--verbose`. Все команды (кроме `clone`) принимают
`--cli-version`/`--core-version`.

Ошибки: любые `ArduinoHubError` логируются и завершают процесс с кодом 1
(`main.py`), `Ctrl+C` — код 0.

---

## 9. Частые проблемы и как их диагностировать

| Симптом | Причина / решение |
|---------|-------------------|
| Нет COM-порта после флеша | CDC отключён по дизайну. Прошивка заново: RESET → flash → отпустить RESET |
| Пропал командный канал, а манифест говорит patched | Старый патчер без проверки; перепатчить свежим `arduino-hub patch` — теперь пайплайн падает с `PatchError`, если правка не легла |
| Не работает правая кнопка | ID репорта определяется по длине; проверьте, что не двойное срезание id-байта |
| X/Y перепутаны после `clone` G305 | `data_index` переставляется вручную (см. 4.2) |
| `HidD_SetFeature: ERROR_GEN_FAILURE` при bring-up | Windows шлёт data stage длиной 16 с продублированным id-байтом; разбор формата см. 6.2 |
| `transport=output` не работает на Linux | Ожидаемо: hid_write без interrupt OUT endpoint не работает. Используйте `feature` |
| Плавает поведение канала | Проверьте, что `feature`-клиент и прошивка собраны из одного `patch` (и `pc_client/target` перегенерирован) |
| Смена `--target` ничего не изменила в Mouse.cpp | Это нормально: регенерируются только `hid_profile.h`/`hid_mapper.h`; правки Mouse.cpp/h маркер-охраняемые |
| В дампе bcdHID = 0x1101 вместо 0x0111 | Ядро пропатчено старой версией патчера (байт в HIGH-позиции). Один свежий `patch` мигрирует литерал (см. docs/plans/enumeration-identity-parity.md §3.1) |
| patch упал с «validation failed» | Профиль источника сломан вручную: кавычки/бэкслеш в строках или пустые строки. Исправьте JSON — ERROR-поля ломают `-D` компиляции |
| Хочу посмотреть, что изменит patch | `arduino-hub patch ... --dry-run` — unified diff всех файлов без записи |
| После flash: Problem 43, «сбой запроса дескриптора конфигурации», device descriptor при этом читается | Ядро патчено версией, декларировавшей EP0 < 64 без синхронных правок аллокации и SendControl (см. ниже) |
| Устройство полностью исчезает из системы после flash, LED заморожен | Ядро патчено версией с банком EP0=32, но без фикса маски в `SendControl` (`& 0x3F` = каждые 64 байта): байт №33 конфиг-дескриптора пишется в полный FIFO → зависание внутри control-ISR. Свежий `patch` ставит `-DUSB_EP0_MAX_PACKET=N`, `USB_EP0_ALLOC` и замену маски на `% USB_EP0_MAX_PACKET`; перезапустите patch → flash |

---

## 10. Структура кода (карта)

```
src/arduino_hub/
  main.py            — CLI entrypoint (argparse) + обработка ошибок
  pipeline.py        — cmd_setup/clone/patch/compile/flash, _patch_for_device
  exceptions.py      — иерархия ArduinoHubError (PatchError, CommandSchemaError, ...)
  logging_config.py  — логирование (-v)
  devices.py         — load/save profiles/sources/<name>.json
  targets.py         — load profiles/targets/<name>.json
  cli/
    downloader.py    — ArduinoCLIDownloader (скачивание + распаковка arduino-cli.exe)
    executor.py      — ArduinoCLIExecutor (тонкая обёртка subprocess)
    manager.py       — ArduinoCLIManager: ensure_cli() → executor
  core/
    installer.py     — ArduinoCoreInstaller: ensure_version(), find_path()
    patcher.py       — IdentityPatcher/CommandChannelPatcher/LibraryPatcher: все правки ядра + генераторы + манифест
  usbhid/
    device_info.py   — DeviceInfo/AxisSpec/TargetProfile/CommandProfile/CapabilityProfile
    descriptor_reader.py — ReportParser: отчёт → DeviceInfo (реконструкция репорта + оси)
    hid_generator.py — generate_report_descriptor (arduino/per_axis), hid_profile_h, hid_mapper_h
    command_schema.py — CommandSchema/CommandDef/CommandArg + load (валидация, типы)
    command_generator.py — командный дескриптор, hid_command_config.h,
                           mouse_commands.h, capability-блоб (константы протокола)
    enumerator.py    — HIDEnumerator (legacy, пайплайном не используется)
```

Пайплайн работает с **файлами ядра на диске** (никакого покомпиляционного
патчинга): правки применяются к исходникам, а собственно изменение поведения
прошивки происходит через компиляцию этих исходников в `compile`/`flash`.
