# План рефакторинга arduino-usb-hub (без функциональных изменений)

Статус: **внедрён** (R1–R7, R11, wire_order, device_kind, R6 — выполнены;
поведение pipeline не изменилось, 50 тестов зелёные, PC-клиент собирается).

Историческая справка: план фиксирует решения аудита и порядок фаз.
Реализация прошла по фазам 1–4, включая R6 (разбиение USBPatcher на
`IdentityPatcher` / `CommandChannelPatcher` / `LibraryPatcher`).

## Контекст

Аудит показал: архитектура в целом здоровая (source/target split,
build-time generation, layering command channel). Rewrite не нужен.
Рефакторинг — точечный: закрыть реальные риски (дрейф патча ядра),
убрать дублирование, подготовить точки расширения для Keyboard/Gamepad.

## Зафиксированные решения

1. **wire-порядок source-полей** — отдельное source-only поле
   `AxisSpec.wire_order` (0..N-1); `data_index` остаётся информационным
   descriptor-ординалом из USB Tree Viewer. Расхождение
   wire-порядок ≠ descriptor-порядок валидируется и попадает в
   `.build/patches.json` (warning). Это фиксирует кварк G305
   («descriptor говорит Y-first, железо шлёт X-first») явно, а не
   ручной правкой `data_index`.
2. **capability blob** — отдельный generated header
   `hid_capability_blob.h`; `HID.cpp` только `#include` и отдаёт готовый
   массив, не описывая структуру руками.

## Общие принципы

- Не обобщать `MouseState` в универсальный `ReportState`. Вместо этого
  отдельные `MouseState` / `KeyboardState` / `GamepadState` + общий
  низкоуровневый HID descriptor/report infrastructure (`hid_items.py`).
- Генерировать только wire-format определения (opcodes, offsets,
  sizes, blob bytes), не поведение/handler'ы.
- R6 (разбиение USBPatcher) — только ПОСЛЕ фаз 1–3, когда реальные
  границы станут очевидны.

---

## Фаза 1 — устранить реальные риски (почти без изменения архитектуры)

### R1 — версионирование патча ядра

Место: `src/arduino_hub/core/patcher.py`, `patch_hid_command_core`.

Проблема: presence-маркеры не отличают «запатчено старым патчером» от
«запатчено текущим». Фактически подтверждено: локальный
`arduino-cli-data/.../HID.cpp` содержит устаревший `readReportPacket`
(без `cli()/sei()` и без `memset` zero-pad), но маркер
`int HID_::availableReportPackets` уже есть — re-patch молча пропускает
апгрейд.

Изменение: заменить presence-маркеры на version-маркер
`// HUB_PATCH_VERSION N`; при смене `N` переприменять патч-блок.

Проверка:
- `tests/test_patcher.py` (`test_patch_hid_command_core*`) — идемпотентность
  и содержимое.
- Ручной re-patch поверх существующего `arduino-cli-data` → `readReportPacket`
  получает `cli/sei` + `memset` (сейчас не получает).

### R7 — фикстура G305

Место: `tests/`.

Проблема: `test_patcher.py`, `test_descriptor_generator.py`,
`test_encode.py`, `test_decoder.py` читают `load_device("g305", REPO_ROOT)`
из gitignored `profiles/sources/g305.json`. Свежий клон → тесты падают.

Изменение: закоммитить `tests/fixtures/g305_device.json`; тесты читают
фикстуру (функция загрузки source-профиля для тестов).

Проверка: `pytest` зелёный в свежем клоне без `profiles/sources/`.

### R11 — валидация TargetProfile

Место: `src/arduino_hub/targets.py` / `src/arduino_hub/usbhid/hid_generator.py`.

Проблема: `report_length`, `buttons`, `axes` нигде не проверяются на
согласованность; ошибка в JSON → молчаливый рассинхрон дескриптора и
репорта.

Изменение: в `load_target`/`generate_report_descriptor` валидировать:
- `report_length == compute_report_layout(...).payload_len + (report_id ? 1 : 0)`;
- `buttons` влезают в `report_length`;
- уникальность `wire_order` source-профилей (см. модель wire-порядка).

Проверка: `pytest` + `arduino-hub patch` на всех трёх targets
(`generic_3btn/5btn/16btn`).

---

## Фаза 2 — убрать дублирование

### R2 — общий `usbhid/hid_items.py`

Место: `src/arduino_hub/usbhid/hid_generator.py`,
`src/arduino_hub/usbhid/descriptor_reader.py`.

Проблема: низкоуровневые HID item-эмиттеры продублированы —
`_append_logical` (byte-в-byte одинаков), `_append_usage_page`,
`_append_usage`, `_append_report_count` инлайн в обоих файлах.

Изменение: вынести в `usbhid/hid_items.py`; обе стороны переключаются
на него. Это же — базовый слой для будущих Keyboard/Gamepad
дескрипторов.

Проверка: `test_descriptor_generator.py` (byte-точное сравнение),
`test_decoder.py`.

### R3 — `hid_capability_blob.h` (единый источник truth для capability)

Место: `src/arduino_hub/usbhid/command_generator.py`,
`src/arduino_hub/core/patcher.py`.

Проблема: `generate_capability_blob()` покрыт тестом, но не вызывается
пайплайном; `_capabilityReport[]` зашит строкой в патче HID.cpp.
Дублирование layout + мёртвый код.

Изменение:
- Новый генератор `generate_hid_capability_blob_h(command, schema)` →
  заголовок с `static const uint8_t HID_CAPABILITY_BLOB[] PROGMEM = {…}`
  (числовые байты из `generate_capability_blob`).
- Патчер пишет заголовок в HID lib dir.
- HID.cpp-патч: `_capabilityReport`-блок заменяется на
  `#include "hid_capability_blob.h"` + использование
  `HID_CAPABILITY_BLOB`. Структура blob живёт в одном месте
  (`command_generator.py`), ядро только отдаёт массив.

Проверка: `test_capability_blob_layout`, обновлённый
`test_patch_hid_command_core` (ассерты на `_capabilityReport` → на
`HID_CAPABILITY_BLOB`/include).

### R4 — сгенерированные wire-format определения команд

Место: `src/arduino_hub/usbhid/command_schema.py`,
`src/arduino_hub/usbhid/command_generator.py`,
`libraries/HubCommand/src/MouseCommandHandler.cpp`,
`pc_client/include/hubclient/mouse_client.hpp`.

Проблема: wire-format размножен руками — `read_i16(&pkt[1])`,
`&pkt[3]`, `n >= 5`, `n >= 2` в устройстве и `pkt[1..4]`,
`MOUSE_CMD_MAX_PACKET` в клиенте. Два источника, рассинхрон при
добавлении команды.

Изменение: `command_schema`/`command_generator` добавляют в
`mouse_commands.h` макросы вида `MOUSE_CMD_<NAME>_<ARG>_OFF` /
`MOUSE_CMD_<NAME>_<ARG>_SIZE` (и `MOUSE_CMD_<NAME>_LEN`). Обе стороны
используют их вместо hardcoded offsets. Только wire-format, не
поведение.

Проверка: `test_command_generator.py` + `client_demo` против прошивки
(MOVE/CLICK/WHEEL) — ok/fail как раньше.

---

## Фаза 3 — подготовить расширение (Keyboard/Gamepad)

### R5 — де-«mouse»-изация генератора команд

Место: `src/arduino_hub/usbhid/command_generator.py`,
`src/arduino_hub/core/patcher.py`.

Проблема: генератор схемы генеричен (любой device), но имя
функции/файла/enum жёстко mouse: `generate_mouse_commands_h`,
`mouse_commands.h`, `MouseOpcode`.

Изменение: `generate_commands_h(schema)`, имя файла и `enum` выводятся
из `schema.name`. Для текущего schema выходной файл `mouse_commands.h`
остаётся идентичным по содержимому.

Проверка: `test_command_generator.py`; сравнение сгенерированного
`mouse_commands.h` до/после (device + pc_client/target).

### device_kind в TargetProfile

Место: `src/arduino_hub/usbhid/device_info.py`, `src/arduino_hub/targets.py`,
`src/arduino_hub/usbhid/hid_generator.py`.

Изменение: `TargetProfile.device_kind` (default `"mouse"`; значения
`mouse | keyboard | gamepad`). На текущем этапе — только поле и
сериализация; поведение генератора не меняется (mouse-путь как сейчас).
Параметризация header дескриптора (usage 0x02 vs 0x06 vs 0x05) — при
появлении первого реального second use case.

Проверка: `pytest`; round-trip target JSON (profiles/sources/targets serialization).

---

## Фаза 4 — структура (только после фаз 1–3)

### R6 — разбиение USBPatcher

Место: `src/arduino_hub/core/patcher.py` (835 строк, god-object).

Проблема: identity-патчи, HID-патчи, command channel, установка
библиотек, PC-заголовки в одном классе.

Изменение: разбить на 2–3 сервиса ПО ФАКТУ выявленных границ (решение
принимается после фаз 1–3, когда станет видно, какие сервисы реально
нужны). НЕ делать полноценный набор классов заранее. Кандидаты:
`IdentityPatcher` (boards/USBCore/HID identity), `CommandChannelPatcher`
(core HID + capability), `LibraryInstaller`/`HeaderWriter` (HubCommand,
pc_client, mouse_commands).

Проверка: весь `test_patcher.py` без изменения сигнатур наружу
(`pipeline.py` вызывает статики USBPatcher).

---

## Модель wire-порядка (X/Y order) — параллельно с фазами

Проблема (из аудита): `data_index` перегружен — это descriptor-ординал
из USB Tree Viewer, но mapper использует его как ключ сортировки
физического wire-порядка. У G305 эти порядки расходятся
(дескриптор: Y-first; железо: X-first), фикс — ручной swap `data_index`,
невидимый и теряемый на re-clone. Три источника (дескриптор-байты,
`axes[].data_index`, `USAGE_FIELD`) уже разошлись в `profiles/sources/g305.json`
и держатся вместе только «по совпадению» (target-список и source-swap
симметрично гасят друг друга).

Изменения:
- `AxisSpec.wire_order: int = 0` (source-only; `targets.py` не
  сериализует).
- `devices.py` сериализует `wire_order` (backward-compat: ключа нет →
  `wire_order` = порядок по `data_index`).
- `descriptor_reader.build_device_info` инициализирует
  `wire_order = vc.data_index`.
- Фикс G305: swap `wire_order` (X-first), а не `data_index`.
- `compute_report_layout()` принимает явный порядок полей (source →
  `wire_order`, target → порядок списка); без молчаливого
  `sorted(by data_index)`.
- Валидация в `generate_hid_mapper_h`: уникальность `wire_order`, сумма
  бит == payload; `logger.warning` + запись в `.build/patches.json`, когда
  `wire_order`-порядок ≠ descriptor-порядку (Logitech-кварк видим).
- Фикстура R7 защёлкивает `SRC_X_OFF == 16` и `SRC_Y_OFF == 32`.

Проверка: `test_decoder.py`/`test_encode.py` (офсеты), новый тест: порядок
без swap (как после re-clone) даёт warning/валидацию.

Примечание: кварк неустраним автоматизацией (descriptor-порядок ≠
wire-порядок определяется только эмпирически), поэтому цель — явное,
валидируемое, тест-защёлкиваемое переопределение, а не его удаление.

---

## Приоритеты (A/B/C/D)

| ID | Приоритет | Суть |
|----|-----------|------|
| R1 | A | Версионирование патча ядра (реальный риск дрейфа) |
| R2 | A | Общий `hid_items.py` (дублирование дескрипторного движка) |
| R5 | B | Де-«mouse»-изация command generator (блокер Keyboard/Gamepad) |
| R11 | B | Валидация TargetProfile / wire_order |
| R7 | B | Фикстура G305 (тесты из коробки) |
| R3 | B | `hid_capability_blob.h` (единый источник truth capability) |
| R4 | B | Generated wire-format определения команд |
| R6 | B | Разбиение USBPatcher — последним |
| — | C | `enumerator.py`, legacy-формат, неиспользуемые exceptions — не трогать |
| — | D | Генерация handler'ов/поведения, runtime HID-парсер, универсальный ReportState — не делать |

## Future-proofing

Заложить уже сейчас:
1. R1 — версионированный патч-маркер (предотвращает дрейф).
2. R5 — `generate_commands_h(schema)` (разблокирует keyboard/gamepad).
3. `device_kind` в TargetProfile (однострочное расширение).
4. R2 — `hid_items.py` (вторая генерация дескриптора переиспользует).
5. R11 — валидация (предохранитель от ручных JSON-ошибок).

Отложить до реального use case:
- Обобщение `MouseState`; второй mapper (Keyboard) — только при реальном
  keyboard target.
- Client-facing контракт отдельно от `hid_command_config.h` — при втором
  клиенте.
- Версионный handshake/capability negotiation — пока протокол v1.
- Параметризация header дескриптора по device_kind — при первом
  Keyboard/Gamepad target.

Обратимые решения: выбор transport, layout/report target-профиля,
`queue_slots`, разделение `hid_profile`/`hid_mapper`, наличие capability.

Технический долг, если оставить как есть: маркерный патч ядра без версии
(уже дал тихий рассинхрон), дублирование capability blob, magic-числа
wire-format в handler+клиенте, mouse-specific имена в генераторе команд.
