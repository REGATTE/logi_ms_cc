# Shortcut profiles

Bundled profiles live in this repository's `shortcuts/config/` directory. The
installed application reads these from `/usr/share/mx-creative-control/shortcuts/config/`
and user profiles from `~/.config/mx-creative-control/shortcuts/config/` (or
`$XDG_CONFIG_HOME/mx-creative-control/shortcuts/config/`). Import a JSON file in
the app, or place it in the user directory and rescan. Assign its profile to an
installed app using the profile dropdown. A custom profile can have empty
`desktop_ids` and `wm_classes` and be assigned manually.

```json
{
  "version": 1,
  "id": "my-editor",
  "name": "My editor",
  "desktop_ids": ["my-editor.desktop"],
  "wm_classes": ["my-editor"],
  "shortcuts": [
    {"slot": 1, "label": "Save", "keys": "Ctrl+S", "icon": "document-save"}
  ]
}
```

Slots count left-to-right and top-to-bottom, from 1 to 9. Omitted slots are
empty. Profile IDs use lowercase letters, digits, hyphens and underscores
(maximum 64 characters). Labels have a 48-character limit. `icon` is an
installed icon-theme name, such as `document-save`; application launcher
icons always come from the installed app's own desktop entry. No downloaded
app artwork is included in this project.

Each shortcut is one key with optional `Ctrl`, `Alt`, `Shift` and `Super`
modifiers. Letters, digits, F1–F24, navigation keys, Enter, Tab, Space,
Escape, Backspace, Delete, Insert, Print and common media keys are supported.
Punctuation can use names such as `Grave`, `Minus`, `Equal`, `BracketLeft`,
`BracketRight`, `Backslash`, `Semicolon`, `Apostrophe`, `Comma`, `Period`,
`Slash` and `Plus`. Spell the plus key `Plus`, as in `Ctrl+Plus`. Multi-chord
sequences, scripts and shell commands are deliberately not supported.

Input uses Linux physical key codes with US punctuation positions. Other
keyboard layouts or app-specific remappings can need a different shortcut.
Generic editing shortcuts act on the focused widget, so Copy requires a
selection and Paste requires an editable field. Desktop shortcuts can also
be intercepted by the Ubuntu desktop environment.

The bundled VS Code profile uses the official
[Linux shortcut reference](https://code.visualstudio.com/shortcuts/keyboard-shortcuts-linux.pdf).
The Firefox profile follows
[Mozilla's shortcut reference](https://support.mozilla.org/en-US/kb/keyboard-shortcuts-perform-firefox-tasks-quickly).
The Warp profile follows its
[Linux keyboard shortcut reference](https://docs.warp.dev/getting-started/keyboard-shortcuts/).
These profiles assume the apps' default bindings. ChatGPT and Lark profiles
contain only conventional editing actions; they do not claim app-specific
Linux integrations. They can be replaced with your own tested bindings.

A user profile with the same ID as a bundled profile overrides it. The import
API rejects collisions unless replacement is explicitly requested. Invalid
manually copied profiles are logged and skipped. The app never executes a
profile file. Automatic profile switching needs focused-window support;
the initial implementation supports X11. Launcher actions and the generic
shortcut page remain available on Wayland.
