# Анализ параметров Logitech G305 LIGHTSPEED Receiver для клонирования

Источник: `profiles/sources/reports/logitech-g305-report.txt` — дамп из USB Device Tree Viewer.

## I. Фактические параметры G305 ресивера

### Device Descriptor
| Поле | Значение | Категория |
|--- |--- |--- |
| bcdUSB | 0x0200 (USB 2.0, Full-Speed only) | DEVICE |
| bDeviceClass | 0x00 (per-interface) | DEVICE |
| bDeviceSubClass | 0x00 | DEVICE |
| bDeviceProtocol | 0x00 | DEVICE |
| bMaxPacketSize0 | **0x20 (32 bytes)** ! | DEVICE |
| idVendor | 0x046D (Logitech) | DEVICE |
| idProduct | 0xC53F | DEVICE |
| bcdDevice | 0x4401 | DEVICE |
| iManufacturer | 1 → "Logitech" | DEVICE |
| iProduct | 2 → "USB Receiver" | DEVICE |
| iSerialNumber | **0 (No String Descriptor)!** | DEVICE |
| bNumConfigurations | 1 | — |

### Configuration Descriptor
| Поле | Значение | Категория |
|--- |--- |--- |
| wTotalLength | 0x0054 (84 bytes) | — |
| bNumInterfaces | **3** (эмулируем только 1 — mouse) | — |
| bConfigurationValue | 1 | — |
| iConfiguration | 4 → "RQR44.01_B0005" | LOGITECH |
| bmAttributes | **0xA0** (Bus Powered + Remote Wakeup) | DEVICE |
| bMaxPower | **0x31 = 98 mA** | DEVICE |

### Интерфейсы ресивера (только нужное для эмуляции — Interface 1)

| Interface | Class | Subclass | Protocol | Endpoint | wMaxPacketSize | bInterval | Назначение |
|--- |--- |--- |--- |--- |--- |--- |--- |
| 0 | HID (0x03) | Boot (1) | Keyboard (1) | EP1 IN Int | 12 bytes | 1 ms | **НЕ НУЖНО** |
| **1** | **HID (0x03)** | **Boot (1)** | **Mouse (2)** | **EP2 IN Int** | **32 bytes** | **1 ms** | **НУЖНО** |
| 2 | HID (0x03) | None (0) | None (0) | EP3 IN Int | 32 bytes | 1 ms | **LOGITECH** |

### HID Descriptor (Interface 1 — Mouse)
| Поле | Значение |
|--- |--- |
| bcdHID | 0x0111 (HID 1.11) |
| bCountryCode | 0x00 (not localized) |
| wDescriptorLength | **0x0094 (148 bytes)** |

### HID Collections внутри Interface 1
| Collection | UsagePage | Usage | ReportID | Input bytes | Нужно? |
|--- |--- |--- |--- |--- |--- |
| **Col01** | **0x0001 (Generic Desktop)** | **0x0002 (Mouse)** | **0x02** | **9 bytes** | **ДА** |
| Col02 | 0x000C (Consumer) | 0x0001 (Consumer Control) | 0x03 | 5 bytes | OPTIONAL |
| Col03 | 0x0001 (Generic Desktop) | 0x0080 (System Control) | 0x04 | 2 bytes | OPTIONAL |
| Col04 | 0xFFBC (Vendor) | 0x0088 (unknown) | 0x08 | 2 bytes | LOGITECH |

### Mouse Collection (Col01) — детали
| Элемент | Параметры |
|--- |--- |
| **Buttons** | 16 кнопок (UsageMin=1, UsageMax=16), ReportID=0x02, Variable, Absolute |
| **X-axis** | 16-bit Relative, LogicalMin=-32767, LogicalMax=+32767 |
| **Y-axis** | 16-bit Relative, LogicalMin=-32767, LogicalMax=+32767 |
| **Wheel** | 8-bit Relative, LogicalMin=-127, LogicalMax=+127 |
| **AC Pan (Horiz. scroll)** | 8-bit Relative, LogicalMin=-127, LogicalMax=+127 |
| **InputReportByteLength** | 9 bytes |

### String Descriptors
| Индекс | Содержимое | Категория |
|--- |--- |--- |
| 0 | Language: 0x0409 (English) | DEVICE |
| 1 | "Logitech" | DEVICE |
| 2 | "USB Receiver" | DEVICE |
| 3 | — (пропущен, нет серийного номера!) | DEVICE |
| 4 | "RQR44.01_B0005" (версия прошивки) | LOGITECH |

### Pipes (открытые endpoint'ы)
| Pipe | Endpoint | Direction | Type | wMaxPacketSize | bInterval |
|--- |--- |--- |--- |--- |--- |
| 0 | EP1 | IN | Interrupt | 0x0C (12) | 1 ms |
| 1 | EP2 | IN | Interrupt | 0x20 (32) | 1 ms |
| 2 | EP3 | IN | Interrupt | 0x20 (32) | 1 ms |

Отсутствуют OUT endpoint'ы — все коммуникации только через HID IN Reports + Control EP0.

---

## II. Что НЕ НУЖНО эмулировать (Logitech vendor-specific)

| Параметр | Описание |
|--- |--- |
| Interface 0 (Keyboard) | Клавиатура ресивера |
| Interface 2 (vendor HID) | Logitech-специфичный интерфейс |
| Col04 в Interface 1 (0xFFBC usage page) | Vendor-specific коллекция |
| String "RQR44.01_B0005" | Версия прошивки ресивера |
| LampArray виртуальные устройства (Slots 00-06) | Logitech G Hub RGB-подсветка |
| HID Reports с интерфейса 2 (UsagePage 0xFF00) | Logitech HID++ протокол |

---

## III. Классификация ВСЕХ параметров

| Поле из отчёта | USB уровень | Назначение | Хранить в DeviceInfo | Патчить Arduino | Приоритет |
|--- |--- |--- |--- |--- |--- |
| **VID** (0x046D) | Device Desc | Идентификация Logitech | Да `vendor_id` | Да `build.vid` | **CRITICAL** |
| **PID** (0xC53F) | Device Desc | Идентификация ресивера | Да `product_id` | Да `build.pid` | **CRITICAL** |
| **bcdDevice** (0x4401) | Device Desc | Версия устройства | Да `bcd_device` | Да (патч `D_DEVICE`) | IMPORTANT |
| **bcdUSB** (0x0200) | Device Desc | USB 2.0 | Да `usb_version` | Да `-DUSB_VERSION` | **CRITICAL** |
| **bDeviceClass** (0x00) | Device Desc | Per-interface | Да `device_class` | Да (уже 0x00 при CDC_DISABLED) | IMPORTANT |
| **bMaxPacketSize0** (32) | Device Desc | Размер EP0 | Да `ep0_max_packet_size` | Да (патч `D_DEVICE`) | **CRITICAL** |
| **bmAttributes** (0xA0) | Config Desc | Bus Powered + Remote Wakeup | Да `bm_attributes` | Да (патч `D_CONFIG`) | **CRITICAL** |
| **bMaxPower** (98 mA) | Config Desc | Потребление | Да `max_power_ma` | Да `-DUSB_CONFIG_POWER=98` | **CRITICAL** |
| **bInterfaceClass** (0x03) | Interface Desc | HID | Да `hid_interface_class` | Да | **CRITICAL** |
| **bInterfaceSubClass** (1) | Interface Desc | Boot Interface | Да `hid_subclass` | Да (патч `HID.cpp:33`) | **CRITICAL** |
| **bInterfaceProtocol** (2) | Interface Desc | Mouse | Да `hid_protocol` | Да (патч `HID.cpp:33`) | **CRITICAL** |
| **bcdHID** (0x0111) | HID Desc | HID 1.11 | Да `bcd_hid` | Да (патч `D_HIDREPORT`) | IMPORTANT |
| **bCountryCode** (0x00) | HID Desc | Not localized | Да `country_code` | Да (патч `D_HIDREPORT`) | OPTIONAL |
| **wDescriptorLength** (148) | HID Desc | Длина репорта | Да (вычисляется) | Да (автоматически) | — |
| **bInterval** (1 ms) | Endpoint Desc | Polling rate 1000Hz | Да `ep_interval_ms` | Да (патч `HID.cpp:35`) | **CRITICAL** |
| **wMaxPacketSize** (32) | Endpoint Desc | Размер EP | Да `ep_max_packet_size` | Да `-DUSB_EP_SIZE=16` ближайший | **CRITICAL** |
| **Manufacturer String** | String Desc | "Logitech" | Да `manufacturer_string` | Да `build.usb_manufacturer` | **CRITICAL** |
| **Product String** | String Desc | "USB Receiver" | Да `product_string` | Да `build.usb_product` | **CRITICAL** |
| **Serial Number** | String Desc | НЕТ (iSerial=0) | Да `serial_number=None` | Да (не отправлять serial) | **CRITICAL** |
| **HID Report Descriptor** | Report Desc | 148 байт | Да `hid_report_descriptor_raw` | Да (как `HIDSubDescriptor`) | **CRITICAL** |
| **Number of buttons** | Report Desc | 16 кнопок | Да `button_count` | Информационно | IMPORTANT |
| **X/Y Logical Range** | Report Desc | ±32767 (16-bit) | Да (внутри дескриптора) | Да | **CRITICAL** |
| **Wheel Logical Range** | Report Desc | ±127 (8-bit) | Да (внутри дескриптора) | Да | **CRITICAL** |
| **AC Pan (horiz. scroll)** | Report Desc | ±127 (8-bit) | Да (внутри дескриптора) | Да | IMPORTANT |
| **InputReportByteLength** | Report Desc | 9 bytes | Да `report_length` | Да (в `SendReport`) | **CRITICAL** |
| **Report ID** | Report Desc | 0x02 | Да `report_id` | Да (в `SendReport`) | **CRITICAL** |
| Language ID (0x0409) | String Desc | English | Да `lang_id` | Да | OPTIONAL |
| Interface 0 (Keyboard) | Interface Desc | Keyboard boot interface | HOST (не DEVICE) | Нет | — |
| Interface 2 (vendor) | Interface Desc | Logitech vendor | LOGITECH | Нет | — |
| String "RQR44.01_B0005" | String Desc | Firmware version | LOGITECH | Нет | — |
| LampArray devices | — | RGB LED virtual | LOGITECH | Нет | — |

---

## IV. Матрица патчей Arduino Core (конкретные значения)

| Параметр | Где | Текущее значение Arduino | Значение G305 | Способ патча |
|--- |--- |--- |--- |--- |
| VID | boards.txt `build.vid` | 0x46d (уже после патча) | 0x046D | OK |
| PID | boards.txt `build.pid` | 0xc53f (уже после патча) | 0xC53F | OK |
| Product | boards.txt `build.usb_product` | "USB Receiver" | "USB Receiver" | OK |
| Manufacturer | boards.txt `build.usb_manufacturer` | "Logitech" | "Logitech" | OK |
| bcdDevice | USBCore.cpp macro `D_DEVICE` | 0x100 | **0x4401** | ПАТЧ |
| bMaxPacketSize0 | USBCore.cpp macro `D_DEVICE` | 64 | **32** | ПАТЧ |
| bmAttributes | USBCore.h macro `D_CONFIG` | 0xA0 | 0xA0 | OK (совпадает) |
| bMaxPower | `-DUSB_CONFIG_POWER` | 500 mA | **98 mA** | `-DUSB_CONFIG_POWER=98` |
| HID Subclass | HID.cpp:33 | 0 (NONE) | **1 (BOOT)** | ПАТЧ |
| HID Protocol | HID.cpp:33 | 0 (NONE) | **2 (MOUSE)** | ПАТЧ |
| bInterval | HID.cpp:35 | 1 | 1 | OK (совпадает) |
| EP MaxPacketSize | `-DUSB_EP_SIZE` / HID.cpp | 64 | **16** (ближайший) | `-DUSB_EP_SIZE=16` |
| bcdHID | HID.h:121 `D_HIDREPORT` | 0x0101 | **0x0111** | ПАТЧ |
| Country Code | HID.h:121 `D_HIDREPORT` | 1 (US) | **0 (not localized)** | ПАТЧ |
| Serial Number | HID.cpp getShortName | "HID3A" | **none (iSerial=0)** | ПАТЧ |
| Report Descriptor | Mouse.cpp | 4-байтный boot report | **148-байтный расширенный** | ПАТЧ (custom) |
| Report ID | Mouse.cpp SendReport | 1 | **2** | ПАТЧ |
| Report Length | Mouse.cpp SendReport | 4 bytes | **9 bytes** | ПАТЧ |
| HID_GET_PROTOCOL | HID.cpp setup() | stub | должен работать | ПАТЧ |
| HID_GET_IDLE | HID.cpp setup() | stall | должен работать | ПАТЧ |
