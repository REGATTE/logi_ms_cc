/* Hostname, focus and an ephemeral window ID cross only a local connection. */
"use strict";

function createWebsiteBridge(api, timers = globalThis) {
  const host = "org.mxcreativecontrol.browser";
  const heartbeatMs = 5000;
  let port = null;
  let generation = 0;
  let interval = null;
  let retry = null;
  let stopped = false;
  let status = {connected: false, domain: null, error: "Connecting to MX Creative Control…"};

  function setStatus(values) {
    status = {...status, ...values};
    if (api.browserAction) {
      Promise.resolve(api.browserAction.setBadgeText({text: status.connected ? "" : "!"})).catch(() => {});
      Promise.resolve(api.browserAction.setBadgeBackgroundColor({color: "#8d5b24"})).catch(() => {});
    }
  }

  function scheduleReconnect() {
    if (stopped || retry !== null) return;
    retry = timers.setTimeout(() => { retry = null; refresh(); }, 3000);
  }

  function connect() {
    if (port || stopped) return port;
    try {
      const connected = api.runtime.connectNative(host);
      port = connected;
      connected.onMessage.addListener(message => {
        if (port !== connected) return;
        if (message && message.ok === true) setStatus({connected: true, error: null});
        else setStatus({connected: false, error: "Open or update MX Creative Control to connect Firefox."});
      });
      connected.onDisconnect.addListener(() => {
        if (port !== connected) return;
        port = null;
        setStatus({connected: false, error: "Connect Firefox from the Website shortcuts page in MX Creative Control."});
        scheduleReconnect();
      });
      return connected;
    } catch (_) {
      setStatus({connected: false, error: "Connect Firefox from the Website shortcuts page in MX Creative Control."});
      scheduleReconnect();
      return null;
    }
  }

  function hostname(tab) {
    if (!tab || tab.incognito || typeof tab.url !== "string") return null;
    try {
      const url = new URL(tab.url);
      if (url.protocol !== "https:" && url.protocol !== "http:") return null;
      const domain = url.hostname.toLowerCase().replace(/\.$/, "");
      if (!domain || domain.length > 253 || !domain.split(".").every(label =>
        /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label))) return null;
      return domain;
    } catch (_) {
      return null;
    }
  }

  async function refresh() {
    const current = ++generation;
    let domain = null;
    let focused = false;
    let windowId = null;
    try {
      // Retain the last browser domain while the Action Ring has OS focus.
      // The native worker still gates every action on the captured Firefox window.
      const window = await api.windows.getLastFocused({windowTypes: ["normal"]});
      if (current !== generation || stopped) return;
      if (window && !window.incognito && window.type === "normal"
          && Number.isInteger(window.id) && window.id > 0 && window.id <= 2147483647) {
        focused = window.focused === true;
        const tabs = await api.tabs.query({windowId: window.id, active: true});
        if (current !== generation || stopped) return;
        domain = hostname(tabs[0]);
        windowId = domain === null ? null : window.id;
      }
    } catch (_) {
      // A closed window or a tab without permission must clear, never reuse, a site.
    }
    if (current !== generation || stopped) return;
    setStatus({domain});
    const target = connect();
    if (target) {
      try {
        target.postMessage({domain, focused: domain !== null && focused, window_id: windowId});
      } catch (_) {
        if (port === target) port = null;
        setStatus({connected: false, error: "The local connection was interrupted. Reconnecting…"});
        scheduleReconnect();
      }
    }
  }

  const activeUpdate = (_id, changes, tab) => {
    if (tab && tab.active && ("url" in changes || "status" in changes)) refresh();
  };
  const bindings = [
    [api.tabs.onActivated, refresh], [api.tabs.onUpdated, activeUpdate],
    [api.tabs.onRemoved, refresh], [api.windows.onFocusChanged, refresh],
    [api.windows.onRemoved, refresh]
  ];

  function start() {
    stopped = false;
    for (const [event, listener] of bindings) event.addListener(listener);
    interval = timers.setInterval(refresh, heartbeatMs);
    refresh();
  }

  function stop() {
    stopped = true;
    ++generation;
    for (const [event, listener] of bindings) event.removeListener(listener);
    if (interval !== null) timers.clearInterval(interval);
    if (retry !== null) timers.clearTimeout(retry);
    interval = retry = null;
    const previous = port;
    port = null;
    if (previous) previous.disconnect();
  }

  return {start, stop, refresh, getStatus: () => ({...status})};
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {createWebsiteBridge};
} else {
  const bridge = createWebsiteBridge(browser);
  browser.runtime.onMessage.addListener(message => {
    if (message && message.type === "website_status") return Promise.resolve(bridge.getStatus());
    return undefined;
  });
  bridge.start();
}
