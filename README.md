# MX Creative Control

A lightweight Ubuntu app for the Logitech MX Creative **Keypad**. Launch your apps from its nine LCD keys, use their original icons, and switch to shortcuts for the app you are working in.

![MX Creative Control device home screen](docs/screenshots/devices.png)

The live device home screen, with the keypad and dialpad grouped as MX Creative Console.

This is an independent community project, not Logitech's Logi Options+ software. The home screen lists connected Logitech devices and an Action Ring editor.

## Features

- **Device home screen:** the keypad and dialpad share an MX Creative Console card. Open it to choose a component, then select the keypad for its Keys editor. Other connected Logitech devices open their own controls and connection details. Refresh and Settings icons stay at the bottom left on every page; Settings opens global preferences.
- **Consistent device editors:** the keypad, dialpad, mouse and newly discovered devices use the Action Ring editor's wide preview, application scope and rounded right-hand panel. Search keypad actions or device controls; selecting an illustrated control highlights its matching row. Connection details expand beneath the device.
- **Action Ring:** up to seven configurable actions around the cursor, plus a separate **+** button fixed at the bottom that opens the editor. The compact ring blooms from the cursor in 220 ms; hovering enlarges an option by 30%. Use default or application-specific layouts, original app icons, launch/focus, shortcuts and screenshots. Drag to reorder and import/export JSON. The native Qt popup exits after use.
- **Shared action picker:** keypad keys and supported device buttons offer **Open application**, **Keyboard shortcut**, and **System** categories. Applications focus an existing window or launch; System offers Screenshot and Open Action Ring. Apply saves the selected action; Remove clears it. The same categories edit individual keys in application shortcut pages.
- **Device control discovery:** the dialpad shows four buttons, one **Dial** mapping and one **Roller** mapping; G402 offers only its **Back** and **Forward** buttons. Those buttons can launch/focus apps, send shortcuts, take screenshots or open the ring on X11. Other models automatically list Ubuntu-reported controls; recognized standard mouse buttons use the same backend. Configure the large dial's directional shortcuts in Website shortcuts. Other wheel and unsupported control mappings remain unavailable, with an explanation in the picker.
- **Visual control selection:** click a control in the G402, MX Master 4 or dialpad illustration to select it in the action panel. Choosing a control from the panel's selector highlights it in the illustration too. The MX Master 4 includes its main buttons, Back, Forward, Gesture, thumb wheel, wheel-mode button and haptic panel; unsupported custom actions are explained when selected. Unknown device layouts use the selector without guessing physical button positions.
- **MX Master 4 haptic actions:** over Bluetooth, select the Haptic Sense Panel on the image and assign an application, shortcut or system action, including Open Action Ring. Removing its assignment, pausing or quitting the worker restores its previous behavior. Wheel-mode and wheel movement mappings remain unavailable.
- **Website shortcuts:** add exact-hostname profiles from **Website shortcuts**. Registered desktop browsers and ChatGPT's embedded browser connect automatically through Linux accessibility, including after browser restarts. Chrome/Chromium, Firefox-family and WebKit browsers must expose the focused document URL; other browsers are discovered from their Linux desktop registration. Override supported device buttons or assign clockwise/counterclockwise shortcuts to the Bluetooth MX Dialpad's large dial. Custom website Action Rings require the optional Firefox add-on; automatic detection retains normal application/default rings. Click a video viewer with Left/Right frame shortcuts to use the dial for frame stepping. Other controls inherit their normal actions. Only a configured, focused website's hostname is passed from the accessibility helper to the worker. See [browser setup and website profiles](docs/WEBSITE-SHORTCUTS.md) for focus and private-window behavior and the optional development add-on's temporary-install limitation.
- **Connection indicators:** each device has a green dot when connected, red when disconnected, and blinking yellow while searching. On both the home screen and Console component selector, a reported battery percentage appears beside the dot beneath the device; devices without battery data show only the dot. Hover for connection details. Devices seen during the open window's session remain visible after disconnecting; unconfirmed connections use steady yellow.
- **Original app icons:** artwork comes from installed desktop entries and icon themes, fills the key with a narrow black border, and has no added white tile. Keys show icons only by default, with optional labels and neutral placeholders for missing artwork.
- **Small background worker:** Rust handles the keypad, assigned device buttons, virtual shortcut input, panel icon and X11 focus events. Qt runs only while settings or the ring is open.
- **Automatic login setup:** turn on sign-in startup in Settings to start the background worker and tray icon at your next login. Works with an installed package or a source checkout, without administrator access or a relaunch.
- **System, light or dark appearance:** follows your desktop by default, with an immediate override in Settings. The window and panel use the same keypad icon.
- **Rounded neumorphic interface:** softly raised panels, recessed inputs and rounded controls in both themes. The frameless window can be dragged from empty header/footer space and resized at its edges; minimize, maximize/restore and close icons sit at the top right.
- **Model-specific device artwork:** bundled official Logitech renders identify the MX Creative Console, G402 and MX Master 4 offline. Other connected models download matching official product images in the background and cache them for offline use. Identification uses hardware model IDs and reported model names, separately from Bluetooth nicknames. Unverified or unavailable images show a labeled placeholder. The G402 controls page retains its side view with clickable Back/Forward buttons. [Artwork sources and attribution](src/mxcc/assets/devices/SOURCES.md).
- **Installed-app discovery:** scan standard desktop entries, Snap and Flatpak applications; choose apps or Screenshot for the launcher.
- **Focus or launch:** on X11, an app key brings an existing matching window forward; it launches the app only when no matching window exists.
- **Compact, reorderable launcher:** available Apps fill keys left-to-right, top-to-bottom. Drag a key to insert it elsewhere; the other keys shift and the keypad updates immediately.
- **Editable shortcut profiles:** manage profiles for assigned apps in Applications, edit their nine actions in the UI, or import declarative JSON from `shortcuts/config/`.
- **Automatic or manual pages:** on X11, an app activated from a keypad key shows its assigned shortcuts while it stays active, then returns to Apps when focus leaves. Arrow buttons, manual profiles and the automatic-switching preference remain in control.
- **Portable settings and explicit permissions:** export/import layouts and profiles, opt into sign-in startup, and use Ubuntu's authentication dialog when device access needs repair.

Closing settings leaves the worker running. Opening settings from the panel icon or app launcher reconnects it, reloads the layout and selects the Apps page. **Quit** in the panel menu stops the worker. One measured idle sample used about **5.5 MiB RAM** with settings closed; see [validation and resource measurements](docs/VALIDATION.md) for conditions and limits.

## Requirements

- **Validated target:** Ubuntu 24.04, amd64, with the keypad connected over USB (`046d:c354`).
- **Building/development:** Python 3.12, Rust **1.89 or newer** with Cargo, Git, and the Ubuntu build dependencies below.
- **Desktop:** X11 for automatic app switching. GNOME needs StatusNotifier/AppIndicator support to display the panel icon; settings also opens from the app launcher.
- **Input access:** the active desktop user needs access to the keypad and `/dev/uinput`. The package supplies the rules and a fixed permission-repair helper.

Other distributions, architectures and Ubuntu versions need separate validation. Shortcuts use physical US-layout key codes; adapt profiles for other layouts or custom application bindings.

## Build and install

The repository contains source and packaging tools. Built `.deb` files and `dist/` are not tracked; build the package before following the install command.

Install Rust 1.89 or newer with Cargo, then prepare the Ubuntu build tools:

```sh
sudo apt install git build-essential pkg-config python3 python3-pip python3-venv \
  python3-gi gir1.2-atspi-2.0 libdbus-1-dev libudev-dev libx11-dev dpkg-dev
git clone https://github.com/REGATTE/logi_ms_cc.git
cd logi_ms_cc
```

Fetch the pinned Qt runtime and locked Rust dependencies, then build and check the package:

```sh
scripts/prepare-runtime.sh --download
cargo fetch --locked --manifest-path worker/Cargo.toml
scripts/build-deb.sh
python3 scripts/check-package.py dist/mx-creative-control_0.3.2_amd64.deb
sudo apt install ./dist/mx-creative-control_0.3.2_amd64.deb
mx-creative-control
```

The package bundles PySide6-Essentials and Shiboken **6.10.2** privately. Installation needs no pip setup; apt installs declared Ubuntu system dependencies. After the fetch steps, package assembly uses local inputs with Cargo's lockfile in offline mode. Copy the built `.deb` to another Ubuntu 24.04 amd64 PC to install it there. [Installation details](docs/INSTALL.md) cover offline requirements, permissions, services and removal.

Connect the keypad, install the applications you want to launch, and open **MX Creative Control**. Select **MX Creative Console** on the device home screen, then choose the keypad. Close other controllers such as OpenLogi or Solaar before using it so they do not compete for the keypad.

The app and worker run as your normal user. Installation or **Settings → Permissions & connection → Set up device permissions** requires administrator authentication. `/dev/uinput` access also lets other programs running as that user create virtual input devices; the dialog explains this access. Permission repair uses the installed root-owned helper, never a script from a writable checkout.

## Configure the keypad

![MX Creative Console component view](docs/screenshots/console.png)

![MX Creative Keypad editor](docs/screenshots/settings.png)

Live console selection and keypad editor on the Ubuntu workstation.

1. Select **MX Creative Console** on the home screen, then choose the keypad by clicking its image. Under **Keys**, select a key and expand **Open application**, **Keyboard shortcut**, or **System** in the right-hand action library. Choose an app, enter a shortcut name and keys, or choose Screenshot/Open Action Ring, then press **Apply to key**. **Remove** clears the key. The **+** beside the application icons selects the next empty key. The library includes all installed apps; applying a key preserves that app's existing shortcut assignment.
2. Drag an Apps key onto another to insert it at that position and shift the other keys. Dropping onto a blank key appends it after the other apps. Reorders save and reload the keypad immediately; shortcut slots keep their assigned positions.
3. **Applications** shows rounded cards for apps and Screenshot currently assigned to the keypad, in key order, with each app listed once. Each card shows its original icon and assigned keys. Search by name, choose a shortcut profile, **Import** a file, or **Edit** shortcuts (**Set up** when none are assigned). **Rescan apps** refreshes installed apps; add newly found apps through the picker under **Keys**.
4. On X11, press an app key to focus its existing window or launch it. With automatic switching enabled and a profile assigned, the keypad shows that app's shortcuts while it is active and returns to Apps when focus leaves. Apps without a profile stay on Apps. In the editor, the app icons above the keypad select their shortcuts. The inspector's bottom actions include **Import shortcuts**, **Edit shortcuts**, **Use shortcuts on keypad**, and **Follow active app**. Arrow buttons and manual shortcut choices override automatic paging; **Follow active app** restores automatic matching.
5. Use the back controls to return to the console or all devices. The Settings gear at the bottom left of every page opens **Settings → Appearance**, where you can choose **System setting**, **Light**, or **Dark**. System setting follows desktop changes automatically. Settings also contains **Permissions & connection**, labels, sign-in startup and configuration export. The keypad's **Keys** and **Applications** tabs sit beside its title.

Device discovery reads USB/HID inventory and connected Bluetooth device metadata while the settings window is open. It does not pair devices or change their configuration. Use the shared Refresh icon after connecting a device; the inventory also refreshes periodically. If multiple supported keypads are connected, the editor asks you to leave one connected so configuration has a single target.

For a mouse or dialpad button, select the control on its illustration or in the
panel's selector, choose an action using the same three categories, and press
**Apply**. These assignments apply across applications. Existing Action Ring
assignments remain until you replace or remove them. Search and draft edits do
not alter saved mappings; unavailable controls cannot be applied.

First-run choices target ChatGPT, VS Code, Lark, Firefox, Warp and Screenshot. Available apps and Screenshot fill the Apps page without gaps, left-to-right and top-to-bottom. Configured apps that are missing on this PC remain in settings for portability but do not leave holes between visible keys. A browser shortcut/PWA must provide a desktop entry to appear in discovery. ChatGPT and Lark profiles contain conventional editing actions; app-specific bindings can be added in the editor.

Bundled profiles are in [`shortcuts/config/`](shortcuts/config/). User profiles belong in:

```text
~/.config/mx-creative-control/shortcuts/config/
```

`XDG_CONFIG_HOME` is respected. Import a new file through the app, or copy it into that directory and rescan. Profiles contain labels, icon names and key combinations; they cannot contain scripts or shell commands. See [the JSON format and example](docs/PROFILES.md) and [shortcut references](shortcuts/README.md). When moving settings, desktop IDs can differ between Snap, Flatpak and distro installations; reassign the corresponding installed app where needed. Original artwork is resolved on the destination PC.

## Configure the Action Ring

Open **Action Ring** on the device home screen. Use the application icons and their **+** to choose or add an app-specific layout. Select a bubble to edit, or the ring’s separate **+** to add an action. The right-hand action library offers installed apps, keyboard shortcuts and Screenshot. Search, choose an action and press **Add action** or **Apply**. Drag bubbles to reorder them. **Try ring** opens the layout for the currently active application; the editor itself uses the default layout. Your existing keypad assignments stay unchanged.

To open it from hardware, choose **Open Action Ring** for a keypad key or a button on a device's controls page. Press once, then click an action. Escape, clicking outside, or pressing the trigger again closes it. Keys **1–7**, or arrows followed by Enter, also select actions. The worker remembers the original app and confirms its window before sending a shortcut. The popup does not replace the keypad's active-app context.

G402 Back/Forward capture suppresses the original navigation click. Dialpad buttons and an assigned large dial use separate HID++ diversion; the small roller retains its normal behavior. Removing an assignment, pausing or quitting normally restores the assigned control's prior state. If the worker is forcibly killed while a dialpad control is assigned, cycle the dialpad's power before using it normally. Bindings apply to a model, so two identical models share assignments. Wayland ring support and continuous ring adjustments are not implemented.

The compact popup hides empty actions and supports at most seven actions. Its separate **+** always opens the matching ring editor, including when the ring is full. Previous eight-action layouts are backed up beside your settings before migration.

Ring-only **Import/Export** transfers layouts while retaining this PC's button bindings. Global **Export configuration** includes both. See [Action Ring configuration](docs/ACTION-RING.md) for JSON and backend details.

## Start automatically at login

Open the Settings gear at the bottom left, then enable **Background & configuration → Start automatically when I sign in**. The app sets up startup for your account immediately and shows the result beneath the switch. No terminal commands, administrator authentication or relaunch are needed.

At your next graphical login, the background worker and tray icon start with your saved configuration. The settings window stays closed until you open it from the tray or app launcher. Turning the switch off removes automatic startup; it leaves the current worker running until you choose **Quit** in the tray menu. Startup is opt-in on each PC.

For source checkouts, keep the checkout, its virtual environment and built worker available. If you move the checkout, reopen the app from its new location to refresh an enabled startup entry. See [startup diagnostics](docs/INSTALL.md#automatic-login-startup) if setup reports an error.

## Develop and test

From the cloned repository, using the requirements above:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
cargo build --manifest-path worker/Cargo.toml --locked
MXCC_WORKER="$PWD/worker/target/debug/mxcc-worker" scripts/run-dev.sh
```

Source runs need the same desktop libraries and device permissions as an installed package, including `python3-gi` and `gir1.2-atspi-2.0` for automatic browser website detection. Its accessibility helper uses system `/usr/bin/python3`; no GI package is needed in the Qt virtual environment. The permission-repair dialog becomes available after package installation. Quit an already running tray worker before testing a rebuilt worker. `scripts/run-dev.sh` selects the checkout's virtual environment; the worker remains independent of the settings process under Ubuntu's user-session service manager.

```sh
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest
cargo test --manifest-path worker/Cargo.toml --locked
cargo fmt --manifest-path worker/Cargo.toml --check
cargo clippy --manifest-path worker/Cargo.toml --all-targets --locked -- -D warnings
```

Automated tests do not access hardware. They cover app discovery, profiles and imports, runtime compilation, real offscreen Qt interactions, permission routing, login setup and rollback, worker recovery, HID packets, shortcut keys and private IPC. [VALIDATION.md](docs/VALIDATION.md) records separate physical-device checks and current limitations. To produce a clean source archive, run `python3 scripts/source-archive.py`.

## Current limits

- Wayland supports app launching, virtual shortcut input and manual profiles; focusing existing windows and automatic app/page detection are not implemented. Use an X11 session for those features.
- G402 Back/Forward, Bluetooth MX Dialpad buttons and recognized standard X11 mouse buttons support the shared action choices. The Bluetooth MX Dialpad's large dial supports directional website shortcuts; its small roller and other wheel mappings remain unimplemented. Multiple connected keypads cannot be configured independently; identical device models share button assignments.
- Applications must expose desktop entries to be discovered. Profiles assume default app shortcuts, which can vary by version, focused widget or user configuration.
- Validation covers one Ubuntu 24.04 amd64 workstation and physical keypad. Other environments need testing before being treated as supported.

## Repository layout

```text
src/mxcc/          Qt settings, discovery, profiles, runtime configuration
worker/            Rust keypad worker, input, focus tracking, tray and IPC
shortcuts/config/  Bundled JSON shortcut profiles
packaging/         Desktop entry, service, permissions and runtime notices
scripts/           Development launcher, package checks and build tools
tests/             Python and offscreen GUI tests
docs/              Installation, profiles, interface contract and validation
```

## License and attribution

Project code is [MIT licensed](LICENSE). The reverse-engineered keypad protocol builds on OpenLogi and `node-logitech-mx-creative-console`; their notices and attribution are in [worker/THIRD_PARTY.md](worker/THIRD_PARTY.md). Qt and Shiboken retain their upstream licenses, documented in [packaging/THIRD_PARTY.md](packaging/THIRD_PARTY.md). Installed app icons and trademarks belong to their respective owners. The device-first interface references are documented in [UI design references](docs/UI-REFERENCES.md); product illustrations are drawn locally in Qt.
