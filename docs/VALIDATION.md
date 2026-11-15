# Validation

Target: Ubuntu 24.04.4 amd64, GNOME on X11, Logitech MX Creative Keypad USB `046d:c354`.

## Automated checks

- 121 Python tests: discovery precedence and desktop IDs, profile validation and atomic imports, settings compilation, original-icon and icons-only fallback rendering, real Qt editor/import/export interactions, portable configuration, private runtime directories, permission-dialog cancellation/authorization routing, window activation recovery, user-service startup, appearance persistence, live system-theme changes, packed launcher order and native key dragging.
- Five final-JPEG pixel tests verify the 118-pixel black canvas, narrow five-pixel inset, proportional artwork fit, transparent-padding trimming and optional labels. White pixels and opaque backgrounds belonging to original icons are preserved; the renderer adds no white tile.
- 14 Rust tests cover HID packet framing and captured acknowledgments, key press edges, shortcut parsing, config validation, page/profile state, private bounded Unix IPC and simulated queue-full recovery.
- `cargo fmt --check` and `cargo clippy --all-targets -- -D warnings`.
- Package inspection verifies root-owned files, safe modes, the fixed permission helper and policy, original bundled Qt runtime, an actual offscreen settings window, packed launchers, native-drop persistence, and Python-to-Rust runtime validation.

Tests run without opening hardware. The package smoke extracts into a temporary directory without installing system files.

## Real hardware and desktop checks

- Keypad detected, user access confirmed, virtual input device opened, tray registered, X11 active-app tracking working.
- User confirmed original app artwork, launching Firefox from its LCD key, and both physical page buttons.
- Launcher and shortcut image uploads verified through device acknowledgments, including multipart JPEGs. The physical device returns `c1` / `c2` packet acknowledgments; its queue-full status requires a bounded whole-image retry.
- Manual Firefox profile, automatic mode, Apps/Shortcuts page changes and pause/resume checked through the worker's real IPC endpoint.
- Twenty rapid page/profile transitions passed. The worker recovered from 67 queue-full acknowledgments, received 87 accepted packet acknowledgments, and settled without an error.
- Real tray activation opened settings. A second activation reused the existing window. Closing it terminated the Qt process while the same worker and keypad connection remained active.
- The actual window was inspected under the workstation's desktop theme; a light/dark palette contrast issue was corrected. See [the screenshot](screenshots/settings.png).
- Icons-only is the initial setting and is also applied to this workstation's configuration.
- Version 0.1.1 replaces the workstation's obsolete prototype launcher and synchronizes the physical Apps page every time settings opens. The user confirmed the keypad now matches the settings preview.
- Recovery was verified with a real stopped worker: reopening the existing settings window started a new managed worker, reapplied the launcher layout and restored Apps page 0 without a display error. The source worker is active in its own user-session unit, separate from the settings process.
- Version 0.1.2 adds two focused Rust tests for source/installed icon resolution and missing-artwork fallback. The live GNOME panel was visually checked: the mint keypad icon is visible, using the same SVG as the application window. D-Bus exposes the resolved absolute icon path.
- The workstation prefers dark appearance; native Qt reports Dark, legacy configuration defaults to System, and the live window opens dark. Light/Dark overrides, system-change signals, dialog/popup palettes, import and persistence are tested. Changing appearance leaves the keypad runtime file unchanged.
- Version 0.1.3 was exercised with a real native mouse drag from key index 0 to 2 in an isolated settings window on X11. The insertion order was persisted, the screenshot was visually checked, and the shortcut page remained unchanged. This check used isolated configuration without changing the live keypad layout.
- The live 0.1.3 window and runtime were checked with five consecutive Apps keys: ChatGPT, Lark, Firefox, Warp and Screenshot. The worker reported keypad/input/panel access ready, Apps page 0 and no error. The final screenshot shows enlarged original artwork on black, with the selection outline still visible.

The GUI and worker run without root. Permission repair uses the packaged fixed helper through Ubuntu's polkit authentication. The privileged repair operation has been reviewed and its argument rejection tested; a package install/administrator dialog is not required for source testing when device access already exists.

## Resource sample

With the settings process closed, the final release worker used **5,604 KiB RSS (5.47 MiB)**, with eight threads and no Qt library loaded. A 15-second idle sample accumulated zero CPU ticks, reported as **0.0% of one core**. This is an observed idle sample, not a maximum or a promise of zero CPU during key presses, icon uploads or window changes. The GUI's Qt runtime uses additional memory only while settings is open.

## Limits

This release has not been validated on another physical keypad, Ubuntu release, CPU architecture, non-US keyboard layout, or a Wayland desktop. Wayland automatic application detection is unavailable; manual profiles remain available. Individual application shortcuts can differ with app versions and user keybindings. Tests validate mappings but do not assert every external app's response.

Use the .deb on a second Ubuntu 24.04 amd64 PC and confirm app discovery, original artwork, page buttons, shortcut output, panel behavior, permissions and sign-in startup there before treating that deployment as verified.
