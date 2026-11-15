# Shortcut profiles

The app discovers installed desktop entries and uses their original icons. A
launcher tile opens that installed app. A shortcut profile is a separate JSON
description of up to nine actions for that app; it never contains a
script or shell command.

Bundled profiles live in the repository's `shortcuts/config/` folder. Installation
places them in `/usr/share/mx-creative-control/shortcuts/config/`. Importing a JSON
file through settings copies a validated version into:

```text
~/.config/mx-creative-control/shortcuts/config/
```

This follows `$XDG_CONFIG_HOME` when set. Imported profiles override a bundled
profile with the same ID, so changes survive application upgrades. Assign a
profile from the shortcut dropdown for the selected app; apps without a bundled
profile can use any imported one. Settings saves assignments and imports as you
apply them. Malformed files are rejected with an explanation.

For a manual shortcut page, choose a profile in the shortcut preview and press
**Use preview on keypad**. This is also available on Wayland, where automatic
window focus switching is unavailable.

## Example

Save this as `my-editor.json` and import it:

```json
{
  "version": 1,
  "id": "my-editor",
  "name": "My editor",
  "desktop_ids": ["my-editor.desktop"],
  "wm_classes": ["my-editor"],
  "shortcuts": [
    {"slot": 1, "label": "Save", "keys": "Ctrl+S", "icon": "document-save"},
    {"slot": 2, "label": "Find", "keys": "Ctrl+F", "icon": "edit-find"},
    {"slot": 3, "label": "Close tab", "keys": "Ctrl+W", "icon": "window-close"}
  ]
}
```

Slots run left-to-right, top-to-bottom from 1 to 9. Unspecified slots are empty.
The profile ID uses lowercase letters, digits, `_` or `-`, starts with a letter
or digit, and is at most 64 characters. Slot numbers must be unique. The version
must be `1`.

`desktop_ids` matches the filenames used by installed `.desktop` launchers.
`wm_classes` matches the app window class for X11 focus switching; this can
differ from the executable name or app title. A profile may be assigned manually
when its desktop ID is not listed. On X11, inspect a window's class with
`xprop WM_CLASS` if needed. A browser PWA and its parent browser can share a
window class, so automatic focus may not distinguish them; select their shortcut
page manually in that case.

`icon` is a local icon-theme name such as `document-save`, not an image download
or executable. App launcher tiles always resolve their image from the installed
app's desktop entry. Missing theme icons use a neutral fallback.

## Supported keys

The Keys page uses the same Application, Keyboard shortcut and System picker
for individual profile keys. Existing keyboard entries keep the `keys` form
shown above. Application and system entries use `action` instead of `keys`:

```json
{"slot": 4, "label": "Firefox", "icon": "firefox", "action": {"type": "launch", "desktop_id": "firefox.desktop"}}
```

Other supported action types are `screenshot`, `open_ring`, and `shortcut`
(with `keys`). An entry must use either the legacy `keys` field or an `action`
object, never both. The bulk shortcut dialog preserves non-keyboard entries;
edit those through the selected key's action picker. Application artwork is
resolved from the installed application on each computer.

Each shortcut is one key plus optional modifiers separated by `+`:

- Modifiers: `Ctrl`, `Alt`, `Shift`, `Super` (`Control` and `Meta` are aliases).
- Letters and digits: `A`–`Z`, `0`–`9`; function keys: `F1`–`F24`.
- Editing: `Enter`, `Tab`, `Space`, `Escape`, `Backspace`, `Delete`, `Insert`.
- Navigation: `Home`, `End`, `PageUp`, `PageDown`, `Up`, `Down`, `Left`, `Right`.
- Punctuation: `Plus`, `Minus`, `Equal`, `Grave`, `BracketLeft`, `BracketRight`,
  `Backslash`, `Semicolon`, `Apostrophe`, `Comma`, `Period`, `Slash`.
- System/media: `Print`, `VolumeUp`, `VolumeDown`, `Mute`, `PlayPause`,
  `NextTrack`, `PrevTrack`.

Spell the plus key `Plus`, for example `Ctrl+Plus`. A sequence of chords, raw
shell commands, application downloads and arbitrary Python are intentionally
not part of this format. Adjust the shortcuts to the application's configured
bindings. Profile imports validate their structure but cannot verify that a
particular app uses those bindings.
