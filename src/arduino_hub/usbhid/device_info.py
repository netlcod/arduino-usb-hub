from dataclasses import dataclass, field


@dataclass
class DeviceInfo:
    name: str = ""
    vendor_id: int = 0
    product_id: int = 0
    manufacturer_string: str = ""
    product_string: str = ""
    serial_number: str | None = None
    path: bytes = field(default=b"", repr=False)
