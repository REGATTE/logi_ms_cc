# Validation

Version 0.3.1. Target: Ubuntu 24.04.4 amd64, GNOME on X11, Logitech MX Creative Keypad USB `046d:c354`.

## Automated checks

Action Ring development checks additionally cover settings migration with a
backup of legacy eight-slot layouts, the seven-action limit, empty-slot removal,
portable app artwork resolution, model bindings, ring-only
and full imports, editor persistence and drag reordering, popup cancellation,
keyboard selection and monitor-edge placement. Rust tests cover immutable ring
actions, single-use/expired tokens, child supervision, XInput mappings and HID++
notification filtering. Checks pass with 292 Python tests and 63 Rust tests,
plus formatting and Clippy. Package smoke includes the ring editor and validates
that packaged udev rules match the fixed permission helper.

The preceding v0.2.0 baseline checks were:

- 192 Python tests: application and Logitech device discovery, desktop IDs, profile validation and atomic imports, settings compilation, launcher activation metadata and profile assignments, original-icon and icons-only fallback rendering, real Qt device navigation/editor/import/export interactions, portable configuration, private runtime directories, permission-dialog cancellation/authorization routing, window activation recovery, user-service startup and compatibility upgrades, appearance persistence, live system-theme changes, packed launcher order and native key dragging. Device checks include partial Bluetooth-query failures, connection dots and reported battery values, shared footer navigation, and recovery after connecting multiple keypads. Editor tests cover assigned-app card order, search and empty states, shortcut controls, and app/profile-chip refresh after assignments and reorders.
- Five final-JPEG pixel tests verify the 118-pixel black canvas, narrow five-pixel inset, proportional artwork fit, transparent-padding trimming and optional labels. White pixels and opaque backgrounds belonging to original icons are preserved; the renderer adds no white tile.
- 40 Rust tests cover HID packet framing and captured acknowledgments, key press edges, shortcut parsing, config validation, page/profile state, private bounded Unix IPC, simulated queue-full recovery, X11 window matching/selection, foreground identities, activation-context expiry and manual overrides.
- `cargo fmt --check` and `cargo clippy --all-targets -- -D warnings`.
- Package inspection verifies root-owned files, safe modes, the fixed permission helper and policy, original bundled Qt runtime, actual offscreen device-home/console/settings/details/editor routes with injected inventory, reported battery indicators, assigned-app cards and search, launch activation metadata, packed launchers, native-drop persistence, and Python-to-Rust runtime validation.

- Control discovery tests cover verified model layouts, new-device capability parsing, composite interfaces, separate Bluetooth/receiver identities, non-HID USB controls, malformed or unavailable metadata, and deduplicated high-resolution wheels. Qt tests verify empty assignment choices, unchanged configuration/runtime files, widget retention during battery updates, and automatic pages for newly reported controls.

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
- The 0.1.4 activation probe used synthetic managed windows on the real X11 desktop: a minimized matching window was restored and focused, repeating activation recognized the already-active window, GTK application-ID matching worked, and an absent target returned no match. The window count stayed at 10 during the probe. Both test windows were removed and prior focus restored afterward. This probe did not press a physical keypad key.
- A separate 0.1.4 probe exercised the production launcher, focus observer and page state with the installed Firefox Snap. Its `Navigator` / `firefox_firefox` window classes matched the desktop metadata. The first activation launched Firefox and switched to Shortcuts after foreground confirmation. Repeating activation reused the already-active window without increasing the window count. Returning focus to Chrome restored Apps. This probe did not press a physical keypad key.
- Reopening settings upgraded the running 0.1.3 worker through the compatibility handshake. The replacement reported version 0.1.4 and `launcher_activation_v1`, with keypad/input access, X11 focus support and tray availability ready, Apps page 0 and no error. The live runtime contains launch metadata and profile assignments for ChatGPT, Lark, Firefox and Warp.
- The user confirmed both 0.1.4 behaviors on the physical keypad: pressing Firefox brought its existing window forward and displayed Firefox shortcuts; switching to another app returned the keypad to Apps.
- Version 0.1.5's home, keypad editor, unsupported-device details and settings were visually checked offscreen at 1200×820 and 970×710 in both light and dark modes. Native mouse navigation then verified the grouped Console card, component selection through the sidebar and device images, keypad editor/back navigation, and dialpad details/back navigation. The worker reported keypad/input access, X11 focus support and tray availability ready, with no error. The home, console and editor screenshots are actual live-window captures.
- Version 0.2.0's Applications cards were inspected at 1200×820 in dark mode and 970×710 in light mode, including the no-match search state. Native navigation verified the revised component selector, keypad inspector, and Applications page. The live home and Console selector both displayed the dialpad's reported 100% battery beside its green dot. The live Applications view displayed original app icons, assigned keys, shortcut counts and rounded controls; the editor reported the keypad connected. These checks do not replace the earlier physical-key confirmations.

Before the Action Ring addition, the control pages were visually checked in dark mode at 1200×820 and light mode at 970×710. G402 offers only Back and Forward; the dialpad offers four buttons, one Dial and one Roller mapping. Native captures show matching image/row highlights and the dialpad's reported 100% battery. Selection tests cover mouse and keyboard interaction, scaled hit regions, scrolling a selected row into view, refresh retention, device changes and removed controls. Both halves of a dial or roller select the same single assignment. At that stage the pages retained empty choices and did not execute mappings or monitor input.

The GUI and worker run without root. Permission repair uses the packaged fixed helper through Ubuntu's polkit authentication. The privileged repair operation has been reviewed and its argument rejection tested; a package install/administrator dialog is not required for source testing when device access already exists.

## Action Ring hardware and desktop checks

Action Ring was separately tested on this X11 desktop with isolated native
windows and no access to the live keypad. Clicking a popup bubble delivered one
test key to the captured window. Escape, an outside click and a second trigger
all closed the session without an action; the outside click was not replayed to
the underlying window. Switching foreground before execution restored the exact
captured target; closing that target rejected the action without typing into
the other test window. Replaying a used token was rejected. Probe workers and
windows were closed and prior desktop focus restored.

The user individually pressed all four dialpad buttons to confirm the physical
mapping: top-left `0x53`, top-middle `0x56`, bottom-left `0x59`, bottom-right `0x5a`.
Calibration restored all original temporary diversion flags. A live Rust
assignment/pause check confirmed that one assigned button was diverted and all
four original flags were restored on pause. The existing external remapper
preset was left unchanged. A native G402 input test confirmed that assigned
Back was consumed and triggered the ring event, while unassigned Forward still
delivered its normal press and release to the test window.

The new editor was visually checked at 1200×820 in dark mode and 970×710 in
light mode. The live service upgraded through the capability handshake and
reported keypad, shortcut input, focus and tray ready with no error. Existing
launcher assignments were retained; ring triggers remain opt-in.
The user then assigned the dialpad's bottom-left button and confirmed that one
press opens the ring and a second press closes it on the final live build.

The compact ring and revised editor were inspected at 1200×820 in dark mode
and 970×710 in light mode. The popup is 280×280, with smaller action icons and a
separate **+** button; empty actions do not occupy positions. An isolated native
integration check exercised the real Rust `ring_configure` command and Qt
single-instance server: **+** opened the captured application’s seven-action
layout in a new GUI, then reused that same process to open an empty default
layout in Add mode. The worker page was preserved and the used token was
rejected. Fragmented requests activated once, oversized requests activated
nothing, and idle connections closed after the timeout. The probe used isolated
settings and no physical device capture.

The live worker then upgraded to `actions_ring_v2` with keypad, input, focus
and tray ready and no error. Migration preserved launcher assignments and the
dialpad bottom-left binding, saved the old eight-action layout beside settings,
and retained the first seven actions. The user confirmed that the physical
dialpad opens the smaller ring and that **+** opens the Action Ring editor.

The shared editor workspace was then checked across keypad, dialpad, G402,
unknown-device and Action Ring pages at 970×710 in both themes. Tests verify
visible preview/action panels, unchanged configuration while navigating,
search-to-apply behavior, filtered-control selection and mapping retention.
Offscreen previews with installed application icons were inspected at 1200×820
and 970×710. Device information and existing empty choices remain available;
the UI refactor adds no worker input capture or background process.

Shared action-picker checks cover application, shortcut and system choices on
keypad launcher keys, individual profile keys, and supported device buttons.
Mixed actions survive reorder, reload, export/import and missing applications;
legacy ring bindings remain intact until replaced. GUI tests verify Apply/Remove,
invalid-input rejection, profile-write rollback, unsupported-control states and
draft retention during discovery refresh. Rust tests verify exact button identity,
paused/removed mappings, simultaneous dialpad releases and device-launch context
across reloads. Expanded shortcut forms and legacy ring assignments were inspected
at 970×710 in both themes. These checks do not constitute a new physical-button
confirmation of every newly selectable action.

The desktop ring was subsequently reduced to 232×232, with 44-pixel buttons
and 28-pixel icons, and opens with a 220 ms bloom from the cursor. All 41
popup/editor tests pass, covering non-overlapping hitboxes, screen-edge origins,
transparent frames, early dismissal, keyboard navigation and clicking actions
during expansion. Hover enlarges options and artwork by 30% around their fixed
centers, including their clickable area, and returns without geometry drift.
The separate **+** stays at the bottom with zero, one or seven actions.
Offscreen previews were inspected in both themes and at 2×
scaling. A real-time Qt event-loop check confirmed completion, stopped animation
and released snapshot storage. The settings editor retains its larger preview.

Frameless-window checks cover transparent rounded corners, opaque edge fill,
native move/resize delegation, minimum-size fallback, maximizing/restoring and
footer window controls. Closing settings stops its UI timers without contacting
the worker. Neumorphic light/dark previews cover the device home, console,
keypad, dialpad, G402, Applications, Action Ring and Settings pages. Known devices
use bundled official product artwork; verified image coordinates keep dialpad
controls and the G402 side buttons selectable after scaling and letterboxing.
Unknown models retain the generic illustration and never inherit a G402 photo.

## Resource sample

With the settings process closed, the previously sampled worker used **5,604 KiB RSS (5.47 MiB)**, with eight threads and no Qt library loaded. A 15-second idle sample accumulated zero CPU ticks, reported as **0.0% of one core**. This sample was taken before the 0.2.0 UI refinements; it is not a maximum or a promise of zero CPU during key presses, icon uploads or window changes. The GUI's Qt runtime uses additional memory only while settings is open.

## Limits

This release has not been validated on another physical keypad, Ubuntu release, CPU architecture, non-US keyboard layout, or a Wayland desktop. Wayland automatic application detection is unavailable; manual profiles remain available. Individual application shortcuts can differ with app versions and user keybindings. Tests validate mappings but do not assert every external app's response.

Use the .deb on a second Ubuntu 24.04 amd64 PC and confirm app discovery, original artwork, page buttons, shortcut output, panel behavior, permissions and sign-in startup there before treating that deployment as verified.
