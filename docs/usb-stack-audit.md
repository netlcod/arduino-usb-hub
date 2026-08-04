
# Полный аудит USB-стека Arduino AVR Core 1.8.6 (Arduino Leonardo / ATmega32U4)

## I. Обзор архитектуры

USB-стек Arduino Leonardo состоит из трёх слоёв:

| Слой | Файлы | Назначение |
|------|-------|------------|
| **Hardware Abstraction** | `USBCore.cpp` (REGISTER-LEVEL) | Прямая работа с регистрами ATmega32U4 (UEINTX, UEDATX, UENUM...) |
| **Core USB Stack** | `USBCore.cpp`, `USBCore.h`, `USBDesc.h`, `USBAPI.h`, `PluggableUSB.cpp`, `PluggableUSB.h` | Дескрипторы, обработка SETUP-пакетов, endpoint'ы |
| **Plug-in Drivers** | `CDC.cpp`, `HID.cpp`, `HID.h` (в `libraries/HID/src/`) | Классовые драйверы через PluggableUSB |
| **User Libraries** | `Mouse/src/Mouse.cpp`, `Keyboard/src/Keyboard.cpp` | HID Report Descriptors, API мыши/клавиатуры |

---

## II. Детальный анализ каждого параметра

### 1. DEVICE DESCRIPTOR

**Файл:** `cores/arduino/USBCore.cpp:73-83`
**Структура:** `DeviceDescriptor` (определена в `USBCore.h:137-152`)
**Макрос:** `D_DEVICE()` (определён в `USBCore.h:269-270`)

```c
// USBCore.h:269-270
#define D_DEVICE(_class,_subClass,_proto,_packetSize0,_vid,_pid,_version,_im,_ip,_is,_configs) \
    { 18, 1, USB_VERSION, _class,_subClass,_proto,_packetSize0,_vid,_pid,_version,_im,_ip,_is,_configs }

// USBCore.cpp:73-83
#ifdef CDC_ENABLED
const DeviceDescriptor USB_DeviceDescriptorIAD =
    D_DEVICE(0xEF,0x02,0x01,64,USB_VID,USB_PID,0x100,IMANUFACTURER,IPRODUCT,ISERIAL,1);
#else // CDC_DISABLED
const DeviceDescriptor USB_DeviceDescriptorIAD =
    D_DEVICE(0x00,0x00,0x00,64,USB_VID,USB_PID,0x100,IMANUFACTURER,IPRODUCT,ISERIAL,1);
#endif
```

**Отправка:** `USBCore.cpp:528-531` в функции `SendDescriptor()`

| Поле | Значение | Комментарий |
|------|----------|-------------|
| `len` | 18 | Фиксировано стандартом |
| `dtype` | 1 | `USB_DEVICE_DESCRIPTOR_TYPE` |
| `usbVersion` | `USB_VERSION` = `0x200` | USB 2.0 (`USBCore.h:133`); можно переопределить через `-DUSB_VERSION=0x210` |
| `deviceClass` | `0xEF` или `0x00` | С CDC — "Miscellaneous" (IAD); без CDC — "per interface" |
| `deviceSubClass` | `0x02` или `0x00` | С CDC — Common Class; без CDC — 0 |
| `deviceProtocol` | `0x01` или `0x00` | С CDC — IAD protocol; без CDC — 0 |
| `packetSize0` | **64** | MaxPacketSize для EP0. **Аппаратное ограничение ATmega32U4: максимум 64 байта для EP0** |
| `idVendor` | `USB_VID` | Из `platform.txt:142`: `-DUSB_VID={build.vid}` -> из `boards.txt:422` |
| `idProduct` | `USB_PID` | Из `platform.txt:142`: `-DUSB_PID={build.pid}` -> из `boards.txt:423` |
| `deviceVersion` | `0x100` | bcdDevice = 1.00. Можно изменить через макрос. |
| `iManufacturer` | `IMANUFACTURER=1` | Индекс строкового дескриптора |
| `iProduct` | `IPRODUCT=2` | Индекс строкового дескриптора |
| `iSerialNumber` | `ISERIAL=3` | Индекс строкового дескриптора |
| `bNumConfigurations` | **1** | Только одна конфигурация |

**Выводы:**
- `packetSize0 = 64` — максимум для ATmega32U4, не может быть больше.
- `bcdDevice = 0x100` — жёстко зашито в макросе. Можно менять.
- При `CDC_DISABLED` используется `deviceClass = 0x00`, что корректно для HID-only устройства (класс указывается в Interface Descriptor). Это **правильно** для клонирования мыши.
- `bNumConfigurations = 1` — стандартно, можно увеличить, но редко имеет смысл.

---

### 2. CONFIGURATION DESCRIPTOR

**Файл:** `cores/arduino/USBCore.h:154-164` (структура `ConfigDescriptor`)
**Макрос:** `D_CONFIG()` в `USBCore.h:272-273`
**Формирование:** `USBCore.cpp:499-511` в функции `SendConfiguration()`

```c
// USBCore.h:272-273
#define D_CONFIG(_totalLength,_interfaces) \
    { 9, 2, _totalLength,_interfaces, 1, 0, \
      USB_CONFIG_BUS_POWERED | USB_CONFIG_REMOTE_WAKEUP, USB_CONFIG_POWER_MA(USB_CONFIG_POWER) }

// USBCore.cpp:501-508
ConfigDescriptor config = D_CONFIG(_cmark + sizeof(ConfigDescriptor), interfaces);
```

| Поле | Значение | Комментарий |
|------|----------|-------------|
| `len` | 9 | Фиксировано стандартом |
| `dtype` | 2 | `USB_CONFIGURATION_DESCRIPTOR_TYPE` |
| `clen` | динамический | Вычисляется в `SendInterfaces()` + `sizeof(ConfigDescriptor)` |
| `numInterfaces` | динамический | Количество зарегистрированных интерфейсов PluggableUSB |
| `config` | **1** | Config number — жёстко 1 |
| `iconfig` | **0** | iConfiguration (нет строки) |
| `attributes` | `0xA0` | **Bus Powered + Remote Wakeup** (`USB_CONFIG_BUS_POWERED\|USB_CONFIG_REMOTE_WAKEUP`) |
| `maxPower` | `USB_CONFIG_POWER_MA(500)` = **250** (x2 = 500mA) | Запрашивает 500 мА |

**Ключевые моменты:**

1. **bmAttributes = Bus Powered + Remote Wakeup (`USBCore.h:93-96`):**
   - `USB_CONFIG_BUS_POWERED = 0x80` — устройство питается от шины.
   - `USB_CONFIG_REMOTE_WAKEUP = 0x20` — поддерживает Remote Wakeup.
   - **Self-Powered (бит 6) НЕ УСТАНОВЛЕН!** Это правильно для Arduino, но отличается от многих мышей (многие заявляют Bus Powered без Remote Wakeup = `0x80`).
   - Флаг Remote Wakeup может вызывать вопросы у KVM-свитчей и некоторых BIOS.

2. **MaxPower = 500 mA** — очень много для HID-мыши (типичные мыши запрашивают 50-100 мА). Можно переопределить: `-DUSB_CONFIG_POWER=100`. Опасно для USB-хабов с ограничением по питанию.

**Рекомендация для клонирования мыши:**
```c
// В boards.txt или platform.txt добавить:
-DUSB_CONFIG_POWER=100
// Для отключения Remote Wakeup — нужно патчить D_CONFIG (нет макроса):
// ... | USB_CONFIG_BUS_POWERED, USB_CONFIG_POWER_MA(100)
```

---

### 3. INTERFACE DESCRIPTOR (HID)

**Файл:** `libraries/HID/src/HID.cpp:29-37`
**Структура:** `HIDDescriptor` в `HID.h:75-80`
**Макросы:** `D_INTERFACE()`, `D_HIDREPORT()`, `D_ENDPOINT()` в `USBCore.h:275-279`

```c
// HID.cpp:31-36
HIDDescriptor hidInterface = {
    D_INTERFACE(pluggedInterface, 1, USB_DEVICE_CLASS_HUMAN_INTERFACE, HID_SUBCLASS_NONE, HID_PROTOCOL_NONE),
    D_HIDREPORT(descriptorSize),
    D_ENDPOINT(USB_ENDPOINT_IN(pluggedEndpoint), USB_ENDPOINT_TYPE_INTERRUPT, USB_EP_SIZE, 0x01)
};
```

**Разбор макросов:**
```c
D_INTERFACE(_n,_numEndpoints,_class,_subClass,_protocol)
-> { 9, 4, _n, 0, _numEndpoints, _class, _subClass, _protocol, 0 }

// Где:
// _n = pluggedInterface (номер, автоматически назначаемый PluggableUSB)
// _numEndpoints = 1 (один IN endpoint для HID reports)
// _class = USB_DEVICE_CLASS_HUMAN_INTERFACE = 0x03
// _subClass = HID_SUBCLASS_NONE = 0
// _protocol = HID_PROTOCOL_NONE = 0
```

| Поле | Значение | Где задаётся |
|------|----------|-------------|
| `len` | 9 | Стандарт |
| `dtype` | 4 | `USB_INTERFACE_DESCRIPTOR_TYPE` |
| `number` | `pluggedInterface` | Автоматически (`PluggableUSB.cpp:89`) |
| `alternate` | 0 | Фиксировано |
| `numEndpoints` | **1** | Только IN-эндпоинт (нет OUT) |
| `interfaceClass` | **`0x03`** | HID (`USB_DEVICE_CLASS_HUMAN_INTERFACE`) |
| `interfaceSubClass` | **`HID_SUBCLASS_NONE = 0`** | Нет Boot Interface! |
| `protocol` | **`HID_PROTOCOL_NONE = 0`** | Нет Boot Protocol! |
| `iInterface` | 0 | Нет строки |

**КРИТИЧЕСКИЕ ОТЛИЧИЯ от типичной HID-мыши:**

| Параметр | Arduino Core (по умолчанию) | Типичная HID-мышь |
|----------|---------------------------|-------------------|
| `interfaceSubClass` | `0` (NONE) | `1` (Boot Interface) |
| `protocol` | `0` (NONE) | `2` (MOUSE) |

**Это главная проблема совместимости!**

BIOS, KVM-свитчи и старые ОС используют **Boot Protocol** для работы с HID до загрузки драйверов. Без `HID_SUBCLASS_BOOT_INTERFACE` и `HID_PROTOCOL_MOUSE` устройство **не будет работать в BIOS и на некоторых KVM-свитчах**.

Arduino Mouse library **не устанавливает** эти параметры. В файле `HID.h:44-49` определены корректные константы, но они **не используются** в `getInterface()`:

```c
// HID.h:44-49 // Определены, но НЕ ПРИМЕНЯЮТСЯ в HID.cpp!
#define HID_SUBCLASS_NONE 0
#define HID_SUBCLASS_BOOT_INTERFACE 1
#define HID_PROTOCOL_NONE 0
#define HID_PROTOCOL_KEYBOARD 1
#define HID_PROTOCOL_MOUSE 2
```

```c
// HID.cpp:33 — всегда использует NONE/NONE!
D_INTERFACE(pluggedInterface, 1, USB_DEVICE_CLASS_HUMAN_INTERFACE,
    HID_SUBCLASS_NONE, HID_PROTOCOL_NONE),
```

**Рекомендация:** Изменить `HID.cpp` так, чтобы можно было задать `subclass` и `protocol` динамически или через конфигурацию.

**Аппаратных ограничений:** нет. ATmega32U4 полностью поддерживает Boot Protocol.

---

### 4. HID DESCRIPTOR

**Файл:** `libraries/HID/src/HID.h:62-73` (структура `HIDDescDescriptor`), `HID.h:75-80` (структура `HIDDescriptor`)
**Макрос:** `D_HIDREPORT()` в `HID.h:121`

```c
// HID.h:62-73
typedef struct {
    uint8_t len;      // 9
    uint8_t dtype;    // 0x21
    uint8_t addr;
    uint8_t versionL; // 0x01
    uint8_t versionH; // 0x01 -> bcdHID = 0x0101 = HID 1.01
    uint8_t country;
    uint8_t desctype; // 0x22 = report
    uint8_t descLenL;
    uint8_t descLenH;
} HIDDescDescriptor;

// HID.h:121
#define D_HIDREPORT(length) { 9, 0x21, 0x01, 0x01, 0, 1, 0x22, lowByte(length), highByte(length) }
```

| Поле | Значение | Комментарий |
|------|----------|-------------|
| `len` | 9 | Фиксировано HID 1.11 |
| `dtype` | `0x21` | `HID_HID_DESCRIPTOR_TYPE` |
| `addr` | `0x01` | Версия HID 1.01 (`bcdHID = 0x0101`) |
| `country` | `1` | **Код страны — всегда 1 (US)!** Жёстко в макросе. |
| `desctype` | `0x22` | Следующий дескриптор — Report |
| `descLen` | `descriptorSize` | Динамический (сумма длин всех HIDSubDescriptor) |

**Проблема с Country Code = 1:**
Для локализованных клавиатур нужен 0 (Not localized) или конкретный код. Для мыши это не критично, но для клавиатур может влиять на раскладку.

**bcdHID = 1.01:** Это HID 1.01, что несколько устарело. Современное значение — 1.11 (`0x0111`). **Нельзя изменить без патча макроса или HID.cpp.**

**Дескриптор отправляется как часть Get Configuration Descriptor** (`HID.cpp:37`).

---

### 5. HID REPORT DESCRIPTOR (для мыши)

**Файл:** `libraries/Mouse/src/Mouse.cpp:26-57`
**Структура:** массив `_hidReportDescriptor[]` в PROGMEM

```c
static const uint8_t _hidReportDescriptor[] PROGMEM = {
    0x05, 0x01,        // USAGE_PAGE (Generic Desktop)
    0x09, 0x02,        // USAGE (Mouse)
    0xa1, 0x01,        // COLLECTION (Application)
    0x09, 0x01,        //   USAGE (Pointer)
    0xa1, 0x00,        //   COLLECTION (Physical)
    0x85, 0x01,        //     REPORT_ID (1)
    0x05, 0x09,        //     USAGE_PAGE (Button)
    0x19, 0x01,        //     USAGE_MINIMUM (Button 1)
    0x29, 0x03,        //     USAGE_MAXIMUM (Button 3)
    0x15, 0x00,        //     LOGICAL_MINIMUM (0)
    0x25, 0x01,        //     LOGICAL_MAXIMUM (1)
    0x95, 0x03,        //     REPORT_COUNT (3)
    0x75, 0x01,        //     REPORT_SIZE (1)
    0x81, 0x02,        //     INPUT (Data,Var,Abs) -- биты кнопок
    0x95, 0x01,        //     REPORT_COUNT (1)
    0x75, 0x05,        //     REPORT_SIZE (5) -- padding 5 бит
    0x81, 0x03,        //     INPUT (Cnst,Var,Abs)
    0x05, 0x01,        //     USAGE_PAGE (Generic Desktop)
    0x09, 0x30,        //     USAGE (X)
    0x09, 0x31,        //     USAGE (Y)
    0x09, 0x38,        //     USAGE (Wheel)
    0x15, 0x81,        //     LOGICAL_MINIMUM (-127)
    0x25, 0x7f,        //     LOGICAL_MAXIMUM (127)
    0x75, 0x08,        //     REPORT_SIZE (8)
    0x95, 0x03,        //     REPORT_COUNT (3)
    0x81, 0x06,        //     INPUT (Data,Var,Rel) -- X, Y, Wheel
    0xc0,              //   END_COLLECTION
    0xc0,              // END_COLLECTION
};
```

**Анализ структуры:**
- Это **стандартный Boot Mouse Report Descriptor** с 3 кнопками, X, Y, и Wheel.
- REPORT_ID = 1.
- Формат репорта: `[button_bits:8][X:8][Y:8][Wheel:8]` = 4 байта.

**Отправка репорта:** `Mouse.cpp:92`
```c
HID().SendReport(1, m, 4);
```
Первый аргумент (`1`) — это Report ID.

**Добавление дескриптора:** `Mouse.cpp:65-66`
```c
static HIDSubDescriptor node(_hidReportDescriptor, sizeof(_hidReportDescriptor));
HID().AppendDescriptor(&node);
```

**Гибкость:** Полная. Вы можете изменить Report Descriptor на любой другой. Достаточно создать новый массив дескриптора и добавить его через `HID().AppendDescriptor()`.

**Важное замечание о SendReport:**
В `HID.cpp:89-95` функция `SendReport` отправляет 2 отдельных USB-пакета: сначала `&id` как отдельный пакет на EP, затем данные с флагом `TRANSFER_RELEASE`. Это сделано для поддержки Report ID как первого байта. Это **не является стандартным способом** — обычно весь репорт (включая Report ID) отправляется в одном пакете. Это работает, но может вызвать проблемы совместимости с некоторыми хостами, ожидающими один непрерывный пакет.

---

### 6. ENDPOINT DESCRIPTOR (IN)

**Определение:** `HID.cpp:35`
```c
D_ENDPOINT(USB_ENDPOINT_IN(pluggedEndpoint), USB_ENDPOINT_TYPE_INTERRUPT, USB_EP_SIZE, 0x01)
```

Разбор макроса (`USBCore.h:278-279`):
```c
#define D_ENDPOINT(_addr,_attr,_packetSize, _interval) \
    { 7, 5, _addr, _attr, _packetSize, _interval }
```

| Поле | Значение | Комментарий |
|------|----------|-------------|
| `addr` | `0x80 \| pluggedEndpoint` | IN, номер назначается PluggableUSB |
| `attr` | `USB_ENDPOINT_TYPE_INTERRUPT = 0x03` | Interrupt Transfer |
| `packetSize` | **`USB_EP_SIZE = 64`** | По умолчанию 64 байта (`USBAPI.h:38`) |
| `interval` | **`0x01`** | **1 мс (bInterval = 1)** |

**КРИТИЧЕСКИЕ МОМЕНТЫ:**

**1. Polling Interval = 1 мс:**
Это **bInterval = 1**, что для Full Speed означает 1 миллисекунду. Это **агрессивно** и хорошо для low-latency. Типичные HID-мыши используют bInterval от 1 до 10 (1-10 мс). У отраслевых мышей часто 1 мс для gaming и 8 мс для офисных.

- Можно изменить: `HID.cpp:35` — заменить `0x01` на нужное значение (например, `0x08` = 8 мс).
- **Аппаратное ограничение ATmega32U4:** минимум 1 мс (1 SOF), максимум 255 мс (при Full Speed). HW поддерживает любой bInterval.
- Для клонирования конкретной мыши **крайне важно** установить корректный bInterval, т.к. ОС ожидает определённый polling rate.

**2. MaxPacketSize = 64:**
Для Interrupt endpoint в Full Speed максимум 64 байта. ATmega32U4 поддерживает 64 (double-buffered), можно уменьшить до 16 через `-DUSB_EP_SIZE=16`. Для HID-мыши 4 байта репорта отлично помещаются даже в 16. 64 — избыточно, но безвредно. **При `USB_EP_SIZE=16` используется EP_SINGLE_16 вместо EP_DOUBLE_64** (`USBCore.cpp:372-378`), что отключает double-buffering, слегка увеличивая latency.

---

### 7. STRING DESCRIPTORS

**Файл:** `cores/arduino/USBCore.cpp:39-66`

#### Language ID
```c
// USBCore.cpp:39-42
const u16 STRING_LANGUAGE[2] = {
    (3<<8) | (2+2),    // bLength=4, bDescriptorType=3
    0x0409             // English (United States)
};
```
- **Жёстко: только английский (0x0409).** Нельзя изменить без патча.
- Отправляется в `SendDescriptor()`: `USBCore.cpp:535-537`.

#### Product String
```c
// USBCore.cpp:49
const u8 STRING_PRODUCT[] PROGMEM = USB_PRODUCT;
```
- `USB_PRODUCT` задаётся через `platform.txt:142`: `'-DUSB_PRODUCT={build.usb_product}'`
- Из `boards.txt:424`
- **Легко изменить** через boards.txt.

#### Manufacturer String
```c
// USBCore.cpp:66
const u8 STRING_MANUFACTURER[] PROGMEM = USB_MANUFACTURER;
```
- `USB_MANUFACTURER` задаётся через `platform.txt`.
- **Хитрость:** если `USB_VID == 0x2341` (Arduino), автоматически `"Arduino LLC"`. Если `USB_VID == 0x1b4f` (SparkFun), автоматически `"SparkFun"`. Иначе `"Unknown"` (`USBCore.cpp:51-64`).
- Но флаг `-DUSB_MANUFACTURER` из `platform.txt:142` переопределяет это.

#### Serial Number String
```c
// USBCore.cpp:544-550
else if (setup.wValueL == ISERIAL) {
#ifdef PLUGGABLE_USB_ENABLED
    char name[ISERIAL_MAX_LEN];
    PluggableUSB().getShortName(name);
    return USB_SendStringDescriptor((uint8_t*)name, strlen(name), 0);
#endif
}
```
- **Динамически формируется** из имён зарегистрированных модулей PluggableUSB.
- Для HID: `"HID" + hex(descriptorSize)` -> например `"HID3A"` (`HID.cpp:65-73`).
- Для CDC: не формируется, т.к. CDC не использует PluggableUSB.
- **Максимальная длина: 20 символов** (`ISERIAL_MAX_LEN` в `USBDesc.h:27`).

**Проблема совместимости:**
Типичные HID-мыши имеют **уникальный серийный номер**. Arduino генерирует псевдослучайную строку типа "HID3A", которая будет **одинаковой для каждой прошивки с тем же размером HID дескриптора**. Это может вызывать:
- Проблемы с Windows, если VID/PID совпадают, а серийник нет — переустановка драйвера.
- Проблемы с Linux udev.

**Рекомендация:** Заменить `getShortName()` на чтение серийного номера из EEPROM или уникального ID чипа. Можно также запатчить `getShortName()` в HID.cpp.

---

### 8. bmAttributes (Configuration Descriptor)

**Файл:** `cores/arduino/USBCore.h:272-273`

```c
#define D_CONFIG(_totalLength,_interfaces) \
    { 9, 2, _totalLength,_interfaces, 1, 0, \
      USB_CONFIG_BUS_POWERED | USB_CONFIG_REMOTE_WAKEUP, USB_CONFIG_POWER_MA(USB_CONFIG_POWER) }
```

**Константы:**
```c
// USBCore.h:93-96
#define USB_CONFIG_BUS_POWERED    0x80
#define USB_CONFIG_SELF_POWERED   0xC0
#define USB_CONFIG_REMOTE_WAKEUP  0x20
```

**Текущее значение:** `0x80 | 0x20 = 0xA0`

| Бит | Значение | Описание |
|-----|----------|----------|
| 7 (bus-powered) | 1 | Питание от USB-шины |
| 6 (self-powered) | 0 | Не запитано от себя |
| 5 (remote wakeup) | 1 | **Поддерживает Remote Wakeup** |
| 4-0 (reserved) | 0 | Зарезервировано |

**Влияние на совместимость:**
- Remote Wakeup (`0x20`) — BIOS может пытаться использовать wakeup, если устройство в suspend. Не все BIOS корректно обрабатывают. Для клонирования мыши, которая НЕ поддерживает remote wakeup, нужно убрать этот флаг.
- **Нельзя изменить без патча D_CONFIG макроса или его вызова.**

---

### 9. MaxPower

**Файл:** `cores/arduino/USBCore.h:99-102`

```c
#define USB_CONFIG_POWER_MA(mA)   ((mA)/2)
#ifndef USB_CONFIG_POWER
 #define USB_CONFIG_POWER         (500)
#endif
```

- Запрашивается **500 mA** (значение в дескрипторе: `250` в единицах по 2 мА).
- **Можно изменить** через `-DUSB_CONFIG_POWER=100` (100 мА -> значение 50).
- **Аппаратных ограничений** нет — ATmega32U4 потребляет около 20-30 мА.
- **Рекомендация для клонирования мыши:** установить значение, соответствующее клонируемому устройству (типично 50-100 мА).

---

### 10. bcdUSB

**Файл:** `cores/arduino/USBCore.h:132-134`

```c
#ifndef USB_VERSION
#define USB_VERSION 0x200
#endif
```

- `0x200` = USB 2.0.
- **Аппаратное ограничение ATmega32U4:** поддерживает USB 2.0 Full Speed (не High Speed). Не может быть больше `0x200`.
- Можно установить `0x110` (USB 1.1), если клонируемая мышь использует USB 1.1.
- **Можно изменить:** `-DUSB_VERSION=0x110`.

---

### 11. HID Requests (SETUP-пакеты класса HID)

**Файл:** `libraries/HID/src/HID.cpp:98-147` (функция `HID_::setup()`)
**Константы запросов:** `HID.h:32-37`

```c
#define HID_GET_REPORT      0x01
#define HID_GET_IDLE        0x02
#define HID_GET_PROTOCOL    0x03
#define HID_SET_REPORT      0x09
#define HID_SET_IDLE        0x0A
#define HID_SET_PROTOCOL    0x0B
```

**Реализация обработчиков в `HID.cpp:98-147`:**

| Запрос | Обработка | Полнота |
|--------|-----------|---------|
| `HID_GET_REPORT` | `return true;` (заглушка с TODO) | **НЕ РЕАЛИЗОВАН** (всегда возвращает пустой пакет) |
| `HID_GET_PROTOCOL` | `return true;` (заглушка с TODO) | **НЕ РЕАЛИЗОВАН** (не отправляет protocol) |
| `HID_GET_IDLE` | `return false;` (проваливается) | **НЕ РЕАЛИЗОВАН** (stall) |
| `HID_SET_PROTOCOL` | `protocol = setup.wValueL;` | **Реализован** (сохраняет значение, но не меняет поведение) |
| `HID_SET_IDLE` | `idle = setup.wValueL;` | **Реализован** (сохраняет значение) |
| `HID_SET_REPORT` | Комментированный код | **НЕ РЕАЛИЗОВАН** |

**Это СЕРЬЁЗНАЯ проблема совместимости для клонирования HID-устройств!**

1. **`HID_GET_REPORT` не реализован:** Host может запросить текущее состояние репорта (например, состояние светодиодов клавиатуры). Arduino всегда возвращает пустой ответ, что нарушает спецификацию.

2. **`HID_GET_PROTOCOL` не реализован:** Host запрашивает текущий protocol (boot/report), Arduino не отправляет значение -> host получает stall или некорректные данные.

3. **`HID_GET_IDLE` не реализован:** Запрос idle rate приводит к stall. Нарушение спецификации.

4. **`HID_SET_PROTOCOL` реализован, но неэффективен:** Сохраняет значение `protocol`, но не меняет Report Descriptor на Boot Report. Для клонирования мыши без Boot Interface (`HID_SUBCLASS_NONE`) этот запрос **не должен приниматься**.

**Аппаратных ограничений:** нет. ATmega32U4 полностью способен обработать все эти запросы.

---

### 12. BOOT PROTOCOL

**Файлы:** `HID.h:44-55`, `HID.cpp:33`, `Mouse.cpp:57`

**Константы в HID.h:**
```c
#define HID_SUBCLASS_NONE             0
#define HID_SUBCLASS_BOOT_INTERFACE   1
#define HID_PROTOCOL_NONE             0
#define HID_PROTOCOL_KEYBOARD         1
#define HID_PROTOCOL_MOUSE            2
#define HID_BOOT_PROTOCOL             0
#define HID_REPORT_PROTOCOL           1
```

**Как используется:**
```c
// HID.cpp:151 - конструктор
HID_::HID_(void) : ..., protocol(HID_REPORT_PROTOCOL), idle(1)

// HID.cpp:33 - Interface Descriptor
D_INTERFACE(..., HID_SUBCLASS_NONE, HID_PROTOCOL_NONE)
```

**Проблема:**
Arduino Mouse library включает в себя **правильный Boot Mouse Report Descriptor** (формат: 3 байта — buttons, X, Y — без Report ID и без Wheel). Но **Interface Descriptor заявляет `HID_SUBCLASS_NONE` и `HID_PROTOCOL_NONE`.** Это означает:
- BIOS не будет использовать это устройство как мышь до загрузки ОС.
- Host **не может** отправить `SET_PROTOCOL(BOOT)`.
- ОС будет использовать Report Protocol, что для мыши обычно работает нормально (Windows/Linux/macOS), но BIOS — нет.

**Для совместимости с BIOS/KVM нужно:**
```c
// HID.cpp:33 — должно быть:
D_INTERFACE(pluggedInterface, 1, USB_DEVICE_CLASS_HUMAN_INTERFACE,
    HID_SUBCLASS_BOOT_INTERFACE, HID_PROTOCOL_MOUSE),
```

---

### 13. USB ENUMERATION

**Файл:** `USBCore.cpp:564-662` (ISR `USB_COM_vect` — обработка Setup-пакетов EP0)

**Обрабатываемые стандартные запросы:**

| Запрос | Строки | Реализация |
|--------|--------|------------|
| `GET_STATUS` (Device) | 586-591 | `_usbCurrentStatus` (Self-Powered + Remote Wakeup) |
| `GET_STATUS` (Endpoint) | 593-599 | Заглушка — всегда `0x0000`; HALT не проверяется |
| `CLEAR_FEATURE` (Device, Remote Wakeup) | 601-608 | Сбрасывает бит в `_usbCurrentStatus` |
| `CLEAR_FEATURE` (Endpoint, HALT) | — | **НЕ РЕАЛИЗОВАН** |
| `SET_FEATURE` (Device, Remote Wakeup) | 609-616 | Устанавливает бит в `_usbCurrentStatus` |
| `SET_FEATURE` (Endpoint, HALT) | — | **НЕ РЕАЛИЗОВАН** |
| `SET_ADDRESS` | 617-621 | Работает |
| `GET_DESCRIPTOR` | 622-625 | `SendDescriptor()` |
| `SET_DESCRIPTOR` | 626-629 | `ok = false` (stall) — корректно |
| `GET_CONFIGURATION` | 630-633 | `Send8(1)` — жёстко возвращает 1 |
| `SET_CONFIGURATION` | 634-642 | `InitEndpoints()`, `_usbConfiguration` |
| `GET_INTERFACE` | 643-645 | Пустой обработчик (не отправляет данных) |
| `SET_INTERFACE` | 646-648 | Пустой обработчик |

**Классовые запросы:**
```c
// USBCore.cpp:385-400
static bool ClassInterfaceRequest(USBSetup& setup) {
    // CDC закомментирован
    return PluggableUSB().setup(setup);
}
```

**Проблемы:**
1. **`GET_CONFIGURATION` всегда возвращает 1** — даже если устройство ещё не сконфигурировано. Должно возвращать 0, когда `_usbConfiguration == 0` (`USBCore.cpp:631-633`). **Это баг!** Хост может подумать, что устройство сконфигурировано, когда это не так.

2. **`GET_STATUS` для endpoint всегда 0** — HALT не проверяется (`USBCore.cpp:593-599`).

3. **`GET_INTERFACE` / `SET_INTERFACE` пустые** — альтернативные настройки не поддерживаются, что нормально.

---

### 14. REMOTE WAKEUP / IS SUSPENDED / SUSPEND/RESUME

**Файл:** `cores/arduino/USBCore.cpp:854-877`, `USBCore.cpp:758-806`

#### Remote Wakeup: `USBDevice_::wakeupHost()` (стр. 854-872)
```c
bool USBDevice_::wakeupHost() {
    UDCON &= ~(1 << RMWKUP);
    if (!(UDCON & (1 << RMWKUP))
      && (_usbSuspendState & (1<<SUSPI))
      && (_usbCurrentStatus & FEATURE_REMOTE_WAKEUP_ENABLED)) {
        USB_ClockEnable();
        UDCON |= (1 << RMWKUP);
        return true;
    }
    return false;
}
```
- **Аппаратный механизм:** ATmega32U4 поддерживает Remote Wakeup через бит `RMWKUP` в `UDCON`. После установки бита чип автоматически генерирует resume signalling (K-state) на 10 мс, затем снимает.
- Функция работает корректно.
- **Но!** В `D_CONFIG` всегда включен Remote Wakeup, даже если клонируемое устройство его не поддерживает.

#### Suspend/Resume: `ISR(USB_GEN_vect)` (стр. 758-806)
```c
// SUSPI (Suspend) обработчик:
UDIEN = (UDIEN & ~(1<<SUSPE)) | (1<<WAKEUPE); // выкл SUSP, вкл WAKEUP
_usbSuspendState = (_usbSuspendState & ~(1<<WAKEUPI)) | (1<<SUSPI);

// WAKEUPI (Resume) обработчик:
UDIEN = (UDIEN & ~(1<<WAKEUPE)) | (1<<SUSPE); // выкл WAKEUP, вкл SUSP
_usbSuspendState = (_usbSuspendState & ~(1<<SUSPI)) | (1<<WAKEUPI);
```
- **Корректно переключает маски прерываний.**
- **НО:** `USB_ClockDisable()` и `USB_ClockEnable()` закомментированы (TODO на стр. 791-793, 800-801). Это означает, что PLL не останавливается при suspend -> повышенное энергопотребление в suspend (около 15-20 мА вместо ~0.5 мА). **Стандарт USB требует <= 0.5 мА в suspend для bus-powered устройств!** Это нарушение спецификации USB.

#### Auto wakeup при отправке данных:
```c
// USBCore.cpp:273-276
if (_usbSuspendState & (1<<SUSPI)) {
    UDCON |= (1 << RMWKUP);
}
```
- Если устройство в suspend и sketch пытается отправить данные, автоматически вызывается Remote Wakeup. Это удобно, но может быть неожиданным для хоста.

---

### 15. ENDPOINT CONFIGURATION

**Файл:** `cores/arduino/USBCore.cpp:338-382`

#### Массив инициализации:
```c
u8 _initEndpoints[USB_ENDPOINTS] = {
    0,                      // EP0 (Control)
#ifdef CDC_ENABLED
    EP_TYPE_INTERRUPT_IN,   // CDC_ENDPOINT_ACM (EP1)
    EP_TYPE_BULK_OUT,       // CDC_ENDPOINT_OUT (EP2)
    EP_TYPE_BULK_IN,        // CDC_ENDPOINT_IN (EP3)
#endif
    // Остальные автоматически 0
};
```

- При CDC_DISABLED — массив `{0, 0, 0, ...}`. PluggableUSB заполняет следующие слоты при `plug()`.

#### InitEP и InitEndpoints:
```c
// USBCore.cpp:356-362
static void InitEP(u8 index, u8 type, u8 size) {
    UENUM = index;
    UECONX = (1<<EPEN);
    UECFG0X = type;
    UECFG1X = size;
}

// USBCore.cpp:365-382
static void InitEndpoints() {
    for (u8 i = 1; i < sizeof(_initEndpoints) && _initEndpoints[i] != 0; i++) {
        UENUM = i;
        UECONX = (1<<EPEN);
        UECFG0X = _initEndpoints[i];
#if USB_EP_SIZE == 16
        UECFG1X = EP_SINGLE_16;    // 0x12 = single bank, 16 bytes
#elif USB_EP_SIZE == 64
        UECFG1X = EP_DOUBLE_64;    // 0x36 = double bank, 64 bytes
#endif
    }
    UERST = 0x7E;  // Reset all endpoints except EP0
    UERST = 0;
}
```

**Аппаратные возможности ATmega32U4:**
- 7 endpoint'ов (`USB_ENDPOINTS = 7`, `USBDesc.h:22`)
- EP0 — Control, 64 байта (EP_SINGLE_64 = 0x32)
- EP1-EP6 — могут быть настроены как BULK, INTERRUPT или ISOCHRONOUS
- Каждый EP имеет 2 банка памяти (double-buffering)
- `USB_EP_SIZE = 64` -> двойная буферизация (EP_DOUBLE_64 = 0x36)
- `USB_EP_SIZE = 16` -> одинарный банк (EP_SINGLE_16 = 0x12)

**Ограничения:**
- **Максимум 7 endpoint'ов — это аппаратный лимит.**
- **EP0 всегда 64 байта** (физически один банк 64 байта).
- Для клонирования мыши используется 1 IN endpoint (плюс EP0) — это 2 из 7.
- Двойная буферизация (`USB_EP_SIZE=64`) позволяет подготовить следующий пакет, пока текущий передаётся — снижает latency.

---

### 16. USB_EP_SIZE

**Файл:** `cores/arduino/USBAPI.h:37-39`

```c
#ifndef USB_EP_SIZE
#define USB_EP_SIZE 64
#endif
```

- По умолчанию **64 байта**.
- Можно изменить: `-DUSB_EP_SIZE=16`.
- Эффект при 16: одинарный банк, меньше потребление памяти (каждый EP использует часть SRAM чипа — DPRAM). Для HID-мыши с 4-байтным репортом вполне достаточно.
- **Аппаратное ограничение:** ATmega32U4 поддерживает размеры 8, 16, 32, 64 для EP (не все комбинации с double-buffering).

---

### 17. USB_VID / USB_PID

**Файлы:** `boards.txt`, `platform.txt:142`

```makefile
# platform.txt:142
build.usb_flags=-DUSB_VID={build.vid} -DUSB_PID={build.pid} ...

# boards.txt (оригинал, до патча):
leonardo.build.vid=0x2341   # Arduino VID
leonardo.build.pid=0x8036   # Leonardo PID (CDC+HID composite)
```

**Гибкость:** Полная. Можно установить любые VID/PID через `boards.txt`.

**НО!** Оригинальные `leonardo.vid.0-3` / `leonardo.pid.0-3` (для upload port discovery) не меняются через `{build.vid}` — они жёстко заданы в boards.txt. При смене `build.vid/pid` на чужие значения, Arduino IDE/Build не сможет автоматически найти порт для загрузки.

**Рекомендация для клонирования:** VID/PID менять через `build.vid/build.pid` в boards.txt.

---

### 18. USB HOST ENUMERATION (как Arduino Core видит хост)

**Файл:** `cores/arduino/USBCore.cpp:810-815`

```c
u8 USBConnected() {
    u8 f = UDFNUML;
    delay(3);
    return f != UDFNUML;
}
```

- Проверяет, активна ли USB-шина (меняется ли frame number).
- Не проверяет VBUS (хотя ATmega32U4 имеет `VBUS` пин). **Это аппаратная особенность схемы Leonardo — VBUS не подключён к пину VBUS чипа.**
- На Arduino Leonardo VBUS подключён через делитель напряжения 1 МОм/2 МОм к пину `PB0` (RXLED). ATmega32U4 **не может аппаратно определить VBUS** на Leonardo без модификации платы.

---

### 19. USB Clock / PLL Configuration

**Файл:** `cores/arduino/USBCore.cpp:681-755` (`USB_ClockEnable()`)

```c
// ATmega32U4:
#if F_CPU == 16000000UL
    PLLCSR |= (1<<PINDIV);    // 16 MHz / 1 = 16 MHz для PLL
#elif F_CPU == 8000000UL
    PLLCSR &= ~(1<<PINDIV);   // 8 MHz / 1 = 8 MHz для PLL
#endif
```

- **Частота USB должна быть 48 МГц.** ATmega32U4 использует PLL для генерации 48 МГц из кварца.
- При 16 МГц кварце: PLL умножает на 6 (96 МГц), затем `/2` -> 48 МГц. `PINDIV = 1` даёт деление входной частоты на 1.
- При 8 МГц кварце: PLL умножает на 6 (48 МГц), без деления (`PINDIV = 0`).
- **Аппаратное ограничение:** кварц должен быть 8 МГц или 16 МГц. Leonardo использует 16 МГц.
- **USB работает на Full Speed (12 Mbps), не High Speed.**

---

### 20. GET_CONFIGURATION Bug

**Файл:** `cores/arduino/USBCore.cpp:630-633`

```c
else if (GET_CONFIGURATION == r) {
    Send8(1);  // Всегда 1, даже если _usbConfiguration == 0!
}
```

**Баг:** Стандарт USB требует возвращать 0, если устройство не сконфигурировано. Здесь всегда возвращается 1. Это может сбить с толку хост при перечислении.

**Исправление:** должно быть `Send8(_usbConfiguration)`.

---

### 21. PluggableUSB — динамическое назначение endpoint'ов

**Файл:** `cores/arduino/PluggableUSB.cpp:73-98`

```c
bool PluggableUSB_::plug(PluggableUSBModule *node) {
    if ((lastEp + node->numEndpoints) > USB_ENDPOINTS) {
        return false;
    }
    // Вставка в linked list
    node->pluggedInterface = lastIf;
    node->pluggedEndpoint = lastEp;
    lastIf += node->numInterfaces;
    for (uint8_t i = 0; i < node->numEndpoints; i++) {
        _initEndpoints[lastEp] = node->endpointType[i];
        lastEp++;
    }
    return true;
}
```

**Инициализация:** `PluggableUSB.cpp:106-107`
```c
PluggableUSB_::PluggableUSB_() :
    lastIf(CDC_ACM_INTERFACE + CDC_INTERFACE_COUNT),  // = 0 + 0 при CDC_DISABLED
    lastEp(CDC_FIRST_ENDPOINT + CDC_ENPOINT_COUNT),    // = 1 + 0 при CDC_DISABLED
    rootNode(NULL)
```

- При `CDC_DISABLED`: `lastIf = 0`, `lastEp = 1`. Первый HID модуль получит interface 0, endpoint 1.
- При `CDC_ENABLED`: `lastIf = 2`, `lastEp = 4`. HID получит interface 2, endpoint 4.

---

### 22. Отправка данных через endpoint

**Файл:** `libraries/HID/src/HID.cpp:89-96`

```c
int HID_::SendReport(uint8_t id, const void* data, int len) {
    auto ret = USB_Send(pluggedEndpoint, &id, 1);
    if (ret < 0) return ret;
    auto ret2 = USB_Send(pluggedEndpoint | TRANSFER_RELEASE, data, len);
    if (ret2 < 0) return ret2;
    return ret + ret2;
}
```

**Проблема:** Report ID (1 байт) отправляется **отдельным пакетом**, затем данные — с флагом `TRANSFER_RELEASE`. Это создаёт два IN-транзакции на шине для одного HID-репорта. Большинство HID-устройств отправляют репорт в одном пакете (включая Report ID как первый байт).

Это может работать нормально для большинства хостов (USB допускает отправку данных меньшими пакетами, чем MaxPacketSize), но:
- Удваивает задержку (2 фрейма вместо 1).
- Может вызвать проблемы с хабами/свитчами/BIOS, которые ожидают полный репорт в одном IN-токене.

**Рекомендация:** объединить id и data в один буфер перед отправкой.

---

## III. ИТОГОВАЯ ТАБЛИЦА

| Параметр | Где находится | Можно изменить | Аппаратное ограничение | Комментарий |
|----------|--------------|----------------|------------------------|-------------|
| **bLength** (Device) | `USBDesc.h:269-270` макрос `D_DEVICE` | Нет (фикс. 18) | Стандарт USB | |
| **bDescriptorType** (Device) | `USBDesc.h:270` | Нет (фикс. 1) | Стандарт USB | |
| **bcdUSB** | `USBCore.h:133`, `USB_VERSION` | Да `-DUSB_VERSION=0x110` | Max 0x200 (FS only) | ATmega32U4 = USB 2.0 FS |
| **deviceClass** | `USBCore.cpp:74-82` | Да (через `-DCDC_DISABLED`: 0x00 vs 0xEF) | Нет | 0x00 — правильно для HID-only |
| **deviceSubClass** | `USBCore.cpp:74-82` | Да | Нет | 0x00 для HID-only |
| **deviceProtocol** | `USBCore.cpp:74-82` | Да | Нет | 0x00 для HID-only |
| **bMaxPacketSize0** | `USBCore.cpp:74-82` | Да (в макросе `D_DEVICE`) | **Max 64** | Аппаратный лимит ATmega32U4 |
| **idVendor** | `platform.txt:142` -> `boards.txt:422` | Да `build.vid` | Нет | |
| **idProduct** | `platform.txt:142` -> `boards.txt:423` | Да `build.pid` | Нет | |
| **bcdDevice** | `USBCore.cpp:74-82` | Да | Нет | Жёстко 0x100 в макросе |
| **iManufacturer** | `USBCore.cpp:66` | Да | Нет | Индекс строки |
| **iProduct** | `USBCore.cpp:49` | Да | Нет | Индекс строки |
| **iSerialNumber** | `USBCore.cpp:544-550` | Да (через `getShortName`) | Нет | Динамический, максимум 20 символов |
| **bNumConfigurations** | `USBCore.cpp:74-82` | Да | Нет | 1 |
| **bLength** (Config) | `USBDesc.h:272-273` | Нет (фикс. 9) | Стандарт USB | |
| **wTotalLength** | `USBCore.cpp:501-504` | Динамический | Нет | |
| **bNumInterfaces** | `USBCore.cpp:503` | Динамический (PluggableUSB) | Нет | |
| **bConfigurationValue** | `USBDesc.h:273` | Да (в макросе) | Нет | Фикс. 1 |
| **iConfiguration** | `USBDesc.h:273` | Да (в макросе) | Нет | 0 (нет строки) |
| **bmAttributes** | `USBDesc.h:273`, `USBCore.h:93-96` | Да (патч макроса) | Нет | Bus Powered + Remote Wakeup |
| **bMaxPower** | `USBCore.h:99-102` | Да `-DUSB_CONFIG_POWER=100` | Нет | 500 mА по умолчанию |
| **bLength** (Interface) | `USBDesc.h:275-276` | Нет (фикс. 9) | Стандарт USB | |
| **bInterfaceNumber** | `HID.cpp:33` | Автоматически (PluggableUSB) | Нет | |
| **bAlternateSetting** | `USBDesc.h:276` | Да (в макросе) | Нет | 0 |
| **bNumEndpoints** | `HID.cpp:33` | Да (в макросе) | **Max 6** (минус EP0) | 1 (только IN) |
| **bInterfaceClass** | `HID.cpp:33` | Да | Нет | 0x03 (HID) |
| **bInterfaceSubClass** | `HID.cpp:33` | Да | Нет | **0 (NONE!) — КРИТИЧНО** |
| **bInterfaceProtocol** | `HID.cpp:33` | Да | Нет | **0 (NONE!) — КРИТИЧНО** |
| **iInterface** | `USBDesc.h:276` | Да (в макросе) | Нет | 0 |
| **bLength** (HID) | `HID.h:121` макрос `D_HIDREPORT` | Нет (фикс. 9) | Стандарт HID | |
| **bDescriptorType** (HID) | `HID.h:121` | Нет (0x21) | Стандарт HID | |
| **bcdHID** | `HID.h:121` | Да (патч макроса) | Нет | **1.01 (должен быть 1.11)** |
| **bCountryCode** | `HID.h:121` | Да (патч макроса) | Нет | **1 (US)** |
| **bNumDescriptors** | `HID.h:121` | Нет (фикс. 1) | Стандарт HID | |
| **bDescriptorType** (Report) | `HID.h:121` | Нет (0x22) | Стандарт HID | |
| **wDescriptorLength** (Report) | `HID.h:121` | Динамический | Нет | Сумма всех `HIDSubDescriptor` |
| **HID Report Descriptor** | `Mouse.cpp:26-57` | Да (полностью) | Нет | 4 байта: [btns][X][Y][Wheel] |
| **bLength** (Endpoint) | `USBDesc.h:278-279` | Нет (фикс. 7) | Стандарт USB | |
| **bEndpointAddress** | `HID.cpp:35` | Автоматически | **Max 6 EP + EP0 = 7** | IN endpoint |
| **bmAttributes** | `HID.cpp:35` | Да | Тип: Interrupt/ Bulk/ Iso | 0x03 (Interrupt) |
| **wMaxPacketSize** | `HID.cpp:35`, `USBAPI.h:38` | Да `-DUSB_EP_SIZE=16` | **Max 64 (double-buffered)** | 64 по умолчанию |
| **bInterval** | `HID.cpp:35` | Да | Min 1 мс | **1 мс (очень часто)** |
| **Language ID** | `USBCore.cpp:39-42` | Да (патч массива) | Нет | Только 0x0409 (English) |
| **Product String** | `USBCore.cpp:49`, `platform.txt:142` | Да `build.usb_product` | Нет | |
| **Manufacturer String** | `USBCore.cpp:66`, `platform.txt:142` | Да `build.usb_manufacturer` | Нет | |
| **Serial Number** | `USBCore.cpp:544-550`, `HID.cpp:65-73` | Да (патч `getShortName`) | Нет | "HIDxx" — не уникален |
| **GET_STATUS** | `USBCore.cpp:586-599` | Да (исходники) | Нет | Endpoint status = всегда 0 |
| **GET_DESCRIPTOR** | `USBCore.cpp:514-561` | Да (расширяемо) | Нет | |
| **SET_ADDRESS** | `USBCore.cpp:617-621` | Да | Нет | |
| **SET_CONFIGURATION** | `USBCore.cpp:634-642` | Да | Нет | |
| **GET_CONFIGURATION** | `USBCore.cpp:630-633` | Да | Нет | **Баг: всегда 1** |
| **SET_FEATURE (Wakeup)** | `USBCore.cpp:609-616` | Да | Нет | |
| **CLEAR_FEATURE (Wakeup)** | `USBCore.cpp:601-608` | Да | Нет | |
| **SET_FEATURE (HALT)** | `USBCore.cpp:593-599` | Да | Нет | **НЕ РЕАЛИЗОВАН** |
| **GET_INTERFACE** | `USBCore.cpp:643-645` | Да | Нет | Пустой обработчик |
| **HID_GET_REPORT** | `HID.cpp:109-111` | Да (патч) | Нет | **НЕ РЕАЛИЗОВАН (stub)** |
| **HID_SET_REPORT** | `HID.cpp:134-143` | Да (патч) | Нет | **НЕ РЕАЛИЗОВАН** |
| **HID_GET_PROTOCOL** | `HID.cpp:113-115` | Да (патч) | Нет | **НЕ РЕАЛИЗОВАН (stub)** |
| **HID_SET_PROTOCOL** | `HID.cpp:124-128` | Да (патч) | Нет | Реализован, но без поведения |
| **HID_GET_IDLE** | `HID.cpp:117-118` | Да (патч) | Нет | **НЕ РЕАЛИЗОВАН (stall)** |
| **HID_SET_IDLE** | `HID.cpp:130-132` | Да (патч) | Нет | Реализован |
| **Remote Wakeup** | `USBCore.cpp:854-872` | Да (макрос + регистры) | Аппаратно (RMWKUP) | Работает |
| **Suspend/Resume** | `USBCore.cpp:758-806` | Да | Аппаратно (UDINT) | **PLL не отключается — нарушение USB** |
| **Endpoint инициализация** | `USBCore.cpp:365-382` | Да | **Max 7 EP** | Double-buffered при EP_SIZE=64 |
| **USB Speed** | `USBCore.cpp:747` | Нет | **Full Speed (12 Mbps)** фиксир. | |
| **VBUS detection** | `USBCore.cpp:810-815` | Да | **Нет на Leonardo hardware** | Леонардо не подключает VBUS pin |
| **Clock Source** | `USBCore.cpp:689-696` | Нет | 8 или 16 МГц кварц | Leonardo: 16 МГц |
| **SendReport (фрагментация)** | `HID.cpp:89-95` | Да (патч) | Нет | **ID и данные - два отдельных пакета** |

---

## IV. Ключевые выводы и рекомендации

### Критические (для клонирования HID-мыши):

1. **`HID_SUBCLASS_BOOT_INTERFACE` и `HID_PROTOCOL_MOUSE`** (`HID.cpp:33`) — должны быть установлены для совместимости с BIOS/KVM. Сейчас — NONE/NONE.

2. **`bInterval = 1 мс`** (`HID.cpp:35`) — крайне агрессивный polling. Для клонирования конкретной мыши надо знать её реальный bInterval (обычно 8-10 мс для офисной, 1-2 мс для игровой).

3. **`bMaxPower = 500 мА`** (`USBCore.h:101`) — нереалистично для мыши, может вызвать проблемы с USB-хабами.

4. **`bcdHID = 1.01`** (`HID.h:121`) — должен быть 1.11.

5. **Отсутствует реализация `HID_GET_REPORT`, `HID_GET_PROTOCOL`, `HID_GET_IDLE`** — нарушение спецификации HID.

6. **`GET_CONFIGURATION` всегда возвращает 1** (`USBCore.cpp:632`) — баг, нарушающий USB-спецификацию.

7. **PLL не отключается при suspend** (`USBCore.cpp:800-801`) — нарушение требования по питанию в suspend (<=500 мкА для bus-powered).

8. **Serial Number не уникален** — одинаков для всех прошивок с тем же размером дескриптора.

9. **Report ID отправляется отдельным пакетом** (`HID.cpp:91`) — удваивает latency, может сбивать некоторые хабы/BIOS.

10. **`bRemoteWakeup` флаг всегда включён в конфигурации** — даже если клонируемое устройство его не поддерживает.
