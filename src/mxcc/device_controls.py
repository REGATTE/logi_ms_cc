"""Describe controls without listening to input or changing device settings.

Linux decodes HID descriptors into each input node's sysfs capabilities. These
are advertised event capabilities, not a reliable count of physical controls:
both the MX Dialpad and G402 advertise sixteen mouse buttons. Verified layouts
therefore override generic capabilities for those models. Control IDs identify
UI slots only; they are not a promise that an action backend can remap them.

References:
https://www.kernel.org/doc/html/latest/input/event-codes.html
https://www.logitech.com/assets/51742/2/g402-hyperion-fury.pdf
https://www.logitech.com/assets/70190/mx_creative_console_-_web_qsg_ww.pdf
https://support.logi.com/hc/en-us/articles/28321445268247-Getting-Started-MX-Master-4
https://github.com/pwr-Solaar/Solaar/blob/master/lib/logitech_receiver/special_keys.py
https://github.com/torvalds/linux/blob/master/drivers/hid/hid-input.c
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import struct
from typing import Iterable


@dataclass(frozen=True)
class DeviceControl:
    id: str
    label: str
    kind: str
    source: str


def _model_controls(buttons, rotary_controls):
    return tuple(DeviceControl(f"button:{index}", name, "button", "model")
                 for index, name in enumerate(buttons, 1)) + tuple(
        DeviceControl(identifier, name, kind, "model") for identifier, name, kind in rotary_controls)


# Layout descriptions only. Do not infer firmware event codes from numbering.
MODEL_CONTROLS = {
    (0x046d, 0xbc00): _model_controls(
        ("Top-left button", "Top-middle button", "Bottom-left button", "Bottom-right button"),
        (("dial:main", "Dial", "dial"),
         ("wheel:roller", "Roller", "wheel"))),
    # G402 intentionally exposes only the requested side-button assignments.
    (0x046d, 0xc07e): (
        DeviceControl("button:4", "Back button", "button", "model"),
        DeviceControl("button:5", "Forward button", "button", "model")),
    # Logitech's Back/Forward defaults are HID buttons 4/5, which Linux maps
    # to BTN_SIDE/BTN_EXTRA (275/276). The connected B042 Bluetooth mouse also
    # reports its physical Gesture button as key:278 (XInput button 11). Mode
    # shift and the haptic panel have no verified ordinary mouse event mapping.
    (0x046d, 0xb042): (
        DeviceControl("key:272", "Left button", "button", "model"),
        DeviceControl("key:273", "Right button", "button", "model"),
        DeviceControl("key:274", "Wheel click", "button", "model"),
        DeviceControl("key:275", "Back button", "button", "model"),
        DeviceControl("key:276", "Forward button", "button", "model"),
        DeviceControl("key:278", "Gesture button", "button", "model"),
        DeviceControl("hidpp:196", "Wheel mode button", "button", "model"),
        DeviceControl("hidpp:416", "Haptic Sense Panel", "button", "model"),
        DeviceControl("rel:8", "Scroll wheel", "wheel", "model"),
        DeviceControl("rel:6", "Thumb wheel", "wheel", "model")),
}


_BUTTON_NAMES = {
    0x110: "Left button", 0x111: "Right button", 0x112: "Wheel click",
    0x113: "Side button", 0x114: "Extra button", 0x115: "Forward button",
    0x116: "Back button", 0x117: "Task button",
    0x14b: "Stylus button", 0x14c: "Second stylus button", 0x149: "Third stylus button",
    0x150: "Gear down", 0x151: "Gear up",
    0x220: "D-pad up", 0x221: "D-pad down", 0x222: "D-pad left", 0x223: "D-pad right",
}
_KEY_NAMES = {
    1: "Escape", 14: "Backspace", 15: "Tab", 28: "Enter", 29: "Left Ctrl",
    42: "Left Shift", 54: "Right Shift", 56: "Left Alt", 57: "Space",
    58: "Caps Lock", 69: "Num Lock", 70: "Scroll Lock", 97: "Right Ctrl",
    100: "Right Alt", 102: "Home", 103: "Up", 104: "Page Up", 105: "Left",
    106: "Right", 107: "End", 108: "Down", 109: "Page Down", 110: "Insert",
    111: "Delete", 113: "Mute", 114: "Volume down", 115: "Volume up",
    116: "Power", 119: "Pause", 125: "Left Super", 126: "Right Super",
    127: "Menu", 142: "Sleep", 143: "Wake", 163: "Next track",
    164: "Play / pause", 165: "Previous track", 166: "Stop playback",
    167: "Record", 172: "Browser home", 173: "Refresh", 212: "Camera",
    224: "Brightness down", 225: "Brightness up",
}
_KEY_NAMES.update({code: name for code, name in zip(range(2, 14), "1234567890-=")})
_KEY_NAMES.update({code: name for code, name in zip(range(16, 28), "QWERTYUIOP[]")})
_KEY_NAMES.update({code: name for code, name in zip(range(30, 42), "ASDFGHJKL;'`")})
_KEY_NAMES.update({code: name for code, name in zip(range(44, 54), "ZXCVBNM,./")})
_KEY_NAMES.update({code: f"F{index}" for index, code in enumerate((*range(59, 69), 87, 88, *range(183, 195)), 1)})


def _read(path: Path) -> str:
    try:
        with path.open(encoding="ascii", errors="strict") as stream:
            value = stream.read(4097)
        return value if len(value) <= 4096 else ""
    except (OSError, UnicodeError):
        return ""


def _bitmap(value: str, *, limit: int, word_bits: int) -> set[int]:
    """Decode kernel bitmap_print_to_pagebuf's most-significant-word first text."""
    words = value.split()
    if not words or len(words) > (limit + word_bits - 1) // word_bits:
        return set()
    if any(not re.fullmatch(r"[0-9a-fA-F]{1," + str(word_bits // 4) + "}", word) for word in words):
        return set()
    result = set()
    for index, word in enumerate(reversed(words)):
        bits = int(word, 16)
        result.update(index * word_bits + bit for bit in range(word_bits)
                      if bits & (1 << bit) and index * word_bits + bit < limit)
    return result


def _is_button(code: int) -> bool:
    # Touch/tool-presence events are not physical buttons. Include Linux's
    # dedicated button ranges, gamepad d-pad and trigger-happy button bank.
    return (0x100 <= code < 0x140 or code in (0x149, 0x14b, 0x14c, 0x150, 0x151)
            or 0x220 <= code <= 0x223 or 0x2c0 <= code <= 0x2e7)


def controls_for_device(*, vendor_id: int, product_id: int, kind: str,
                        input_paths: Iterable[Path] = (), word_bits: int | None = None) -> tuple[DeviceControl, ...]:
    """Read already-associated inputN directories; never enumerate input devices.

    Known physical layouts take precedence over broad HID advertisements. For
    other devices, controls carry ``source='linux'`` to distinguish reported
    capabilities from verified physical controls. Pointer motion, touch/tool
    presence and synthesized keyboard capabilities of pointing devices are
    excluded. Missing or unreadable capability files simply contribute nothing.
    ``word_bits`` is injectable for 32-bit and 64-bit sysfs fixtures.
    """
    known = MODEL_CONTROLS.get((vendor_id, product_id))
    if known is not None and (vendor_id, product_id) != (0x046d, 0xb042):
        return known
    if (vendor_id, product_id) == (0x046d, 0xc354):
        return ()  # Existing keypad editor owns its controls and mappings.
    word_bits = word_bits or struct.calcsize("L") * 8
    if word_bits not in (32, 64):
        raise ValueError("sysfs bitmap word size must be 32 or 64 bits")
    keys, axes = set(), set()
    for directory in dict.fromkeys(map(Path, input_paths)):
        keys.update(_bitmap(_read(directory / "capabilities/key"), limit=0x300, word_bits=word_bits))
        axes.update(_bitmap(_read(directory / "capabilities/rel"), limit=0x10, word_bits=word_bits))
    if known is not None:
        # Keep existing generic mappings addressable without claiming that the
        # descriptor's unused/extra button usages are named physical controls.
        additional = tuple(DeviceControl(f"key:{code}", f"Additional reported button {code - 271}",
                                         "button", "linux")
                           for code in (277,) if code in keys)
        return known + additional
    controls = []
    for code in sorted(keys - {0}):
        if _is_button(code):
            label = _BUTTON_NAMES.get(code, f"Button {code - 0x110 + 1}" if 0x110 <= code < 0x120 else f"Button 0x{code:03X}")
            controls.append(DeviceControl(f"key:{code}", label, "button", "linux"))
        elif code < 0x100 or 0x160 <= code < 0x220 or 0x224 <= code < 0x2c0 or code >= 0x2e8:
            if kind not in ("mouse", "dialpad", "receiver"):
                controls.append(DeviceControl(f"key:{code}", _KEY_NAMES.get(code, f"Key {code}"), "key", "linux"))
    # One slot per rotary control; a future action defines its direction semantics.
    # High-resolution and legacy reports describe the same physical wheel.
    for aliases, canonical, kind, label in (
        ({8, 11}, 8, "wheel", "Scroll wheel"),
        ({6, 12}, 6, "wheel", "Horizontal scroll wheel"),
        ({7}, 7, "dial", "Dial"),
    ):
        if axes & aliases:
            controls.append(DeviceControl(f"rel:{canonical}", label, kind, "linux"))
    return tuple(controls)
