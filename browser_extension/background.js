/**
 * background.js — Usability Recorder Web Tracker
 *
 * ARCHITECTURE (Long-lived Port + WebSocket):
 *
 *   content.js  ←—port——→  background.js  ←—WebSocket——→  Desktop App
 *
 * Why ports instead of sendMessage?
 *   In MV3, chrome.runtime.sendMessage wakes the service worker but the SW
 *   may be in mid-initialization (WebSocket not yet reconnected, isRecording
 *   state not yet restored from storage) — events get dropped.
 *
 *   Long-lived ports (chrome.runtime.connect) keep the service worker alive
 *   as long as at least one tab has the port open. No more sleep problem.
 *   Also works from HTTPS pages (sendMessage is extension-internal, not a
 *   cross-origin network request).
 *
 * Flow:
 *   1. Tab loads → content.js connects a port → SW stays alive
 *   2. SW connects WebSocket to desktop app → receives "idle" or "start_recording"
 *   3. SW relays cmd to all content scripts via their ports
 *   4. User clicks Start → desktop app broadcasts {"cmd":"start_recording"}
 *   5. SW relays to ports → content scripts start capturing events
 *   6. content.js sends events via port → SW forwards to WebSocket → desktop app
 */

// ─── WebSocket State ──────────────────────────────────────────────────────────

const DEFAULT_PORT = 9877;
let WS_PORT = DEFAULT_PORT;
let socket = null;
let isConnected = false;
let isRecording = false;
let reconnectTimer = null;

// ─── Active Tab Tracking ──────────────────────────────────────────────────────

let activeTabId = null;   // only events from this tab are forwarded

// Initialise on startup
chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
  if (tabs && tabs[0]) activeTabId = tabs[0].id;
});

// Keep activeTabId up-to-date on every window focus change
chrome.windows.onFocusChanged.addListener((windowId) => {
  if (windowId === chrome.windows.WINDOW_ID_NONE) return;
  chrome.tabs.query({ active: true, windowId }, (tabs) => {
    if (tabs && tabs[0]) activeTabId = tabs[0].id;
  });
});

// ─── Port Management (one port per content-script tab) ────────────────────────

// All ports (all tabs)
const activePorts = new Set();
// tabId → Set<port>  (multiple content.js instances can exist per tab)
const portsByTabId = new Map();

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "event-port") return;

  const tabId = port.sender?.tab?.id ?? null;
  activePorts.add(port);

  // Register in portsByTabId (Set per tabId)
  if (tabId !== null) {
    if (!portsByTabId.has(tabId)) portsByTabId.set(tabId, new Set());
    portsByTabId.get(tabId).add(port);
  }

  // Tell this port its initial state right now
  const isThisTabActive = (tabId !== null && tabId === activeTabId);
  try {
    if (!isRecording) {
      port.postMessage({ cmd: "idle" });
    } else if (isThisTabActive) {
      port.postMessage({ cmd: "recording_active" }); // active tab reconnected mid-session
    } else {
      port.postMessage({ cmd: "recording_paused" }); // other tab — don't record
    }
  } catch {}

  // Forward events ONLY from the currently active tab
  port.onMessage.addListener((msg) => {
    if (!isRecording) return;
    if (tabId === null || tabId !== activeTabId) return;  // strict filter
    if (msg && msg.event) trySend(msg);
  });

  port.onDisconnect.addListener(() => {
    activePorts.delete(port);
    if (tabId !== null) {
      const set = portsByTabId.get(tabId);
      if (set) {
        set.delete(port);
        if (set.size === 0) portsByTabId.delete(tabId);
      }
    }
  });
});

/** Send a command to ALL connected content-script ports (all tabs). */
function broadcastToAllTabs(cmd) {
  for (const port of activePorts) {
    try { port.postMessage({ cmd }); } catch {}
  }
}

/** Send a command to ALL ports belonging to a specific tabId. */
function sendToTab(tabId, cmd) {
  const set = portsByTabId.get(tabId);
  if (!set) return;
  for (const port of set) {
    try { port.postMessage({ cmd }); } catch {}
  }
}

// ─── WebSocket Connection ─────────────────────────────────────────────────────

function connect() {
  if (socket && (socket.readyState === WebSocket.CONNECTING ||
                 socket.readyState === WebSocket.OPEN)) return;

  try {
    socket = new WebSocket(`ws://127.0.0.1:${WS_PORT}`);

    socket.onopen = () => {
      isConnected = true;
      setStatus("connected");
      console.log(`[WebTracker BG] Connected to ws://127.0.0.1:${WS_PORT}`);
      // Flush any queued events
      while (eventQueue.length > 0 && isRecording) {
        const ev = eventQueue.shift();
        trySend(ev);
      }
    };

    socket.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        handleServerCmd(msg);
      } catch {}
    };

    socket.onerror = () => { /* onclose will handle it */ };

    socket.onclose = () => {
      isConnected = false;
      setStatus("disconnected");
      console.log("[WebTracker BG] Disconnected. Retrying in 3s…");
      scheduleReconnect();
    };
  } catch {
    scheduleReconnect();
  }
}

function scheduleReconnect() {
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(connect, 3000);
}

// ─── Server Command Handler ───────────────────────────────────────────────────

function handleServerCmd(msg) {
  const cmd = msg.cmd;

  if (cmd === "start_recording") {
    isRecording = true;
    setStatus("recording");
    chrome.storage.session.set({ isRecording: true }).catch(() => {});

    // Send the right command to EVERY port:
    //   active tab  → "start_recording"  (start capturing)
    //   all others  → "recording_paused" (keep port alive, don't capture)
    for (const [tabId, portSet] of portsByTabId) {
      const cmd = (tabId === activeTabId) ? "start_recording" : "recording_paused";
      for (const port of portSet) {
        try { port.postMessage({ cmd }); } catch {}
      }
    }
    // Also send paused to any null-tabId ports (shouldn't exist but be safe)
    for (const port of activePorts) {
      if (!port.sender?.tab?.id) {
        try { port.postMessage({ cmd: "recording_paused" }); } catch {}
      }
    }

    console.log("[WebTracker BG] Recording STARTED — active tab:", activeTabId,
                " | connected tabs:", [...portsByTabId.keys()]);

    // Capture the active tab page context (background scripting — exactly 1 event)
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (tabs && tabs[0]) captureAndSendTab(tabs[0], "session_start");
    });

  } else if (cmd === "stop_recording") {
    isRecording = false;
    setStatus("connected");
    chrome.storage.session.set({ isRecording: false }).catch(() => {});
    broadcastToAllTabs("stop_recording");
    console.log("[WebTracker BG] Recording STOPPED");

  } else if (cmd === "idle") {
    isRecording = false;
    setStatus("connected");
    chrome.storage.session.set({ isRecording: false }).catch(() => {});
  }
}

// ─── Status Storage ───────────────────────────────────────────────────────────

function setStatus(status) {
  chrome.storage.local.set({
    ws_status:    status,
    ws_port:      WS_PORT,
    active_tabs:  activePorts.size,
    is_recording: isRecording,
  });
}

// ─── Event Queue + Sending ────────────────────────────────────────────────────

const eventQueue = [];
const MAX_QUEUE  = 100;

// Popup stats
let visitCount = 0;
const uniqueUrls    = new Set();
const uniqueDomains = new Set();

function trySend(payload) {
  if (!isConnected || !socket || socket.readyState !== WebSocket.OPEN) {
    if (isRecording && eventQueue.length < MAX_QUEUE) eventQueue.push(payload);
    return;
  }
  try {
    socket.send(JSON.stringify(payload));
    updateStats(payload);
  } catch {
    if (isRecording && eventQueue.length < MAX_QUEUE) eventQueue.push(payload);
  }
}

function updateStats(payload) {
  if (payload.url) uniqueUrls.add(payload.url);
  if (payload.domain) uniqueDomains.add(payload.domain);
  visitCount++;
  chrome.storage.local.set({
    visit_count:    visitCount,
    unique_urls:    uniqueUrls.size,
    unique_domains: uniqueDomains.size,
    last_event:     payload,
  });
}

// ─── Capture Tab (for page_visit / tab_switch / session_start) ───────────────

async function captureAndSendTab(tab, eventType) {
  if (!isRecording) return;
  if (!tab || !tab.url) return;
  if (tab.url.startsWith("chrome") || tab.url === "about:blank") return;

  // Try to get rich page data via scripting
  let pageData = {};
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id, allFrames: false },
      func: () => {
        const h1    = document.querySelector("h1");
        const meta  = document.querySelector('meta[name="description"]');
        const ogT   = document.querySelector('meta[property="og:title"]');
        return {
          h1:               h1   ? h1.innerText.trim().slice(0, 200) : "",
          meta_description: meta ? meta.getAttribute("content") : "",
          og_title:         ogT  ? ogT.getAttribute("content")  : "",
          referrer:         document.referrer || "",
          timestamp_browser: Date.now() / 1000,
        };
      },
    });
    if (results && results[0] && results[0].result) {
      pageData = results[0].result;
    }
  } catch {}

  trySend({
    event:            eventType,
    url:              tab.url,
    domain:           new URL(tab.url).hostname,
    title:            tab.title || "",
    h1:               pageData.h1               || "",
    meta_description: pageData.meta_description || "",
    og_title:         pageData.og_title         || "",
    referrer:         pageData.referrer         || "",
    timestamp_browser: pageData.timestamp_browser || Date.now() / 1000,
  });
}

// ─── webNavigation: Full page loads ──────────────────────────────────────────

chrome.webNavigation.onCompleted.addListener(
  async (details) => {
    if (details.frameId !== 0 || !isRecording) return;
    if (!details.url || details.url.startsWith("chrome")) return;

    // Let the tab settle (DOM ready), then capture
    await new Promise(r => setTimeout(r, 400));
    const tab = await chrome.tabs.get(details.tabId).catch(() => null);
    if (tab) captureAndSendTab(tab, "page_visit");
  },
  { url: [{ schemes: ["http", "https"] }] }
);

// ─── Tab Switches ─────────────────────────────────────────────────────────────

chrome.tabs.onActivated.addListener(async (activeInfo) => {
  const prevTabId = activeTabId;
  activeTabId = activeInfo.tabId;

  if (isRecording) {
    // Pause the tab that lost focus
    if (prevTabId !== null && prevTabId !== activeTabId) {
      sendToTab(prevTabId, "recording_paused");
    }
    // Activate the new tab
    sendToTab(activeTabId, "recording_active");
  }

  // Always capture tab_switch page context (background.js scripting approach)
  if (isRecording) {
    const tab = await chrome.tabs.get(activeInfo.tabId).catch(() => null);
    if (tab) captureAndSendTab(tab, "tab_switch");
  }
});

// ─── Keepalive Alarm ──────────────────────────────────────────────────────────

chrome.alarms.create("keepAlive", { periodInMinutes: 0.4 });

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name !== "keepAlive") return;
  // Reconnect if needed
  if (!isConnected) connect();
  // Update tab count in storage
  setStatus(isRecording ? "recording" : isConnected ? "connected" : "disconnected");
});

// ─── Inject content.js into existing tabs ────────────────────────────────────

async function injectIntoExistingTabs() {
  const tabs = await chrome.tabs.query({});
  for (const tab of tabs) {
    if (!tab.url || !tab.id) continue;
    if (!tab.url.startsWith("http://") && !tab.url.startsWith("https://")) continue;
    chrome.scripting.executeScript({
      target: { tabId: tab.id, allFrames: false },
      files:  ["content.js"],
    }).catch(() => {});
  }
}

chrome.runtime.onInstalled.addListener(injectIntoExistingTabs);
chrome.runtime.onStartup.addListener(injectIntoExistingTabs);

// ─── Restore State on SW Wake-up ──────────────────────────────────────────────

chrome.storage.local.get(["ws_port"], (result) => {
  if (result.ws_port) WS_PORT = parseInt(result.ws_port, 10) || DEFAULT_PORT;

  // Restore recording state from session storage
  chrome.storage.session.get(["isRecording"], (s) => {
    isRecording = s.isRecording || false;
    connect();
  });
});
