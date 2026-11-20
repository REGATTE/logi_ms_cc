# Install on Ubuntu

The initial package targets Ubuntu 24.04 with its system Python 3.12, on the
architecture used to build it. It controls the USB MX Creative **Keypad** and
supports application, shortcut and system mappings on supported mouse/dialpad
buttons. Website profiles also support directional shortcuts on the Bluetooth
MX Dialpad's large dial. Other wheels and unsupported controls remain unavailable.
This is an independent
community application, not Logitech's Logi Options+ software.

## Install a built package

Copy the `.deb` from `dist/` to the office PC, then run:

```sh
sudo apt install ./mx-creative-control_0.4.0_amd64.deb
mx-creative-control
```

When upgrading an existing installation, close its settings window before
installing the new package, then reopen settings afterward. The single-instance
window otherwise keeps running the old interface code. Reopening loads the new
interface, regenerates its runtime configuration and checks worker compatibility.

The package includes its pinned Qt runtime. Installation does not run pip or
download application code; apt may download declared Ubuntu system dependencies.
For a completely offline PC, those Ubuntu dependencies must already be present.
An arm64 machine requires an arm64 build.
ChatGPT embedded-browser website detection uses the declared `python3-gi` and
`gir1.2-atspi-2.0` dependencies through system `/usr/bin/python3`.

The rounded settings window has no native title bar. Drag empty header or footer
space to move it, use its edges to resize, and use the top-right icons to
minimize, maximize/restore or close. Closing settings leaves the worker running.
Artwork for the Creative Console, G402 and MX Master 4 is bundled for offline
use. Other connected Logitech models download an exact matching product image
from Logitech in the background, then reuse the cached image offline. The
lookup sends only the hardware-reported model name, not a Bluetooth nickname,
device address or serial number. No matching official image, or no network on
the first lookup, leaves a labeled placeholder. Failed lookups retry after
15 minutes; they never substitute a different model's photo.

Connect the keypad over USB. Open **MX Creative Control** from Ubuntu's app
launcher and select **MX Creative Console** on the device home screen. Choose
the keypad by clicking its image. Under **Keys**, select a key,
choose an installed app or Screenshot, then press **Apply to key** at the bottom
of the rounded right-hand inspector. The picker includes all installed apps;
applying a key preserves the app's existing shortcut assignment. App images come
from their original desktop icons.
Artwork fills each key with a narrow black border and no added white tile.
Re-scan after installing an additional
app; the app must provide a desktop entry to appear in the list. A ChatGPT browser
shortcut or PWA appears if the browser creates a desktop entry for it.

The **Applications** page lists only currently assigned, available apps and
Screenshot, in key order with duplicate entries removed. Rounded cards show each
app's original icon, assigned keys, and shortcut count. Search the cards, choose a
shortcut profile, import JSON, or edit shortcuts (**Set up** for apps without a
profile). **Rescan apps** refreshes installed apps. Add apps through the **Keys**
picker. The Keys page has no profile dropdown or key-number heading; app icons
above the keypad select shortcuts. Apply, Import, Edit, Use shortcuts on keypad,
and Follow active app actions are grouped at the bottom of the inspector.

Other Logitech devices receive a controls page automatically. The MX Dialpad
shows its four buttons, one **Dial** mapping and one **Roller** mapping.
G402 offers only the **Back** and **Forward** buttons. Other models
use Ubuntu's reported button, key and wheel capabilities. Capability reporting
does not always correspond one-to-one with physical buttons; hidden proprietary
controls may need a model description. A device without reported controls shows
an empty state. Reading this metadata requires no additional input permissions.

On the G402, MX Master 4 and dialpad pages, click an outlined control in the device image to
select it in the control dropdown. Choosing a control in the dropdown highlights
it on the image. For supported buttons, choose an application, keyboard shortcut
or system action, then press **Apply**; **Remove** clears its mapping. Selecting
a control alone does not change device behavior. Each wheel or dial is shown as
one physical control. Configure the MX Dialpad's large dial in **Website shortcuts**;
other wheel movement mappings remain unavailable. Devices without known
physical layouts still support selection through the dropdown.

The MX Master 4 uses a front three-quarter view so its main buttons and side
buttons can be selected together. Its Gesture button and, over Bluetooth, its
**Haptic Sense Panel** can use the same action choices, including
**System → Open Action Ring**. Select the panel on the image, choose an action,
then press **Apply**. The panel uses temporary HID++ button diversion; removing
its assignment, pausing the worker or quitting the worker restores its previous
behavior. Closing the settings window leaves the worker and assignment active.
Set up device permissions if the panel reports that access is required.
The wheel-mode button can be selected, but its custom actions are not implemented.
The main wheel's image selects **Wheel click**; wheel
movement remains a separate dropdown entry. Any extra input reported by Linux
without a verified physical location stays in the dropdown.

The home screen lists connected Logitech USB/HID and Bluetooth devices, grouping
the keypad and dialpad under **MX Creative Console**. The **Website shortcuts**
button opens website profiles for device buttons, large-dial directional shortcuts and Action Rings.
ChatGPT's embedded browser is detected automatically when you click inside a
website. Clicking chat or the address bar restores normal actions. Click the
video viewer before using a frame-step dial shortcut. For Firefox, follow
[the browser setup guide](WEBSITE-SHORTCUTS.md) to connect the optional companion
extension. Custom website Action Rings work in Firefox only; normal app/default
rings remain available in ChatGPT. Device discovery continues to work without
either browser connection. The shared bottom-left
Refresh icon is available on every page and updates the inventory; it also refreshes while the window is
open. Open the Console card to see its components side by side and select the
keypad or dialpad by its image. The dialpad and other devices
open control assignments and connection details. Supported buttons can launch
or focus applications, send shortcuts, take screenshots or open the Action Ring.
Both selection pages show each device's reported battery percentage beside its
connection dot beneath the device. No percentage appears when battery data is
unavailable.
Discovery does not pair Bluetooth devices. Only explicit control assignments
enable capture. Dialpad buttons, its large dial and the MX Master 4 haptic panel
use separate HID++ diversion; the small roller is left unchanged.
Connect just one supported keypad to use the editor; multiple keypads are
shown individually, but their configuration cannot be targeted independently.
Back controls return to the console or all devices. The Settings gear at the bottom left of every page opens
global settings, which are also accessible from the editor.

The Apps page packs available apps and Screenshot left-to-right, top-to-bottom,
with empty keys only at the end. Missing configured apps remain in settings for
use on another PC; they do not leave gaps between visible keys. Drag an Apps key
onto another to insert it there and shift the remaining apps. Dropping onto an
empty key appends it. Reordering saves the layout and reloads the keypad
immediately. Shortcut profiles keep their explicit slot positions.

On X11, pressing an app key focuses an existing matching window. The app is
launched only when no matching window exists. With automatic switching enabled,
an app activated from the keypad shows its assigned shortcuts while it remains
active; leaving that app returns the keypad to Apps. Apps without an assigned
profile stay on Apps. Using the physical arrow buttons or a manual profile
overrides automatic page choices. Disabling automatic switching leaves page
selection manual while app keys still focus or launch their applications.

The Rust worker stays in the background and owns the tray icon. Opening settings
starts the Qt process; closing settings releases that process. Ubuntu's default
GNOME session has AppIndicator support; other GNOME setups need an extension
that displays StatusNotifier/AppIndicator tray icons. Settings remain available
from the application launcher even if the desktop hides the tray.

The panel icon matches the application's mint nine-key icon. Source checkouts
resolve the SVG directly, so installing it into a global icon theme is not
required. The Settings gear at the bottom left of every page opens **Settings → Appearance → Theme**, with System setting (default),
Light and Dark. System setting follows desktop appearance changes while the
window is open; explicit choices persist across restarts.

Opening or reopening settings also starts a stopped worker, reloads the saved
layout and selects the Apps page on the physical keypad. Ubuntu supervises the
worker with the installed user service; source checkouts use a transient user
service and `scripts/run-dev.sh`. This startup does not enable the sign-in option.

## Automatic login startup

Open **Settings → Background & configuration** and enable **Start automatically
when I sign in**. The app configures startup for the current account and shows
success or an error beneath the switch. This works for installed packages and
source checkouts, requires no administrator authentication, and does not need a
relaunch. Package installation does not enable startup for all users.

At graphical sign-in, a short-lived launcher starts the worker and tray icon
using the current desktop session. It does not open settings or leave Python/Qt
running in the background. An already running compatible worker is reused.
Turning the switch off removes login startup without stopping the current
worker; use **Quit** in the tray menu to stop it now.

The app manages `~/.config/autostart/mx-creative-control.desktop`, respecting
`XDG_CONFIG_HOME`. It safely replaces its own entry and migrates a previously
enabled `mx-creative-control.service` login trigger. For source runs, keep the
checkout, virtual environment and worker build available. Reopening an opted-in
app repairs the entry after moving the checkout. If setup fails, the switch
returns to its previous state and displays the error; no device layout changes
are needed.

For startup diagnostics with an installed package:

```sh
systemctl --user status mx-creative-control.service
journalctl --user -u mx-creative-control.service -n 60
```

For a source checkout, use `mx-creative-control-dev.service` in those commands.
After saving a layout, `mx-creative-control startup` (installed) or
`scripts/run-dev.sh startup` (source) exercises the background startup path
without opening settings or enabling the preference.

Only one program should own the keypad. Stop an older OpenLogi/prototype keypad
service before starting this app; otherwise both programs may overwrite its
screen images or respond to the same button presses.

## Device permissions

The package supplies active-seat udev rules for the USB keypad `046d:c354`,
Bluetooth dialpad `046d:bc00`, and `/dev/uinput`. These allow keypad output,
diversion of assigned dialpad buttons or the large dial, and configured shortcut input.
G402 ring triggers use per-device X11 button grabs without extra device permissions.
The rules do not grant access to reading other
keyboards, change user groups, or make device files writable by everyone.

Open the footer's Settings gear and find **Permissions & connection** for access
status, reconnect checks, and **Set up device permissions**. These controls are
in global Settings, outside the keypad's Keys/Applications navigation.
If settings reports missing access, use this permission setup action. The app
explains the requested access, then Ubuntu's polkit dialog requests an
administrator's authentication. The fixed root-owned helper repairs the
packaged permission rule, reloads udev, loads `uinput` if needed, and refreshes the matching
devices. The GUI and worker continue to run as your normal user. Canceling the
dialog leaves settings usable. If a workplace policy prevents this access, an
administrator must approve the device setup; the app cannot bypass that policy.

When running from source, permission repair is available only after the package
has installed the root-owned helper and policy. The app never elevates a helper
from a writable checkout. XDG paths and `MXCC_BUNDLED_PROFILES` may select local
user configuration during development; they do not change the privileged
helper's fixed destinations or commands.

Use `scripts/run-dev.sh` to open the source checkout with its own Qt runtime.
When the packaged user unit is installed, settings starts that unit. Otherwise,
on a desktop with a systemd user manager, source settings starts the transient
`mx-creative-control-dev.service` unit. It survives closing the window or the
terminal that opened it and restarts on failures. Login startup remains opt-in
through the Settings switch described above.
Its log is available with `journalctl --user -u mx-creative-control-dev.service`.
On desktops without a user manager, the app uses a detached worker and writes
`worker.log` beneath its user configuration directory.

If the keypad still reports no access, unplug/reconnect it and check that this
is an active local desktop session. Access is intentionally tied to the active
seat. Rules placed by an administrator in `/etc/udev/rules.d/` may override the
packaged rule and are not overwritten by the helper.

Existing-window activation, automatic shortcut-page switching and Action Ring support X11.
Wayland keeps launching apps and manually selected shortcut pages working, but
this implementation cannot focus existing Wayland windows or detect their
global focus changes. The settings status
shows this limitation. Use the app icons above the keypad to select shortcuts,
or press **Use shortcuts on keypad** to apply the current shortcut view manually.
**Follow active app** restores automatic matching. Select **Ubuntu on Xorg** at login if automatic focus
switching is required. Keyboard shortcuts are emitted as Linux key codes, so
application bindings and the current keyboard layout still determine their
effect.

## Build a package

Build as a normal user on Ubuntu 24.04. Install the development tools once:

```sh
sudo apt install build-essential pkg-config python3 python3-pip python3-venv \
  libdbus-1-dev libudev-dev libx11-dev dpkg-dev
```

Use a stable Rust toolchain satisfying `worker/Cargo.toml` (the dependency lock
file is included). Fetch the pinned build inputs, then assemble offline:

```sh
scripts/prepare-runtime.sh --download
cargo fetch --locked --manifest-path worker/Cargo.toml
scripts/build-deb.sh
python3 scripts/check-package.py dist/mx-creative-control_0.4.0_amd64.deb
python3 scripts/source-archive.py
```

`--download` is the explicit network step for the Qt wheels. Keep
`build/wheelhouse/` and its `SHA256SUMS` for repeat builds; running
`scripts/prepare-runtime.sh` without arguments checks and reuses that wheelhouse.
The package builder uses `cargo --locked --offline`, bundles only the two pinned
Qt packages plus the app, and writes the package and SHA-256 checksum to `dist/`.
No root access is required to build. The source archive excludes the old
prototype, backups, personal configuration, `.venv`, dependency caches and
compiled output.

Both assembly scripts normalize archive owners, modes and timestamps using
`SOURCE_DATE_EPOCH` (default `0`). Reproducible rebuilding requires the same
source, architecture, Rust toolchain and pinned dependency inputs. The source
archive is suitable for another developer; it is not a vendored offline Rust
toolchain or dependency bundle. See `packaging/THIRD_PARTY.md` for Qt notices
and source locations, and `worker/THIRD_PARTY.md` for worker attribution.

For an already installed development runtime, the package builder also accepts
`--runtime-dir .venv/lib/python3.12/site-packages`. It checks the exact Qt package
versions and copies only PySide6, shiboken6 and their license metadata.

## Move settings or uninstall

Copy `~/.config/mx-creative-control/` to the other PC to carry your layout and
imported shortcut profiles. Desktop app IDs may differ between distro, Flatpak,
Snap and local installations; select the corresponding installed app in settings
when necessary. Cached key images are regenerated locally.

To uninstall:

```sh
systemctl --user disable --now mx-creative-control.service
sudo apt remove mx-creative-control
systemctl --user daemon-reload
```

Quit any running settings window or worker first. Removal leaves your personal
configuration and cache intact. Delete `~/.config/mx-creative-control/` and
`~/.cache/mx-creative-control/` yourself only if you want to discard them.
Sign out after removal to revoke any device ACLs already granted to the current
desktop session.
