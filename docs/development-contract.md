# Implementation contract

The product is an Ubuntu MX Creative Console utility. A Rust worker owns HID,
assigned button capture, uinput, focus tracking, IPC and the tray. The PySide6
settings process exits when its window closes. Action Ring runs as a separate
on-demand Qt process and never constructs the settings window.

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
The keypad, Action Ring, dialpad and other devices use `CustomizationWorkspace`:
a flat preview stage with application scope across the top and a rounded
320-pixel action panel on the right. Shared mint selection styling follows
the system/light/dark palette. The keypad has horizontal Keys/Applications
navigation and a searchable action library; selecting an action does not save
until Apply. Its plus button selects the next empty key, or keeps the selected
key and explains replacement when full. The dialpad and other devices show
searchable controls and expandable connection facts in the same workspace.
Their assignments apply to all applications; per-app ring actions remain in
the ring editor. A compact control selector and device hotspots select the same
input. Empty-control and disconnected-device states use
the same surface without inventing available actions.
`ActionPicker` supplies Open application, Keyboard shortcut and System categories
to every mapping page. System offers Screenshot and Open Action Ring; the ring
editor omits recursive ring actions. Drafts and search do not save. Apply and
Remove update supported mappings; unsupported controls show disabled choices
with an availability explanation.
Multiple connected supported keypads block ambiguous configuration rather
than implying independent routing that the worker does not support.

Keypad navigation contains only Keys and Applications. Applications lists only
assigned available apps and Screenshot, deduplicated in keypad order. Rounded
cards show original icons, assigned key positions, and shortcut counts, with
profile selection, import, and editing in each app card. Screenshot remains a
system action without profile controls. Search filters by name or desktop ID;
empty layouts and searches without matches have distinct messages. Device
permission status, repair, and reconnect controls belong to the global Settings
page opened by the footer gear; authorization feedback is displayed there.

General inventory belongs to the GUI: read USB/HID sysfs metadata and bounded
connected-device Bluetooth metadata through existing system tools. Discovery does
not open input handles, start Bluetooth discovery or pair devices. Assigned
button backends are separate: XInput2 mouse grabs and scoped dialpad HID++.
The inventory scan runs asynchronously while settings is open; window closure
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
Supported button selectors persist model-scoped action assignments. Battery-only
refreshes retain selected controls, unapplied drafts and keyboard focus.
The existing functional keypad editor remains separate from these controls.

The home includes an Action Ring card. Its editor provides up to seven configured actions plus a separate add button,
original app artwork, drag reordering, a default layout and per-app overrides.
See `ACTION-RING.md` for the portable schema and input boundaries. The worker
captures foreground and immutable actions before opening the popup, validates
single-use tokens and restores the exact original window before shortcuts.
Ring focus is excluded from keypad automatic paging. Qt owns the popup click
grab; cancellation never replays the dismissal click into another application.

`device_interaction.py` overlays shaped, keyboard-accessible controls on verified
G402 and dialpad drawings. Hit regions use the same scale and letterboxing as
the artwork, with exact masks to avoid neighboring controls intercepting clicks.
Image clicks and the control selector synchronize one selected control.
Metadata-only updates preserve selection and focus; changing
devices or losing the selected capability clears selection. Unknown layouts
remain selectable in the list and never receive guessed physical hotspots.

Portable settings may contain `device_actions`, a list of target identifiers
(`vendor_id`, `product_id`, `control_id`) plus `label`, `icon` and `action`.
Actions use the shared declarative schema; no arbitrary command strings are
accepted. Compilation resolves installed applications and emits target identifiers
plus the runtime action. Legacy `action_ring.bindings` continue to open the ring;
duplicate targets across the two lists are rejected, with at most 256 combined.
Captured button events carry exact identity and resolve against current settings
at dispatch, ignoring paused or removed mappings. All direct actions use the same
focus and execution path as keypad keys. `device_actions_v1` is the compatibility
capability required by the new editor.

Opening or reactivating settings ensures the worker is running, reloads the
compiled runtime and selects Apps page 0. The worker is supervised by the
systemd user manager when available; source runs use a transient unit. Closing
settings does not stop the worker. Tray Quit / shutdown IPC stops it explicitly.

Login startup is an explicit per-account preference. `service.set_autostart`
atomically manages an XDG autostart desktop entry for the installed or source
launcher, replacing only entries marked as app-owned. It disables a legacy
enabled user-service login trigger without stopping the current worker and
restores the previous entry if migration fails. Saving this preference never
compiles device layouts or sends keypad IPC. Settings rolls setup back if saving
the preference fails and repairs an enabled entry when reopened.
The `startup` CLI command imports the installed worker's allowlisted desktop
environment into the user manager and calls `ensure_running`, without importing
Qt or changing the preference. New transient workers stop with the graphical
session. No relaunch or administrator authentication is required for login setup.

## Paths

- User configuration: `$XDG_CONFIG_HOME/mx-creative-control`, default `~/.config/mx-creative-control`.
- User profiles: `shortcuts/config/*.json` below that directory.
- Login entry: `$XDG_CONFIG_HOME/autostart/mx-creative-control.desktop`, default `~/.config/autostart/mx-creative-control.desktop`.
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
(key combination), `screenshot` (Print), `open_ring`, `none`. Empty tiles do nothing.
On X11, a launch action focuses an existing window matching its class candidates;
only when no match exists is its desktop entry launched with `gtk-launch`, without
a command shell. An assigned `profile_id` identifies the app's shortcut profile;
an absent/null value does not establish a launch-specific profile, so normal
focused-app matching still applies.

With `auto_switch` enabled on X11, the focused app's exact window class or desktop
identity selects its configured profile regardless of how it was opened. Apps
without a matching profile show Apps. Physical/page controls override automatic
paging until foreground changes; a manually selected profile stays pinned until
Follow active app clears it. Disabling automatic switching leaves pages manual.
Keypad launch actions still wait at most 12 seconds for confirmed foreground
before selecting their assigned profile; expiry discards that pending context
and resumes normal foreground matching. Manual page/profile selection cancels
the pending context. A URL/document launcher without its own profile retains
the confirmed active app's shortcuts until focus leaves. On Wayland, launch actions
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
automatic pages. The required `actions_ring_v2` capability additionally identifies
workers with the ring config/session protocol. Settings checks this before reusing a running worker. If it
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
Settings: `version:1`, `launchers` nine entries (desktop ID, `"@screenshot"`, `"@action-ring"`,
portable action entry, or null), `profile_assignments` map desktop ID to profile ID, `auto_switch`
boolean, `start_at_login` boolean, `show_labels` boolean.
The optional `theme` setting is `system` (default), `light`, or `dark`; legacy
version-1 settings without it normalize to `system`. Appearance belongs to the
Qt process and does not enter the Rust runtime or trigger keypad image uploads.

## Boundaries

Use original icons from installed desktop entries/theme. No downloaded branding
or invented app logos. Handle absent apps explicitly. Permissions are scoped
to keypad 046d:c354, Bluetooth dialpad 046d:bc00 and uinput; no reading ordinary keyboards. Root-owned
polkit helper has a fixed operation and accepts no arbitrary paths/commands.
X11 existing-window activation and automatic focus/page handling are supported;
on Wayland report these integrations unavailable accurately and retain app
launching and manual shortcut pages.

Minimum tests cover desktop-entry discovery, invalid/imported profiles,
runtime compilation, HID packet/event handling, shortcut parsing, IPC/control
state, and a GUI smoke test. Hardware tests require the actual keypad and
must not synthesize unrelated keystrokes without user interaction.
