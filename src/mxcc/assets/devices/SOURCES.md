# Device artwork

These are official Logitech product renders, downloaded from Logitech's public
product CDN for local, offline identification of the user's devices. Logitech
retains the artwork and trademark rights. They are not original project artwork
and their inclusion does not imply Logitech endorsement.

The bundled PNG files are unchanged CDN responses. The app selects source
rectangles at paint time and scales proportionally.

- `mx-creative-console.png`: [MX Creative Console product page](https://www.logi.com/en-us/shop/p/buy-mx-creative-console), [top-view graphite render](https://resource.logitech.com/w_1400,c_limit,f_png/d_transparent.gif/content/dam/logitech/en/products/keyboards/mx-creative-console/buy/gallery/2025/mx-creative-console-top-view-graphite-2.png).
- `g402-angle.png`: [G402 Hyperion Fury product page](https://www.logitechg.com/en-eu/shop/p/g402-hyperion-fury-fps-gaming-mouse), [angled render](https://resource.logitechg.com/w_800,c_limit,f_png/d_transparent.gif/content/dam/gaming/en/products/g402/gallery-1.png).
- `g402-side.png`: the same G402 product page, [side render](https://resource.logitechg.com/w_1000,c_limit,f_png/d_transparent.gif/content/dam/gaming/en/products/g402/gallery-4.png). The interactive page uses this view because both G4/G5 side buttons are visible.
- `mx-master-4.png`: [MX Master 4 product page](https://www.logi.com/en-us/shop/p/mx-master-4.910-007559), [graphite angled render](https://resource.logitech.com/w_800,c_limit,f_png/d_transparent.gif/content/dam/logitech/en/products/mice/mx-master-4/gallery/mx-master-4-graphite-3qtr-angle-gallery-8.png). Bluetooth model ID `046d:b042` was read from the connected device.
- `mx-master-4-controls.png`: the same product page, [front three-quarter graphite render](https://resource.logitech.com/w_1000,c_limit,f_png/d_transparent.gif/content/dam/logitech/en/products/mice/mx-master-4/gallery/mx-master-4-graphite-front-3qtr-angle-gallery-10.png). This view exposes the main and side buttons together. Regions follow the [official control overview](https://support.logi.com/hc/en-us/articles/28321445268247-Getting-Started-MX-Master-4). A local Bluetooth button check on 2026-11-15 confirmed Back, Forward and Gesture as XInput buttons 8, 9 and 11 (`key:275`, `key:276`, `key:278`). The haptic panel supports action assignments through HID++ control `0x01a0` (`hidpp:416`); a local hardware check confirmed temporary diversion and restoration on pause and assignment removal. The wheel-mode button has a selectable region, but its custom actions remain unavailable.

Retrieved 2026-11-15. Known model artwork is selected by Logitech model ID, then
by exact model-name aliases for other transport IDs. The catalog is in
`device_artwork.py`: add the unchanged render, source, ID and exact name aliases
there to bundle another model for first-run offline use. Other connected models
are looked up in Logitech's official product catalog in the background. The
lookup uses the hardware-reported model name, not a Bluetooth nickname, address
or serial number. Only exact product matches and official Logitech CDN images
are accepted. Downloaded artwork and its source URLs are cached under
`$XDG_CACHE_HOME/mx-creative-control/devices/` (normally
`~/.cache/mx-creative-control/devices/`) for later offline use. Missing or
unverifiable matches retain the generic illustration with a
"Model image unavailable" label; they never borrow another model's photo.
The Creative Console grouping uses its known
keypad/dialpad members. The hardware's actual color may differ from the graphite
reference render.
