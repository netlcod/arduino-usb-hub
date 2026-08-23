# План: HID Clone Architecture — Leonardo → Logitech HID Mouse

## Архитектура

```
profiles/sources/
  g305.json              # Source Profile (что шлёт ресивер)
       |
       v
DeviceProfile
       |
       +----------------+----------------+
       |                |                |
       v                v                v
Source Decoder     Target Encoder    USB Identity
(usage/offset      (buttons, axes,   (VID, PID, strings,
 table from JSON)   report layout)    bcdDevice, power)
       |                |
       +-------+--------+
               |
               v
       generated/
         hid_profile.h     # только USB HID описание: HID_DESCRIPTOR[], HID_REPORT_ID, HID_REPORT_LENGTH
         hid_mapper.h      # трансформация: decode_input() + encode_output()
               |
               v
       patched Arduino HID (Mouse.cpp стабилен, только #include)
```

## Ключевые решения

### Q1. Decoder — генерация кода из profile (НЕ runtime parser)
- AVR не тратит RAM/flash на универсальный HID parser
- Генерируем только **таблицу смещений**:
```cpp
struct HidInputReport {
    uint16_t buttons;
    int16_t x;
    int16_t y;
    int8_t wheel;
    int8_t pan;
};
bool decode_report(const uint8_t* src, uint8_t len, HidInputReport& out);
// out.buttons = read_bits(src, 0, 16);
// out.y       = read_bits_signed(src, 16, 16);
// out.x       = read_bits_signed(src, 32, 16);
```

### Q2. Разделение source/target
```
profiles/sources/g305.json              # Source Profile — отчёт USB Tree Viewer
profiles/targets/generic_3btn.json      # Target: Arduino Mouse (3 btn, X/Y/Wheel 8-bit)
profiles/targets/generic_5btn.json      # Target: 5 btn (XButton1/2), 8-bit X/Y/Wheel
profiles/targets/generic_16btn.json     # Target: полный клон (16 btn, 16-bit X/Y, AC Pan)
```
- `arduino-hub patch --device g305 --target generic_3btn` → translate 9-byte → 3-button mouse
- `arduino-hub patch --device g305 --target generic_5btn` → + боковые (Назад/Вперёд)
- `arduino-hub patch --device g305 --target generic_16btn` → pass-through, native report

### Q3. Два сгенерированных файла (не один)
- **`hid_profile.h`** — только USB HID: `#define HID_REPORT_ID 2`, `HID_REPORT_LENGTH 9`, `HID_DESCRIPTOR[] PROGMEM`
- **`hid_mapper.h`** — трансформация: `MouseState {buttons, x, y, wheel}`, `decode_input()`, `encode_output()`
- Можно менять/тестировать независимо; проще добавить keyboard/gamepad позже

### Q4. pytest (обязательно)
```
tests/
  test_descriptor_generator.py
  test_decoder.py
  test_encode.py
  test_patcher.py
  fixtures/
    g305_reports.bin
```
Проверки:
- `generate(generic_16btn_profile) == 91 bytes (05 01 09 02 ...)` — сверка с JSON
- `generate(generic_5btn_profile) == 54 bytes` (usage max 05, padding 03)
- `decode(02 05 00 10 00 20 00 01 00) == {buttons:5, y:16, x:32, wheel:1}`
- encode для каждого target: маска кнопок (TGT_BUTTONS_DATA_BITS), порядок полей `[btn][Y][X][wheel][pan]`, clamp

### Q5. Идемпотентность — manifest
- `.build/patches.json`: `{"device":"g305", "patches":{"USBCore.cpp":"4401", "HID.cpp":"boot_mouse", "Mouse.cpp":"generated_profile_v3"}}`
- Существующие маркеры (`// PATCHED`) — оставить на первый этап, manifest — целевой механизм (rollback, проверка состояния)

## Файлы для патча

| Файл | Что |
|------|-----|
| `boards.txt` | VID, PID, usb_product, usb_manufacturer, `-DCDC_DISABLED -DUSB_EP_SIZE=16 -DUSB_CONFIG_POWER=98` |
| `USBCore.cpp` | bcdDevice 0x100→0x4401; iSerialNumber→0 (Windows не запрашивает string 3) |
| `HID.h` | bcdHID 0x0101→0x0111 |
| `HID.cpp` | subclass/protocol из HID policy (BOOT_MOUSE → 1/2), не из JSON |
| `Mouse.cpp` | стабилен: `#include "hid_profile.h"` + `#include "hid_mapper.h"`; вызовы decode→encode→SendReport |

Не трогаем: EP0 (остаётся 64), endpoint (16), boot mode compatibility, HID++, consumer/vendor interfaces, WinUSB.

## Этапы

**Этап 1 — HID Translation Layer (MVP):**
G305 9-byte report → decode → 3-button mouse (16→3 buttons, 16-bit→8-bit X/Y, wheel) → Windows видит «Logitech USB Receiver», 3-button mouse

**Этап 2 — Full profile:**
Меняется только `--target generic_16btn` → 16 buttons, 16-bit X/Y, ReportID=2, AC Pan; pass-through. Архитектура не меняется.
Mouse-библиотека расширена: `_buttons` → `uint16_t` (патч идемпотентный, маркеры).

**Этап 2b — Side buttons:**
`profiles/targets/generic_5btn.json` (копия 3btn, buttons=5) → боковые кнопки как XButton1/XButton2 без смены формата репорта.
