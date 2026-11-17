# Website shortcuts

Open **Website shortcuts** on the device home screen or a mouse/dialpad page.
Choose **Add website** and enter a hostname or HTTP(S) address. The profile
matches the exact hostname: `github.com` and `gist.github.com` are separate.
Paths, ports and query strings do not form separate profiles.

Profiles work in Firefox and ChatGPT's embedded browser on X11. ChatGPT's
embedded browser is detected automatically through Linux accessibility; no
Firefox add-on is needed for that browser. Firefox uses the optional companion
add-on described below. The connection line identifies which browser is active.

In **Control shortcuts**, choose a device and button, select **Keyboard
shortcut**, enter a label and key combination, then **Apply**. Application and
system actions are also available. **Use normal action** removes just that
website's override. Controls without an override retain their global assignment
or normal device behavior.

For the MX Dialpad, select **Large dial** to enter separate **Clockwise** and
**Counterclockwise** keyboard shortcuts. Both directions are saved together by
**Apply**. **Use video frame shortcuts** fills in Right for the next frame and
Left for the previous frame. Each accepted dial increment sends one shortcut;
the website must support these keys, and its video viewer must have focus.
Turn slowly for precise frame stepping. Rapid spins are rate-limited and old
movement is discarded so the video stops moving promptly.
For `http://10.64.192.14:8800`, open a recording and click its video viewer
before turning the dial. This site's Left/Right shortcuts move one frame at a
time. In ChatGPT, clicking the chat or browser address bar returns to normal
actions; click back inside the video viewer to resume frame stepping.

Only the large dial is diverted while its assignment is active. The small
roller keeps its normal behavior. Leaving the website, pausing the worker,
or removing the assignment restores the dial's previous reporting mode.
Small roller and mouse wheel movement mappings remain unavailable.

In **Action Ring**, enable **Use a separate Action Ring for this website** and
edit its actions. Website-specific Action Rings work in Firefox only. ChatGPT's
embedded browser supports website dial and button shortcuts; its normal
application or default ring remains available when no website-specific ring
is selected. Attempting to open a custom website ring in ChatGPT reports this
limitation. The shared profile editor still lets you configure a ring for use
in Firefox.

Up to seven ring actions are supported. Assign a device button to
**System → Open Action Ring** to open it. A website ring takes priority over
the browser's application ring and the default ring. Turning off the checkbox
restores that normal selection. An enabled, empty ring is intentionally empty.
The ring's **+** button returns to the matching website editor.

## ChatGPT embedded browser

Add a website profile, open that website in ChatGPT's embedded browser, and
click inside the page. The worker uses the focused web document's accessibility
URI to identify its hostname. Focus on chat, native controls or the address bar
does not activate a website profile. A page that does not expose a focused web
document URI uses normal actions. Other embedded-browser applications are not
currently supported.

Ubuntu packages install `python3-gi` and `gir1.2-atspi-2.0` as dependencies.
Source checkouts need these system packages too. The helper runs with
`/usr/bin/python3` independently of the app's Qt virtual environment. If desktop
accessibility is unavailable, the connection line reports that problem while
ChatGPT is active; Firefox remains a separate connection.

## Connect Firefox (optional)

1. In **Website shortcuts**, click **Set up Firefox**. This registers a native
   messaging host for your user; no administrator access is required.
2. Click **Open Firefox setup**, or open
   `about:debugging#/runtime/this-firefox` in Firefox.
3. Choose **Load Temporary Add-on** and select the `manifest.json` shown in
   the setup dialog. The dialog also opens the extension folder and copies its path.
4. Allow the local connection if Firefox asks. The extension's toolbar popup
   shows whether it is connected; the app shows the active domain while Firefox
   is in the foreground.

The supplied development extension is unsigned. Temporary installation lasts
until Firefox restarts; load it again after a restart. A persistent release
installation requires Mozilla signing. This app does not disable signature
verification or change Firefox security preferences. On Firefox Snap, the
desktop's WebExtensions portal must be available and authorized.

Only one Firefox profile may connect at a time. Multiple normal windows within
that profile are supported. Private windows and non-HTTP(S) pages use normal
actions. Website switching in both supported browsers requires the existing
X11 desktop integration.

## Local data and fallback

The Firefox extension requests tab metadata access so it can determine the active
hostname. It sends only that hostname, browser focus state and a temporary
browser-window identifier to the local
worker, never full URLs, paths, queries, page content, titles or history. It does
not send browsing data to a server. Current browser context is temporary and
is not written into settings.

The ChatGPT helper reads only the focused web document's URI through the local
accessibility interface and forwards its hostname with window identity. It
does not read chat text, page text, browser history or hidden tabs. Full URIs
are not sent to the worker or stored in settings. The worker checks the active
application and window before using this context.

Profiles are saved under `website_profiles` and included in configuration
export/import. Existing settings without this field continue to work. Removing
a website restores normal actions on that site. Losing the Firefox connection
expires its context after 15 seconds. Embedded-browser focus loss or a stopped
helper clears that source's context. The worker checks foreground window identity;
a website ring is invalidated when its originating website context changes.

See the [extension README](../browser-extension/firefox/README.md) for protocol
and Mozilla setup references.
