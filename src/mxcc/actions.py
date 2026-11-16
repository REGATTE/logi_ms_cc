"""Shared portable actions for keys and supported device buttons."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy

from .shortcuts import normalize_shortcut


ENTRY_FIELDS = {"label", "icon", "action"}
TARGET_FIELDS = {"vendor_id", "product_id", "control_id"}


def validate_action_entry(value, *, allow_ring=True):
    """Normalize declarative actions; executable commands and paths are rejected."""
    from .profiles import DESKTOP_PATTERN, ICON_PATTERN
    if not isinstance(value, dict) or set(value) != ENTRY_FIELDS:
        raise ValueError("Actions require label, icon and action")
    label, icon, action = value["label"], value["icon"], value["action"]
    if (not isinstance(label, str) or not label.strip() or len(label.encode("utf-8")) > 256
            or any(ord(char) < 32 or ord(char) == 127 for char in label)):
        raise ValueError("Action label must be nonempty text of at most 256 UTF-8 bytes")
    if not isinstance(icon, str) or not ICON_PATTERN.fullmatch(icon):
        raise ValueError("Action icons must be icon theme names, without a path")
    if not isinstance(action, dict):
        raise ValueError("Action must be an object")
    kind = action.get("type")
    if kind == "launch" and set(action) == {"type", "desktop_id"}:
        identifier = action["desktop_id"]
        if not isinstance(identifier, str) or not DESKTOP_PATTERN.fullmatch(identifier):
            raise ValueError("Application actions require a desktop entry ID")
    elif kind == "shortcut" and set(action) == {"type", "keys"}:
        action = {"type": "shortcut", "keys": normalize_shortcut(action["keys"])}
    elif kind == "screenshot" and set(action) == {"type"}:
        pass
    elif kind == "open_ring" and allow_ring and set(action) == {"type"}:
        pass
    else:
        raise ValueError("Choose an application, keyboard shortcut or supported system action")
    return {"label": label.strip(), "icon": icon, "action": dict(action)}


def _applications(apps):
    return apps if isinstance(apps, Mapping) else {app.id: app for app in apps}


def launcher_entry(value, apps=()):
    """Present old launcher strings and new custom entries through one picker."""
    from .profiles import DESKTOP_PATTERN, ICON_PATTERN
    if value is None:
        return None
    if isinstance(value, dict):
        return validate_action_entry(value)
    if value == "@screenshot":
        return {"label": "Screenshot", "icon": "applets-screenshooter", "action": {"type": "screenshot"}}
    if value == "@action-ring":
        return {"label": "Action Ring", "icon": "view-grid", "action": {"type": "open_ring"}}
    if not isinstance(value, str) or not DESKTOP_PATTERN.fullmatch(value):
        raise ValueError("Launcher slots must be application IDs, actions or null")
    app = _applications(apps).get(value)
    return {"label": app.name if app else value.removesuffix(".desktop"),
            "icon": app.icon if app and ICON_PATTERN.fullmatch(app.icon) else "application-x-executable",
            "action": {"type": "launch", "desktop_id": value}}


def launcher_available(value, apps=()):
    apps = _applications(apps)
    entry = launcher_entry(value, apps)
    return entry is not None and (entry["action"]["type"] != "launch"
                                  or entry["action"]["desktop_id"] in _applications(apps))


def compile_entry(value, apps, selections):
    """Resolve local app identities/artwork, omitting an unavailable application."""
    entry = validate_action_entry(value)
    if entry["action"]["type"] != "launch":
        return entry
    identifier = entry["action"]["desktop_id"]
    app = _applications(apps).get(identifier)
    if app is None:
        return None
    classes = list(dict.fromkeys(value.casefold() for value in
                                (app.wm_class, app.id.removesuffix(".desktop")) if value))
    if any(len(value.encode("utf-8")) > 255 or any(ord(char) < 32 or ord(char) == 127 for char in value)
           for value in classes):
        raise ValueError("Application window class is invalid")
    profile = selections.get(identifier)
    entry.update(label=app.name, icon=app.icon)
    entry["action"].update(wm_classes=classes, profile_id=profile["id"] if profile else None)
    return entry


def profile_entry_action(entry):
    """Convert a validated profile row to the action picker's portable format."""
    return validate_action_entry({"label": entry["label"], "icon": entry.get("icon", "input-keyboard"),
                                  "action": deepcopy(entry["action"]) if "action" in entry else
                                  {"type": "shortcut", "keys": entry["keys"]}})


def supported_device_target(vendor_id, product_id, control_id):
    if type(vendor_id) is not int or vendor_id != 0x046d or type(product_id) is not int or not 1 <= product_id <= 0xffff:
        return False
    if product_id == 0xc07e:
        return control_id in ("button:4", "button:5")
    if product_id == 0xbc00:
        return control_id in ("button:1", "button:2", "button:3", "button:4")
    if product_id == 0xb042 and control_id == "hidpp:416":
        return True
    return control_id in tuple(f"key:{code}" for code in range(272, 279))


def validate_device_actions(values, legacy_bindings=()):
    if not isinstance(values, list) or len(values) + len(legacy_bindings) > 256:
        raise ValueError("At most 256 device button assignments are supported")
    seen = {(entry["vendor_id"], entry["product_id"], entry["control_id"]) for entry in legacy_bindings}
    result = []
    for item in values:
        if not isinstance(item, dict) or set(item) != TARGET_FIELDS | ENTRY_FIELDS:
            raise ValueError("Device assignments require vendor_id, product_id, control_id, label, icon and action")
        target = tuple(item[name] for name in ("vendor_id", "product_id", "control_id"))
        if not supported_device_target(*target):
            raise ValueError("This device control does not support action assignments")
        if target in seen:
            raise ValueError("Duplicate device button assignment")
        seen.add(target)
        result.append({**{name: item[name] for name in TARGET_FIELDS},
                       **validate_action_entry({name: item[name] for name in ENTRY_FIELDS})})
    return result
