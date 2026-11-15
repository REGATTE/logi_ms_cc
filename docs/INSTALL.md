# Install on Ubuntu

The initial package targets Ubuntu 24.04 with its system Python 3.12, on the
architecture used to build it. It controls the USB MX Creative **Keypad**; the
wireless dialpad is outside this application's scope. This is an independent
community application, not Logitech's Logi Options+ software.

## Install a built package

Copy the `.deb` from `dist/` to the office PC, then run:

```sh
sudo apt install ./mx-creative-control_0.1.1_amd64.deb
mx-creative-control
```

The package includes its pinned Qt runtime. Installation does not run pip or
download application code; apt may download declared Ubuntu system dependencies.
For a completely offline PC, those Ubuntu dependencies must already be present.
An arm64 machine requires an arm64 build.

Connect the keypad over USB. Open **MX Creative Control** from Ubuntu's app
launcher, select a tile under **Customize**, choose an installed app, then press
**Apply to key**. App images come from their original desktop icons.
Re-scan after installing an additional
app; the app must provide a desktop entry to appear in the list. A ChatGPT browser
shortcut or PWA appears if the browser creates a desktop entry for it.

The Rust worker stays in the background and owns the tray icon. Opening settings
starts the Qt process; closing settings releases that process. Ubuntu's default
GNOME session has AppIndicator support; other GNOME setups need an extension
that displays StatusNotifier/AppIndicator tray icons. Settings remain available
from the application launcher even if the desktop hides the tray.

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

If settings reports missing access, use its permission setup action. The app
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

Automatic switching to the focused app's shortcuts initially supports X11.
Wayland keeps launching apps and manually selected shortcut pages working, but
does not expose global app focus to this implementation. The settings status
shows this limitation. In the shortcut preview, choose a profile and press
**Use preview on keypad** to select it manually. Select **Ubuntu on Xorg** at login if automatic focus
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
python3 scripts/check-package.py dist/mx-creative-control_0.1.1_amd64.deb
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
