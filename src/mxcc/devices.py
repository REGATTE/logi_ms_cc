"""Read-only Logitech inventory for the settings window, never the worker.

USB/sysfs and BlueZ describe devices already present on this computer. Discovery
does not open input/HID handles, start Bluetooth discovery, or pair devices.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
import re
import subprocess
import time
from typing import Mapping


LOGITECH_VENDOR = 0x046D
KEYPAD_PRODUCT = 0xC354
DIALPAD_PRODUCT = 0xBC00
_MAC = r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}"
_BLUEZ_PATH = re.compile(r"/org/bluez/hci\d+/dev_(?:[0-9A-Fa-f]{2}_){5}[0-9A-Fa-f]{2}")


@dataclass(frozen=True)
class Device:
    id: str
    name: str
    kind: str
    connection: str
    connected: bool | None
    supported: bool
    vendor_id: int
    product_id: int
    battery_percent: int | None = None
    detail: str = ""


def _read(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            return stream.read(4096).decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def _entries(path: Path) -> list[Path]:
    try:
        return sorted(path.iterdir())
    except OSError:
        return []


def _number(value: str) -> int | None:
    try:
        return int(value, 16)
    except ValueError:
        return None


def _name(value: str, product: int) -> str:
    if product == KEYPAD_PRODUCT:
        return "MX Creative Keypad"
    if product == DIALPAD_PRODUCT:
        return "MX Dialpad"
    return re.sub(r"^(?:Logitech|Logi)\s+", "", value, flags=re.IGNORECASE).strip() or f"Logitech device {product:04x}"


def _kind(name: str, product: int) -> str:
    text = name.casefold()
    if product == KEYPAD_PRODUCT:
        return "keypad"
    if product == DIALPAD_PRODUCT or "dialpad" in text:
        return "dialpad"
    if "receiver" in text:
        return "receiver"
    if any(word in text for word in ("keyboard", "mx keys", "pop keys", "wave keys", "craft")):
        return "keyboard"
    if any(word in text for word in ("mouse", "trackball", "mx master", "mx anywhere", "lift")):
        return "mouse"
    if any(word in text for word in ("webcam", "brio", "streamcam")):
        return "webcam"
    if any(word in text for word in ("headset", "headphone", "zone", "earbud")):
        return "headset"
    if any(word in text for word in ("litra", "light")):
        return "light"
    return "device"


def _device(identifier: str, name: str, product: int, connection: str,
            *, connected: bool | None = True, battery: int | None = None, detail: str = "") -> Device:
    name = _name(name, product)
    return Device(identifier, name, _kind(name, product), connection, connected,
                  product == KEYPAD_PRODUCT, LOGITECH_VENDOR, product, battery, detail)


def _property(value):
    # Accept both plain fixture properties and busctl's scalar JSON variants.
    return value.get("data") if isinstance(value, dict) and "type" in value else value


def _bluez_devices(objects: Mapping) -> tuple[dict[str, Device], set[str]]:
    devices, connected = {}, set()
    for path, interfaces in objects.items():
        if not isinstance(path, str) or not _BLUEZ_PATH.fullmatch(path) or not isinstance(interfaces, dict):
            continue
        properties = interfaces.get("org.bluez.Device1", {})
        if not isinstance(properties, dict):
            continue
        address = _property(properties.get("Address")) or path.rsplit("dev_", 1)[-1].replace("_", ":")
        if not isinstance(address, str) or not re.fullmatch(_MAC, address):
            continue
        address = address.lower()
        if _property(properties.get("Connected")) is not True:
            continue
        connected.add(address)
        modalias = _property(properties.get("Modalias"))
        match = re.fullmatch(r"usb:v([0-9A-Fa-f]{4})p([0-9A-Fa-f]{4})d[0-9A-Fa-f]{4}", modalias or "") if isinstance(modalias, str) else None
        if not match or int(match[1], 16) != LOGITECH_VENDOR:
            continue
        product = int(match[2], 16)
        name = _property(properties.get("Name")) or _property(properties.get("Alias"))
        if not isinstance(name, str):
            name = ""
        battery_properties = interfaces.get("org.bluez.Battery1", {})
        battery = _property(battery_properties.get("Percentage")) if isinstance(battery_properties, dict) else None
        if type(battery) is not int or not 0 <= battery <= 100:
            battery = None
        devices[address] = _device(f"bluetooth:{address}", name, product, "Bluetooth", battery=battery)
    return devices, connected


def _read_bluez() -> Mapping | None:
    """Read only connected devices; cap the complete query at three seconds.

    busctl 255 cannot JSON-encode some nested GetManagedObjects values. Reading
    scalar properties avoids that limitation without a Python D-Bus dependency.
    Missing tools/service are optional: live Bluetooth HID nodes still work.
    """
    deadline = time.monotonic() + 3

    def run(arguments):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(arguments, 3)
        result = subprocess.run(arguments, capture_output=True, text=True,
                                timeout=min(remaining, 1), check=False)
        if result.returncode or len(result.stdout) > 1024 * 1024:
            return None
        return result.stdout

    try:
        connected = run(["bluetoothctl", "devices", "Connected"])
        if connected is None:
            return None
        addresses = {match[1].lower() for line in connected.splitlines()
                     if (match := re.fullmatch(rf"Device ({_MAC}) .+", line))}
        if not addresses:
            return {}
        tree = run(["busctl", "--system", "--timeout=1", "--list", "tree", "org.bluez"])
        if tree is None:
            return None
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        return None

    # Keep all confirmed connections even if a later property query exhausts
    # the deadline. Their live HID metadata can still provide model identity.
    objects = {}
    for path in tree.splitlines():
        if not _BLUEZ_PATH.fullmatch(path):
            continue
        address = path.rsplit("dev_", 1)[-1].replace("_", ":").lower()
        if address in addresses:
            objects[path] = {"org.bluez.Device1": {"Connected": True, "Address": address}}
    for path, interfaces in objects.items():
        if time.monotonic() >= deadline:
            break
        properties = interfaces["org.bluez.Device1"]
        try:
            output = run(["busctl", "--system", "--timeout=1", "--json=short", "get-property",
                          "org.bluez", path, "org.bluez.Device1", "Connected", "Modalias", "Alias"])
            if output is None:
                continue
            values = [json.loads(line)["data"] for line in output.splitlines()]
            if len(values) != 3:
                continue
            properties.update(zip(("Connected", "Modalias", "Alias"), values))
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
            continue
        if properties["Connected"] is True and isinstance(properties["Modalias"], str) and properties["Modalias"].lower().startswith("usb:v046d"):
            try:
                output = run(["busctl", "--system", "--timeout=1", "--json=short", "get-property",
                              "org.bluez", path, "org.bluez.Battery1", "Percentage"])
                if output:
                    interfaces["org.bluez.Battery1"] = {"Percentage": json.loads(output)["data"]}
            except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
                pass  # Battery reporting is optional; retain device identity.
    return objects


def discover_devices(*, usb_root: Path = Path("/sys/bus/usb/devices"),
                     hid_root: Path = Path("/sys/bus/hid/devices"),
                     bluez_objects: Mapping | None = None) -> list[Device]:
    """Return a deduplicated inventory; inject paths and BlueZ data in tests.

    ``connected=None`` means a receiver child is present but Linux has not
    reported its wireless link state. Only the MX Creative Keypad is configurable
    by this application. Multiple keypads remain separate inventory entries.
    """
    devices: dict[str, Device] = {}
    usb_nodes: dict[Path, Device] = {}
    for path in _entries(Path(usb_root)):
        vendor, product = _number(_read(path / "idVendor")), _number(_read(path / "idProduct"))
        if vendor != LOGITECH_VENDOR or product is None:
            continue
        identity = _read(path / "serial") or path.name
        device = _device(f"usb:{vendor:04x}:{product:04x}:{identity}", _read(path / "product"), product, "USB")
        devices[device.id] = device
        usb_nodes[path.resolve()] = device

    objects = _read_bluez() if bluez_objects is None else bluez_objects
    bluetooth, connected_bluetooth = _bluez_devices(objects or {})
    for path in _entries(Path(hid_root)):
        properties = dict(line.split("=", 1) for line in _read(path / "uevent").splitlines() if "=" in line)
        identity = properties.get("HID_ID", "").split(":")
        if len(identity) != 3:
            continue
        bus, vendor, product = (_number(value) for value in identity)
        if vendor != LOGITECH_VENDOR or product is None or bus not in (3, 5):
            continue
        name = properties.get("HID_NAME", "")
        unique = properties.get("HID_UNIQ", "").lower()
        if bus == 5:
            if not re.fullmatch(_MAC, unique) or (objects is not None and unique not in connected_bluetooth):
                continue
            # The HID node is sufficient vendor evidence if BlueZ lacks Modalias.
            identifier = f"bluetooth:{unique}"
            # Composite Bluetooth devices expose several HID interfaces with
            # one address. Keep the richer BlueZ record for every interface.
            devices.setdefault(identifier, bluetooth.get(unique) or _device(identifier, name, product, "Bluetooth"))
            continue
        parent = next((usb_nodes[ancestor] for ancestor in path.resolve().parents if ancestor in usb_nodes), None)
        if parent is not None and parent.kind != "receiver":
            continue  # Composite USB HID interfaces belong to one physical card.
        if parent is not None and (product == parent.product_id or "receiver" in name.casefold()):
            continue
        link = "USB receiver" if parent is not None else "USB"
        base = parent.id if parent is not None else "hid-usb"
        token = unique or properties.get("HID_PHYS", "").split("/input", 1)[0] or path.name.split(".", 1)[0]
        identifier = f"{base}:{vendor:04x}:{product:04x}:{token}"
        devices[identifier] = _device(identifier, name, product, link,
                                     connected=None if parent is not None else True,
                                     detail="Wireless connection state is not reported by the receiver." if parent is not None else "")
    devices.update({device.id: device for device in bluetooth.values()})
    # USB battery reporting, where available, is attached to physical devices.
    for path, device in usb_nodes.items():
        for supply in _entries(path / "power_supply"):
            capacity = _read(supply / "capacity")
            if capacity.isdigit() and 0 <= int(capacity) <= 100:
                devices[device.id] = replace(device, battery_percent=int(capacity))
                break
    return sorted(devices.values(), key=lambda device: (device.kind != "keypad", device.name.casefold(), device.id))
