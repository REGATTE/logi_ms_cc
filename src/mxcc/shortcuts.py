"""Shared shortcut spelling and validation for JSON profile files."""

from __future__ import annotations

import re

MODIFIERS = {
    "ctrl": "Ctrl", "control": "Ctrl", "shift": "Shift", "alt": "Alt",
    "super": "Super", "meta": "Super",
}
ALIASES = {
    "return": "Enter", "esc": "Escape", "spacebar": "Space",
    "printscreen": "Print", "pageup": "PageUp", "pagedown": "PageDown",
    "volumedown": "VolumeDown", "volumeup": "VolumeUp", "mute": "Mute",
    "playpause": "PlayPause", "nexttrack": "NextTrack", "prevtrack": "PrevTrack",
    "`": "Grave", "-": "Minus", "=": "Equal", "[": "BracketLeft",
    "]": "BracketRight", "\\": "Backslash", ";": "Semicolon",
    "'": "Apostrophe", ",": "Comma", ".": "Period", "/": "Slash",
}
KEYS = {
    "Enter", "Tab", "Space", "Escape", "Backspace", "Delete", "Insert",
    "Home", "End", "PageUp", "PageDown", "Up", "Down", "Left", "Right",
    "Print", "VolumeUp", "VolumeDown", "Mute", "PlayPause", "NextTrack",
    "PrevTrack", "Plus", "Minus", "Equal", "Grave", "BracketLeft",
    "BracketRight", "Backslash", "Semicolon", "Apostrophe", "Comma", "Period", "Slash",
}
KEY_LOOKUP = {key.lower(): key for key in KEYS} | ALIASES


def normalize_shortcut(value: str) -> str:
    """Return a canonical chord or raise ValueError for unsupported input.

    Only one ordinary key plus modifiers is supported. Sequences and arbitrary
    commands intentionally are not part of the profile format.
    """
    if not isinstance(value, str) or not value or len(value) > 100:
        raise ValueError("Shortcut must be a nonempty string of at most 100 characters")
    parts = [part.strip() for part in value.split("+")]
    if any(not part for part in parts):
        raise ValueError("Shortcut contains an empty key; spell the + key as Plus")
    modifiers: set[str] = set()
    keys: list[str] = []
    for part in parts:
        lowered = part.lower()
        if lowered in MODIFIERS:
            modifier = MODIFIERS[lowered]
            if modifier in modifiers:
                raise ValueError(f"Duplicate modifier: {modifier}")
            modifiers.add(modifier)
        elif re.fullmatch(r"[A-Za-z0-9]", part):
            keys.append(part.upper())
        elif re.fullmatch(r"[Ff](?:[1-9]|1[0-9]|2[0-4])", part):
            keys.append(part.upper())
        elif lowered in KEY_LOOKUP:
            keys.append(KEY_LOOKUP[lowered])
        else:
            raise ValueError(f"Unsupported key: {part}")
    if len(keys) != 1:
        raise ValueError("Shortcut must contain exactly one ordinary key")
    return "+".join([m for m in ("Ctrl", "Alt", "Shift", "Super") if m in modifiers] + keys)
