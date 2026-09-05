# Upstream bug report: ArduinoCore-avr — control-IN transfers hang when EP0 is configured smaller than 64 bytes

> Prepared from the arduino-usb-hub project findings (2026-08).
> Intended for filing against `ArduinoCore-avr` (verified on 1.8.6,
> `USBCore.cpp`). Text below is ready to paste into a GitHub issue.

---

## Title

`USBCore.cpp: SendControl() releases the EP0 FIFO on a hardcoded 64-byte boundary — control transfers hang forever when EP0 is configured smaller than 64`

## Summary

`USBCore.cpp::SendControl()` decides "the EP0 FIFO is full, release this
packet" using a hardcoded 64-byte mask:

```c
if (!((_cmark + 1) & 0x3F))
    ClearIN();   // Fifo is full, release this packet
```

If endpoint 0 is configured with a bank smaller than 64 bytes (the
ATmega32U4 hardware supports 8/16/32/64 for control endpoints via
`UECFG1X.EPSIZE`), the release condition never fires at the real bank
boundary. The first control-IN response longer than the bank size writes
past the full FIFO, and the next `WaitForINOrOUT()` spins forever waiting
for `TXINI` — permanently hanging inside the USB control ISR. From the
host's perspective enumeration dies partway through.

## Environment

- Board: Arduino Leonardo (ATmega32U4), also applicable to 16U4-based designs
- Core: ArduinoCore-avr 1.8.6 (`cores/arduino/USBCore.cpp`)
- Host: Windows 11 (xHCI), also reproduced logically on Linux analysis
- Build: CDC-disabled HID-only firmware (`-DCDC_DISABLED`), Mouse library

## Reproduction

1. Disable CDC and configure the device descriptor with a 32-byte EP0:

   ```c
   // USBCore.cpp, CDC_DISABLED branch:
   const DeviceDescriptor USB_DeviceDescriptorIAD =
       D_DEVICE(0x00,0x00,0x00,32,USB_VID,USB_PID,0x100,IMANUFACTURER,IPRODUCT,ISERIAL,1);
   ```

2. Configure the EP0 FIFO to actually match 32 bytes (otherwise the
   transfer fails differently — see "Two distinct failure modes" below):

   ```c
   #define EP_SINGLE_32 0x22          // EPSIZE=010 (32B) | ALLOC — not defined in stock core
   InitEP(0,EP_TYPE_CONTROL,EP_SINGLE_32);
   ```

3. Flash and plug in.

### Actual result

- `GET_DESCRIPTOR(Device)` (18 bytes ≤ 32) succeeds.
- `GET_DESCRIPTOR(Configuration)` (41 bytes on this firmware) freezes the
  device mid data-stage at byte 33:
  - Windows: "This device cannot start" / *сбой запроса дескриптора
    конфигурации* while the 18-byte device descriptor is still visible in
    USBView, or — depending on the host controller state — the device
    disappears from the bus entirely.
- On-board LED frozen in whatever state the ISR left it (the CPU spins in
  `USB_COM_vect`).

### Expected result

Control-IN responses are split into packets matching the configured EP0
bank size (41 bytes = 32 + 9; 77 bytes = 32 + 32 + 13), exactly as real
devices with `bMaxPacketSize0 = 8/16/32` behave.

## Root cause

```c
static int _cmark;
static int _cend;

static bool SendControl(u8 d)
{
    if (_cmark < _cend)
    {
        if (!WaitForINOrOUT())
            return false;
        Send8(d);
        if (!((_cmark + 1) & 0x3F))     // <-- hardcoded 64-byte assumption
            ClearIN();                  // Fifo is full, release this packet
    }
    _cmark++;
    return true;
}
```

The full-bank test must match the *configured* EP0 bank size, not a fixed
64. With a 32-byte bank:

1. Bytes 0–31 are written without a release (the mask does not trigger).
2. Byte 33 (`_cmark == 32`) is pushed into an already-full FIFO — the byte
   is lost and `TXINI` stays low.
3. The following `WaitForINOrOUT()` waits for `TXINI` forever → permanent
   spin inside the control ISR → the whole MCU stops servicing anything.

Note the asymmetry that makes this easy to miss: the DEVICE decides
control-IN packet boundaries (it owns the FIFO), so no amount of host-side
correctness can paper over this. Device descriptors (18 bytes) fit into a
32-byte bank, which is why early enumeration stages look healthy and the
failure surfaces only on the first response longer than 32 bytes.

## Why it has gone unnoticed so far

Stock sketches never touch EP0: `USB_Init()` hardcodes

```c
InitEP(0,EP_TYPE_CONTROL,EP_SINGLE_64);   // init ep0
```

and `-DUSB_EP_SIZE=n` (used by `InitEndpoints()` for data endpoints)
does not affect EP0. The latent bug only fires when someone legitimately
reconfigures EP0 — e.g., emulating a real device whose
`bMaxPacketSize0` is 8/16/32 (very common among actual HID mice), or
trying to save DPRAM.

Per the ATmega16U4/32U4 datasheet (§21.9, §22), EP0 supports programmable
FIFO up to 64 bytes with all of 8/16/32/64 legal (`EPSIZE` 000–011),
single-bank. We also confirmed the encoding independently
(`EP_SINGLE_32 = 0x22` = EPSIZE `010` | ALLOC) before suspecting the
core. One more trap documented in the datasheet: `CFGOK` is set "even in
the case where there is a conflict in the memory allocation", and the
stock `InitEP()` never checks it anyway — so an invalid configuration
fails silently rather than loudly.

## Two distinct failure modes (worth knowing for triage)

| EP0 bank | Descriptor declares | Failure |
|---|---|---|
| 64 (stock) | 32 | Config descriptor (41 B) leaves as one illegal 41-byte packet → host aborts with *config descriptor failure*; device partially visible |
| 32 (correctly allocated) | 32 | FIFO overflows at byte 33 → ISR hang → device invisible / frozen |

We hit both in sequence while adding EP0 parity to our emulator; the
second mode looks like a dead board and is much harder to triage.

## Suggested fix

Make the bank size explicit and share it between the allocation site and
the release condition:

```c
// near the EP_* defines:
#ifndef USB_EP0_MAX_PACKET
#define USB_EP0_MAX_PACKET 64
#endif

#if USB_EP0_MAX_PACKET == 8
#define USB_EP0_ALLOC EP_SINGLE_8        // 0x02 — needs to be added
#elif USB_EP0_MAX_PACKET == 16
#define USB_EP0_ALLOC EP_SINGLE_16
#elif USB_EP0_MAX_PACKET == 32
#define USB_EP0_ALLOC EP_SINGLE_32       // 0x22 — needs to be added
#else
#define USB_EP0_ALLOC EP_SINGLE_64
#endif

...
    InitEP(0,EP_TYPE_CONTROL,USB_EP0_ALLOC);   // init ep0
...

static bool SendControl(u8 d)
{
    if (_cmark < _cend)
    {
        if (!WaitForINOrOUT())
            return false;
        Send8(d);
        if (!((_cmark + 1) % USB_EP0_MAX_PACKET))   // was: & 0x3F
            ClearIN();
    }
    _cmark++;
    return true;
}
```

`% 64` compiles to the same AND for the default configuration, so stock
behavior is bit-for-bit unchanged; other power-of-two sizes get correct
release cadence. (Alternatively keep a `static uint8_t ep0Bank` set next
to the `InitEP(0,...)` call.)

## Related observation (minor)

`USB_RecvControl()` similarly caps at a fixed 64:

```c
// Use fixed 64 because control EP always have 64 bytes even on 16u2.
auto recvLength = length;
if (recvLength > 64) recvLength = 64;
```

This is benign in practice — the HOST chunks control-OUT data stages
according to the declared `wMaxPacketSize0`, and typical control-OUT
payloads here are tiny — but for consistency it should derive from the
same constant once EP0 becomes configurable.

## Verification of the fix

With the patch above applied (`USB_EP0_MAX_PACKET=32`,
`EP_SINGLE_32`, `% USB_EP0_MAX_PACKET`):

- Full clean enumeration on Windows 11 as a composite HID device with
  `bMaxPacketSize0 = 32`;
- Control responses chunk legally: config descriptor 41 = 32 + 9,
  HID report descriptor 77 = 32 + 32 + 13;
- Long-run stability: continuous mouse traffic + periodic feature
  reports over the same EP0, no hangs across repeated replugs;
- Default path unchanged (64 → identical machine behavior).
