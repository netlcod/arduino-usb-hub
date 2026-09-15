# План: полный клон Logitech ресивера (композит + HID++ канал)

Статус: **записан, не начат** (2026-09).
Связанные документы: `docs/plans/hid-clone-architecture.md` (текущая
архитектура и решение про канал команд), `docs/device-clone-plan.md`
(параметры ресивера), `docs/plans/refactoring-plan.md` (отложенные
расширения: KeyboardState, device_kind), `docs/plans/g305-xy-order-investigation.md`.

## 1. Цель

Довести клон до полной неотличимости от ресивера Logitech G305
LIGHTSPEED (VID `046D`, PID `C53F`): композит из трёх HID-интерфейсов,
все коллекции, и — главное — **командный канал бота поверх канала,
который предоставляет сам ресивер (HID++)**, вместо самодельного
usage page 0xFF00-репорта (fingerprint-добавка, решение 2026-09 —
см. `hid-clone-architecture.md`, раздел «Канал команд»).

Эталонная структура ресивера (из `device-clone-plan.md`):

| Interface | Class/Sub/Proto | EP | wMaxPacketSize | Содержимое |
|---|---|---|---|---|
| 0 | HID 1/1 (Boot Keyboard) | EP1 IN | 12 Б | keyboard + consumer + system коллекции |
| 1 | HID 1/2 (Boot Mouse) | EP2 IN | 32 Б | mouse (9Б, RID 2) + consumer (5Б, RID 3) + system (2Б, RID 4) + vendor 0xFFBC (2Б, RID 8) |
| 2 | HID 0/0 (vendor) | EP3 IN | 32 Б | HID++ (usage page 0xFF00, отчёты 7/20/32 Б) |

OUT endpoint'ов у оригинала нет — всё через IN-репорты и control EP0.

## 2. Зачем это нужно

- Полное маскирование:Сейчас клон неотличим на уровне device/interface
  дескрипторов мыши, но имеет один интерфейс вместо трёх и самодельный
  вендорный канал (report id 3/4), которого у оригинала нет.
- Управление ботом: HID++-интерфейс — легитимный двунаправленный канал,
  его наличие у клона не выдаёт самодельщину; G HUB-совместимость —
  опциональная глубина (см. §5).

## 3. Фазы

### Фаза A — KeyboardState и не-мышиные коллекции (без композита)

Модель данных по принципу refactoring-плана: НЕ обобщать `MouseState`,
а добавить отдельные `KeyboardState` / `ConsumerState` / `SystemState`
(последние два тривиальны — битовые поля 5/2 байт) + общий
низкоуровневый слой `hid_items.py` (уже существует).

- `usbhid/`: генерация keyboard-дескриптора (boot keyboard, 8-byte
  modifiers + N-key rollover-переменные, report id по профилю),
  consumer- и system-коллекций. Descriptor-спеки — из source-профиля
  ресивера (пере-clone с полным дампом).
- `libraries/`: клавиатурная библиотека-эквивалент Mouse (KeyboardCore
  на базе `KeyboardState`); encode-мапперы по образцу `hid_mapper.h`.
- `device_kind` в TargetProfile уже есть (`mouse`); расширить сериализацию
  на `keyboard` и параметризовать header-дескриптор (usage 0x06).

Критерий: дескриптор интерфейса 0 байт-в-байт совпадает с дампа
ресивера; клавиатура работает под mouhid/kbdhid.

### Фаза B — мультиинтерфейсный композит

- PluggableUSB: несколько модулей HID (по одному на интерфейс 0/1/2),
  `bNumInterfaces=3`, номера endpoint'ов раздаются ядром.
- Генерация: дескрипторы всех трёх интерфейсов + конфиг-дескриптор с
  тремя интерфейсами (wTotalLength 84); EP1 (12Б), EP2 (32Б), EP3 (32Б).
- DPRAM-бюджет ATmega32U4 (832 Б): EP0 32 + 12 + 32 + 32 (+ командный
  OUT 16, если сохранить) ≈ 124–140 Б — влезает с запасом.
- `patch`-пайплайн: boards.txt-флаги и identity-патчи не меняются;
  добавляется генерация мультиинтерфейсного профиля. Валидация
  report-id-пространства между интерфейсами (у ресивера RID 2/3/4/8
  на интерфейсе 1 — уникальность внутри интерфейса).
- PC-клиент: выбор нужного интерфейса по usage page (уже умеет).

Критерий: USB Tree Viewer показывает те же 3 интерфейса, 4 коллекции
интерфейса 1, те же InputReportByteLength (12/9/5/2/2).

### Фаза C — HID++ канал вместо самодельного

- Research-этап: снять за реальным ресивером (USBPcap) транзакции
  интерфейса 2: report id'ы, размеры (ожидание: 0x10/0x11 → 7/20 Б;
  сверить very long), GET_REPORT/SET_REPORT поведение, handshake при
  инициализации G HUB.
  Артефакт: `profiles/sources/reports/<receiver>-hidpp.pcapng` + профиль.
- Минимальная эмуляция: отвечать на базовые пинги/регистры (device info),
  чтобы Windows и hidapi не видели отличий; G HUB-глубина — опционально.
- Транспорт бота: `MouseClient` переключается на HID++-интерфейс
  (usage page фильтр уже есть); команды упаковываются в HID++ long-пакеты.
  Самодельный канал (0xFF00-репорт id 3/4) переводится в fallback/dev
  (флаг в target-профиле) и впоследствии удаляется.
- `interrupt_out` OUT-endpoint самодельного канала на полном клоне не
  добавляется (у оригинала OUT-endpoint'ов нет).

Критерий: анализ (USB Tree Viewer, USBPcap, hidapi-enum) не выдаёт
отличий от ресивера; команды бота работают через HID++-интерфейс.

### Фаза C — reference-реализации (только справочник, без зависимостей)

HID++ — **проприетарный протокол Logitech**; используем существующие
реализации как спецификацию, реализуем минимальное подмножество сами.

| Источник | Что брать |
|---|---|
| `Logitech/cpg-docs` (github.com/Logitech/cpg-docs) | **Официальная публичная дока Logitech**: структура HID++ 2.0 пакетов (report id / device index / feature index / func+software id / params), feature-таблицы. Первоисточник |
| `hid-logitech-dj.c` + `hid-logitech-hidpp.c` (kernel) | Канонический парсер + **точный HID++ descriptor ресиверов**: usage page 0xFF00, RID **0x10 short** (6 Б payload, отчёт 7 Б) / **0x11 long** (19 Б payload, отчёт 20 Б) (+0x12 very long, 64 Б, если ресивер декларирует). C53F — `recvr_type_gaming_hidpp` (не DJ: pairing-модель не нужна) |
| `cvuchener/hidpp` (C++17) | Библиотека/инструменты; формат ReprogControls |
| `PixlOne/logiops` | C++ реализация 1.0/2.0: `Report` short/long layout, feature discovery (`root` 0x0000 → `featureSet` 0x0001), device index 0xFF/0x01–06 |
| `pwr-Solaar/Solaar` (Python) | Самая полная реализация поверх hidraw; notifications, ReprogControls rawXY-divert |
| `hidpp` (Rust crate) + openlogi.org/hidpp | Компактный справочник протокола 1.0/2.0 |

Уточнение к research-этапу: report id'ы и размеры интерфейса 2 у C53F
ожидаются 0x10/0x11 (7/20 Б) — сверить с дампом; «32 Б» в таблице выше —
это wMaxPacketSize endpoint'а, не размер отчёта.

### Принцип по X/Y-потоку

Парсинг raw X/Y за щитом существующие репозитории не решают (кварк
ресивера C53F нигде не задокументирован; см.
`g305-xy-order-investigation.md`, эксперименты E1–E5). Справочник по
report-протоколу за щитом: `mustaffxx/usb-host-shield-mouse` (G502,
`HIDUniversal` — прецедент E5). Битность (8/16) определяется report
descriptor'ом ресивера и не зависит от `HIDBoot`/`HIDUniversal`.

## 4. Зависимости и порядок

1. **A** независима от B/C — можно начинать сразу (вся инфраструктура
   генерации дескрипторов готова, `hid_items.py` общий).
2. **B** требует A (коллекции интерфейса 0) и EP-паритета (EP_SIZE из
   профиля — реализовано в этой сессии, `-DUSB_EP_SIZE=<ep_max_packet_size>`).
3. **C** требует B (HID++ живёт на интерфейсе 2) и research-этапа.
4. G305 XY-кварк (`wire_order`) остаётся до результатов
   `g305-xy-order-investigation.md` — на клон ресивера не влияет.

## 5. Открытые вопросы

- Глубина HID++: достаточно ли «Windows-невидимости» (минимальные
  ответы на control-запросы) или нужен совместимый с G HUB handshake?
  Решить после research-этапа фазы C.
- Судьба самодельного канала: полный fallback или удаление после
  стабилизации HID++-транспорта.
- Системные требования к `KeyboardState` из реального use case
  (клавиатурные нажатия бота?) — если их нет, интерфейс 0 эмулируется
  «мёртвым» (дескриптор есть, данных нет) — этого достаточно для
  маскирования.
