# pc_client — PC-сторона командного канала

C++17 клиент поверх [hidapi](https://github.com/libusb/hidapi) для отправки
команд устройству (клон мыши на Arduino Leonardo + USB Host Shield),
подготовленному проектом [arduino-usb-hub](../).

Устройство выглядит как обычная HID-мышь плюс vendor-defined top-level
collection (usage page `0xFF00`, usage `0x01`). Клиент открывает именно эту
vendor collection и шлёт бинарные команды: движение, колесо, кнопки.

## Структура

```
include/hubclient/
  transport.hpp      CommandTransport: FeatureReportTransport / OutputReportTransport
  capability.hpp     чтение capability report (GET_REPORT Feature, report id 4)
  mouse_client.hpp   MouseClient — высокоуровневый API: open / move / wheel / click / ...
examples/
  client_demo.cpp    smoke-тест: --list, квадрат, скролл, клик
generated/           СГЕНЕРИРОВАНЫ, не редактировать (см. ниже)
  mouse_commands.h   опкоды + wire-format (_OFF/_SIZE/_LEN макросы) из commands/mouse.json
  command_channel.h  wiring конкретного target: transport, report id, queue slots
```

## Откуда берутся generated/*.h (важно)

Заголовки генерирует **не этот проект**, а `arduino-hub patch` из репозитория
arduino-usb-hub — тот же прогон, что патчит прошивку:

1. В arduino-usb-hub: `arduino-hub patch --device <n> --target <t>` (+ flash).
2. Скопировать `arduino-usb-hub/pc_client/generated/{mouse_commands.h,command_channel.h}`
   сюда, в `generated/`.
3. Собрать и использовать.

**Правило рассинхронизации**: прошивка и клиент должны происходить из одного
patch-рана. Если изменились `targets/<t>.json` или `commands/mouse.json` →
re-patch → re-flash → заново скопировать заголовки → rebuild клиента.
`command_channel.h` зависит от target (например, `HID_COMMAND_TRANSPORT`),
`mouse_commands.h` — только от протокола (`commands/mouse.json`).

## Сборка (Windows, CMake >= 3.16)

```bash
cmake -B pc_client/build pc_client
cmake --build pc_client/build --config Release
```

hidapi 0.15.0 подтягивается автоматически через FetchContent; POST_BUILD-шаг
копирует `hidapi.dll` рядом с exe (PATH трогать не нужно).

## Запуск

```bash
client_demo --list          # все HID-устройства: VID/PID/usage page/usage/path
client_demo [VID] [PID]     # квадрат, скролл, клик; дефолт 0x046D:0xC53F
```

Каждая команда печатает ok/fail (+ hidapi error при неудаче). На устройстве
`HUB_CMD_MARKER` (в `MouseCommandHandler.cpp` прошивки) мигает LED `opcode`
раз на каждый пакет — удобно для диагностики, в проде выключить (0).

## Использование как библиотеки

```cpp
#include "hubclient/mouse_client.hpp"

hubclient::MouseClient mouse;
if (mouse.open(vid, pid)) {      // ищет vendor collection (usage page 0xFF00)
  mouse.move(100, 0);            // int16 dx, dy
  mouse.wheel(-5);               // int8 delta
  mouse.click(1);                // кнопки — ЛОГИЧЕСКИЕ номера 1..16,
  mouse.click(4);                // независимы от layout target
}
// mouse.handle() — сырой hidapi handle для error reporting
```

## Протокол — правила для агента/разработчика

Эти инварианты нельзя нарушать при доработках:

- **Оффсеты и опкоды — только через макросы** из `generated/mouse_commands.h`
  (`MOUSE_CMD_*`, `MOUSE_CMD_*_OFF/_SIZE/_LEN`). Никогда не хардкодить байтовые
  оффсеты пакетов.
- **Фиксированные константы канала** (живут в command_generator.py
  arduino-usb-hub, НЕ конфигурируются): command report id 3, payload 15,
  hidapi buffer 16 (`[report id][payload]`), capability report id 4,
  capability payload 8.
- **Transport выбирается на этапе patch** (`targets/<t>.json` →
  `command_channel.h`):
  - `feature` — SET_REPORT, кроссплатформенно;
  - `output` — **только Windows**: `hid_write` на Linux это raw `write()` по
    hidraw и без interrupt OUT endpoint падает;
  - `interrupt_out` — кроссплатформенно, устройство поллит команды из `loop()`.
- **Capability**: если в target включён (`capability.enabled`), клиент при
  `open()` читает реальный transport/report id/payload len из устройства и они
  переопределяют дефолты из `command_channel.h`. При `HID_CAPABILITY_ENABLED == 0`
  чтение скомпилировано out.
- **Кнопки — логические числа 1..16**, маппятся в биты на стороне прошивки;
  один и тот же код работает для 3/5/16-кнопочных targets.
