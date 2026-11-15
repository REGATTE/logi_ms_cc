# Implementation contract

The product is an Ubuntu MX Creative Keypad utility. Other Logitech devices have
discovered control-mapping placeholders; executing their assignments is not yet
implemented. A Rust worker owns HID, uinput, focus tracking,
IPC and the tray. The PySide6 settings process exits when its window closes.

The initial Qt view is a Logitech-only device home screen. The keypad and dialpad
appear under one MX Creative Console card, which opens a side-by-side component
view with clickable component images. Selecting one
connected supported keypad opens its Keys editor, with app icons above the key
preview and a rounded inspector on the right. The inspector has no key-number
heading or profile dropdown. Its application picker includes all installed apps
and Screenshot; Apply preserves the app's existing shortcut assignment. Apply,
Import, Edit, Use shortcuts on keypad, and Follow active app actions sit at the
bottom. App icons select their assigned shortcut views. Back controls return to
the console and home. A shared footer outside
the page stack keeps Refresh and Settings icons at the bottom left on every
page. Settings opens global preferences and returns to the originating page.
The dialpad and other devices open their own controls page with connection facts
and intentionally empty assignment dropdowns. Multiple connected supported keypads block ambiguous configuration rather
than implying independent routing that the worker does not support.

Keypad navigation contains only Keys and Applications. Applications lists only
assigned available apps and Screenshot, deduplicated in keypad order. Rounded
cards show original icons, assigned key positions, and shortcut counts, with
profile selection, import, and editing in each app card. Screenshot remains a
system action without profile controls. Search filters by name or desktop ID;
empty layouts and searches without matches have distinct messages. Device
permission status, repair, and reconnect controls belong to the global Settings
page opened by the footer gear; authorization feedback is displayed there.

Inventory belongs only to the GUI: read USB/HID sysfs metadata and bounded
connected-device Bluetooth metadata through existing system tools. Do not open
device input handles, start Bluetooth discovery, pair devices, or add worker
polling. The scan runs asynchronously while settings is open; window closure
stops refreshes. Device illustrations are original Qt drawing code, with external
visual references recorded in `docs/UI-REFERENCES.md` and no bundled screenshots.
Each device has its own status dot: connected green, disconnected red, searching
blinking yellow, unconfirmed steady yellow. On both the home screen and Console
component selector, show a reported battery percentage beside the dot beneath
its device; omit the percentage when battery data is absent.
Confirmed devices stay green during
background refreshes; explicit Refresh searches all indicators. Blink timers run
only while the indicator is visible and searching. The GUI retains devices seen
during its current session as disconnected when absent from a successful scan.

`device_controls.py` describes controls as immutable IDs, labels, kinds and
sources. `devices.py` associates inputN capability directories with exact USB
parents, receiver child identities or Bluetooth addresses, then merges their
button/key/relative wheel capabilities without opening `/dev/input` handles.
Duplicate interfaces and high-resolution wheel aliases yield one control slot.
Each physical wheel/dial has one assignment slot, not separate direction slots;
the future action backend will interpret the signed movement for that action.
Pointer motion, touch-presence events and pointing-device keyboard emulation
are excluded. Explicit `(vendor_id, product_id)` entries in `MODEL_CONTROLS`
override overly broad advertisements. The MX Dialpad uses its documented layout;
G402 exposes only Back and Forward, retaining IDs `button:4` and `button:5`.
Unknown models use Linux-reported controls and an honest availability note; no controls
are invented if metadata is absent. IDs describe UI slots, not firmware codes.
The UI keeps empty, enabled selectors with no callbacks that persist or execute
assignments. Battery-only refreshes retain those widgets and keyboard focus.
The existing functional keypad editor is separate from these placeholders.

`device_interaction.py` overlays shaped, keyboard-accessible controls on verified
G402 and dialpad drawings. Hit regions use the same scale and letterboxing as
the artwork, with exact masks to avoid neighboring controls intercepting clicks.
Image clicks and row/combo focus synchronize one selected control and scroll
its row into view. Metadata-only updates preserve selection and focus; changing
devices or losing the selected capability clears selection. Unknown layouts
remain selectable in the list and never receive guessed physical hotspots.

Opening or reactivating settings ensures the worker is running, reloads the
compiled runtime and selects Apps page 0. The worker is supervised by the
systemd user manager when available; source runs use a transient unit. Closing
settings does not stop the worker. Tray Quit / shutdown IPC stops it explicitly.

## Paths

- User configuration: `$XDG_CONFIG_HOME/mx-creative-control`, default `~/.config/mx-creative-control`.
- User profiles: `shortcuts/config/*.json` below that directory.
- Bundled profiles: repository `shortcuts/config/*.json`.
- Cached JPEG faces: `$XDG_CACHE_HOME/mx-creative-control/keys`, default `~/.cache/mx-creative-control/keys`.
- IPC: `$XDG_RUNTIME_DIR/mx-creative-control/control.sock`.
- Source layout: `worker/` Rust crate; `src/mxcc/` Python package; `tests/`; `packaging/`; `scripts/`.

## Shortcut profile format

```json
{"version":1,"id":"vscode","name":"VS Code","desktop_ids":["code.desktop"],"wm_classes":["code"],"shortcuts":[{"slot":1,"label":"Commands","keys":"Ctrl+Shift+P","icon":"system-run"}]}
```

Exactly one schema version, safe profile ID `[a-z0-9][a-z0-9_-]{0,63}`,
unique slots 1–9, at most nine shortcuts, supported key names/modifiers.
Profiles contain shortcuts, labels and icon names only, never executable code.
Profiles with no match can be assigned manually to a desktop app in settings.

## Runtime configuration consumed by Rust

```json
{
  "version":1,
  "auto_switch":true,
  "pages":[
    {"name":"Apps","keys":[{"label":"VS Code","action":{"type":"launch","desktop_id":"code.desktop","wm_classes":["code"],"profile_id":"vscode"},"image":"HASH.jpg"},null,null,null,null,null,null,null,null]},
    {"name":"Shortcuts","keys":[{"label":"Copy","action":{"type":"shortcut","keys":"Ctrl+C"},"image":"HASH.jpg"},null,null,null,null,null,null,null,null]}
  ],
  "profiles":[{"id":"vscode","wm_classes":["code"],"page":{"name":"VS Code","keys":[null,null,null,null,null,null,null,null,null]}}]
}
```

Image names are simple filenames relative to the keys cache; no paths/traversal.
Pages have exactly nine optional keys. Supported action types: `launch`
(desktop entry ID, `wm_classes` array and optional `profile_id`), `shortcut`
(key combination), `screenshot` (Print), `none`. Empty tiles do nothing.
On X11, a launch action focuses an existing window matching its class candidates;
only when no match exists is its desktop entry launched with `gtk-launch`, without
a command shell. An assigned `profile_id` identifies the app's shortcut profile;
an absent/null value means no automatic shortcut page for that key.

With `auto_switch` enabled, pressing an app key arms automatic paging for that
keypad-activated app: show its assigned shortcuts once it is active, keep them
while it stays active, and return to Apps when focus leaves. Apps without a
profile stay on Apps. Explicit physical/page controls and manual profile choices
override automatic page selection. With `auto_switch` disabled, app activation
still works but page selection remains manual. Ordinary focus changes do not
initiate this keypad-activation flow by themselves. Activation waits at most
12 seconds for confirmed foreground; expiry discards the pending context.
Manual page/profile selection cancels that context, so later focus changes do
not re-arm it. On Wayland, launch actions
launch the desktop entry; existing-window activation and automatic paging are
unavailable. The keypad's two physical navigation buttons remain usable.

The Apps page packs available desktop apps and Screenshot left-to-right,
top-to-bottom with trailing empty keys only. Launcher normalization preserves
available entries in relative order, then missing desktop IDs in relative
order, then nulls; missing apps remain saved but do not create visible gaps.
Shortcut pages retain their explicit slot positions and are never compacted.
Native Apps-key dragging inserts the source at the destination, shifting other
visible entries rather than swapping them; dropping onto a blank key appends.
Each accepted reorder saves settings, compiles the new runtime, and reloads
the worker immediately. Shortcut-page keys cannot be reordered by this gesture.

## IPC

One JSON request line and one JSON response line per Unix stream connection.
Requests: `{"command":"status"}`, `{"command":"reload"}`,
`{"command":"pause","value":true}`, `{"command":"page","index":1}`,
`{"command":"shutdown"}`. Responses contain `ok` plus optional `error`.
Status additionally contains `connected`, `keypad_access`, `input_access`,
`session` (`x11`/`wayland`), `focus_supported`, `active_app`, `page`, `paused`,
`version` and `capabilities`. The `launcher_activation_v1` capability identifies
workers that accept launch activation metadata and manage the corresponding
automatic pages. Settings checks this before reusing a running worker. If it
is absent, settings verifies the replacement binary's version before requesting
a normal shutdown and starting the compatible worker.
Socket directory is user-owned mode 0700, socket mode 0600. Limit requests.
Worker CLI supports `--config PATH --cache PATH --socket PATH --gui-command PATH`
and `--no-tray` for test/headless use; defaults follow paths above.
`--check-config PATH` validates runtime config and exits without hardware.

## Frontend public Python API

`mxcc.paths`: `config_dir()`, `cache_dir()` (returns keys directory),
`profiles_dir()`, `runtime_path()`, `settings_path()`, `socket_path()`,
`bundled_profiles_dir()`.

`mxcc.applications`: `Application` dataclass with `id`, `name`, `icon`,
`exec_line`, `wm_class`, `path`; `discover_applications()` returns sorted apps.

`mxcc.devices`: `Device` dataclass describes identity, name, kind, connection,
connected/supported status, USB vendor/product IDs and optional battery/detail
fields; `discover_devices()` returns the connected Logitech inventory.
`mxcc.device_views` renders the home cards and original product illustrations;
`mxcc.device_navigation` contains the asynchronous scanner and details view.
`MainWindow(..., devices=inventory)` injects deterministic inventory for tests.

`mxcc.profiles`: `ProfileError`, `load_profiles()` -> dict ID to profile dict,
`validate_profile(data)` -> validated dict, `import_profile(path)` -> profile
dict (atomic copy into user profile directory, reject malformed input).

`mxcc.configuration`: `load_settings()` -> dict,
`save_settings(settings)` atomic, `default_settings(apps)` -> dict,
`compile_runtime(settings, apps, profiles, render_face)` -> runtime dict.
`compact_launchers(launchers, apps)` -> nine entries with available launchers,
then retained missing IDs, then nulls; it does not modify the input list.
`render_face(label, icon_name, app_or_none)` callback returns JPEG filename;
frontend provides Qt icon rendering, compiler does not import Qt.
Settings: `version:1`, `launchers` nine entries (desktop ID, `"@screenshot"`,
or null), `profile_assignments` map desktop ID to profile ID, `auto_switch`
boolean, `start_at_login` boolean, `show_labels` boolean.
The optional `theme` setting is `system` (default), `light`, or `dark`; legacy
version-1 settings without it normalize to `system`. Appearance belongs to the
Qt process and does not enter the Rust runtime or trigger keypad image uploads.

## Boundaries

Use original icons from installed desktop entries/theme. No downloaded branding
or invented app logos. Handle absent apps explicitly. Permissions are scoped
to keypad 046d:c354 and uinput; no reading ordinary keyboards. Root-owned
polkit helper has a fixed operation and accepts no arbitrary paths/commands.
X11 existing-window activation and automatic focus/page handling are supported;
on Wayland report these integrations unavailable accurately and retain app
launching and manual shortcut pages.

Minimum tests cover desktop-entry discovery, invalid/imported profiles,
runtime compilation, HID packet/event handling, shortcut parsing, IPC/control
state, and a GUI smoke test. Hardware tests require the actual keypad and
must not synthesize unrelated keystrokes without user interaction.
