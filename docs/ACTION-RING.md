# Action Ring

The ring is a separate on-demand Qt popup. The Rust worker owns the selected
layout, original foreground window, and a single-use session token. The popup
can select one of up to seven stored actions; it cannot submit arbitrary commands.
Only app launch/focus, keyboard shortcuts and Screenshot are supported.

## Configuration

Settings version 1 has an optional `action_ring` field. Old settings migrate to
a default ring using up to four existing launcher apps plus Copy, Paste and
Screenshot, without assigning any hardware triggers. Layouts contain zero to
seven configured actions. Nulls are removed, and unavailable apps are omitted
from the runtime popup. Visible actions are evenly spaced with a separate **+**
button. The popup is 280 pixels across, with 50-pixel buttons and 30-pixel icons.

The **+** button always opens the matching layout in settings, reusing an open
window. At seven actions it opens editing controls instead of creating an eighth
action. With fewer actions it opens the action picker. Opening through **+** does
not force a worker reload or select a keypad page.

Existing eight-slot settings migrate on load, retaining the first seven nonempty
actions. Before the first save, the original settings are backed up beside the
settings file as `settings.before-seven-action-ring-HASH.json`. A failed backup
prevents the save. New configurations and imports reject more than seven entries.

```json
{
  "layouts": {
    "default": [
      {"label":"Copy","icon":"edit-copy","action":{"type":"shortcut","keys":"Ctrl+C"}},
      {"label":"Firefox","icon":"firefox","action":{"type":"launch","desktop_id":"firefox.desktop"}},
      {"label":"Screenshot","icon":"applets-screenshooter","action":{"type":"screenshot"}}
    ]
  },
  "bindings": [
    {"vendor_id":1133,"product_id":49278,"control_id":"button:4"}
  ]
}
```

Add a desktop ID such as `code.desktop` alongside `default` for an app override.
Default is used when no app-specific layout matches. App artwork and window
classes are resolved locally from desktop entries. Missing apps retain their
portable settings but are hidden from the popup on that computer. Shortcut
icons use theme names. Scripts, shell commands and nested ring actions are
rejected. Shortcuts use the same chord syntax and physical US key codes as
[keypad profiles](PROFILES.md).

The full configuration export includes ring layouts and model bindings. The
ring editor's dedicated JSON export contains only versioned layouts; importing
it preserves local device assignments. Both validate before replacing data.

## Runtime behavior

- Keypad launchers use `@action-ring`, compiled to `{"type":"open_ring"}`.
- G402 Back and Forward use XInput2 passive grabs on the matched physical device.
  Capture includes the release, suppresses the original navigation action and
  leaves other buttons alone. Other models use verified X11 button labels where
  available. Hotplug refreshes grabs; pause and shutdown release them.
- Bluetooth MX Dialpad buttons use HID++ `REPROG_CONTROLS_V4` temporary diversion
  on assigned controls only. Normal shutdown, pause and unassignment restore the
  previous diversion flags. Persistent mappings and wheel input are unchanged.
  A forced kill or unplug during diversion can require a power cycle to reset
  temporary flags. The permission helper targets this model only.
- A ring layout and original foreground are captured before launching Qt. App
  identity matching happens once, so popup focus cannot change the layout.
- Executing a shortcut restores and verifies that exact original window and
  its identity. If it disappeared or activation fails, no shortcut is emitted.
  Launch actions reuse the existing focus-or-launch behavior.
- Cancelling runs no action. Sessions expire after two minutes. Old tokens
  cannot execute against a new ring. Qt exits after use; the worker reaps and
  supervises the child. The settings window need not be open.

IPC adds `ring_toggle`, `ring_context`, `ring_execute` (token and zero-based
slot), `ring_cancel` (token) and `ring_configure` (token). The latter opens
`configure --ring-layout ID --add-ring-action` using the captured layout, never
an arbitrary UI-supplied command. Status reports `actions_ring_v2`,
`ring_supported`, `ring_open`, `ring_error`, `device_buttons` and
`dialpad_buttons`. Device statuses expose active bindings and errors, including
missing dialpad permissions. Unix socket permissions remain private to the user.

The target is Ubuntu on Xorg. Wayland activation/input integration and
continuous ring adjustments are deferred. Bindings identify models rather than
individual serial numbers; identical models share assignments.

For isolated desktop diagnostics, `mxcc-worker --no-keypad --no-tray` avoids
opening the physical keypad. Use a separate config/socket and empty bindings
when testing synthetic windows; this leaves the live device controller alone.

Protocol and UI references: [XInput2 specification](https://www.x.org/releases/current/doc/inputproto/XI2proto.txt),
[Qt popup window behavior](https://doc.qt.io/qt-6/qt.html#WindowType-enum), and
[Logitech Actions Ring](https://support.logi.com/hc/en-001/articles/17844647906967-Actions-Ring).
