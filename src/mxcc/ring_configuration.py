"""Portable Action Ring layouts and device bindings, with no executable commands."""

from __future__ import annotations

from copy import deepcopy

from .profiles import DESKTOP_PATTERN, ICON_PATTERN
from .shortcuts import normalize_shortcut

MAX_ACTIONS = 7


def default_ring(launchers=()) -> dict:
    slots = [{"label": identifier.removesuffix(".desktop"), "icon": "application-x-executable",
              "action": {"type": "launch", "desktop_id": identifier}}
             for identifier in dict.fromkeys(launchers)
             if isinstance(identifier, str) and DESKTOP_PATTERN.fullmatch(identifier)][:4]
    slots.extend([
        {"label": "Copy", "icon": "edit-copy", "action": {"type": "shortcut", "keys": "Ctrl+C"}},
        {"label": "Paste", "icon": "edit-paste", "action": {"type": "shortcut", "keys": "Ctrl+V"}},
        {"label": "Screenshot", "icon": "applets-screenshooter", "action": {"type": "screenshot"}},
    ])
    return {"layouts": {"default": slots}, "bindings": []}


def _text(value, name, maximum):
    if (not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > maximum
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError(f"{name} must be nonempty text of at most {maximum} UTF-8 bytes")
    return value.strip()


def validate_ring(value: object) -> dict:
    """Return an independent normalized layout; reject scripts and unknown fields."""
    if not isinstance(value, dict) or set(value) != {"layouts", "bindings"}:
        raise ValueError("Action Ring requires layouts and bindings")
    layouts = value["layouts"]
    if not isinstance(layouts, dict) or "default" not in layouts or len(layouts) > 257:
        raise ValueError("Action Ring needs a default layout and at most 256 application layouts")
    result = {"layouts": {}, "bindings": []}
    for identifier, slots in layouts.items():
        if identifier != "default" and (not isinstance(identifier, str) or not DESKTOP_PATTERN.fullmatch(identifier)):
            raise ValueError("Ring layout IDs must be default or desktop entry IDs")
        if not isinstance(slots, list) or len(slots) > MAX_ACTIONS:
            raise ValueError("Each Action Ring supports at most seven actions; the + button is separate")
        normalized = []
        for slot in slots:
            if slot is None:
                continue
            if not isinstance(slot, dict) or set(slot) != {"label", "icon", "action"}:
                raise ValueError("Ring slots require label, icon and action")
            label = _text(slot["label"], "Action label", 256)
            icon = slot["icon"]
            if not isinstance(icon, str) or not ICON_PATTERN.fullmatch(icon):
                raise ValueError("Ring icons must be icon theme names; app artwork is resolved locally")
            action = slot["action"]
            if not isinstance(action, dict):
                raise ValueError("Ring actions must be objects")
            kind = action.get("type")
            if kind == "launch" and set(action) == {"type", "desktop_id"}:
                desktop_id = action["desktop_id"]
                if not isinstance(desktop_id, str) or not DESKTOP_PATTERN.fullmatch(desktop_id):
                    raise ValueError("Ring launch actions require a desktop entry ID")
            elif kind == "shortcut" and set(action) == {"type", "keys"}:
                action = {"type": "shortcut", "keys": normalize_shortcut(action["keys"])}
            elif kind == "screenshot" and set(action) == {"type"}:
                pass
            else:
                raise ValueError("Ring actions must launch an app, send a shortcut or take a screenshot")
            normalized.append({"label": label, "icon": icon, "action": dict(action)})
        result["layouts"][identifier] = normalized
    bindings = value["bindings"]
    if not isinstance(bindings, list) or len(bindings) > 256:
        raise ValueError("Action Ring supports at most 256 device button assignments")
    seen = set()
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) != {"vendor_id", "product_id", "control_id"}:
            raise ValueError("Ring bindings require vendor_id, product_id and control_id")
        vendor, product, control = binding["vendor_id"], binding["product_id"], binding["control_id"]
        if type(vendor) is not int or vendor != 0x046d or type(product) is not int or not 1 <= product <= 0xffff:
            raise ValueError("Ring device bindings require a Logitech vendor and valid product ID")
        if not isinstance(control, str) or not control.startswith(("button:", "key:")):
            raise ValueError("Only device buttons can open the Action Ring")
        prefix, number = control.split(":", 1)
        if not number.isascii() or not number.isdigit() or str(int(number)) != number:
            raise ValueError("Invalid device button identifier")
        if (prefix == "button" and not 1 <= int(number) <= 32) or (prefix == "key" and not 0x110 <= int(number) <= 0x11f):
            raise ValueError("Unsupported device button identifier")
        if product == 0xc07e and control not in ("button:4", "button:5"):
            raise ValueError("G402 exposes only Back and Forward")
        if product == 0xbc00 and control not in ("button:1", "button:2", "button:3", "button:4"):
            raise ValueError("MX Dialpad exposes four ring buttons")
        key = (vendor, product, control)
        if key in seen:
            raise ValueError("Duplicate ring device binding")
        seen.add(key)
        result["bindings"].append(dict(binding))
    return result


def migrate_legacy_ring(value):
    """Read the earlier eight-slot schema without silently accepting new overflow.

    Validate every old action, including the one that cannot remain active.
    The settings writer backs up the original file before persisting migration.
    """
    if not isinstance(value, dict) or not isinstance(value.get("layouts"), dict):
        return value
    if not any(isinstance(slots, list) and len(slots) == 8 for slots in value["layouts"].values()):
        return value
    migrated = deepcopy(value)
    for identifier, slots in value["layouts"].items():
        if isinstance(slots, list) and len(slots) == 8:
            validated = []
            for offset in (0, 7):
                part = validate_ring({"layouts": {"default": slots[offset:offset + 7]}, "bindings": []})
                validated.extend(part["layouts"]["default"])
            migrated["layouts"][identifier] = validated[:MAX_ACTIONS]
    return migrated


def compile_ring(value, apps, selections) -> dict:
    """Resolve local app artwork and identities without baking paths into exports."""
    ring = validate_ring(value)
    indexed = {app.id: app for app in apps}

    def classes(app, identifier):
        values = list(dict.fromkeys(value.casefold() for value in
                                   ((app.wm_class if app else ""), identifier.removesuffix(".desktop")) if value))
        for name in values:
            _text(name, "Application window class", 255)
        return values

    layouts = []
    for identifier, slots in ring["layouts"].items():
        resolved = []
        for slot in deepcopy(slots):
            if slot and slot["action"]["type"] == "launch":
                desktop_id = slot["action"]["desktop_id"]
                app = indexed.get(desktop_id)
                if app is None:
                    continue
                slot["label"], slot["icon"] = app.name, app.icon
                profile = selections.get(desktop_id)
                slot["action"].update(wm_classes=classes(app, desktop_id),
                                      profile_id=profile["id"] if profile else None)
            resolved.append(slot)
        layouts.append({"id": identifier, "wm_classes": [] if identifier == "default" else classes(indexed.get(identifier), identifier),
                        "slots": resolved})
    return {"layouts": layouts, "bindings": ring["bindings"]}
