"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const {createWebsiteBridge} = require("../background.js");

class Event {
  constructor() { this.listeners = new Set(); }
  addListener(fn) { this.listeners.add(fn); }
  removeListener(fn) { this.listeners.delete(fn); }
  async emit(...args) { await Promise.all([...this.listeners].map(fn => fn(...args))); }
}
const tick = () => new Promise(resolve => setImmediate(resolve));

function fixture() {
  let window = {id: 7, type: "normal", focused: true, incognito: false};
  let tab = {id: 2, active: true, incognito: false, url: "https://example.com/private?token=secret"};
  const ports = [];
  const intervals = new Map();
  const timeouts = new Map();
  let nextTimer = 1;
  const timers = {
    setInterval(fn, ms) { assert.equal(ms, 5000); const id = nextTimer++; intervals.set(id, fn); return id; },
    clearInterval(id) { intervals.delete(id); },
    setTimeout(fn, ms) { assert.equal(ms, 3000); const id = nextTimer++; timeouts.set(id, fn); return id; },
    clearTimeout(id) { timeouts.delete(id); }
  };
  const api = {
    runtime: {connectNative(host) {
      assert.equal(host, "org.mxcreativecontrol.browser");
      const port = {onMessage: new Event(), onDisconnect: new Event(), sent: [],
        postMessage(message) { this.sent.push(message); },
        disconnect() { this.disconnected = true; this.onDisconnect.emit(); }};
      ports.push(port);
      return port;
    }},
    browserAction: {setBadgeText: async () => {}, setBadgeBackgroundColor: async () => {}},
    tabs: {onActivated: new Event(), onUpdated: new Event(), onRemoved: new Event(),
      query: async options => { assert.equal(options.active, true); assert.equal(options.windowId, window.id); return [{...tab}]; }},
    windows: {onFocusChanged: new Event(), onRemoved: new Event(), getLastFocused: async () => ({...window})}
  };
  const bridge = createWebsiteBridge(api, timers);
  return {api, bridge, ports, timers, intervals, timeouts,
    setWindow: value => { window = {...window, ...value}; },
    setTab: value => { tab = {...tab, ...value}; },
    messages: () => ports.flatMap(port => port.sent)};
}

test("sends only canonical hostname and focus, never path/title/credentials", async () => {
  const f = fixture();
  f.setTab({url: "https://user:secret@Example.COM:8443/private?access=token#anchor", title: "Private title"});
  f.bridge.start(); await tick();
  assert.deepEqual(f.messages(), [{domain: "example.com", focused: true, window_id: 7}]);
  await f.ports[0].onMessage.emit({ok: true});
  assert.equal(f.bridge.getStatus().connected, true);
  f.bridge.stop();
  assert.equal(f.ports[0].disconnected, true);
  assert.equal(f.intervals.size, 0);
});

test("tab switch and active URL change refresh exact domains", async () => {
  const f = fixture(); f.bridge.start(); await tick();
  f.setTab({url: "https://www.example.com"});
  await f.api.tabs.onActivated.emit({tabId: 2});
  assert.deepEqual(f.messages().at(-1), {domain: "www.example.com", focused: true, window_id: 7});
  f.setTab({url: "https://next.example.com/path"});
  await f.api.tabs.onUpdated.emit(2, {url: "https://next.example.com/path"}, {active: true});
  assert.equal(f.messages().at(-1).domain, "next.example.com");
  const count = f.messages().length;
  await f.api.tabs.onUpdated.emit(8, {url: "https://background.example.com"}, {active: false});
  assert.equal(f.messages().length, count);
});

test("OS blur keeps last browser domain but explicitly prevents rebinding focus", async () => {
  const f = fixture(); f.bridge.start(); await tick();
  f.setWindow({focused: false});
  await f.api.windows.onFocusChanged.emit(-1);
  assert.deepEqual(f.messages().at(-1), {domain: "example.com", focused: false, window_id: 7});
  await [...f.intervals.values()][0]();
  assert.deepEqual(f.messages().at(-1), {domain: "example.com", focused: false, window_id: 7});
});

test("private window, private tab, internal URL and inaccessible window clear context", async () => {
  const f = fixture(); f.bridge.start(); await tick();
  f.setWindow({incognito: true}); await f.bridge.refresh();
  assert.deepEqual(f.messages().at(-1), {domain: null, focused: false, window_id: null});
  f.setWindow({incognito: false}); f.setTab({incognito: true}); await f.bridge.refresh();
  assert.equal(f.messages().at(-1).domain, null);
  f.setTab({incognito: false, url: "about:config"}); await f.bridge.refresh();
  assert.equal(f.messages().at(-1).domain, null);
  f.api.windows.getLastFocused = async () => { throw new Error("Window closed"); };
  await f.bridge.refresh();
  assert.deepEqual(f.messages().at(-1), {domain: null, focused: false, window_id: null});
});

test("stale asynchronous results cannot overwrite the newest tab", async () => {
  const f = fixture();
  const pending = [];
  f.api.tabs.query = () => new Promise(resolve => pending.push(resolve));
  const first = f.bridge.refresh(); await tick();
  f.setWindow({id: 19});
  const second = f.bridge.refresh(); await tick();
  pending[1]([{url: "https://new.example", active: true}]); await second;
  pending[0]([{url: "https://old.example", active: true}]); await first;
  assert.deepEqual(f.messages(), [{domain: "new.example", focused: true, window_id: 19}]);
});

test("native disconnect reconnects once and sends fresh context", async () => {
  const f = fixture(); f.bridge.start(); await tick();
  const old = f.ports[0]; await old.onDisconnect.emit();
  assert.equal(f.bridge.getStatus().connected, false);
  assert.equal(f.timeouts.size, 1);
  f.setTab({url: "https://new.example"});
  [...f.timeouts.values()][0](); await tick();
  assert.equal(f.ports.length, 2);
  assert.deepEqual(f.ports[1].sent, [{domain: "new.example", focused: true, window_id: 7}]);
  await old.onMessage.emit({ok: true});
  assert.equal(f.bridge.getStatus().connected, false);
  await f.ports[1].onMessage.emit({ok: true});
  assert.equal(f.bridge.getStatus().connected, true);
});

test("worker failure retries on heartbeat without exposing error contents", async () => {
  const f = fixture(); f.bridge.start(); await tick();
  await f.ports[0].onMessage.emit({ok: false, error: "sensitive URL"});
  assert.equal(f.bridge.getStatus().connected, false);
  assert.ok(!f.bridge.getStatus().error.includes("sensitive"));
  await [...f.intervals.values()][0]();
  assert.equal(f.messages().length, 2);
  await f.ports[0].onMessage.emit({ok: true});
  assert.equal(f.bridge.getStatus().connected, true);
});

test("unload invalidates an in-flight query and sends no later messages", async () => {
  const f = fixture(); let resolve;
  f.api.windows.getLastFocused = () => new Promise(done => { resolve = done; });
  f.bridge.start();
  f.bridge.stop();
  resolve({id: 7, type: "normal", focused: true}); await tick();
  assert.deepEqual(f.messages(), []);
});
