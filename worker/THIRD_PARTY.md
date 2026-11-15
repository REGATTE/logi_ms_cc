# Protocol acknowledgements

`src/protocol.rs` adapts the vendor framing, image packet construction and grid
snapshot decoding from [OpenLogi v0.9.0](https://github.com/AprilNEA/OpenLogi/tree/v0.9.0),
specifically `openlogi-device/src/{keypad_vendor.rs,write/key_display.rs,session/keyboard.rs}`
and the geometry in `openlogi-device-registry/src/keypad.rs`. OpenLogi is dual
MIT/Apache-2.0 licensed; its notices are included in `licenses/`.
We use that material under MIT. OpenLogi credits the MIT-licensed
`node-logitech-mx-creative-console` for the underlying packet layout.

Our acknowledgement and queue handling corrects the OpenLogi implementation
using live captures and the original MIT-licensed
[node library image writer](https://github.com/Julusian/node-logitech-mx-creative-console/blob/main/packages/core/src/services/imageWriter/imageWriter.ts)
and [draw queue](https://github.com/Julusian/node-logitech-mx-creative-console/blob/main/packages/core/src/services/buttonsLcdDisplay/drawQueue.ts).
Each fragment gets an acknowledgement with `0xc0 | part`; status byte 0x00
means accepted, 0xfb means the device's image queue is full. The following
byte is not an error status. We await each fragment, retry a busy image in
full after 10 ms with a one-second bound, and pace accepted tiles by 8 ms.
Only one tile is drawn per event-loop turn so new page requests replace
obsolete pending work. Captured packets and simulated transport tests cover
this behavior. The node library's MIT notice is included in `licenses/`.

HID++ page-button control uses feature 0x1b04 documented by OpenLogi's
`openlogi-hidpp/src/feature/reprog_controls.rs`. Only temporary diversion is
changed, and the original diversion bit is restored when the worker stops.
The dialpad button backend uses the same feature and original Rust framing.
Live queries and individually confirmed button presses verified its control IDs:
top-left `0x53`, top-middle `0x56`, bottom-left `0x59`, bottom-right `0x5a`.

Other Rust dependencies retain their respective licenses in Cargo metadata.
