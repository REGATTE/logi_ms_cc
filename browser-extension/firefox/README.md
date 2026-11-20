# Firefox website shortcuts

This add-on is optional. MX Creative Control automatically detects focused
Firefox websites through Linux accessibility for button and large-dial shortcuts,
including after Firefox restarts. Install the add-on for website-specific
Action Rings or to use its tab-based context instead of accessibility detection.

This extension reports the active tab's hostname, focus state and ephemeral
browser window ID to the local MX Creative Control worker. It sends no URL paths, query strings, page content, titles,
history, credentials or browsing data to a network service. Private windows and
non-HTTP(S) pages do not select a website profile. Domains match exactly;
`example.com` and `www.example.com` are separate profiles.

In MX Creative Control, open Website shortcuts and choose **Set up Firefox**.
This registers a native messaging host for your user and prepares the extension
ZIP. In Firefox, open `about:debugging#/runtime/this-firefox`, choose **Load
Temporary Add-on**, and select this directory's `manifest.json` or the prepared
ZIP. Allow the Firefox connection prompt if shown. The toolbar popup reports
the connection and selected domain.

A temporary add-on is removed when Firefox restarts; dial and button shortcuts
then use automatic detection without reloading it. A permanent install on
release Firefox requires a Mozilla-signed extension; this development build is
unsigned. Do not disable Firefox's signature verification. The same source can
be submitted to Mozilla for unlisted signing when distributing the app.

On Ubuntu's Firefox Snap, the host-side WebExtensions portal or native messaging
proxy starts the bridge. The installer uses the standard
`~/.mozilla/native-messaging-hosts/` host manifest location. It does not copy
executables into the Snap, alter Firefox preferences, or grant portal permission
silently. An unavailable portal is reported as a disconnected extension.

The worker receives a five-second heartbeat and discards abandoned contexts
after fifteen seconds. It applies website actions only to the matching
foreground Firefox window. The extension retains its last-focused normal tab
while the Action Ring holds OS focus so the ring can use the originating site.
While connected, it takes priority over accessibility detection even when its
domain is empty (for example in private windows). Without this add-on, a
configured website can match in a private Firefox window because public
accessibility metadata does not reliably identify private mode.

References:

- [Mozilla native messaging](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/Native_messaging)
- [Mozilla native manifest locations](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/Native_manifests)
- [Firefox Snap portal design](https://firefox-source-docs.mozilla.org/toolkit/components/extensions/webextensions/native-messaging-portal-design.html)
- [Temporary installation](https://extensionworkshop.com/documentation/develop/temporary-installation-in-firefox/)
- [Signing and distribution](https://extensionworkshop.com/documentation/publish/signing-and-distribution-overview/)
