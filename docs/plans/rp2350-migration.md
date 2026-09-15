# План: миграция на RP2350 (с Leonardo/ATmega32U4 + USB Host Shield)

Статус: **записан, не начат** (2026-09). Плата: RP2350 (заказана).
Связанные: `receiver-full-clone.md` (полный клон ресивера — упрощается
на RP2350), `g305-xy-order-investigation.md` (E5 — может остаться
невостребованным, см. §6), `hid-clone-architecture.md`.

## 1. Зачем: RP2350 vs Leonardo/ATmega32U4

| Аспект | ATmega32U4 (Leonardo) | RP2350 |
|---|---|---|
| Ядро | 8-бит AVR @ 16 МГц | 2× Cortex-M33 @ 150 МГц (+RISC-V Hazard3 опция), FPU |
| RAM | 2.5 КБ | 520 КБ |
| USB device | Фикс. контроллер, 7 EP, **патчи ядра** (EP0/EP_SIZE/маски) | Нативный USB-контроллер (встроенный PHY): FS **host или device**; до 16 IN + 16 OUT endpoint'ов, double-buffering |
| USB host | Внешний MAX3421E (щит), 8-бит SPI, библиотека felis с HIDBoot/HIDComposite | **PIO USB host** (Pico-PIO-USB: FS/LS, hub, multi-port) + TinyUSB host |
| Дескрипторы | Литералы в C-макросах ядра → весь patcher.py | Обычные C-структуры в прошивке → **данные, не патчи** |
| Командный канал | Кастомный SET_REPORT ring в ядре, control-ISR ограничения | TinyUSB device HID: FEATURE/OUTPUT/OUT-EP колбэки штатно |
| Debug | CDC (который мы выключаем!) + reset-танец | Второй порт всегда CDC + SWD; **нет CDC-жонглирования** |
| HID++/композит | Упирается в 7 EP / 832 Б DPRAM / 16 МГц | Тривиально: endpoint'ов и CPU много |

Суть: **все identity-патчи ядра AVR (patcher.py, ~2000 строк) становятся
генерацией данных**; весь стек клонирования переносится в «свои»
исходники вместо хирургии ядра Arduino. RP2350 vs RP2040: быстрее,
больше RAM/PIO; USB-возможности те же (dual mode: PIO-host + нативный
device).

## 2. Архитектура на RP2350

```
[Мышь/ресивер] --USB-A (PIO USB host, TinyUSB host)--> RP2350
RP2350 --USB-C (нативный device, TinyUSB device)--> [ПК]
```

Ровно пример `tinyusb_dual_host_hid_to_device_cdc` из pico-examples
+ Pico-PIO-USB (`sekigon-gonnoc/Pico-PIO-USB`, RP2040/RP2350, FS/LS host,
hub support; 1 PIO + 3 SM, 15 КБ RAM/ROM; 1.5k pull-up на D+ обязателен).

Плата: **Waveshare RP2350-USB-A / RP2350-PiZero** — обе имеют два порта
(нативный + PIO USB-A); прецедент: `godfreyrobotics/RP2350-HID-bridge`
(той же форм-фактор: device-USB + host-PIO).

## 3. Что переносится, что уходит

**Переносится без изменений:**
- `profiles/` — source/target/protocol профили (source of truth);
- `usbhid/hid_items.py`, генераторы report-дескриптора, command schema
  (wire-форматы hardware-agnostic);
- **`hid_mapper.h` / decode_input / encode_output** — уже чистый C,
  компилируется на RP2350 как есть;
- `pc_client/` (hidapi) — устройству всё равно, кто клиент;
- валидация профилей (`validation.py`), документация.
**Уходит (заменяется):**
- `core/patcher.py` целиком (IdentityPatcher/CommandChannelPatcher,
  EP0/EP_ALLOC/USBCore.h/HID.h/HID.cpp-патчи) → новый backend-генератор
  дескрипторов TinyUSB (descriptor structs = чистые данные);
- arduino-cli пайплайн (setup/compile/flash) → pico-sdk/CMake/UF2 (или
  arduino-pico core, если выберем Arduino-API-путь);
- USB Host Shield библиотека (felis) + все HIDBoot/HIDComposite кварки;
- CDC-танцы: на RP2350 второй порт — всегда Serial.

**Открытый вопрос — arduino-pico vs чистый pico-sdk:**
- *arduino-pico core* (Earle Philhower, RP2350 поддерживается) +
  Adafruit TinyUSB Arduino: привычный Arduino API, меньше контроля над
  дескрипторами (Adafruit TinyUSB даёт свой HID класс — identity-паритет
  через него достижим, но требует проверки).
- *pico-sdk + TinyUSB напрямую*: полный контроль дескрипторов (это
  критично для identity-паритетности — наша главная задача).
  Рекомендация: **pico-sdk** (контроль дескрипторов важнее API-сахара);
  arduino-pico — запасной путь.

## 4. Фазы

### P0 — Feasibility spike (критично)
Собрать `tinyusb_dual_host_hid_to_device_cdc` на RP2350-плате
(PICO_BOARD=..., Waveshare-борда): G305-ресивер за PIO-портом →
репорты в CDC второго порта. Известное трение: сообщество рапортует
проблемы SDK/TinyUSB/PIO-USB на RP2350 dual mode (форум RPi, фев 2026)
— spike обязателен до любых других шагов. Критерий: сырые репорты
ресивера читаются стабильно ≥10 мин.

### P1 — Device-клон MVP (замена Leonardo)
- Генератор дескрипторов RP2350: target-профиль → TinyUSB descriptor
  (VID/PID/строки/bcdDevice/EP0/bmAttributes/power/bInterval/EP size —
  **все identity-поля просто данные**);
- decode_input/encode_output (переиспользуются) → MouseState →
  TinyUSB hid n_report;
- command channel: FEATURE/OUTPUT/OUT-EP колбэки TinyUSB вместо
  core-ручки;capability blob — те же байты;
- PC-клиент не меняется.

Критерий: клон перечисляется как G305-ресивер (профиль), команды из
client_demo работают; USB Tree Viewer parity тот же, что на AVR.

### P2 — Host-путь (замена USB Host Shield)
- PioUsb host + TinyUSB host HID; парсинг report descriptor'а мыши
  (вместо felis-кварков; wire_order-модель остаётся — эмпирика X/Y
  переносится как есть);
- замыкание: host-мышь → decode → target-профиль → device. Основной
  use case (бот: команда → движение) становится полностью локальным.

### P3 — Полный клон ресивера (см. receiver-full-clone.md)
Композит 3 интерфейса + клавиатура/consumer/system (KeyboardState) и
HID++-канал — на RP2350 снимаются AVR-ограничения (EP/DPRAM/ядро);
фазы A/B/C того плана выполняются уже на новой платформе. Порядок:
P1/P2 (эта платформа) → фазы A/B → C (HID++).

### P4 — Обратная совместимость / стенд
Leonardo-пайплайн остаётся в репо как референс/стенд (не удаляем до
успешного P2); profiles/validation/pc_client общие.

## 5. Риски

| Риск | Митигация |
|---|---|
| RP2350 dual (PIO host + device) нестабилен (форумные репорты) | P0-spike первым; fallback: RP2040-платформа где путь обкатан, или host только на RP2350 native (без device одновременно) |
| PIO host требует pull-up/резисторы на D+/D- | Плата Waveshare RP2350-USB-A уже разводит это |
| 1 kHz polling под PIO — тайминги | TinyUSB host hid poll; проверить latency дампом (как HUB_DEBUG_DUMP) |
| X/Y-кварк ресивера | переносится как есть (wire_order в профиле); RP2350-хост — независимый способ перепроверить (TinyUSB сам парсит descriptor) |
| Потеря Arduino API | только если выберем pico-sdk; decode/mapper — чистый C, sketches переписываются на ~100 строк |

## 6. Связь с текущим планом клона ресивера

receiver-full-clone.md фазы A/B/C были рассчитаны на AVR-ограничения.
После P0-spike принять решение: выполнять фазы A/B/C на RP2350 (рекомендуется —
снимает риски композитного дескриптора и HID++-буферов), а AVR-ветку
заморозить на текущем состоянии (identity parity + командный канал
работают). E5-расследование X/Y остаётся в силе только для AVR-ветки:
на RP2350 TinyUSB host сам декодирует report descriptor — свап
wire-порядка может проявиться иначе (проверить на P0/P2 дампов).
