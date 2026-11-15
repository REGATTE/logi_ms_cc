# Implementation contract

The product is an Ubuntu MX Creative Keypad utility. Dialpad functionality is
out of scope. A Rust worker owns HID, uinput, focus tracking, IPC and the tray.
The PySide6 settings process exits when its window closes.

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
    {"name":"Apps","keys":[{"label":"Firefox","action":{"type":"launch","desktop_id":"firefox.desktop"},"image":"HASH.jpg"},null,null,null,null,null,null,null,null]},
    {"name":"Shortcuts","keys":[{"label":"Copy","action":{"type":"shortcut","keys":"Ctrl+C"},"image":"HASH.jpg"},null,null,null,null,null,null,null,null]}
  ],
  "profiles":[{"id":"vscode","wm_classes":["code"],"page":{"name":"VS Code","keys":[null,null,null,null,null,null,null,null,null]}}]
}
```

Image names are simple filenames relative to the keys cache; no paths/traversal.
Pages have exactly nine optional keys. Supported action types: `launch`
(desktop entry ID, launched without a shell using `gtk-launch`), `shortcut`
(key combination), `screenshot` (Print), `none`. Empty tiles do nothing.
The Apps page does not change with window focus; page 1 follows the focused app when auto_switch
is enabled. The keypad's two physical navigation buttons switch the two pages.

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
`session` (`x11`/`wayland`), `focus_supported`, `active_app`, `page`, `paused`.
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
X11 automatic focus is supported initially; on Wayland report integration
unavailable accurately and retain launch/manual shortcut pages.

Minimum tests cover desktop-entry discovery, invalid/imported profiles,
runtime compilation, HID packet/event handling, shortcut parsing, IPC/control
state, and a GUI smoke test. Hardware tests require the actual keypad and
must not synthesize unrelated keystrokes without user interaction.
