# UI design references

The device home screen and keypad editor use Logitech's published Logi Options+
documentation as a visual reference for choosing a device, navigating its keys,
showing app/profile choices and inspecting a selected control.

- [Settings & Configuration — MX Creative Console](https://support.logi.com/hc/en-hk/articles/25407700552599-Settings-Configuration-MX-Creative-Console)
- [Home-screen reference](https://www.logitech.com/assets/70191/8/gs_1.jpg)
- [Device-navigation reference](https://www.logitech.com/assets/70191/9/gs_1.jpg)
- [Key-editor reference](https://www.logitech.com/assets/70191/11/gs_1.jpg)
- [MX Creative Console hardware guide](https://www.logitech.com/assets/70190/mx_creative_console_-_web_qsg_ww.pdf) — dialpad buttons, dial and roller.
- [G402 hardware guide](https://www.logitech.com/assets/51742/2/g402-hyperion-fury.pdf) — eight programmable buttons and their names.
- [Linux input event codes](https://www.kernel.org/doc/html/latest/input/event-codes.html) — input capability reporting and wheel events used for generic device discovery.
- [MX Master 4 settings reference supplied by the user](https://static0.xdaimages.com/wordpress/wp-content/uploads/wm/2025/09/logi-options-mx-master-4-for-mac-settings.png?q=49&fit=contain&w=750&h=422&dpr=2) — simple dark canvas, app strip and control callouts.
- [Action Ring editor reference supplied by the user](https://static0.xdaimages.com/wordpress/wp-content/uploads/wm/2025/09/option-to-tweak-action-rings-shortcuts-in-macos-using-the-logi-options-app.png) — compact centered ring with outside labels, application icons along the top, and a separate action library on the right. The native editor follows this hierarchy in both light and dark themes, with only implemented actions listed.

The same preview/action-library layout now applies to every device editor through
one shared Qt workspace. Keypad application icons and searchable actions, dialpad
and mouse controls, and newly discovered device inputs retain their own behavior
inside the same rounded panel, spacing and mint selection style.

These are reference links, not bundled artwork. Device illustrations in the app
are original `QPainter` drawing code; app icons come from the user's installed
applications. The interface remains native Qt; supported buttons can open Action
Ring, while other device actions remain empty placeholders. The Logitech name and product names identify detected
hardware. This independent community project is not affiliated with Logitech.
