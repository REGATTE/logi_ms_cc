"use strict";
browser.runtime.sendMessage({type: "website_status"}).then(status => {
  document.getElementById("connection").textContent = status.connected
    ? "Connected to MX Creative Control" : status.error || "Not connected";
  document.getElementById("website").textContent = status.domain || "No supported website selected";
}).catch(() => {
  document.getElementById("connection").textContent = "Reload the extension to reconnect.";
});
