/**
 * popup.js — Logic for the extension popup UI.
 *
 * NEW: In the direct-WS architecture, status is read from chrome.storage.local
 * which is updated by each content.js tab. The popup now reflects:
 *   - How many tabs are connected
 *   - Whether recording is active
 *   - Event counts and last event details
 */

const dot          = document.getElementById("dot");
const statusText   = document.getElementById("status-text");
const portDisplay  = document.getElementById("port-display");
const portInput    = document.getElementById("port-input");
const setPortBtn   = document.getElementById("set-port-btn");
const statVisits   = document.getElementById("stat-visits");
const statUrls     = document.getElementById("stat-urls");
const statDomains  = document.getElementById("stat-domains");
const recentUrl    = document.getElementById("recent-url");
const recentH1     = document.getElementById("recent-h1");
const recentTime   = document.getElementById("recent-time");

// ── Read persistent state from storage ───────────────────────────────────────

function refreshUI() {
  chrome.storage.local.get(
    ["ws_status", "ws_port", "visit_count", "unique_urls", "unique_domains",
     "last_event", "is_recording", "connected_tabs"],
    (data) => {
      // Connection status
      const status     = data.ws_status    || "disconnected";
      const recording  = data.is_recording || false;
      const tabCount   = data.connected_tabs || 0;

      // Determine display state
      // In new architecture: content.js writes ws_status per-tab.
      // "recording" is stored when recording starts.
      let displayStatus, displayColor, dotClass;

      if (recording) {
        displayStatus = `🔴 Recording`;
        displayColor  = "#c62828";
        dotClass      = "dot recording";
      } else if (status === "connected" || status === "recording" || tabCount > 0) {
        displayStatus = tabCount > 1
          ? `🟢 Connected (${tabCount} tabs)`
          : "🟢 Connected";
        displayColor  = "#2e7d32";
        dotClass      = "dot connected";
      } else if (status === "connecting") {
        displayStatus = "Connecting…";
        displayColor  = "#f57c00";
        dotClass      = "dot connecting";
      } else {
        displayStatus = "Disconnected";
        displayColor  = "#757575";
        dotClass      = "dot disconnected";
      }

      dot.className           = dotClass;
      statusText.textContent  = displayStatus;
      statusText.style.color  = displayColor;

      // Port
      const port = data.ws_port || 9877;
      portDisplay.textContent = port;
      portInput.value = port;

      // Stats
      statVisits.textContent  = data.visit_count   || 0;
      statUrls.textContent    = data.unique_urls    || 0;
      statDomains.textContent = data.unique_domains || 0;

      // Last event
      if (data.last_event) {
        const ev = data.last_event;
        // Show element_description if it's a click/form event, else URL
        if (ev.element_description) {
          recentUrl.textContent = ev.element_description.slice(0, 60);
          recentH1.textContent  = ev.url ? `↗ ${ev.url.slice(0, 50)}` : "";
        } else {
          recentUrl.textContent = ev.url || "—";
          recentH1.textContent  = ev.title ? `"${ev.title.slice(0, 50)}"` : "";
        }
        const ago = ev.timestamp_browser
          ? `${((Date.now() / 1000) - ev.timestamp_browser).toFixed(1)}s ago`
          : "";
        recentTime.textContent = ago;
      }
    }
  );
}

// ── Port change ───────────────────────────────────────────────────────────────

setPortBtn.addEventListener("click", () => {
  const newPort = parseInt(portInput.value, 10);
  if (isNaN(newPort) || newPort < 1024 || newPort > 65535) {
    portInput.style.borderColor = "#c62828";
    return;
  }
  portInput.style.borderColor = "";
  // Save to storage — content.js tabs listen to storage.onChanged and reconnect
  chrome.storage.local.set({ ws_port: newPort });
  portDisplay.textContent = newPort;
  dot.className = "dot connecting";
  statusText.textContent = "Reconnecting…";
  statusText.style.color = "#f57c00";
  setTimeout(refreshUI, 2000);
});

// ── Poll storage for live updates while popup is open ────────────────────────

refreshUI();
const interval = setInterval(refreshUI, 1000);
window.addEventListener("unload", () => clearInterval(interval));
