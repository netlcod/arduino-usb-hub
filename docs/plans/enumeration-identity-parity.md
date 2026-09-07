# План: Enumeration Identity Parity + валидация профиля + dry-run

Статус: внедрён (коммит fc46dab: IdentityPatcher A–J, validation.py, dry-run, атомарные записи, манифест).
Связанные документы: `docs/plans/hid-clone-architecture.md` (архитектура),
`docs/device-clone-plan.md` (исходный анализ параметров),
`docs/usb-stack-audit.md` (аудит ядра 1.8.6).

---

## 1. Цель

Довести **все перечислимые** (enumeration-level) поля USB/HID дескрипторов и
поведение class-запросов до значений из `profiles/sources/<name>.json`, чтобы
клонированное устройство было неотличимо от оригинала не только по репортам,
но и по ответам на стандартные запросы хоста.

Попутно закрывается остаток матрицы из `device-clone-plan.md` §IV и
обнаруженные расхождения между планом, аудитом и фактическим кодом ядра.

## 2. Принципы

1. **Универсальность.** Инструмент клонирует *любое* HID-устройство. Ни одного
   значения конкретной мыши в коде патчера нет — все identity-поля берутся из
   `DeviceInfo` (source-профиль). Конкретные устройства упоминаются только как
   примеры в тестах/доках.
2. **Всегда явный патч.** Поля применяются независимо от того, совпадает ли
   значение со стоком: состояние «запатчено» не зависит от значений, что
   упрощает идемпотентность и проверку манифестом.
3. **Per-device значения — через перегенерируемые артефакты.** Значения,
   меняющиеся от профиля к профилю (`-D` флаги), живут в
   `leonardo.build.extra_flags` boards.txt, который переписывается на каждом
   `patch`. Маркерные правки исходников ядра — одноразовые и значений не
   содержат (только макросы-обёртки).
4. **Fail-loud.** Если правка обязана лечь (guard для флага, версия патча), а
   не легла — `PatchError` до записи манифеста.

## 3. Обнаруженная ошибка текущего патча (исправить в этом же батче)

### 3.1 bcdHID пишется в неверную позицию — на проводе 0x1101 вместо 0x0111

Макрос стока:

```c
// libraries/HID/src/HID.h
#define D_HIDREPORT(length) { 9, 0x21, 0x01, 0x01, 0, 1, 0x22, lowByte(length), highByte(length) }
```

C-структура `HIDDescDescriptor` в том же файле объявляет поле `addr` между
`dtype` и `versionL`, но макрос написан по **wire-layout спецификации HID**
(§6.2.1), где поле `addr` отсутствует:

| офсет | поле по спеке | сток |
|---|---|---|
| 0 | bLength | 9 |
| 1 | bDescriptorType | 0x21 |
| 2 | **bcdHID low** | 0x01 |
| 3 | **bcdHID high** | 0x01 |
| 4 | bCountryCode | 0 |
| 5 | bNumDescriptors | 1 |
| 6 | bDescriptorType (report) | 0x22 |
| 7..8 | wDescriptorLength | … |

Текущий regex патчера (`patcher.py`, правка «младший байт bcdHID»)

```python
r"(D_HIDREPORT\(length\) \{ 9, 0x21, 0x01,) 0x[0-9A-Fa-f]{2}(, 0, 1, 0x22)"
```

заменяет **офсет 3 (старший байт)** → на проводе `LE(0x01, 0x11)` =
`0x1101`. Windows это переживает, но значение неверно.

**Исправление:** перезаписывать оба байта целиком из `device.bcd_hid`
(`lowByte`/`highByte`), а не один. Это же снимает зависимость от того, какой
из двух байтов считать «младшим».

Следствие: аудит (`usb-stack-audit.md` §4) содержит ту же ошибку чтения
структуры — утверждение «bCountryCode всегда 1 (US)» неверно, на проводе сток
отдаёт country = **0** (совпадает с большинством устройств). В этот батч
правку аудита не включаем; при желании — отдельным коммитом.

## 4. Изменения по файлам

### 4.1 `src/arduino_hub/core/patcher.py` — `IdentityPatcher`

| # | Правка | Где | Механизм |
|---|--------|-----|----------|
| A | **bcdDevice + EP0 maxPacketSize** в `D_DEVICE` | `USBCore.cpp` | Расширить существующий regex: вместе с `0x100` (bcdDevice) переписывать аргумент packetSize0 (сток `64`) ← `device.ep0_max_packet_size`; ISERIAL→0 как раньше. Идемпотентность — по итоговым значениям обоих полей |
| B | **bcdHID оба байта** ← `device.bcd_hid` | `HID.h` | Заменить правку из §3.1: regex захватывает два байта после `{ 9, 0x21, `, подставляет `lowByte`/`highByte` |
| C | **bCountryCode** ← `device.country_code` | `HID.h` | Тот же литерал: пятый элемент инициализатора (`0` в стоке). Правка B и C — одна операция над одним литералом |
| D | **bmAttributes**: `-DUSB_CONFIG_ATTRIBUTES=0x…` | boards.txt `extra_flags` | Значение `device.bm_attributes`. Всегда добавляется |
| E | **bmAttributes**: guard в ядре | `USBCore.h` | Новый маркерный патч: перед `#define D_CONFIG` вставить `#ifndef USB_CONFIG_ATTRIBUTES / #define USB_CONFIG_ATTRIBUTES (USB_CONFIG_BUS_POWERED \| USB_CONFIG_REMOTE_WAKEUP) / #endif`; внутри тела `D_CONFIG` заменить конъюнкцию на `USB_CONFIG_ATTRIBUTES`. После правки — проверка наличия маркера, иначе `PatchError`. Первый патч этого файла — добавить его в манифест и в лог |
| F | **bcdUSB**: `-DUSB_VERSION=0x…` | boards.txt `extra_flags` | Значение `device.usb_version`. Ядро уже обёрнуто в `#ifndef USB_VERSION` (`USBCore.h:132-134`) — файлы ядра не трогаются |
| G | **bInterval**: `-DHID_EP_INTERVAL=0x…` | boards.txt `extra_flags` | Значение `device.ep_interval_ms` (минимум 1 — валидация §5) |
| H | **bInterval**: использование макроса | `HID.cpp` | В строке `D_ENDPOINT(..., USB_EP_SIZE, 0x01)` заменить литерал интервала на `HID_EP_INTERVAL`; рядом (маркер-охраняемо) добавить дефолт `#ifndef HID_EP_INTERVAL / #define HID_EP_INTERVAL 0x01 / #endif`. Затрагивает и IN-, и OUT-дескрипторы (interrupt_out) |
| I | **GET_IDLE**: корректный ответ | `HID.cpp` | Сток: тело пустое (`// TODO: Send8(idle);`), проваливается в stall. Заменить (в рамках `_patch_hid_cpp`, bump `HUB_PATCH_VERSION`) на `USB_SendControl(0, &idle, 1); return true;` — механизм тот же, что у capability blob (`USB_SendControl(TRANSFER_PGM, …)`) |
| J | **GET_PROTOCOL**: корректный ответ | `HID.cpp` | Сток: `return true;` без данных. → `USB_SendControl(0, &protocol, 1); return true;` |

Примечания:

- Правки E, H, I, J входят в семейство `_patch_hid_*` (реализация сошла с
  `USBCore.h`-версионирования — правка содержимо-идемпотентна, маркер v1
  остался). Старое ядро апгрейдится in place по существующей схеме
  `_upgrade_hid_cpp`.
- `USBCore.cpp` правка A — в `IdentityPatcher._usbcore_descriptor_content`
  (без версии-маркера, идемпотентность по содержимому); дисковый враппер
  `patch_usbcore` удалён (единый путь — `build_patch_edits`).
- CDC-ветка `D_DEVICE` не трогается: пайплайн всегда отключает CDC до этой
  правки.
- Device class triple (bDeviceClass/SubClass/Protocol) **не патчится**: при
  `-DCDC_DISABLED` сток даёт `00/00/00` — значение практически всех мышей
  (per-interface class). Уклонение источника ловит валидация (WARN, §5).
- Интерфейсы/коллекции сверх mouse-интерфейса (keyboard/vendor у ресиверов)
  остаются вне скоупа — см. `device-clone-plan.md` §II («что НЕ эмулировать»).

### 4.2 boards.txt: итоговый вид `extra_flags`

```
leonardo.build.extra_flags={build.usb_flags} -DCDC_DISABLED -DUSB_EP_SIZE=16
    -DUSB_CONFIG_POWER=<max_power_ma>
    -DUSB_VERSION=0x<usb_version>
    -DUSB_CONFIG_ATTRIBUTES=0x<bm_attributes>
    -DHID_EP_INTERVAL=0x<ep_interval_ms>
```

Все значения — из source-профиля; состав строки пересобирается целиком на
каждом `patch` (как сейчас).

### 4.3 Новый модуль `src/arduino_hub/core/validation.py`

```python
@dataclass
class ValidationIssue:
    severity: str      # "ERROR" | "WARN"
    field: str
    message: str

def validate_identity(device: DeviceInfo) -> list[ValidationIssue]: ...
KNOWN_VENDORS: dict[int, list[str]]   # ~40 записей, подмножество usb.ids
```

Правила:

| Severity | Поле | Условие |
|----------|------|---------|
| ERROR | manufacturer/product | пустая строка |
| ERROR | manufacturer/product | содержит `"` или `\` (ломают `-DUSB_PRODUCT`/`-DUSB_MANUFACTURER`) или control-символы (< 0x20) |
| ERROR | serial | при непустом серийнике: `"`/`\`/контрол-символы (ломают generated `hub_serial_string.h`); пустой серийник — норма (iSerialNumber=0) |
| WARN | manufacturer/product/serial | длина > 126 (лимит USB) |
| WARN | vendor_id ↔ manufacturer | VID известен (`KNOWN_VENDORS`), но имя производителя не входит в список имён вендора. Пропускать, если строка пустая или `"Unknown"` |
| ERROR (было WARN, поднято 2026-09: вне спецификации дескриптор на проводе, fail-fast до записи) | max_power_ma | вне 1..500; отдельно WARN при > 200 (нереалистично для HID) |
| ERROR (было WARN, поднято 2026-09: нестандартный EP0 ломает enumeration без CDC-запасного пути, fail-fast до записи) | ep0_max_packet_size | не в {8, 16, 32, 64} |
| ERROR (добавлено 2026-09: бит 7 — reserved=1, без него конфиг-дескриптор невалиден) | bm_attributes | бит bus-powered (0x80) не установлен |
| WARN | usb_version | > 0x0200 (ATmega32U4 — Full Speed only) |
| WARN | device_class | != 0x00 (не воспроизводим патчем) |
| WARN | bm_attributes | бит self-powered (0x40) установлен (типичные мыши — bus-powered; поведение не эмулируется) |
| WARN | bcd_hid | > 0xFFFF (в HID-дескриптор попадут только младшие 16 бит) |
| WARN | ep_interval_ms | < 1 |

Вызов: в `pipeline._patch_for_device` **до** записи файлов. Результаты:
warning-лог + секция `validation` в `.build/patches.json`. ERROR блокирует
patch. Проверка строк защищает от ручных правок `profiles/sources/*.json`
(профили редактируются вручную — например, перестановка `data_index`/`wire_order`),
при клонировании живого отчёта мисматч «VID↔строка» невозможен по построению.

### 4.4 Атомарная запись файлов ядра

Хелпер в `patcher.py`:

```python
def _atomic_write_text(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
```

Через него проходят все записи патчера: `boards.txt`, `USBCore.cpp/.h`,
`HID.h/.cpp`, генерируемые заголовки (`hid_profile.h`, `hid_mapper.h`,
`hid_command_config.h`, `mouse_commands.h`). Защита от частично записанного
файла ядра при сбое процесса.

### 4.5 `patch --dry-run`

- Флаг `--dry-run` в argparse (`pipeline.py`, команда `patch`).
- Все правки уже «строка → строка»: собрать результат в памяти, вывести
  unified diff (`difflib`) по каждому затронутому файлу + отчёт валидации +
  превью секций манифеста. Ничего не писать.
- Код возврата: 1 при ERROR-валидации.

## 5. Совместимость и миграция

- **Ядро, пропатченное предыдущей версией**: bcdHID-литерал после старого
  патча — `{ 9, 0x21, 0x01, 0xNN, 0, 1, 0x22 }`. Новый regex обязан матчить
  эту форму (два байта после `0x21,` произвольные) и приводить к правильной.
  Покрывается `_upgrade_*`-веткой по `HUB_PATCH_VERSION`.
- **Повторный `setup`** (перезакачка AVR core) откатывает все правки ядра —
  штатный путь отката, отдельный backup/restore не делается.
- Командный канал не затрагивается: правки не меняют ни report id/payload,
  ни endpoint-логику interrupt_out (меняется только литерал интервала,
  общий для обоих endpoint-дескрипторов).

## 6. Тесты

| Файл | Кейсы |
|------|-------|
| `tests/test_patcher.py` | A: `D_DEVICE` c новыми EP0+bcdDevice; повторный прогон стабилен. B+C: bcdHID = точная пара байт из профиля; форма после *старого* патча мигрирует; стоковая форма патчится. E: guard вставлен один раз; повторный — no-op; отсутствие guard'а при включённом флаге → `PatchError`. H: `HID_EP_INTERVAL` в обоих `D_ENDPOINT`. I/J: ответы GET_IDLE/GET_PROTOCOL присутствуют, stall убран. F/G/D: состав `extra_flags` полный и детерминированный |
| `tests/test_validation.py` | каждое правило §4.3 (ERROR и WARN ветки), skip-логика vendor-проверки на `"Unknown"` |
| `tests/test_command_generator.py` | без изменений (канал не задет) — регрессионный прогон |
| dry-run | на tmp-копии ядра: diff печатается, файлы не изменяются, код возврата при ERROR-валидации |

Тестовые профили — синтетические `DeviceInfo` (не привязаны к конкретной
мыши): пара с нестандартными EP0/bInterval/атрибутами + «дефолтная мышь».

## 7. Документация

- `AGENTS.md`: architecture/gotchas — список identity-полей, принцип «всегда
  явно», `extra_flags` состав, `USBCore.h` теперь патчится (откат — re-setup),
  исправление bcdHID (важно для тех, кто сверял дампы ранее).
- `docs/pipeline-tutorial.md`: §4.3 (шаги patch), §8 CLI (`--dry-run`),
  §9 таблица проблем (GET_IDLE больше не stall; сверка дампа с профилем).
- `AGENTS.md`/tutorial: упоминание, что остаточные отличия от многоинтерфейсных
  ресиверов (1 интерфейс vs N, отсутствие vendor-коллекций, `USB_EP_SIZE=16`)
  — осознанные решения, задокументированные в `hid-clone-architecture.md`.

## 8. Критерии приёмки

1. `pytest` зелёный.
2. `patch --dry-run` показывает диффы всех шести файлов (boards.txt,
   USBCore.cpp, USBCore.h, HID.h, HID.cpp, + генераты в списке) и не пишет
   на диск.
3. После `flash` дамп USB Tree Viewer по любому source-профилю совпадает с
   профилем по полям: VID/PID, bcdDevice, bcdUSB, bMaxPacketSize0,
   bmAttributes, bMaxPower, строки, iSerialNumber, bcdHID (оба байта),
   bCountryCode, subclass/protocol интерфейса (boot policy), wMaxPacketSize
   interrupt EP (=16, задокументированное отличие), bInterval.
4. GET_IDLE/GET_PROTOCOL отвечают данными (не stall) — проверяется любым
   USB-анализатором либо отсутствием ошибок enumeration в журнале Windows
   (`Microsoft-Windows-Kernel-PnP`/driver frameworks user-mode logs).

## 9. Риски

| Риск | Митигция |
|------|----------|
| `USBCore.h` — новый патченный файл, первый опыт версионирования | тот же механизм маркеров + `PatchError`-проверка сразу после правки |
| `-DUSB_CONFIG_ATTRIBUTES` без guard в ядре = ошибка компиляции | fail-loud при patch (E); компиляция падает громдо до flash |
| Отправка данных в GET_IDLE/GET_PROTOCOL ломает существующий SET_REPORT разбор | переиспользуется доказанный `USB_SendControl`-путь capability; тесты I/J |
| Расхождение старых прошивок/ядер после фикса bcdHID | `_upgrade`-ветка мигрирует литерал; в манифесте фиксируется версия патча |

## 10. Correction: EP0 требует синхронной правки аллокации

Пункт §4.1 A изначально обосновывался тезисом «хост дробит control-трансферы
до заявленного bMaxPacketSize0, поэтому заявить 32 при банке 64 безопасно».
Тезис **ошибочен** и был опровергнут полевой проверкой (scratch-сборка
25.08.2026): размер пакета на IN-этапе control-трансфера определяет
**устройство** — `SendControl` отдаёт пакет, когда заполняется аппаратный
банк EP0 (сток `EP_SINGLE_64`), хост не может заставить устройство дробить
мельче. Итог: device descriptor (18 байт) читался нормально, а конфиг-
дескриптор 41 байт уходил одним пакетом > 32 → Windows рвал enumeration
(`USB\CONFIG_DESCRIPTOR_FAILURE`, Problem 43, Current Config Value 0x00).

Исправление (вошло в этот же план как расширение A): декларация в
`D_DEVICE` сопровождается `-DUSB_EP0_MAX_PACKET=N` и макросом
`USB_EP0_ALLOC` вокруг `InitEP(0,EP_TYPE_CONTROL,...)` (USBCore.cpp),
выбирающим банк из {8→`0x02`, 16→`0x12`, 32→`0x22`, 64→`0x32`} —
кодировка UECFG1X (EPSIZE<<4 \| ALLOC) выведена из стоковых
`EP_SINGLE_16 0x12` / `EP_SINGLE_64 0x32`. После фикса все control-ответы
дробятся легально: конфиг 41 = 32+9, HID report 77 = 32+32+13.

### 10.1 Root cause подтверждён (второй слой проблемы)

Полевая проверка alloc-фикса показала: с банком 32 устройство исчезало из
системы полностью (LED заморожен), хотя кодировка `EP_SINGLE_32=0x22`
верна по даташиту, а с банком 64 (декларация 64) всё работало. Статический
анализ `USBCore.cpp` нашёл настоящий root cause — **стоковый `SendControl`
релизит FIFO по жёсткой маске каждые 64 байта**:

```c
if (!((_cmark + 1) & 0x3F))
    ClearIN();   // Fifo is full, release this packet
```

При банке 32 байт №33 конфиг-дескриптора пишется в полный банк,
`TXINI` не выставляется, следующая итерация зависает в `WaitForINOrOUT()`
навсегда внутри control-ISR. Иерархия симптомов теперь полна:
device descriptor (18 Б) читается при любом банке; конфиг (41 Б) убивает
enumeration на 33-м байте. Финальный фикс: маска заменена на
`(_cmark + 1) % USB_EP0_MAX_PACKET` (default 64 → семантика стока).
Аудит смежного `USB_RecvControl`: хардкод «64» на OUT-стороне безвреден —
хост сам дробит OUT по заявленному размеру, наши OUT-пейлоады ≤16 байт.
Поле профиля `ep_max_packet_size` инертно (размер interrupt-endpoint'ов
задаётся захардкоженным `-DUSB_EP_SIZE=16`) — не путать с
`ep0_max_packet_size`, который патчится.
