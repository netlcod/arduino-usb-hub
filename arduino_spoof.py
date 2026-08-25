#!/usr/bin/env python3
# arduino_spoof.py - arduino leonardo usb identity spoof tool
# patches boards.txt + USBCore.cpp so the board enumerates as a target mouse
 
from __future__ import annotations
 
import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, asdict, replace
from pathlib import Path
 
VERSION = '2.0'
 
BACKUP_SUFFIX    = '.spoof.bak'
BOARDS_BEGIN     = '# === ARDUINO_SPOOF BEGIN (do not edit between markers) ==='
BOARDS_END       = '# === ARDUINO_SPOOF END ==='
USBCORE_OVR_BEGIN = '// === ARDUINO_SPOOF OVERRIDES BEGIN (do not edit between markers) ==='
USBCORE_OVR_END   = '// === ARDUINO_SPOOF OVERRIDES END ==='
 
 
class C:
    R       = '\033[0m'
    B       = '\033[1m'
    DIM     = '\033[2m'
    RED     = '\033[31m'
    GREEN   = '\033[32m'
    YELLOW  = '\033[33m'
    BLUE    = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN    = '\033[36m'
    GREY    = '\033[90m'
    BOLD_GREEN  = '\033[1;32m'
    BOLD_RED    = '\033[1;31m'
    BOLD_YELLOW = '\033[1;33m'
    BOLD_CYAN   = '\033[1;36m'
    REV         = '\033[7m'
 
     @classmethod
    def disable(cls):
        for name in list(vars(cls)):
            if name.isupper() and isinstance(getattr(cls, name), str):
                setattr(cls, name, '')
 
 
def enable_ansi():
    if os.name == 'nt':
        os.system('')
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, OSError):
        pass
 
 
def supports_color():
    if not sys.stdout.isatty():
        return False
    if os.environ.get('NO_COLOR'):
        return False
    return True
 
 
def _is_tty():
    return sys.stdin.isatty() and sys.stdout.isatty()
 
 
# ---- raw key input, set up once at import time ----
 
_CAN_RAW = False
 
if os.name == 'nt':
    try:
        import msvcrt as _msvcrt
 
        def _getch_raw():
            ch = _msvcrt.getwch()
            if ch in ('\x00', '\xe0'):
                ch2 = _msvcrt.getwch()
                if ch2 == 'H': return 'up'
                if ch2 == 'P': return 'down'
                if ch2 == 'K': return 'left'
                if ch2 == 'M': return 'right'
                return 'special'
            if ch in ('\r', '\n'): return 'enter'
            if ch == '\x1b':       return 'esc'
            if ch == '\x03':       raise KeyboardInterrupt
            return ch
 
        _CAN_RAW = True
    except ImportError:
        def _getch_raw():
            raise RuntimeError('msvcrt not available')
else:
    try:
        import tty as _tty
        import termios as _termios
        import select as _select
 
        def _getch_raw():
            fd  = sys.stdin.fileno()
            old = _termios.tcgetattr(fd)
            try:
                _tty.setraw(fd)
                ch = sys.stdin.read(1)
                if ch == '\x1b':
                    r, _, _ = _select.select([sys.stdin], [], [], 0.05)
                    if r:
                        ch2 = sys.stdin.read(1)
                        if ch2 == '[':
                            ch3 = sys.stdin.read(1)
                            if ch3 == 'A': return 'up'
                            if ch3 == 'B': return 'down'
                            if ch3 == 'C': return 'right'
                            if ch3 == 'D': return 'left'
                    return 'esc'
                if ch in ('\r', '\n'): return 'enter'
                if ch == '\x03':       raise KeyboardInterrupt
                return ch
            finally:
                _termios.tcsetattr(fd, _termios.TCSADRAIN, old)
 
        _CAN_RAW = True
    except ImportError:
        def _getch_raw():
            raise RuntimeError('tty/termios not available')
 
 
# ---- display helpers ----
 
def hr(char='-', width=64):
    print(C.GREY + char * width + C.R)
 
 
def header(title, sub=''):
    inner = 64
    print()
    print(C.BOLD_CYAN + '╔' + '═' * inner + '╗' + C.R)
    t = ('  ' + title).ljust(inner)
    print(C.BOLD_CYAN + '║' + C.R + C.B + t + C.R + C.BOLD_CYAN + '║' + C.R)
    if sub:
        s = ('  ' + sub).ljust(inner)
        print(C.BOLD_CYAN + '║' + C.R + C.GREY + s + C.R + C.BOLD_CYAN + '║' + C.R)
    print(C.BOLD_CYAN + '╚' + '═' * inner + '╝' + C.R)
 
 
def section(title):
    print()
    dashes = '─' * max(0, 60 - len(title) - 5)
    print(C.CYAN + f'─── {title} {dashes}' + C.R)
 
 
def ok(msg):   print(C.BOLD_GREEN  + ' ✓ ' + C.R + msg)
def warn(msg): print(C.BOLD_YELLOW + ' ! ' + C.R + msg)
def err(msg):  print(C.BOLD_RED    + ' ✗ ' + C.R + msg)
def info(msg): print(C.CYAN        + ' i ' + C.R + msg)
 
 
# ---- data model ----
 
  @Dataclass
class MouseProfile:
    key:            str
    label:          str
    vid:            int
    pid:            int
    manufacturer:   str
    product:        str
    bcd_usb:        int  = 0x0200
    bcd_device:     int  = 0x0100
    device_class:   int  = 0x00
    device_subclass: int = 0x00
    device_protocol: int = 0x00
    packet_size_0:  int  = 8
    max_power_ma:   int  = 100
    has_serial:     bool = False
    builtin:        bool = False
    notes:          str  = ''
 
    def to_dict(self):
        d = asdict(self)
        d.pop('builtin', None)
        return d
 
     @classmethod
    def from_dict(cls, d):
        allowed = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in allowed})
 
 
BUILTIN_PROFILES = {
    'generic-3btn': MouseProfile(
        key='generic-3btn',
        label='Generic 3-Button Optical Mouse',
        vid=0x0461, pid=0x4D81,
        manufacturer='PixArt',
        product='USB Optical Mouse',
        bcd_usb=0x0110, bcd_device=0x0100,
        device_class=0x00, device_subclass=0x00, device_protocol=0x00,
        packet_size_0=8, max_power_ma=100,
        has_serial=False, builtin=True,
        notes='Neutral OEM identity; safest baseline.',
    ),
    'ms-basic-optical': MouseProfile(
        key='ms-basic-optical',
        label='Microsoft Basic Optical Mouse',
        vid=0x045E, pid=0x00CB,
        manufacturer='Microsoft',
        product='Microsoft Basic Optical Mouse',
        bcd_usb=0x0200, bcd_device=0x0114,
        device_class=0x00, device_subclass=0x00, device_protocol=0x00,
        packet_size_0=8, max_power_ma=100,
        has_serial=False, builtin=True,
        notes='Very common office mouse; widely whitelisted by Windows.',
    ),
    'dell-ms116': MouseProfile(
        key='dell-ms116',
        label='Dell MS116 Wired Mouse',
        vid=0x413C, pid=0x301A,
        manufacturer='Dell',
        product='Dell MS116 Mouse',
        bcd_usb=0x0200, bcd_device=0x0100,
        device_class=0x00, device_subclass=0x00, device_protocol=0x00,
        packet_size_0=8, max_power_ma=100,
        has_serial=False, builtin=True,
        notes='Ubiquitous OEM-bundled office mouse.',
    ),
}
 
KNOWN_VENDORS = {
    0x046D: ['logitech'],
    0x045E: ['microsoft'],
    0x1532: ['razer'],
    0x1038: ['steelseries'],
    0x1B1C: ['corsair'],
    0x0738: ['mad catz'],
    0x044F: ['thrustmaster'],
    0x046A: ['cherry'],
    0x04D9: ['holtek', 'a4tech'],
    0x093A: ['pixart'],
    0x0461: ['primax'],
    0x413C: ['dell'],
    0x09DA: ['a4tech'],
    0x18F8: ['maxxter', '[maxxter]'],
    0x248A: ['maxxter'],
    0x05AC: ['apple'],
    0x2341: ['arduino'],
    0x1B4F: ['sparkfun'],
}
 
 
# ---- profile storage ----
 
def user_config_dir():
    if os.name == 'nt':
        base = os.environ.get('APPDATA') or str(Path.home() / 'AppData' / 'Roaming')
        return Path(base) / 'arduino_spoof'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'arduino_spoof'
    base = os.environ.get('XDG_CONFIG_HOME') or str(Path.home() / '.config')
    return Path(base) / 'arduino_spoof'
 
 
def custom_profiles_path():
    return user_config_dir() / 'profiles.json'
 
 
def load_custom_profiles():
    path = custom_profiles_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    out = {}
    for key, d in raw.items():
        try:
            p = MouseProfile.from_dict(d)
            p.builtin = False
            out[key] = p
        except (TypeError, ValueError):
            continue
    return out
 
 
def save_custom_profiles(profiles):
    user_config_dir().mkdir(parents=True, exist_ok=True)
    payload = {k: p.to_dict() for k, p in profiles.items()}
    path = custom_profiles_path()
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    os.replace(tmp, path)
 
 
def all_profiles():
    merged = {}
    merged.update(BUILTIN_PROFILES)
    merged.update(load_custom_profiles())
    return merged
 
 
# ---- arduino install discovery ----
 
def arduino15_roots():
    candidates = []
    if os.name == 'nt':
        if os.environ.get('LOCALAPPDATA'):
            candidates.append(Path(os.environ['LOCALAPPDATA']) / 'Arduino15')
        candidates.append(Path.home() / 'AppData' / 'Local' / 'Arduino15')
    elif sys.platform == 'darwin':
        candidates.append(Path.home() / 'Library' / 'Arduino15')
    else:
        candidates.append(Path.home() / '.arduino15')
        candidates.append(Path.home() / 'snap' / 'arduino' / 'current' / '.arduino15')
    seen = set()
    out  = []
    for c in candidates:
        if c not in seen:
            out.append(c)
            seen.add(c)
    return out
 
 
def discover_arduino_install():
    versions = []
    for root in arduino15_roots():
        avr_root = root / 'packages' / 'arduino' / 'hardware' / 'avr'
        if not avr_root.is_dir():
            continue
        for child in avr_root.iterdir():
            if not child.is_dir():
                continue
            try:
                parts = tuple(int(x) for x in child.name.split('.'))
            except ValueError:
                continue
            if (child / 'boards.txt').is_file():
                versions.append((parts, child))
    if not versions:
        return None
    versions.sort()
    return versions[-1][1]
 
 
def core_paths(install):
    return {
        'boards':    install / 'boards.txt',
        'usbcore_c': install / 'cores' / 'arduino' / 'USBCore.cpp',
        'usbcore_h': install / 'cores' / 'arduino' / 'USBCore.h',
        'usbdesc_h': install / 'cores' / 'arduino' / 'USBDesc.h',
    }
 
 
# ---- file I/O ----
 
def read_text(path):
    raw      = path.read_bytes()
    encoding = 'utf-8-sig' if raw.startswith(b'\xef\xbb\xbf') else 'utf-8'
    try:
        text = raw.decode(encoding)
    except UnicodeDecodeError:
        encoding = 'cp1252'
        text     = raw.decode(encoding, errors='replace')
    if b'\r\n' in raw:
        newline = '\r\n'
    elif b'\r' in raw and b'\n' not in raw:
        newline = '\r'
    else:
        newline = '\n'
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    return text, encoding, newline
 
 
def write_text_atomic(path, text, encoding, newline):
    text = text.replace('\n', newline)
    tmp  = path.with_suffix(path.suffix + '.tmp')
    tmp.write_bytes(text.encode(encoding))
    os.replace(tmp, path)
 
 
def ensure_backup(path):
    bak = path.with_suffix(path.suffix + BACKUP_SUFFIX)
    if not bak.exists():
        shutil.copy2(path, bak)
    return bak
 
 
# ---- boards.txt patching ----
 
def render_boards_block(p):
    vid = f'0x{p.vid:04X}'
    pid = f'0x{p.pid:04X}'
    return (
        f'{BOARDS_BEGIN}\n'
        f'# Spoofed Leonardo profile mimicking: {p.label}\n'
        f'# Generated by arduino_spoof.py v{VERSION} - re-run to update.\n'
        f'leonardo1.name=Arduino Leonardo MOD 1 ({p.label})\n'
        f'\n'
        f'leonardo1.vid.0=0x2341\n'
        f'leonardo1.pid.0=0x0036\n'
        f'leonardo1.vid.1={vid}\n'
        f'leonardo1.pid.1={pid}\n'
        f'leonardo1.upload_port.0.vid=0x2341\n'
        f'leonardo1.upload_port.0.pid=0x0036\n'
        f'leonardo1.upload_port.0.board=leonardo1\n'
        f'leonardo1.upload_port.1.vid={vid}\n'
        f'leonardo1.upload_port.1.pid={pid}\n'
        f'leonardo1.upload_port.1.board=leonardo1\n'
        f'\n'
        f'leonardo1.upload.tool=avrdude\n'
        f'leonardo1.upload.tool.default=avrdude\n'
        f'leonardo1.upload.tool.network=arduino_ota\n'
        f'leonardo1.upload.protocol=avr109\n'
        f'leonardo1.upload.maximum_size=28672\n'
        f'leonardo1.upload.maximum_data_size=2560\n'
        f'leonardo1.upload.speed=57600\n'
        f'leonardo1.upload.disable_flushing=true\n'
        f'leonardo1.upload.use_1200bps_touch=true\n'
        f'leonardo1.upload.wait_for_upload_port=true\n'
        f'\n'
        f'leonardo1.bootloader.tool=avrdude\n'
        f'leonardo1.bootloader.tool.default=avrdude\n'
        f'leonardo1.bootloader.low_fuses=0xff\n'
        f'leonardo1.bootloader.high_fuses=0xd8\n'
        f'leonardo1.bootloader.extended_fuses=0xcb\n'
        f'leonardo1.bootloader.file=caterina/Caterina-Leonardo.hex\n'
        f'leonardo1.bootloader.unlock_bits=0x3F\n'
        f'leonardo1.bootloader.lock_bits=0x2F\n'
        f'\n'
        f'leonardo1.build.mcu=atmega32u4\n'
        f'leonardo1.build.f_cpu=16000000L\n'
        f'leonardo1.build.vid={vid}\n'
        f'leonardo1.build.pid={pid}\n'
        f'leonardo1.build.usb_product="{p.product}"\n'
        f'leonardo1.build.usb_manufacturer="{p.manufacturer}"\n'
        f'leonardo1.build.board=AVR_LEONARDO\n'
        f'leonardo1.build.core=arduino\n'
        f'leonardo1.build.variant=leonardo\n'
        f'leonardo1.build.extra_flags={{build.usb_flags}} -DCDC_DISABLED\n'
        f'{BOARDS_END}\n'
    )
 
 
_LEONARDO1_LINE = re.compile(r'^\s*leonardo1\.', re.MULTILINE)
_BOARDS_BLOCK   = re.compile(re.escape(BOARDS_BEGIN) + r'.*?' + re.escape(BOARDS_END) + r'\n?', re.DOTALL)
 
 
def patch_boards_txt(text, profile):
    text    = _BOARDS_BLOCK.sub('', text)
    cleaned = [l for l in text.split('\n') if not _LEONARDO1_LINE.match(l)]
    text    = '\n'.join(cleaned)
    text    = re.sub(r'\n{4,}', '\n\n\n', text)
    text    = text.rstrip() + '\n\n'
    text   += render_boards_block(profile)
    return text
 
 
# ---- USBCore.cpp patching ----
 
def render_usbcore_overrides(p):
    return (
        f'{USBCORE_OVR_BEGIN}\n'
        f'// Target: {p.label}  VID=0x{p.vid:04X}  PID=0x{p.pid:04X}\n'
        f'// Generated by arduino_spoof.py v{VERSION} - do not hand-edit.\n'
        f'#undef  USB_VERSION\n'
        f'#define USB_VERSION      0x{p.bcd_usb:04X}\n'
        f'#undef  USB_CONFIG_POWER\n'
        f'#define USB_CONFIG_POWER {p.max_power_ma}\n'
        f'{USBCORE_OVR_END}\n'
    )
 
 
def render_d_device(p, *, cdc_enabled):
    iserial = 'ISERIAL' if p.has_serial else '0'
    if cdc_enabled:
        return (
            f'\tD_DEVICE(0xEF,0x02,0x01,{p.packet_size_0},'
            f'USB_VID,USB_PID,0x{p.bcd_device:04X},'
            f'IMANUFACTURER,IPRODUCT,{iserial},1);'
        )
    return (
        f'\tD_DEVICE(0x{p.device_class:02X},'
        f'0x{p.device_subclass:02X},'
        f'0x{p.device_protocol:02X},'
        f'{p.packet_size_0},USB_VID,USB_PID,'
        f'0x{p.bcd_device:04X},IMANUFACTURER,IPRODUCT,{iserial},1);'
    )
 
 
_OVR_BLOCK = re.compile(re.escape(USBCORE_OVR_BEGIN) + r'.*?' + re.escape(USBCORE_OVR_END) + r'\n?', re.DOTALL)
_LEGACY_OVR_BLOCK = re.compile(
    r'//\s*[—─━–-]+\s*Clone-identity overrides\s*[—─━–-]+\n'
    r'(?:.*?\n)*?'
    r'//\s*[—─━–-]+\n',
    re.MULTILINE,
)
_LAST_INCLUDE_AT_TOP = re.compile(r'((?:^#include[^\n]*\n)+)', re.MULTILINE)
_D_DEVICE_CALL       = re.compile(r'^\s*D_DEVICE\s*\([^;]*\)\s*;', re.MULTILINE)
_CDC_IFDEF           = re.compile(r'^\s*#\s*ifdef\s+CDC_ENABLED\b', re.MULTILINE)
_CDC_ELSE            = re.compile(r'^\s*#\s*else\b',   re.MULTILINE)
_CDC_ENDIF           = re.compile(r'^\s*#\s*endif\b',  re.MULTILINE)
 
 
def patch_usbcore_overrides(text, profile):
    block = render_usbcore_overrides(profile)
    if _OVR_BLOCK.search(text):
        return _OVR_BLOCK.sub(block, text, count=1)
    if _LEGACY_OVR_BLOCK.search(text):
        return _LEGACY_OVR_BLOCK.sub(block, text, count=1)
    m = _LAST_INCLUDE_AT_TOP.search(text)
    if not m:
        return block + '\n' + text
    return text[:m.end()] + '\n' + block + text[m.end():]
 
 
def patch_usbcore_descriptor(text, profile):
    out, last_end = [], 0
    for m in _D_DEVICE_CALL.finditer(text):
        out.append(text[last_end:m.start()])
        prefix = text[:m.start()]
        last_ifdef = last_else = last_endif = None
        for mm in _CDC_IFDEF.finditer(prefix): last_ifdef = mm
        for mm in _CDC_ELSE.finditer(prefix):  last_else  = mm
        for mm in _CDC_ENDIF.finditer(prefix): last_endif = mm
        cdc_on = False
        if last_ifdef and (last_endif is None or last_ifdef.start() > last_endif.start()):
            if last_else is None or last_else.start() < last_ifdef.start():
                cdc_on = True
        out.append(render_d_device(profile, cdc_enabled=cdc_on))
        last_end = m.end()
    out.append(text[last_end:])
    return ''.join(out)
 
 
# ---- post-patch validation ----
 
def validate_install(install, profile):
    issues = []
    paths  = core_paths(install)
 
    boards_text, _, _ = read_text(paths['boards'])
    checks = [
        ('leonardo1.build.vid=',              f'0x{profile.vid:04X}'),
        ('leonardo1.build.pid=',              f'0x{profile.pid:04X}'),
        ('leonardo1.build.usb_product=',      f'"{profile.product}"'),
        ('leonardo1.build.usb_manufacturer=', f'"{profile.manufacturer}"'),
    ]
    for prefix, want in checks:
        line = next((l for l in boards_text.splitlines() if l.startswith(prefix)), None)
        if line is None:
            issues.append(f'boards.txt: missing line "{prefix}..."')
        elif line[len(prefix):].strip() != want:
            issues.append(f'boards.txt: {prefix} expected {want!r}, got {line[len(prefix):]!r}')
 
    cpp_text, _, _ = read_text(paths['usbcore_c'])
    if USBCORE_OVR_BEGIN not in cpp_text:
        issues.append('USBCore.cpp: override block missing')
    else:
        if f'#define USB_VERSION      0x{profile.bcd_usb:04X}' not in cpp_text:
            issues.append('USBCore.cpp: USB_VERSION mismatch')
        if f'#define USB_CONFIG_POWER {profile.max_power_ma}' not in cpp_text:
            issues.append('USBCore.cpp: USB_CONFIG_POWER mismatch')
    if f'0x{profile.bcd_device:04X}' not in cpp_text:
        issues.append(f'USBCore.cpp: bcdDevice 0x{profile.bcd_device:04X} not found')
    if not _D_DEVICE_CALL.search(cpp_text):
        issues.append('USBCore.cpp: no D_DEVICE() call found')
    return issues
 
 
# ---- safety checks ----
 
  @Dataclass
class SafetyIssue:
    severity: str
    field:    str
    message:  str
 
 
def check_profile_safety(p):
    issues = []
 
    if p.vid <= 0 or p.vid > 0xFFFF:
        issues.append(SafetyIssue('ERROR', 'vid', f'idVendor 0x{p.vid:04X} out of range.'))
    if p.pid <= 0 or p.pid > 0xFFFF:
        issues.append(SafetyIssue('ERROR', 'pid', f'idProduct 0x{p.pid:04X} out of range.'))
    if not p.manufacturer:
        issues.append(SafetyIssue('ERROR', 'manufacturer', 'Manufacturer string is empty.'))
    if not p.product:
        issues.append(SafetyIssue('ERROR', 'product', 'Product string is empty.'))
    for fname, s in (('manufacturer', p.manufacturer), ('product', p.product)):
        if '"' in s or '\\' in s:
            issues.append(SafetyIssue('ERROR', fname,
                f'String contains "/\\ which breaks compiler -D flags: {s!r}'))
        if len(s) > 126:
            issues.append(SafetyIssue('ERROR', fname, f'String too long ({len(s)} chars, max 126).'))
        if not all(0x20 <= ord(c) <= 0x7E or 0xA0 <= ord(c) <= 0xFFFF for c in s):
            issues.append(SafetyIssue('ERROR', fname, f'String has control chars: {s!r}'))
    if p.packet_size_0 not in (8, 16, 32, 64):
        issues.append(SafetyIssue('ERROR', 'packet_size_0',
            f'bMaxPacketSize0={p.packet_size_0} must be 8, 16, 32, or 64.'))
    if not (0x0100 <= p.bcd_usb <= 0x0310):
        issues.append(SafetyIssue('ERROR', 'bcd_usb',
            f'bcdUSB 0x{p.bcd_usb:04X} outside sane range (0x0100..0x0310).'))
    if not (0x0000 <= p.bcd_device <= 0xFFFF):
        issues.append(SafetyIssue('ERROR', 'bcd_device',
            f'bcdDevice 0x{p.bcd_device:04X} out of range.'))
    if p.max_power_ma <= 0 or p.max_power_ma > 500:
        issues.append(SafetyIssue('ERROR', 'max_power_ma',
            f'bMaxPower {p.max_power_ma}mA outside 1..500.'))
    if not (0x00 <= p.device_class <= 0xFF):
        issues.append(SafetyIssue('ERROR', 'device_class', 'bDeviceClass out of byte range.'))
 
    if p.packet_size_0 != 8:
        issues.append(SafetyIssue('WARN', 'packet_size_0',
            f'bMaxPacketSize0={p.packet_size_0} known to fail on Leonardo+USB Host Shield. Use 8.'))
    if p.max_power_ma > 200:
        issues.append(SafetyIssue('WARN', 'max_power_ma',
            f'bMaxPower={p.max_power_ma}mA exceeds typical Leonardo budget (~200mA). '
            f'Real mice rarely report >100mA.'))
    if p.device_class == 0xFF:
        issues.append(SafetyIssue('WARN', 'device_class',
            'bDeviceClass=0xFF (vendor-specific) may cause OS to load an incompatible vendor driver.'))
    if p.device_class != 0x00:
        issues.append(SafetyIssue('WARN', 'device_class',
            f'Real HID mice use bDeviceClass=0x00. You set 0x{p.device_class:02X}.'))
    if p.vid in KNOWN_VENDORS:
        names = KNOWN_VENDORS[p.vid]
        if not any(n in p.manufacturer.lower() for n in names):
            issues.append(SafetyIssue('WARN', 'manufacturer',
                f'VID 0x{p.vid:04X} belongs to {", ".join(names)} '
                f'but manufacturer string is {p.manufacturer!r}.'))
    if p.has_serial:
        issues.append(SafetyIssue('INFO', 'has_serial',
            'iSerialNumber requires a serial string in the sketch; skip unless you supply one.'))
    if p.bcd_usb >= 0x0300:
        issues.append(SafetyIssue('INFO', 'bcd_usb',
            'bcdUSB >= 0x0300 implies SuperSpeed; Leonardo is full-speed. Host will likely downgrade.'))
    return issues
 
 
def print_safety_report(issues):
    if not issues:
        return
    for it in issues:
        if it.severity == 'ERROR':   err(f'{it.field}: {it.message}')
        elif it.severity == 'WARN':  warn(f'{it.field}: {it.message}')
        else:                        info(f'{it.field}: {it.message}')
 
 
# ---- parsers ----
 
_HEX = r'0?x?([0-9A-Fa-f]+)'
 
 
def _parse_hex(s):
    m = re.search(_HEX, s)
    if not m:
        return None
    try:
        return int(m.group(1), 16)
    except ValueError:
        return None
 
 
def parse_usbtreeviewer_dump(text):
    def find(pat, flags=0):
        m = re.search(pat, text, flags)
        return m.group(1).strip() if m else None
 
    vid_s = find(r'^\s*idVendor\s*:\s*0x([0-9A-Fa-f]+)', re.MULTILINE)
    pid_s = find(r'^\s*idProduct\s*:\s*0x([0-9A-Fa-f]+)', re.MULTILINE)
    if not vid_s or not pid_s:
        vid_s = find(r'^\s*Vendor ID\s*:\s*0x([0-9A-Fa-f]+)', re.MULTILINE) or vid_s
        pid_s = find(r'^\s*Product ID\s*:\s*0x([0-9A-Fa-f]+)', re.MULTILINE) or pid_s
    if not vid_s or not pid_s:
        return None
    vid = int(vid_s, 16)
    pid = int(pid_s, 16)
    if vid == 0 and pid == 0:
        return None
 
    bcd_usb_s    = find(r'^\s*bcdUSB\s*:\s*0x([0-9A-Fa-f]+)',       re.MULTILINE)
    bcd_dev_s    = find(r'^\s*bcdDevice\s*:\s*0x([0-9A-Fa-f]+)',     re.MULTILINE)
    dclass_s     = find(r'^\s*bDeviceClass\s*:\s*0x([0-9A-Fa-f]+)',  re.MULTILINE)
    dsub_s       = find(r'^\s*bDeviceSubClass\s*:\s*0x([0-9A-Fa-f]+)', re.MULTILINE)
    dproto_s     = find(r'^\s*bDeviceProtocol\s*:\s*0x([0-9A-Fa-f]+)', re.MULTILINE)
    pkt_s        = find(r'^\s*bMaxPacketSize0\s*:\s*0x([0-9A-Fa-f]+)', re.MULTILINE)
    serial_idx_s = find(r'^\s*iSerialNumber\s*:\s*0x([0-9A-Fa-f]+)', re.MULTILINE)
 
    mfr  = find(r'^\s*Manufacturer String\s*:\s*"([^"]+)"', re.MULTILINE)
    prod = find(r'^\s*Product String\s*:\s*"([^"]+)"',      re.MULTILINE)
    if not mfr:
        mfr = find(r'^\s*iManufacturer\s*:\s*0x[0-9A-Fa-f]+\s*"([^"]+)"', re.MULTILINE)
    if not prod:
        prod = find(r'^\s*iProduct\s*:\s*0x[0-9A-Fa-f]+\s*"([^"]+)"',     re.MULTILINE)
    mfr  = mfr  or 'Unknown'
    prod = prod or 'USB Device'
 
    max_power_ma = 100
    m = re.search(r'\((\d+)\s*mA\)', text)
    if m:
        max_power_ma = int(m.group(1))
 
    return MouseProfile(
        key='custom', label=f'{mfr} {prod}',
        vid=vid, pid=pid, manufacturer=mfr, product=prod,
        bcd_usb=int(bcd_usb_s, 16) if bcd_usb_s else 0x0200,
        bcd_device=int(bcd_dev_s, 16) if bcd_dev_s else 0x0100,
        device_class=int(dclass_s, 16) if dclass_s else 0x00,
        device_subclass=int(dsub_s, 16) if dsub_s else 0x00,
        device_protocol=int(dproto_s, 16) if dproto_s else 0x00,
        packet_size_0=int(pkt_s, 16) if pkt_s else 8,
        max_power_ma=max_power_ma,
        has_serial=bool(serial_idx_s and int(serial_idx_s, 16) != 0),
    )
 
 
def parse_lsusb_output(text):
    def find(pat):
        m = re.search(pat, text, re.MULTILINE)
        return m.group(1).strip() if m else None
 
    vid_s = find(r'^\s*idVendor\s+0x([0-9A-Fa-f]+)')
    pid_s = find(r'^\s*idProduct\s+0x([0-9A-Fa-f]+)')
    if not vid_s or not pid_s:
        return None
    vid = int(vid_s, 16)
    pid = int(pid_s, 16)
 
    bcd_usb_s = find(r'^\s*bcdUSB\s+(\d+\.\d+)')
    bcd_dev_s = find(r'^\s*bcdDevice\s+(\d+\.\d+)')
 
    def _bcd(s):
        if not s: return None
        try:
            maj, mn = s.split('.')
            return (int(maj) << 8) | int(mn[:2].ljust(2, '0'))
        except ValueError:
            return None
 
    dclass_s = find(r'^\s*bDeviceClass\s+(\d+)')
    dsub_s   = find(r'^\s*bDeviceSubClass\s+(\d+)')
    dproto_s = find(r'^\s*bDeviceProtocol\s+(\d+)')
    pkt_s    = find(r'^\s*bMaxPacketSize0\s+(\d+)')
    maxpower = find(r'^\s*MaxPower\s+(\d+)mA')
    serial_s = find(r'^\s*iSerial\s+\d+\s*(.*)$')
 
    mfr  = find(r'^\s*iManufacturer\s+\d+\s+(.+)$')
    prod = find(r'^\s*iProduct\s+\d+\s+(.+)$')
    mfr  = (mfr  or 'Unknown').strip()
    prod = (prod or 'USB Device').strip()
 
    return MouseProfile(
        key='custom', label=f'{mfr} {prod}',
        vid=vid, pid=pid, manufacturer=mfr, product=prod,
        bcd_usb=_bcd(bcd_usb_s) or 0x0200,
        bcd_device=_bcd(bcd_dev_s) or 0x0100,
        device_class=int(dclass_s) if dclass_s else 0,
        device_subclass=int(dsub_s) if dsub_s else 0,
        device_protocol=int(dproto_s) if dproto_s else 0,
        packet_size_0=int(pkt_s) if pkt_s else 8,
        max_power_ma=int(maxpower) if maxpower else 100,
        has_serial=bool(serial_s and serial_s.strip() and serial_s.strip() != '0'),
    )
 
 
# ---- live device probing ----
 
  @Dataclass
class ProbedDevice:
    vid:          int
    pid:          int
    manufacturer: str
    product:      str
    raw:          str = ''
 
 
def probe_usb_devices():
    if os.name == 'nt':      return _probe_windows()
    if sys.platform == 'darwin': return _probe_macos()
    return _probe_linux()
 
 
def _probe_windows():
    ps = (
        "Get-CimInstance Win32_PnPEntity | "
        "Where-Object { $_.DeviceID -match 'USB\\\\VID_[0-9A-F]{4}&PID_[0-9A-F]{4}' "
        "-and ($_.Service -eq 'HidUsb' -or $_.PNPClass -eq 'Mouse' -or "
        "$_.PNPClass -eq 'HIDClass') } | "
        "Select-Object Name,Manufacturer,DeviceID | ConvertTo-Json -Compress -Depth 3"
    )
    try:
        out = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', ps],
            capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if out.returncode != 0 or not out.stdout.strip():
        return []
    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    devs = []
    seen = set()
    for item in data:
        dev_id = item.get('DeviceID') or ''
        m = re.search(r'VID_([0-9A-F]{4})&PID_([0-9A-F]{4})', dev_id, re.I)
        if not m:
            continue
        vid = int(m.group(1), 16)
        pid = int(m.group(2), 16)
        if (vid, pid) in seen:
            continue
        seen.add((vid, pid))
        devs.append(ProbedDevice(
            vid=vid, pid=pid,
            manufacturer=str(item.get('Manufacturer') or '').strip() or 'Unknown',
            product=str(item.get('Name') or '').strip() or 'USB Device',
            raw=dev_id,
        ))
    return devs
 
 
def _probe_linux():
    try:
        out = subprocess.run(['lsusb'], capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if out.returncode != 0:
        return []
    devs = []
    for line in out.stdout.splitlines():
        m = re.search(r'ID\s+([0-9A-Fa-f]{4}):([0-9A-Fa-f]{4})\s*(.*)', line)
        if not m:
            continue
        vid  = int(m.group(1), 16)
        pid  = int(m.group(2), 16)
        desc = m.group(3).strip() or 'USB Device'
        if vid in (0x1d6b,):
            continue
        parts = desc.split(None, 1)
        mfr, prod = (parts[0], parts[1]) if len(parts) == 2 else ('Unknown', desc)
        devs.append(ProbedDevice(vid=vid, pid=pid, manufacturer=mfr, product=prod, raw=line))
    return devs
 
 
def _probe_macos():
    try:
        out = subprocess.run(
            ['system_profiler', '-json', 'SPUSBDataType'],
            capture_output=True, text=True, timeout=15)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if out.returncode != 0:
        return []
    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError:
        return []
    devs = []
 
    def walk(node):
        if isinstance(node, list):
            for x in node: walk(x)
            return
        if not isinstance(node, dict):
            return
        vid = node.get('vendor_id')
        pid = node.get('product_id')
        if isinstance(vid, str) and isinstance(pid, str):
            try:
                devs.append(ProbedDevice(
                    vid=int(vid.split()[0], 16),
                    pid=int(pid.split()[0], 16),
                    manufacturer=node.get('manufacturer', 'Unknown'),
                    product=node.get('_name', 'USB Device'),
                ))
            except (ValueError, IndexError):
                pass
        for v in node.values():
            walk(v)
 
    walk(data.get('SPUSBDataType', []))
    return devs
 
 
# ---- spinner ----
 
class _Spinner:
    _FRAMES = '|/-\\'
 
    def __init__(self, msg):
        self.msg   = msg
        self._stop = threading.Event()
        self._t    = threading.Thread(target=self._run, daemon=True)
 
    def _run(self):
        i = 0
        while not self._stop.is_set():
            print(f'\r{C.CYAN}{self._FRAMES[i % 4]}{C.R} {self.msg}', end='', flush=True)
            i += 1
            time.sleep(0.1)
 
    def __enter__(self):
        if sys.stdout.isatty():
            self._t.start()
        return self
 
    def __exit__(self, *_):
        self._stop.set()
        if self._t.is_alive():
            self._t.join()
        print('\r\033[K', end='', flush=True)
 
 
# ---- interactive prompts ----
 
def _input(prompt_text):
    try:
        return input(prompt_text)
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(130)
 
 
def ask(text, default=''):
    suffix = f' {C.GREY}[{default}]{C.R}' if default else ''
    s = _input(f'{C.B}? {C.R}{text}{suffix}: ').strip()
    return s or default
 
 
def ask_int(text, default=None, lo=0, hi=0xFFFFFFFF):
    d = '' if default is None else str(default)
    while True:
        raw = ask(f'{text} ({lo}..{hi})', d)
        if not raw and default is not None:
            return default
        try:
            v = int(raw, 0)
        except ValueError:
            err('Not a valid integer (decimal or 0x hex).')
            continue
        if v < lo or v > hi:
            err(f'Value out of range: must be {lo}..{hi}.')
            continue
        return v
 
 
def ask_hex16(text, default=None):
    d = '' if default is None else f'0x{default:04X}'
    while True:
        raw = ask(f'{text} (hex, e.g. 0x046D)', d).strip()
        if not raw and default is not None:
            return default
        # accept 0x-prefixed or bare hex digits
        try:
            v = int(raw, 16) if not raw.lower().startswith('0x') else int(raw, 0)
        except ValueError:
            try:
                v = int(raw)
            except ValueError:
                err('Not a valid hex value (try: 0x046D or 046D).')
                continue
        if v < 0 or v > 0xFFFF:
            err('Value must be in range 0x0000..0xFFFF.')
            continue
        return v
 
 
def ask_yn(text, default=True):
    suffix = 'Y/n' if default else 'y/N'
    prompt = f'{C.B}? {C.R}{text} {C.GREY}[{suffix}]{C.R}: '
 
    if _CAN_RAW and _is_tty():
        print(prompt, end='', flush=True)
        while True:
            ch = _getch_raw()
            if ch == 'enter':
                print('y' if default else 'n')
                return default
            if ch.lower() == 'y':
                print('y')
                return True
            if ch.lower() == 'n':
                print('n')
                return False
    else:
        while True:
            raw = _input(prompt).strip().lower()
            if not raw:
                return default
            if raw in ('y', 'yes'):  return True
            if raw in ('n', 'no'):   return False
            err('Please answer y or n.')
 
 
def _menu_row(text, selected):
    text = text[:72] if len(text) > 72 else text
    if selected:
        return f'  {C.CYAN}>{C.R} {C.B}{text}{C.R}\033[K'
    return f'    {C.DIM}{text}{C.R}\033[K'
 
 
def _fallback_menu(title, choices, default=0):
    print(C.B + title + C.R)
    for i, c in enumerate(choices, 1):
        marker = C.CYAN + '>' + C.R if (i - 1) == default else ' '
        print(f'  {marker} {i}) {c}')
    while True:
        raw = ask(f'Choice [1-{len(choices)}]', str(default + 1))
        try:
            i = int(raw)
            if 1 <= i <= len(choices):
                return i - 1
        except ValueError:
            pass
        err(f'Enter a number between 1 and {len(choices)}.')
 
 
def ask_choice(title, choices, default=0):
    if not _CAN_RAW or not _is_tty():
        return _fallback_menu(title, choices, default)
 
    sel = max(0, min(default, len(choices) - 1))
    n   = len(choices)
 
    print()
    print(C.B + title + C.R)
    print(C.GREY + '  ↑↓ navigate   Enter select   Esc cancel   1-9 jump' + C.R)
    for i, c in enumerate(choices):
        print(_menu_row(c, i == sel))
 
    while True:
        try:
            key = _getch_raw()
        except KeyboardInterrupt:
            print(f'\033[{n + 1}A\033[J', end='', flush=True)
            raise
 
        prev = sel
        if key == 'up':
            sel = (sel - 1) % n
        elif key == 'down':
            sel = (sel + 1) % n
        elif key == 'enter':
            print(f'\033[{n + 1}A\033[J', end='', flush=True)
            print(f'  {C.GREEN}>{C.R} {choices[sel]}')
            return sel
        elif key == 'esc':
            print(f'\033[{n + 1}A\033[J', end='', flush=True)
            print(C.GREY + '  (cancelled)' + C.R)
            return None
        elif key.isdigit() and key != '0':
            idx = int(key) - 1
            if idx < n:
                sel = idx
 
        if sel != prev:
            print(f'\033[{n}A', end='', flush=True)
            for i, c in enumerate(choices):
                print(_menu_row(c, i == sel))
 
 
def ask_multiline(text):
    print()
    print(C.B + text + C.R)
    print(C.GREY + '  Paste below. End with ===END=== on its own line, or Ctrl-D / Ctrl-Z.' + C.R)
    hr('─')
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == '===END===':
            break
        lines.append(line)
    hr('─')
    return '\n'.join(lines)
 
 
def print_profile(p, title=''):
    if title:
        section(title)
    rows = [
        ('key',             p.key),
        ('label',           p.label),
        ('VID / PID',       f'0x{p.vid:04X} / 0x{p.pid:04X}'),
        ('Manufacturer',    f'"{p.manufacturer}"'),
        ('Product',         f'"{p.product}"'),
        ('bcdUSB',          f'0x{p.bcd_usb:04X}'),
        ('bcdDevice',       f'0x{p.bcd_device:04X}'),
        ('Class/Sub/Proto', f'0x{p.device_class:02X} / 0x{p.device_subclass:02X} / 0x{p.device_protocol:02X}'),
        ('bMaxPacketSize0', str(p.packet_size_0)),
        ('bMaxPower',       f'{p.max_power_ma} mA'),
        ('Serial',          'yes' if p.has_serial else 'no'),
    ]
    if p.notes:
        rows.append(('Notes', p.notes))
    w = max(len(r[0]) for r in rows)
    for label, val in rows:
        print(f'  {C.GREY}{label.ljust(w)}{C.R}  {val}')
 
 
# ---- profile editing ----
 
def sanitize_key(s):
    s = re.sub(r'[^a-z0-9-]+', '-', s.lower().strip())
    s = re.sub(r'-+', '-', s).strip('-')
    return s or 'custom'
 
 
def edit_profile_interactively(p):
    section('Edit profile (press Enter to keep current value)')
    p = replace(p,
        manufacturer=ask('Manufacturer',              p.manufacturer),
        product=ask('Product',                        p.product),
        vid=ask_hex16('idVendor',                     p.vid),
        pid=ask_hex16('idProduct',                    p.pid),
        bcd_usb=ask_hex16('bcdUSB',                   p.bcd_usb),
        bcd_device=ask_hex16('bcdDevice',             p.bcd_device),
        device_class=ask_int('bDeviceClass',          p.device_class,    0, 0xFF),
        device_subclass=ask_int('bDeviceSubClass',    p.device_subclass, 0, 0xFF),
        device_protocol=ask_int('bDeviceProtocol',    p.device_protocol, 0, 0xFF),
        packet_size_0=ask_int('bMaxPacketSize0 (8/16/32/64)', p.packet_size_0, 8, 64),
        max_power_ma=ask_int('bMaxPower mA',          p.max_power_ma,    1, 500),
        has_serial=ask_yn('Publish serial number?',   p.has_serial),
    )
    p.label = f'{p.manufacturer} {p.product}'.strip()
    return p
 
 
def save_profile_with_safety(p, allow_override=True):
    issues = check_profile_safety(p)
    print_safety_report(issues)
    errors = [i for i in issues if i.severity == 'ERROR']
    if errors:
        err(f'{len(errors)} hard error(s) — cannot save.')
        return None
    warns = [i for i in issues if i.severity == 'WARN']
    if warns and allow_override:
        if not ask_yn(f'{len(warns)} warning(s). Save anyway?', default=False):
            return None
    return p
 
 
# ---- creation flows ----
 
def flow_create_from_dump():
    section('Create profile from USB Device Tree Viewer dump')
    info('In USB Device Tree Viewer: right-click device -> Save as text (or copy the view pane).')
    dump = ask_multiline('Paste dump:')
    if not dump.strip():
        warn('Nothing pasted.')
        return None
    p = parse_usbtreeviewer_dump(dump)
    if not p:
        err('Could not find idVendor/idProduct in that text.')
        return None
    key    = sanitize_key(ask('Profile key', sanitize_key(p.product)))
    p.key   = key
    p.label = ask('Friendly label', p.label)
    print_profile(p, 'Derived profile')
    if ask_yn('Edit any fields before saving?', default=False):
        p = edit_profile_interactively(p)
    return save_profile_with_safety(p)
 
 
def flow_create_from_lsusb():
    section('Create profile from lsusb -v dump')
    info("Run: lsusb -v -d VID:PID  (replace with your device's VID:PID)")
    dump = ask_multiline('Paste lsusb output:')
    if not dump.strip():
        warn('Nothing pasted.')
        return None
    p = parse_lsusb_output(dump)
    if not p:
        err('Could not find idVendor/idProduct in that text.')
        return None
    key    = sanitize_key(ask('Profile key', sanitize_key(p.product)))
    p.key   = key
    p.label = ask('Friendly label', p.label)
    print_profile(p, 'Derived profile')
    if ask_yn('Edit any fields before saving?', default=False):
        p = edit_profile_interactively(p)
    return save_profile_with_safety(p)
 
 
def flow_create_from_probe():
    section('Create profile from connected device')
    info('Plug your target mouse into THIS machine (not the Arduino), then continue.')
    if not ask_yn('Continue?', default=True):
        return None
 
    with _Spinner('Scanning USB devices...'):
        devs = probe_usb_devices()
 
    if not devs:
        err('No USB HID devices found. On Linux try installing usbutils.')
        return None
 
    labels = [
        f'VID 0x{d.vid:04X} / PID 0x{d.pid:04X}  —  {d.manufacturer} / {d.product}'
        for d in devs
    ]
    idx = ask_choice('Pick the device to clone:', labels)
    if idx is None:
        return None
    d = devs[idx]
 
    p = MouseProfile(
        key=sanitize_key(d.product),
        label=f'{d.manufacturer} {d.product}',
        vid=d.vid, pid=d.pid,
        manufacturer=d.manufacturer or 'Unknown',
        product=d.product or 'USB Device',
    )
    info('OS probe gives VID/PID and strings only.')
    info('Other descriptor fields (bcdUSB, bMaxPacketSize0, MaxPower) use safe defaults.')
    info('For bit-perfect parity, use the USB Device Tree Viewer / lsusb dump flow.')
    print_profile(p, 'Probed profile')
    if ask_yn('Edit any fields before saving?', default=False):
        p = edit_profile_interactively(p)
    return save_profile_with_safety(p)
 
 
def flow_create_manual():
    section('Create profile manually')
    seed = MouseProfile(key='custom', label='Custom Mouse',
        vid=0x0461, pid=0x4D81, manufacturer='Unknown', product='USB Mouse')
    p      = edit_profile_interactively(seed)
    p.key   = sanitize_key(ask('Profile key', sanitize_key(p.product)))
    p.label = ask('Friendly label', f'{p.manufacturer} {p.product}')
    print_profile(p, 'Final profile')
    return save_profile_with_safety(p)
 
 
def flow_save_custom(p):
    customs = load_custom_profiles()
    if p.key in BUILTIN_PROFILES:
        warn(f'Key "{p.key}" shadows a built-in preset.')
    if p.key in customs:
        if not ask_yn(f'Overwrite existing custom profile "{p.key}"?', default=False):
            info('Cancelled.')
            return
    customs[p.key] = p
    save_custom_profiles(customs)
    ok(f'Saved "{p.key}" to {custom_profiles_path()}')
 
 
def flow_pick_profile():
    profiles = all_profiles()
    if not profiles:
        warn('No profiles available.')
        return None
    keys   = list(profiles.keys())
    labels = []
    for k in keys:
        p   = profiles[k]
        tag = '[builtin]' if p.builtin else '[custom] '
        labels.append(f'{tag}  {k:<24}  {p.label}')
    idx = ask_choice('Select a profile:', labels)
    if idx is None:
        return None
    return profiles[keys[idx]]
 
 
def flow_edit_or_delete_custom():
    customs = load_custom_profiles()
    if not customs:
        warn('No custom profiles saved yet.')
        return
    keys   = list(customs.keys())
    labels = [f'{k:<24}  {customs[k].label}' for k in keys]
    idx = ask_choice('Pick a custom profile:', labels)
    if idx is None:
        return
    key = keys[idx]
    p   = customs[key]
    print_profile(p, f'Profile "{key}"')
    action = ask_choice('Action:', ['Edit', 'Delete', 'Cancel'])
    if action == 0:
        new_p = edit_profile_interactively(p)
        new_p.key = key
        new_p = save_profile_with_safety(new_p)
        if new_p:
            customs[key] = new_p
            save_custom_profiles(customs)
            ok(f'Updated "{key}".')
    elif action == 1:
        if ask_yn(f'Really delete "{key}"?', default=False):
            customs.pop(key, None)
            save_custom_profiles(customs)
            ok(f'Deleted "{key}".')
 
 
# ---- apply / diff / restore / check ----
 
def apply_profile(install, profile, *, dry_run=False):
    paths = core_paths(install)
    for label, p in paths.items():
        if label in ('boards', 'usbcore_c') and not p.is_file():
            err(f'Required file not found: {p}')
            return False
 
    boards_text, b_enc, b_nl = read_text(paths['boards'])
    new_boards = patch_boards_txt(boards_text, profile)
 
    cpp_text, c_enc, c_nl = read_text(paths['usbcore_c'])
    new_cpp = patch_usbcore_overrides(cpp_text, profile)
    new_cpp = patch_usbcore_descriptor(new_cpp, profile)
 
    if dry_run:
        print()
        section('boards.txt diff')
        sys.stdout.write(_diff(boards_text, new_boards, 'boards.txt'))
        section('USBCore.cpp diff')
        sys.stdout.write(_diff(cpp_text, new_cpp, 'USBCore.cpp'))
        return True
 
    issues = check_profile_safety(profile)
    print_safety_report(issues)
    if any(i.severity == 'ERROR' for i in issues):
        err('Hard safety errors — refusing to apply.')
        return False
 
    if boards_text != new_boards:
        ensure_backup(paths['boards'])
        print(f'  patching boards.txt ... ', end='', flush=True)
        write_text_atomic(paths['boards'], new_boards, b_enc, b_nl)
        print(C.GREEN + 'done' + C.R)
    else:
        info('boards.txt unchanged')
 
    if cpp_text != new_cpp:
        ensure_backup(paths['usbcore_c'])
        print(f'  patching USBCore.cpp ... ', end='', flush=True)
        write_text_atomic(paths['usbcore_c'], new_cpp, c_enc, c_nl)
        print(C.GREEN + 'done' + C.R)
    else:
        info('USBCore.cpp unchanged')
 
    post = validate_install(install, profile)
    if post:
        err('Post-apply validation failed:')
        for i in post:
            print(f'  - {i}')
        return False
 
    print()
    ok(f'Spoof applied — profile "{profile.key}"')
    print()
    print('  Next steps:')
    print(f'    1. Arduino IDE -> Tools -> Board -> "Arduino Leonardo MOD 1 ({profile.label})"')
    print(f'    2. Re-flash your sketch.')
    print(f'    3. Verify with USB Device Tree Viewer / lsusb -v:')
    print(f'         VID=0x{profile.vid:04X}  PID=0x{profile.pid:04X}')
    print(f'         bcdUSB=0x{profile.bcd_usb:04X}  bcdDevice=0x{profile.bcd_device:04X}')
    print(f'         iManufacturer="{profile.manufacturer}"  iProduct="{profile.product}"')
    print(f'         bMaxPower=0x{profile.max_power_ma // 2:02X} ({profile.max_power_ma} mA)')
    return True
 
 
def _diff(before, after, label):
    if before == after:
        return f'{label}: (no change)\n'
    return ''.join(difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f'{label} (current)',
        tofile=f'{label} (after spoof)',
        n=3,
    ))
 
 
def flow_restore(install):
    restored = 0
    for label, p in core_paths(install).items():
        bak = p.with_suffix(p.suffix + BACKUP_SUFFIX)
        if bak.is_file():
            shutil.copy2(bak, p)
            ok(f'Restored {p}')
            restored += 1
    if restored == 0:
        warn('No .spoof.bak files found — nothing to restore.')
        return 1
    return 0
 
 
def flow_check(install, profile):
    paths       = core_paths(install)
    cpp_text, _, _    = read_text(paths['usbcore_c'])
    boards_text, _, _ = read_text(paths['boards'])
 
    in_cpp    = USBCORE_OVR_BEGIN in cpp_text
    in_boards = BOARDS_BEGIN in boards_text
    cpp_bak   = paths['usbcore_c'].with_suffix(paths['usbcore_c'].suffix + BACKUP_SUFFIX)
    brd_bak   = paths['boards'].with_suffix(paths['boards'].suffix + BACKUP_SUFFIX)
 
    section('Current state')
    print(f'  Override block in USBCore.cpp : {"yes" if in_cpp    else "no"}')
    print(f'  Spoof block in boards.txt     : {"yes" if in_boards else "no"}')
    print(f'  USBCore.cpp backup            : {"present" if cpp_bak.is_file() else "missing"}')
    print(f'  boards.txt backup             : {"present" if brd_bak.is_file() else "missing"}')
 
    if profile is None:
        return 0 if (in_cpp and in_boards) else 1
    issues = validate_install(install, profile)
    if issues:
        err(f'Validation failed for profile "{profile.key}":')
        for i in issues:
            print(f'  - {i}')
        return 1
    ok(f'Install matches profile "{profile.key}".')
    return 0
 
 
# ---- main interactive menu ----
 
def interactive_main(install):
    header(
        f'Arduino Leonardo USB Spoof Tool  v{VERSION}',
        'patches boards.txt + USBCore.cpp for mouse identity spoofing',
    )
    if install:
        info(f'Arduino AVR install: {install}')
    else:
        warn('Arduino AVR install not found. Use --install-path to set a custom path.')
 
    opts = [
        'Apply a profile',
        'Check current state',
        'Create profile from USB Device Tree Viewer dump',
        'Create profile from lsusb -v dump',
        'Create profile from connected device',
        'Create profile manually',
        'Edit / delete a custom profile',
        'List all profiles',
        'Restore original files',
        'Quit',
    ]
 
    while True:
        choice = ask_choice('Main menu', opts)
        if choice is None:
            continue
        if choice == 0:
            if not install: err('No install path set.'); continue
            p = flow_pick_profile()
            if not p: continue
            print_profile(p, 'Selected profile')
            if not ask_yn('Apply this profile?', default=True): continue
            apply_profile(install, p)
        elif choice == 1:
            if not install: err('No install path set.'); continue
            flow_check(install, None)
        elif choice == 2:
            p = flow_create_from_dump()
            if p: flow_save_custom(p)
        elif choice == 3:
            p = flow_create_from_lsusb()
            if p: flow_save_custom(p)
        elif choice == 4:
            p = flow_create_from_probe()
            if p: flow_save_custom(p)
        elif choice == 5:
            p = flow_create_manual()
            if p: flow_save_custom(p)
        elif choice == 6:
            flow_edit_or_delete_custom()
        elif choice == 7:
            section('All profiles')
            for k, p in all_profiles().items():
                tag = '[builtin]' if p.builtin else '[custom] '
                print(f'  {C.GREY}{tag}{C.R} {C.B}{k:<24}{C.R} {p.label}')
                if p.notes:
                    print(f'           {C.GREY}{p.notes}{C.R}')
        elif choice == 8:
            if not install: err('No install path set.'); continue
            flow_restore(install)
        elif choice == 9:
            return 0
 
 
# ---- CLI subcommands ----
 
def _resolve_install(args):
    if args.install_path:
        p = Path(args.install_path)
        return p if p.is_dir() else None
    return discover_arduino_install()
 
 
def cmd_apply(args, *, dry=False):
    profiles = all_profiles()
    if args.profile not in profiles:
        err(f'Unknown profile "{args.profile}". Try list-profiles.')
        return 2
    install = _resolve_install(args)
    if not install:
        err('Arduino AVR install not found. Use --install-path.')
        return 2
    return 0 if apply_profile(install, profiles[args.profile], dry_run=dry) else 1
 
 
def cmd_check(args):
    install = _resolve_install(args)
    if not install:
        err('Arduino AVR install not found.')
        return 2
    profile = None
    if args.profile:
        profile = all_profiles().get(args.profile)
        if not profile:
            err(f'Unknown profile "{args.profile}".')
            return 2
    return flow_check(install, profile)
 
 
def cmd_restore(args):
    install = _resolve_install(args)
    if not install:
        err('Arduino AVR install not found.')
        return 2
    return flow_restore(install)
 
 
def cmd_list_profiles(args):
    for k, p in all_profiles().items():
        tag = '[builtin]' if p.builtin else '[custom] '
        print(f'{tag} {k}')
        print(f'  label   {p.label}')
        print(f'  vid/pid 0x{p.vid:04X} / 0x{p.pid:04X}')
        print(f'  bcdUSB  0x{p.bcd_usb:04X}  bcdDevice 0x{p.bcd_device:04X}  '
              f'pkt0={p.packet_size_0}  power={p.max_power_ma}mA')
        if p.notes:
            print(f'  notes   {p.notes}')
        print()
    return 0
 
 
def cmd_delete_profile(args):
    customs = load_custom_profiles()
    if args.profile not in customs:
        err(f'No custom profile named "{args.profile}".')
        return 2
    del customs[args.profile]
    save_custom_profiles(customs)
    ok(f'Deleted "{args.profile}".')
    return 0
 
 
def cmd_export_profile(args):
    profile = all_profiles().get(args.profile)
    if not profile:
        err(f'Unknown profile "{args.profile}".')
        return 2
    print(json.dumps(profile.to_dict(), indent=2))
    return 0
 
 
def cmd_import_profile(args):
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        err(f'Invalid JSON: {e}')
        return 2
    try:
        p = MouseProfile.from_dict(data)
    except (TypeError, ValueError) as e:
        err(f'Bad profile shape: {e}')
        return 2
    p.builtin = False
    saved = save_profile_with_safety(p, allow_override=False)
    if not saved:
        return 1
    customs = load_custom_profiles()
    customs[p.key] = p
    save_custom_profiles(customs)
    ok(f'Imported "{p.key}".')
    return 0
 
 
def main(argv):
    enable_ansi()
    if not supports_color():
        C.disable()
 
    parser = argparse.ArgumentParser(
        prog='arduino_spoof',
        description='Arduino Leonardo USB spoof tool.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Run with no subcommand for the interactive menu.',
    )
    parser.add_argument('--install-path', help='Override Arduino AVR install path.')
    parser.add_argument('--no-color', action='store_true', help='Disable ANSI colors.')
 
    sub = parser.add_subparsers(dest='command')
 
    sp = sub.add_parser('apply', help='Apply a profile.')
    sp.add_argument('--profile', required=True)
 
    sp = sub.add_parser('dry-run', help='Show diff without writing.')
    sp.add_argument('--profile', required=True)
 
    sp = sub.add_parser('check', help='Check current install state.')
    sp.add_argument('--profile', default=None)
 
    sub.add_parser('restore',        help='Restore .spoof.bak files.')
    sub.add_parser('list-profiles',  help='List all profiles.')
 
    sp = sub.add_parser('delete-profile', help='Delete a custom profile.')
    sp.add_argument('profile')
 
    sp = sub.add_parser('export-profile', help='Print a profile as JSON.')
    sp.add_argument('profile')
 
    sub.add_parser('import-profile', help='Read a profile JSON from stdin.')
 
    args = parser.parse_args(argv)
    if args.no_color:
        C.disable()
 
    if args.command is None:
        install = _resolve_install(args)
        try:
            return interactive_main(install)
        except SystemExit as e:
            return int(e.code) if e.code is not None else 0
        except KeyboardInterrupt:
            print()
            return 130
 
    dispatch = {
        'apply':           lambda: cmd_apply(args, dry=False),
        'dry-run':         lambda: cmd_apply(args, dry=True),
        'check':           lambda: cmd_check(args),
        'restore':         lambda: cmd_restore(args),
        'list-profiles':   lambda: cmd_list_profiles(args),
        'delete-profile':  lambda: cmd_delete_profile(args),
        'export-profile':  lambda: cmd_export_profile(args),
        'import-profile':  lambda: cmd_import_profile(args),
    }
    try:
        return dispatch[args.command]()
    except KeyboardInterrupt:
        print()
        return 130
 
 
if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
