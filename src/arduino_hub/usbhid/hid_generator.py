"""HID translation layer generators.

Produces:
- `generate_report_descriptor(target)` — raw HID report descriptor bytes
  for a target profile (byte-compatible with the Arduino Mouse descriptor
  for the `arduino` layout and with the Logitech-style reconstruction for
  the `per_axis` layout).
- `generate_hid_profile_h(target, descriptor)` — `hid_profile.h`: USB HID
  description for the device side (HID_DESCRIPTOR[], HID_REPORT_ID,
  HID_REPORT_LENGTH).
- `generate_hid_mapper_h(source, target)` — `hid_mapper.h`: source→target
  translation (decode_input on the host shield side, encode_output on the
  Mouse library side).

Report field offsets follow the physical report layout: buttons occupy a
full byte field first, then each axis in report order (by data index for
source profiles, by list order for target profiles).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from arduino_hub.usbhid.device_info import AxisSpec, DeviceInfo, TargetProfile
from arduino_hub.usbhid.hid_items import (
    append_logical,
    append_report_count,
    append_usage,
    append_usage_page,
)

logger = logging.getLogger(__name__)

USAGE_X = 0x30
USAGE_Y = 0x31
USAGE_WHEEL = 0x38
USAGE_PAN = 0x0238

USAGE_FIELD = {
    USAGE_X: "x",
    USAGE_Y: "y",
    USAGE_WHEEL: "wheel",
    USAGE_PAN: "pan",
}

_USAGE_PAGE_BUTTON = 0x09
_USAGE_PAGE_GENERIC = 0x01
_LAYOUT_ARDUINO = "arduino"
_LAYOUT_PER_AXIS = "per_axis"


@dataclass
class ReportField:
    """One value field in a report (in physical layout order)."""
    name: str | None
    bit_offset: int
    bits: int


@dataclass
class ReportLayout:
    """Physical layout of a report: buttons first, then axes."""
    report_id: int
    report_length: int
    buttons_data_bits: int
    fields: list[ReportField] = field(default_factory=list)

    @property
    def buttons_field_bits(self) -> int:
        return ((self.buttons_data_bits + 7) // 8) * 8

    @property
    def payload_len(self) -> int:
        total = self.buttons_field_bits + sum(f.bits for f in self.fields)
        return (total + 7) // 8


def compute_report_layout(
    report_id: int,
    report_length: int,
    buttons: int,
    axes: list[AxisSpec],
    order_key=None,
) -> ReportLayout:
    """Compute bit offsets of every report field in physical order.

    `order_key` is the sort key for the physical (wire) field order:
    pass `lambda a: a.wire_order` for source profiles (where the wire
    order can differ from the descriptor order, e.g. the G305 X/Y
    quirk), or `None` to keep the given list order (target profiles:
    the JSON list order is the single source of truth).
    """
    offset = ((buttons + 7) // 8) * 8
    if order_key is not None:
        axes = sorted(axes, key=order_key)
    fields = []
    for axis in axes:
        fields.append(ReportField(USAGE_FIELD.get(axis.usage), offset, axis.bits))
        offset += axis.bits
    return ReportLayout(report_id, report_length, buttons, fields)


# ── Report descriptor generation ────────────────────────────────


def generate_report_descriptor(target: TargetProfile) -> bytes:
    """Generate the raw HID report descriptor for a target profile."""
    buf = bytearray()

    # USAGE_PAGE (Generic Desktop), USAGE (Mouse), COLLECTION (Application),
    # USAGE (Pointer), COLLECTION (Physical), REPORT_ID
    buf.extend((0x05, 0x01, 0x09, 0x02, 0xA1, 0x01, 0x09, 0x01, 0xA1, 0x00,
                0x85, target.report_id & 0xFF))

    # ── Buttons ──
    buttons = target.buttons
    if buttons > 0:
        buf.extend((0x05, _USAGE_PAGE_BUTTON))       # USAGE_PAGE (Button)
        buf.extend((0x19, 0x01))                     # USAGE_MINIMUM (1)
        if buttons <= 0xFF:
            buf.extend((0x29, buttons))              # USAGE_MAXIMUM
        else:
            buf.extend((0x2A, buttons & 0xFF, (buttons >> 8) & 0xFF))
        buf.extend((0x15, 0x00))                     # LOGICAL_MINIMUM (0)
        buf.extend((0x25, 0x01))                     # LOGICAL_MAXIMUM (1)
        if target.layout == _LAYOUT_ARDUINO:
            append_report_count(buf, buttons)            # REPORT_COUNT first
            buf.extend((0x75, 0x01))                     # REPORT_SIZE (1)
        else:
            buf.extend((0x75, 0x01))
            append_report_count(buf, buttons)
        buf.extend((0x81, 0x02))                     # INPUT (Data,Var,Abs)

        padding = (8 - buttons % 8) % 8
        if padding:
            buf.extend((0x95, 0x01))                 # REPORT_COUNT (1)
            buf.extend((0x75, padding))              # REPORT_SIZE (pad)
            buf.extend((0x81, 0x03))                 # INPUT (Cnst,Var,Abs)

    # ── Axes ──
    if target.layout == _LAYOUT_ARDUINO:
        # Group consecutive axes sharing the same attributes into one item
        axes = list(target.axes)
        i = 0
        while i < len(axes):
            a = axes[i]
            j = i + 1
            while (j < len(axes)
                   and axes[j].usage_page == a.usage_page
                   and axes[j].bits == a.bits
                   and axes[j].logical_min == a.logical_min
                   and axes[j].logical_max == a.logical_max
                   and axes[j].relative == a.relative):
                j += 1
            append_usage_page(buf, a.usage_page)
            for g in axes[i:j]:
                append_usage(buf, g.usage)
            append_logical(buf, a.logical_min)
            append_logical(buf, a.logical_max)
            buf.extend((0x75, a.bits & 0xFF))
            append_report_count(buf, j - i)
            buf.extend((0x81, 0x06 if a.relative else 0x02))
            i = j
    else:
        for a in target.axes:
            append_usage_page(buf, a.usage_page)
            append_usage(buf, a.usage)
            append_logical(buf, a.logical_min)
            append_logical(buf, a.logical_max)
            buf.extend((0x75, a.bits & 0xFF))
            buf.extend((0x95, 0x01))
            buf.extend((0x81, 0x06 if a.relative else 0x02))

    # END_COLLECTION (Physical), END_COLLECTION (Application)
    buf.extend((0xC0, 0xC0))
    return bytes(buf)


# ── hid_profile.h ───────────────────────────────────────────────


def generate_hid_profile_h(target: TargetProfile, descriptor: bytes) -> str:
    """C++ header with the USB HID description of the target report."""
    lines = [
        "#ifndef HID_PROFILE_H",
        "#define HID_PROFILE_H",
        "",
        "#include <stdint.h>",
        "#include <avr/pgmspace.h>",
        "",
        f"#define HID_REPORT_ID {target.report_id}",
        f"#define HID_REPORT_LENGTH {target.report_length}",
        "",
        "static const uint8_t HID_DESCRIPTOR[] PROGMEM = {",
    ]
    for i in range(0, len(descriptor), 8):
        chunk = ", ".join(f"0x{b:02X}" for b in descriptor[i:i + 8])
        lines.append(f"  {chunk},")
    lines += ["};", "", "#endif", ""]
    return "\n".join(lines)


# ── hid_mapper.h ────────────────────────────────────────────────

_HELPERS = """\
static inline uint16_t read_bits(const uint8_t* src, uint16_t bit_off, uint8_t bit_len) {
  uint16_t v = 0;
  for (uint8_t i = 0; i < bit_len; i++) {
    if (src[(bit_off + i) >> 3] & (1 << ((bit_off + i) & 7))) v |= (1U << i);
  }
  return v;
}

static inline int16_t sign_extend(uint16_t v, uint8_t bit_len) {
  if (bit_len < 16) {
    if (v & (1U << (bit_len - 1))) v |= (uint16_t)~(uint16_t)((1U << bit_len) - 1);
  }
  return (int16_t)v;
}

static inline void write_bits(uint8_t* dst, uint16_t bit_off, uint8_t bit_len, uint16_t v) {
  for (uint8_t i = 0; i < bit_len; i++) {
    uint8_t byte = (bit_off + i) >> 3;
    uint8_t mask = (uint8_t)(1 << ((bit_off + i) & 7));
    if (v & (1U << i)) dst[byte] |= mask;
    else dst[byte] &= (uint8_t)~mask;
  }
}

static inline int16_t clamp_i16(int16_t v, int16_t lo, int16_t hi) {
  if (v < lo) return lo;
  if (v > hi) return hi;
  return v;
}
"""


def _mapper_constants(prefix: str, layout: ReportLayout) -> list[str]:
    lines = [
        f"#define {prefix}_REPORT_ID {layout.report_id}",
        f"#define {prefix}_REPORT_LEN {layout.report_length}",
        f"#define {prefix}_BUTTONS_DATA_BITS {layout.buttons_data_bits}",
    ]
    for f in layout.fields:
        if not f.name:
            continue
        tag = f.name.upper()
        lines.append(f"#define {prefix}_{tag}_OFF {f.bit_offset}")
        lines.append(f"#define {prefix}_{tag}_BITS {f.bits}")
    return lines


def _validate_source_wire_order(source: DeviceInfo) -> None:
    """Raise on duplicate/missing wire order in a source profile."""
    orders = [a.wire_order for a in source.axes]
    if len(orders) != len(set(orders)):
        raise ValueError(
            f"Source '{source.name}' has duplicate wire_order values: "
            f"{sorted(orders)}"
        )


def source_layout_warnings(source: DeviceInfo) -> list[str]:
    """Warnings about source profile layout consistency.

    The key one: when the wire order differs from the descriptor order
    (some devices, e.g. the G305 receiver, send X before Y although
    the descriptor lists Y first), the override must be explicit and
    visible — it is otherwise silently lost on re-clone.
    """
    warnings: list[str] = []
    if not source.axes:
        return warnings

    wire = [a.usage for a in sorted(source.axes, key=lambda a: a.wire_order)]
    desc = [a.usage for a in sorted(source.axes, key=lambda a: a.data_index)]
    if wire != desc:
        warnings.append(
            f"wire order ({[hex(u) for u in wire]}) differs from descriptor "
            f"order ({[hex(u) for u in desc]}) — explicit wire_order override "
            "in effect"
        )

    payload = compute_report_layout(
        source.report_id, source.report_length, source.button_count, source.axes,
        order_key=lambda a: a.wire_order,
    ).payload_len
    if source.report_length != payload + 1:
        warnings.append(
            f"report_length {source.report_length} != payload {payload} + report id"
        )
    return warnings


def generate_hid_mapper_h(source: DeviceInfo, target: TargetProfile) -> str:
    """C++ header translating source reports into target reports."""
    _validate_source_wire_order(source)
    src = compute_report_layout(
        source.report_id, source.report_length, source.button_count, source.axes,
        order_key=lambda a: a.wire_order,
    )
    tgt = compute_report_layout(
        target.report_id, target.report_length, target.buttons, target.axes
    )

    warnings = source_layout_warnings(source)
    if warnings:
        logger.warning(
            "Source '%s' layout warnings: %s",
            source.name,
            "; ".join(warnings),
        )

    lines = [
        "#ifndef HID_MAPPER_H",
        "#define HID_MAPPER_H",
        "",
        "#include <stdint.h>",
        "",
        "// Source report (host shield side)",
    ]
    lines += _mapper_constants("SRC", src)
    lines.append(f"#define SRC_PAYLOAD_LEN {src.payload_len}")
    lines += ["", "// Target report (Mouse library side)"]
    lines += _mapper_constants("TGT", tgt)
    lines += ["", "struct MouseState {", "  uint16_t buttons;", "  int16_t x;",
              "  int16_t y;", "  int8_t wheel;", "  int8_t pan;", "};", "",
              _HELPERS, "inline bool decode_input(const uint8_t* src, uint8_t len, MouseState& out) {",
              "  out.buttons = 0; out.x = 0; out.y = 0; out.wheel = 0; out.pan = 0;",
              "  // Detect the report ID byte by length, not by value: with the right",
              "  // button held the first payload byte is 0x02 == SRC_REPORT_ID.",
              "  if (len == SRC_REPORT_LEN && src[0] == SRC_REPORT_ID) { src++; len--; }",
              "  if (len < SRC_PAYLOAD_LEN) return false;",
              "  out.buttons = read_bits(src, 0, SRC_BUTTONS_DATA_BITS);"]
    for f in src.fields:
        if not f.name:
            continue
        tag = f.name.upper()
        if f.name in ("wheel", "pan"):
            lines.append(
                f"  out.{f.name} = (int8_t)sign_extend(read_bits(src, "
                f"SRC_{tag}_OFF, SRC_{tag}_BITS), SRC_{tag}_BITS);"
            )
        else:
            lines.append(
                f"  out.{f.name} = sign_extend(read_bits(src, "
                f"SRC_{tag}_OFF, SRC_{tag}_BITS), SRC_{tag}_BITS);"
            )
    lines.append("  return true;")
    lines.append("}")
    lines += ["", "inline void encode_output(const MouseState& state, uint8_t* report) {"]
    lines.append(
        "  write_bits(report, 0, TGT_BUTTONS_DATA_BITS, "
        "state.buttons & ((1U << TGT_BUTTONS_DATA_BITS) - 1));"
    )
    for f in tgt.fields:
        if not f.name:
            continue
        axis = _axis_by_usage(target, f.name)
        lines.append(
            f"  write_bits(report, TGT_{f.name.upper()}_OFF, "
            f"TGT_{f.name.upper()}_BITS, (uint16_t)clamp_i16(state.{f.name}, "
            f"{axis.logical_min}, {axis.logical_max}));"
        )
    lines += ["}", "", "#endif", ""]
    return "\n".join(lines)


def _axis_by_usage(target: TargetProfile, name: str) -> AxisSpec:
    for axis in target.axes:
        if USAGE_FIELD.get(axis.usage) == name:
            return axis
    raise ValueError(f"Target '{target.name}' has no axis for '{name}'")
