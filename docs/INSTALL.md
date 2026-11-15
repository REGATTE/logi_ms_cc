# Install on Ubuntu

The initial package targets Ubuntu 24.04 with its system Python 3.12, on the
architecture used to build it. It controls the USB MX Creative **Keypad**; other
connected Logitech devices have control-mapping placeholders and connection details.
Those assignment dropdowns are intentionally empty and do not change input behavior.
This is an independent
community application, not Logitech's Logi Options+ software.

## Install a built package

Copy the `.deb` from `dist/` to the office PC, then run:

```sh
sudo apt install ./mx-creative-control_0.2.0_amd64.deb
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

On the G402 and dialpad pages, click an outlined control in the device image to
highlight its matching assignment row; the list scrolls to the selected control.
Clicking or focusing a row highlights the corresponding image control. Each
wheel or dial is selected as one physical control with a single assignment.
The future action will interpret direction (for example, increasing/decreasing
volume or scrubbing forward/backward). Selection does not assign an action or alter device behavior.
Devices without known physical layouts still support selection in the list.

The home screen lists connected Logitech USB/HID and Bluetooth devices, grouping
the keypad and dialpad under **MX Creative Console**. The shared bottom-left
Refresh icon is available on every page and updates the inventory; it also refreshes while the window is
open. Open the Console card to see its components side by side and select the
keypad or dialpad by its image. The dialpad and other devices
open control-mapping placeholders and connection details.
Both selection pages show each device's reported battery percentage beside its
connection dot beneath the device. No percentage appears when battery data is
unavailable.
No Bluetooth pairing or configuration of other devices is performed.
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

Enable **Start the keypad worker when I sign in** in Settings if desired.
Package installation does not enable the service for all users. User service commands, if needed:

```sh
systemctl --user daemon-reload
systemctl --user enable mx-creative-control.service
systemctl --user status mx-creative-control.service
journalctl --user -u mx-creative-control.service -n 60
```

Only one program should own the keypad. Stop an older OpenLogi/prototype keypad
service before starting this app; otherwise both programs may overwrite its
screen images or respond to the same button presses.

## Device permissions

The package supplies an active-seat udev rule for Logitech USB ID `046d:c354`
and `/dev/uinput`. The first grants access to the keypad; the second allows the
worker to emit configured keys. It does not grant access to reading other
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
terminal that opened it, restarts on failures, and is not enabled at login.
Its log is available with `journalctl --user -u mx-creative-control-dev.service`.
On desktops without a user manager, the app uses a detached worker and writes
`worker.log` beneath its user configuration directory.

If the keypad still reports no access, unplug/reconnect it and check that this
is an active local desktop session. Access is intentionally tied to the active
seat. Rules placed by an administrator in `/etc/udev/rules.d/` may override the
packaged rule and are not overwritten by the helper.

Existing-window activation and automatic shortcut-page switching support X11.
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
python3 scripts/check-package.py dist/mx-creative-control_0.2.0_amd64.deb
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
