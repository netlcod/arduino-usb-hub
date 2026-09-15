# AGENTS.md

## Run

```bash
pip install -e .
# or: pip install arduino_hub
```

```bash
arduino-hub setup
arduino-hub clone --name mydevice --report profiles/sources/reports/<report>.txt
arduino-hub patch --device mydevice --target generic_5btn
arduino-hub compile --sketch path/to/sketch.ino
arduino-hub flash --device mydevice --target generic_5btn --sketch path/to/sketch.ino --port COM6
```

## Development

Use a venv:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

## Workflow

- **Commit after each phase**: every completed phase of a plan/document
  (refactoring R-phases, investigation experiments, full-clone phases)
  is committed separately — green tests first, then a focused commit
  describing that phase. Do not batch multiple phases into one commit.

## Architecture

```
src/arduino_hub/
  main.py            — CLI entrypoint (argparse)
  pipeline.py        — subcommand implementations
  exceptions.py      — custom exception hierarchy
  logging_config.py  — logging setup
  devices.py         — load/save source profiles to profiles/sources/<name>.json
  targets.py         — load/save target profiles from profiles/targets/<name>.json
  cli/
    downloader.py    — ArduinoCLIDownloader: download + extract arduino-cli.exe
                       (SHA256-pinned archive, .version marker for stale-CLI warning)
    executor.py      — ArduinoCLIExecutor: thin subprocess wrapper
    manager.py       — ArduinoCLIManager: ensure_cli() → executor
  core/
    installer.py     — ArduinoCoreInstaller: ensure_version(), find_path()
    patcher.py       — IdentityPatcher (D_DEVICE/bcdDevice/iSerialNumber+serial,
                       boards.txt, USBCore.h guard, D_HIDREPORT, boot policy,
                       USB_EP_ALLOC bank map in InitEndpoints),
                       CommandChannelPatcher
                       (write_hid_command_config, write_hid_capability_blob,
                       patch_hid_command_core — versioned via
                       // HUB_PATCH_VERSION), LibraryPatcher
                       (patch_mouse_library, install_hub_command_library,
                       write_pc_client_headers); build_patch_edits() →
                       [FileEdit] + apply_edits()/render_edits_diff()
                       (dry-run), build_manifest_patches(), write_patch_manifest()
                       → .build/patches.json; все записи файлов — атомарные
                       (_atomic_write_text: tmp + os.replace)
    validation.py    — validate_identity(DeviceInfo) → [ValidationIssue]:
                       ERROR ломают -D флаги (кавычки/бэкслеш/контрол-символы,
                       пустые строки, серийник), enumeration (ep0 вне
                       {8,16,32,64}) или валидность дескриптора (power вне
                       1..500, bmAttributes без бита 0x80); WARN — длины
                       >126, VID↔производитель (KNOWN_VENDORS, только для
                       ручных правок профиля), диапазоны bcdUSB/bcd_hid/интервал,
                       self-powered бит, power > 200
  usbhid/
    device_info.py   — DeviceInfo, AxisSpec (incl. data_index + wire_order),
                       TargetProfile (incl. device_kind), CommandProfile,
                       CapabilityProfile dataclasses
    descriptor_reader.py — ReportParser: USB Tree Viewer report → DeviceInfo
                       (includes HID report descriptor reconstruction + axes)
    hid_items.py     — shared low-level HID item emitters (usage_page, usage,
                       logical, report_count) — single source for descriptor
                       generation and reconstruction
    hid_generator.py — generate_report_descriptor() (layouts "arduino"/
                       "per_axis"), generate_hid_profile_h(),
                       generate_hid_mapper_h() (source order by wire_order,
                       target order = JSON list), source_layout_warnings(),
                       target validation
    command_schema.py — CommandSchema/CommandDef/CommandArg + load from
                       profiles/protocol/<name>.json (opcodes, typed payload layout)
    command_generator.py — generate_command_descriptor() (vendor collection,
                       fixed protocol constants: cmd report id 3, payload 15,
                       hidapi buffer 16; capability report id 4, payload 8),
                       generate_hid_command_config_h(), generate_commands_h()
                       (opcodes + wire-format OFF/SIZE/LEN macros, file name
                       from schema name), generate_capability_blob() +
                       generate_hid_capability_blob_h()
profiles/
  sources/g305.json   — cloned source profile (gitignored, user data)
  targets/            — generic_3btn / generic_5btn / generic_16btn (see below)
  protocol/mouse.json — command protocol source of truth
targets detail:
  generic_3btn.json   — Arduino Mouse compatible (3 btn, 8-bit X/Y/Wheel), command off
  generic_5btn.json   — 5 btn (XButton1/XButton2), 16-bit X/Y + 8-bit Wheel, command interrupt_out
  generic_16btn.json  — full clone: 16 btn, 16-bit X/Y, Wheel, AC Pan, ReportID 2, command feature
```

- `examples/mouse/` — reference Arduino sketch for Leonardo + USB Host Shield
  (`Usb.Task()` + `Cmd.poll()` in loop)
- `profiles/sources/g305.json` — cloned source profile (G305 receiver, 9-byte report)
- `arduino-cli-data/user/libraries/Mouse/src/` — patched Mouse library +
  generated `hid_profile.h` / `hid_mapper.h` (decode_input / encode_output)
- `libraries/HubCommand/` — device command channel library (static sources in
  repo, copied to user libraries by `patch`): CommandTransport (Feature/Output/
  InterruptOut/None), DeviceCommandHandler, MouseCommandHandler;
  `mouse_commands.h` generated from profiles/protocol/mouse.json
- `pc_client/` — C++ client (hidapi via FetchContent): transport.hpp,
  capability.hpp, mouse_client.hpp, examples/client_demo.cpp; `target/`
  headers written by `patch` (gitignored, needed before client build;
  `patch --client-out <dir>` exports them)

### Command channel (PC → Arduino)

```
PC: MouseClient → CommandProtocol (mouse_commands.h) → CommandTransport (hidapi)
Device: core HID → CommandTransport → MouseCommandHandler → Mouse API
  - feature/output:  SET_REPORT ring (control transfer)
  - interrupt_out:   interrupt OUT endpoint, polled via USB_Recv in loop()
```

- **Wiring per target** (`profiles/targets/<t>.json`): `command.enabled` /
  `command.transport` (`feature`|`output`|`interrupt_out`) /
  `command.queue_slots` (4–8), `capability.enabled`. Fixed constants (report id
  3, payload 15, hidapi buffer 16; capability report id 4, payload 8) live in
  `command_generator.py` and are NOT configurable.
- **Protocol** (`profiles/protocol/mouse.json`) → `mouse_commands.h` for device AND
  pc_client (opcodes + wire-format `_OFF/_SIZE/_LEN` macros); validated at
  patch time against the 15-byte payload. Both MouseCommandHandler and
  mouse_client.hpp build packets from the generated macros (no hardcoded
  offsets).
- **Capability blob**: single source of truth is `generate_capability_blob()`
  in `command_generator.py`; `patch` writes `hid_capability_blob.h` into the
  core HID library and the patched HID.cpp only `#include`s it and serves
  `HID_CAPABILITY_BLOB` on GET_REPORT(Feature) — the blob structure is never
  baked into HID.cpp.
- **Flow**: `arduino-hub patch` writes core `hid_command_config.h` +
  `hid_capability_blob.h`, patches HID.h/.cpp (versioned via
  `// HUB_PATCH_VERSION`, stale cores upgraded in place), regenerates
  `hid_profile.h` (mouse + vendor command collection), copies HubCommand,
  writes `pc_client/target/*`. Transport switch = edit target JSON →
  re-patch → re-flash → rebuild client (headers regenerate).
- **PC client build**: `cmake -B pc_client/build pc_client && cmake --build
  pc_client/build --config Release`; POST_BUILD copies hidapi.dll next to the
  exe. hidapi 0.15: `hid_enumerate(vid, pid)` (no serial arg).
- **Diagnostics**: `HUB_CMD_MARKER` in `libraries/HubCommand/src/
  MouseCommandHandler.cpp` blinks LED_BUILTIN `opcode` times per received
  packet (set to 0 for production). `client_demo --list` prints VID/PID/usage
  of all HID devices; every command prints ok/fail + hidapi error.
- **`transport=output` is Windows-only**: hidapi `hid_write` on Linux is a raw
  `write()` on the hidraw fd and needs an interrupt OUT endpoint; without one
  it fails. `feature` and `interrupt_out` work on both platforms. Use
  `feature` for cross-platform clients or `interrupt_out` when the device
  must poll commands from `loop()`.

## Commands

| Command   | What it does |
|-----------|-------------|
| `setup`   | Download Arduino CLI (to `.build/tools/`), install AVR core |
| `clone --name <n> --report <p>` | Parse USB Device Tree Viewer report → `profiles/sources/<n>.json` |
| `patch --device <n> --target <t> [--client-out <dir>] [--dry-run]` | CDC disable + полная identity-паритетность (VID/PID, строки, bcdDevice, **EP0 maxPacketSize**, **EP maxPacketSize (interrupt EP)**, **bcdUSB**, **bmAttributes** через guard в USBCore.h, **bCountryCode**, bcdHID оба байта) + boot mouse policy + command channel (core SET/GET_REPORT ring, interrupt OUT endpoint, HubCommand lib, PC headers) + generate Mouse library headers + валидация профиля + манифест; `--client-out` копирует заголовки PC-клиента наружу; `--dry-run` печатает unified diff всех правок без записи |
| `compile --sketch <p>` | Compile sketch only |
| `flash --device <n> --target <t> --sketch <p> --port <x>` | Patch + compile + upload |

`--target` (default `generic_3btn`) selects the output profile from `profiles/targets/`;
use `generic_5btn` for side buttons (Back/Forward) and `generic_16btn` for the
full pass-through report.
All steps are idempotent: re-running checks existing state and skips completed work.
Patch state is recorded in `.build/patches.json`.

## Gotchas

- **After flashing, CDC is disabled**: Leonardo becomes a cloned HID device with no serial port. To re-flash, hold reset button, run flash command, release reset when upload starts (see README Warning section).
- **Identity-поля патчатся всегда явно** (принцип): состав `extra_flags` (`-DUSB_VERSION`, `-DUSB_CONFIG_ATTRIBUTES`, `-DHID_EP_INTERVAL`, `-DUSB_EP0_MAX_PACKET`, `-DUSB_EP_SIZE`) и литералы дескрипторов пересобираются на каждом `patch` независимо от того, совпадает ли значение со стоком — правка профиля подхватывается без «детекции изменений по VID/PID».
- **`bMaxPacketSize0` < 64 требует двух синхронных правок, иначе enumeration умирает**: (1) поле в `D_DEVICE` + аллокация банка через `-DUSB_EP0_MAX_PACKET=N`/макрос `USB_EP0_ALLOC` вокруг `InitEP(0,...)`; (2) **root cause невидимых устройств** — стоковый `SendControl` решает «банк полон» по жёсткой маске `(_cmark+1) & 0x3F` (каждые 64 байта): при банке 32 байт №33 пишется в полный FIFO и `WaitForINOrOUT()` зависает навсегда внутри control-ISR (конфиг-дескриптор 41 байт = смерть на 33-м байте; device descriptor 18 байт при этом читается). Патчер заменяет маску на `% USB_EP0_MAX_PACKET`. Симптомы до фикса: «сбой запроса дескриптора конфигурации» (банк 64/декларация 32) или полное исчезновение устройства с замороженным LED (банк 32 без фикса маски).
- **`ep_max_packet_size` профиля патчится** (2026-09, по образцу EP0): `-DUSB_EP_SIZE=N` в `extra_flags` + пропатченный `InitEndpoints()` в USBCore.cpp — стоковая лесенка `#if USB_EP_SIZE == 16/#elif 64` заменена макросом `USB_EP_ALLOC` (8/16/32 → single bank, 64 → double bank; другие размеры = `#error`). hidapi-буфер клиента от размера EP не зависит (размеры репортов определяются report-дескриптором), репорты ≤16 Б влезают в банк 32 без изменений. Не путать с `ep0_max_packet_size` (дескриптор + `USB_EP0_ALLOC` + маска `SendControl`).
- **`D_HIDREPORT` в HID.h следует wire-layout спеки, а не соседней структуре**: байт 2 = bcdHID LOW, байт 3 = HIGH, байт 4 = country (структура `HIDDescDescriptor` с полем `addr` вводит в заблуждение). Патчер пишет оба байта bcdHID + country; старые ядра с патчем «только младший байт» (на проводе было 0x1101 вместо 0x0111) мигрируются автоматически.
- **USBCore.h теперь тоже патчится** (`#ifndef USB_CONFIG_ATTRIBUTES` guard, версия `// HUB_PATCH_VERSION 1`): повторный `setup` (перезакачка core) откатывает все правки ядра — штатный путь отката.
- **GET_IDLE/GET_PROTOCOL отвечают данными** (`USB_SendControl(0, &idle|&protocol, 1)`, v3 патча): до этого GET_IDLE ставился в stall, GET_PROTOCOL возвращал пустой пакет.
- **SendReport одним пакетом** (v4 патча, guard `len <= 0` с v5): сток слал id и payload двумя IN-транзакциями; патчер собирает `buf[len+1]` и шлёт одним `USB_Send(…|TRANSFER_RELEASE,…)`; v4-ядра без guard мигрируют одной строкой.
- **GET_CONFIGURATION отвечает живым значением** (`Send8(_usbConfiguration)` вместо стокового `Send8(1)`): патчится в `USBCore.cpp` вместе с дескриптором.
- **Serial parity**: `clone` парсит String Descriptor 3 (индексно по `iSerialNumber`); профиль с серийником получает `D_DEVICE …,IPRODUCT,3,1)` + generated `hub_serial_string.h` (`HUB_SERIAL_ENABLED` + строка), ядро отдаёт её в `GET_DESCRIPTOR(String, iSerial)`; профиль без серийника — `IPRODUCT,0,1)` (хост строку 3 не запрашивает) + пустой заголовок. Строки серийника валидируются как `-D`-строки (кавычки/бэкслеш/контрол-символы = ERROR). Стоковый `getShortName()` ("HIDxx") остаётся fallback в `#else` — недостижим в обоих состояниях.
- **Fail-loud в патчере — тотальный**: каждая замена якорится и проверяется результат (маркер/новый текст присутствует после замены), иначе `PatchError`. Молчаливый no-op со штампом версии — та же ошибка класса, что тихий рассинхрон R1.
- **AVR core path discovery**: uses `find_path()` which scans `arduino-cli-data/packages/arduino/hardware/avr/` for the requested version. No hardcoded path.
- **Arduino CLI binary is auto-downloaded**: to `.build/tools/` (gitignored). Release archive is SHA256-pinned (`CLI_SHA256` in downloader.py; mismatch = loud failure, unpinned version = warning). A `.version` marker records the downloaded version — a stale binary vs `--cli-version` logs a warning. Windows only.
- **Name conflict with hidapi**: imported as `usbhid/` internally to avoid collision with the `hid` module from hidapi.
- **G305 receiver: descriptor says Y first, shield-side data is X first** (host-dependent behavior). Windows' own HID caps (`InputCaps` in USB Tree Viewer dumps) decode Y@DataIndex16 before X@17, and the mouse works correctly plugged straight into a PC — yet behind the USB Host Shield the receiver emits X in the earlier field (verified empirically: right→down until calibrated). `profiles/sources/g305.json` keeps `data_index` at the reported ordinals and encodes the calibration in the source-only `wire_order` field (X=16, Y=17 — swapped vs descriptor). Re-running `clone` restores the reported order — re-apply the `wire_order` swap manually; the patch run surfaces the divergence as a `source layout` warning in `.build/patches.json` (never silent). Raw-report diagnostics: `HUB_DEBUG_DUMP` in `hidmouserptparser.h`.
- **`wire_order` is a sort key, not a bit offset**: `compute_report_layout()`
  assigns offsets by walking the axes ordered by `wire_order` (source) or by
  the JSON list (target). Target profiles don't serialize `wire_order` —
  the list order is their single source of truth.
- **Report ID byte must be detected by length, not value**: with the right button held the first payload byte is `0x02 == SRC_REPORT_ID`; `decode_input()` uses `len == SRC_REPORT_LEN && src[0] == SRC_REPORT_ID` (double stripping broke the right button).
- **Sketch macros must live in `hidmouserptparser.h`**, not `mouse.ino`: the `.ino` is a separate translation unit and its `#define`s are invisible to `hidmouserptparser.cpp`.
- **Mouse library holds buttons as `uint16_t`** (patched for the 16-button profile): `press(bit)`/`release(bit)` accept bits up to `0x8000`; the generated `encode_output()` masks to `TGT_BUTTONS_DATA_BITS`. Switching `--target` regenerates only `hid_profile.h`/`hid_mapper.h` — Mouse.cpp/h edits are marker-guarded and stay.
- **Command channel fixed constants**: command report id 3, descriptor `REPORT_COUNT` 15 (payload), hidapi buffer 16 (`[ID][payload]`) — see `command_generator.py`. Capability (meta-level GET_REPORT Feature, report id 4, payload 8) is optional per target (`capability.enabled`).
- **Windows sends SET_REPORT with wLength == 16, not 15**: `HidD_SetFeature`/`HidD_SetOutputReport` put the report id in `wValueL` AND also send it as the first byte of the data stage, so `wLength` is the full buffer length (16 = 1 + 15 payload). The core SET_REPORT handler reads up to `HID_COMMAND_TOTAL_LEN` and strips the duplicated report id byte (skips `scratch[0]` when `reportId == 3 && length == 16`, and when `reportId == 0`). This was the root cause of the `HidD_SetFeature: ERROR_GEN_FAILURE` stall during bring-up.
- **Core HID command patch is transport-only**: SET_REPORT does a bounded copy into a fixed ring (drop-new when full, default 4 slots); command processing happens only in `loop()` via `Cmd.poll()`. Never call Mouse API from the USB control-transfer handler.
- **interrupt_out transport** (`transport=interrupt_out`): the patched core claims a second endpoint (IN + OUT, `epType[2]`, `PluggableUSBModule(2,1,…)`, `bNumEndpoints=2`, `D_ENDPOINT(USB_ENDPOINT_OUT(pluggedEndpoint+1), …)`) — but only when `transport=interrupt_out`; the OUT endpoint must NOT be added for feature/output, otherwise `hid_write` would misroute into a bogus OUT endpoint. The OUT endpoint is read by polling `USB_Recv(pluggedEndpoint+1, …)` in `loop()`, NOT in the control ISR. On the wire the interrupt OUT report carries the report id as the FIRST data byte (16 bytes `[0x03][payload 15]`), unlike SET_REPORT where the id sits in `wValueL` — `readOutReport()` skips `scratch[0]`.
- **Command protocol** (`profiles/protocol/mouse.json`) is build-time source of truth: generates `mouse_commands.h` (device + pc_client) with opcodes AND argument `_OFF/_SIZE/_LEN` macros, validated against the 15-byte payload at patch time; handlers/clients never hardcode offsets.
- **Core patch is versioned** (`// HUB_PATCH_VERSION N` in HID.h/HID.cpp): re-patching a core patched by an older patcher upgrades it in place (`_upgrade_hid_cpp`), and a current-version core is skipped (idempotent fast path). Bump `HID_H_PATCH_VERSION`/`HID_CPP_PATCH_VERSION` when the patch content changes.
- **The command channel rides the existing HID interface** (vendor collection, usage page 0xFF00): no second USB interface. `interrupt_out` adds a second *endpoint* (IN+OUT) on that same interface; feature/output keep a single IN endpoint. The mouse collection stays byte-identical to target-only builds; PC client selects the vendor top-level collection via `hid_enumerate` + usage page filter.
- **Button arguments are logical numbers (1..16)**, mapped to bits in MouseCommandHandler — the protocol is independent of target layout (works for 3/5/16-button targets).
- **Re-patch before flashing after editing `profiles/targets/*.json` or `profiles/protocol/mouse.json`**: the patch step regenerates device headers (core config, hid_profile) AND `pc_client/target/*`; the flashed firmware and the built client must come from the same patch run.
- **Full masking requires the receiver-provided channel, not our own** (decision 2026-09): the self-made command channel (usage page 0xFF00, report id 3/4) is a fingerprint add-on the original does not have. For a full clone, bot commands must ride the receiver's own vendor interface (HID++) instead; until then the self-made channel stays the default and the HID++ transport becomes fallback/dev-only. See `docs/plans/receiver-full-clone.md` and `docs/plans/hid-clone-architecture.md` (channel section).
