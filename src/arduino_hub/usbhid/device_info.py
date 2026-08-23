from dataclasses import dataclass, field


@dataclass
class HIDCollection:
    """A single HID collection (report group) within an interface."""
    usage_page: int = 0
    usage: int = 0
    report_id: int = 0
    input_report_length: int = 0
    output_report_length: int = 0
    feature_report_length: int = 0


@dataclass
class HIDReportDescriptor:
    """Raw HID Report Descriptor for one HID interface."""
    interface_number: int = 0
    data: bytes = b""
    collections: list[HIDCollection] = field(default_factory=list)


@dataclass
class AxisSpec:
    """A single HID value field (axis) in a report.

    For source profiles (profiles/sources/*.json) `data_index` is the
    descriptor ordinal as reported by USB Tree Viewer, while
    `wire_order` is the physical field sequence in the report data.
    Some devices (e.g. G305 receiver) send X before Y although the
    descriptor lists Y first; `wire_order` is the explicit, validated
    override for that quirk.
    """
    usage: int = 0x30
    bits: int = 8
    logical_min: int = -127
    logical_max: int = 127
    usage_page: int = 0x01
    relative: bool = True
    data_index: int = 0
    wire_order: int = 0


@dataclass
class CommandProfile:
    """PC → Arduino command channel wiring (per target).

    report_id / report_length are fixed constants of the command
    protocol, they are not configurable here.
    """
    enabled: bool = False
    transport: str = "feature"  # "feature" | "output" | "interrupt_out"
    queue_slots: int = 4


@dataclass
class CapabilityProfile:
    """Optional meta-level capability report (GET_REPORT Feature)."""
    enabled: bool = False


@dataclass
class TargetProfile:
    """Output HID profile: the report the Leonardo will present to the PC."""
    name: str = ""
    device_kind: str = "mouse"  # "mouse" | "keyboard" | "gamepad"
    report_id: int = 1
    report_length: int = 4
    buttons: int = 3
    layout: str = "per_axis"
    axes: list[AxisSpec] = field(default_factory=list)
    command: CommandProfile = field(default_factory=CommandProfile)
    capability: CapabilityProfile = field(default_factory=CapabilityProfile)


@dataclass
class DeviceInfo:
    """Full USB HID device descriptor data for cloning."""

    # ── Identification ──────────────────────────────────
    name: str = ""

    # ── Device Descriptor ───────────────────────────────
    vendor_id: int = 0
    product_id: int = 0
    bcd_device: int = 0x0100
    usb_version: int = 0x0200
    device_class: int = 0x00
    device_subclass: int = 0x00
    device_protocol: int = 0x00
    ep0_max_packet_size: int = 64

    # ── Configuration Descriptor ────────────────────────
    bm_attributes: int = 0x80
    max_power_ma: int = 100

    # ── HID Interface ───────────────────────────────────
    hid_interface_class: int = 0x03
    hid_subclass: int = 1
    hid_protocol: int = 2
    bcd_hid: int = 0x0111
    country_code: int = 0

    # ── Endpoint ────────────────────────────────────────
    ep_max_packet_size: int = 16
    ep_interval_ms: int = 1

    # ── HID Report ──────────────────────────────────────
    hid_report_descriptors: list[HIDReportDescriptor] = field(default_factory=list)
    report_id: int = 1
    report_length: int = 4
    button_count: int = 3
    axes: list[AxisSpec] = field(default_factory=list)

    # ── Strings ─────────────────────────────────────────
    manufacturer_string: str = ""
    product_string: str = ""
    serial_number: str | None = None
    lang_id: int = 0x0409

    # ── Internal (not for Arduino patching) ─────────────
    path: bytes = field(default=b"", repr=False)

    @property
    def report_descriptor_size(self) -> int:
        return sum(len(r.data) for r in self.hid_report_descriptors)

    @property
    def has_serial(self) -> bool:
        return self.serial_number is not None and len(self.serial_number) > 0
