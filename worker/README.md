# Native worker

Build with Rust 1.89 or newer:

```sh
cargo build --release --locked
cargo test --locked
cargo clippy --all-targets -- -D warnings
```

The executable is `target/release/mxcc-worker`. See
`../docs/development-contract.md` for runtime JSON and IPC. The normal worker
opens real hardware; `--check-config PATH` only validates configuration.
Tests do not open HID/uinput or inject keystrokes. One IPC test needs permission
to bind a private Unix socket under the temporary directory.

The main loop blocks on HID and a wake socket. X11 focus changes arrive as
property events, and tray actions arrive over D-Bus. Device/permission retries
run every five seconds only while a required handle is unavailable. No Qt,
webview, GPU context, image renderer or ordinary keyboard input monitor runs
in this process. Only changed cached key faces are uploaded, with device
acknowledgements; null tiles explicitly clear their displays.

Only the wired MX Creative Keypad `046d:c354` is supported. HID++ navigation
diversion is temporary and restored on normal shutdown/SIGTERM. Detection
refuses a device already open by OpenLogi or Solaar. Other managers are never
stopped automatically. An abrupt kill cannot run restoration; restart and
quit normally or reconnect the keypad if needed.

Keyboard combinations use Linux physical key codes for a US keyboard layout.
They are shortcuts, not arbitrary text typing. X11 foreground matching uses
WM_CLASS. An Apps key first finds and activates a matching X11 window; it
launches the desktop entry only if none exists. With automatic switching
enabled, a keypad-activated app's assigned shortcut page is shown while the app
stays active, then Apps is restored when focus leaves. Apps without an assigned
profile remain on Apps. Explicit page arrows and manual profile selection
override automatic paging, and disabling `auto_switch` leaves page selection
manual. Wayland existing-window activation and automatic matching are not
implemented; app launch and manual selection remain available. Manual selection
works on both: `{"command":"profile","id":"vscode"}` selects a configured
profile, `"default"` selects the general page, and `null` restores automatic
selection. Use `{"command":"page","index":1}` to show it. Status includes
the current `manual_profile`. A removed manual profile resets to automatic
on reload.

Automatic paging waits for confirmed foreground after a successful app-key
action, with a 12-second bound. A timeout or explicit page/profile choice cancels
that activation context; ordinary focus changes do not re-arm it later.

The worker runs unprivileged. Scoped udev/uinput permissions and the settings
dialog are provided by the Ubuntu package. Socket directories are mode 0700,
sockets 0600, and requests are capped at 4096 bytes. Application actions pass
validated desktop IDs directly to `gtk-launch`, without a command shell, when
no existing X11 window is found (or on Wayland). Runtime launch actions include
window-class candidates and an optional assigned profile ID; the frontend
compiles these from installed desktop entries and profile assignments.

See `THIRD_PARTY.md` for protocol source attribution and license notices.
