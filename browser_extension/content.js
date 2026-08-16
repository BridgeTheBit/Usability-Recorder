/**
 * content.js — Usability Recorder Web Tracker
 *
 * ARCHITECTURE: Long-lived Port
 *   content.js  ←——port——→  background.js  ←——WebSocket——→  Desktop App
 *
 * Why NOT direct WebSocket from content.js?
 *   Content scripts run in the web page context. HTTPS pages CANNOT connect
 *   to ws://localhost (Mixed Content + Private Network Access block).
 *
 * Why NOT chrome.runtime.sendMessage?
 *   sendMessage wakes the service worker but isRecording state may not be
 *   restored yet when the message handler fires — events get silently dropped.
 *
 * Why Long-lived Port?
 *   chrome.runtime.connect() creates a persistent message channel that:
 *   1. Keeps the service worker alive (no 30s sleep problem)
 *   2. Works from HTTPS pages (extension-internal, not a network request)
 *   3. Delivers bidirectional messages reliably
 */

(function () {
  "use strict";

  if (window.__usabilityRecorderInjected) return;
  window.__usabilityRecorderInjected = true;

  // ─── Port to Background Service Worker ────────────────────────────────────

  let bgPort    = null;
  let isRecording = false;

  function connectPort() {
    try {
      bgPort = chrome.runtime.connect({ name: "event-port" });

      bgPort.onMessage.addListener((msg) => {
        if (!msg || !msg.cmd) return;

        if (msg.cmd === "start_recording") {
          // Session started AND this is the active tab.
          // Do NOT send session_start here — background.js handles it
          // via scripting (exactly 1 event, prevents duplicates from
          // multiple content.js instances in the same tab).
          isRecording = true;
          resetScrollMilestones();

        } else if (msg.cmd === "recording_active") {
          // This tab just became the active tab mid-session
          isRecording = true;

        } else if (msg.cmd === "recording_paused") {
          // Another tab is now active — stop sending events, stay connected
          isRecording = false;

        } else if (msg.cmd === "stop_recording" || msg.cmd === "idle") {
          isRecording = false;
        }
      });

      bgPort.onDisconnect.addListener(() => {
        bgPort      = null;
        isRecording = false;
        // Service worker restarted — reconnect after a short delay
        setTimeout(connectPort, 2000);
      });

    } catch {
      setTimeout(connectPort, 3000);
    }
  }

  connectPort();

  // ─── Send Event via Port ───────────────────────────────────────────────────

  function sendEvent(payload) {
    if (!isRecording || !bgPort) return;
    const clean = {};
    Object.keys(payload).forEach(k => {
      if (payload[k] !== undefined && payload[k] !== null) clean[k] = payload[k];
    });
    try { bgPort.postMessage(clean); } catch {}
  }

  // ─── Helpers ──────────────────────────────────────────────────────────────

  function now()        { return Date.now() / 1000; }
  function pageUrl()    { return location.href; }
  function pageDomain() { return location.hostname; }
  function pageTitle()  { return document.title; }

  function buildPageContext(eventType) {
    const h1   = document.querySelector("h1");
    const meta = document.querySelector('meta[name="description"]');
    return {
      event:             eventType,
      url:               pageUrl(),
      domain:            pageDomain(),
      title:             pageTitle(),
      h1:                h1   ? h1.innerText.trim().slice(0, 200) : "",
      meta_description:  meta ? meta.getAttribute("content") : "",
      referrer:          document.referrer || "",
      timestamp_browser: now(),
    };
  }

  function cssPath(el) {
    const parts = [];
    let cur = el;
    for (let i = 0; i < 6 && cur && cur !== document.body; i++) {
      let seg = cur.tagName.toLowerCase();
      if (cur.id) { parts.unshift(seg + `#${cur.id}`); break; }
      if (cur.className && typeof cur.className === "string")
        seg += cur.className.trim().split(/\s+/).slice(0, 2).map(c => `.${c}`).join("");
      if (cur.parentElement) {
        const sibs = Array.from(cur.parentElement.children).filter(s => s.tagName === cur.tagName);
        if (sibs.length > 1) seg += `:nth-of-type(${sibs.indexOf(cur) + 1})`;
      }
      parts.unshift(seg);
      cur = cur.parentElement;
    }
    return parts.join(" > ");
  }

  function interactiveAncestor(el) {
    const TAGS  = new Set(["a","button","input","select","textarea","label","summary","details","option"]);
    const ROLES = new Set(["button","link","menuitem","menuitemcheckbox","menuitemradio",
                           "tab","option","treeitem","switch","checkbox","radio",
                           "combobox","listbox","slider","spinbutton","gridcell"]);
    let cur = el;
    for (let i = 0; i < 8 && cur && cur !== document.body; i++) {
      if (TAGS.has(cur.tagName.toLowerCase())) return cur;
      const role = (cur.getAttribute("role") || "").toLowerCase();
      if (ROLES.has(role)) return cur;
      if (cur.getAttribute("onclick") !== null || cur.getAttribute("tabindex") !== null) return cur;
      cur = cur.parentElement;
    }
    return el;
  }

  function elementText(el) {
    const labelledBy = el.getAttribute("aria-labelledby");
    if (labelledBy) {
      const ref = document.getElementById(labelledBy.split(" ")[0]);
      if (ref && ref.innerText) return ref.innerText.trim().slice(0, 120);
    }
    const aria = el.getAttribute("aria-label");
    if (aria) return aria.trim().slice(0, 120);
    if (el.getAttribute("alt"))   return el.getAttribute("alt").trim().slice(0, 120);
    if (el.getAttribute("title")) return el.getAttribute("title").trim().slice(0, 120);
    if ((el.tagName === "INPUT" || el.tagName === "SELECT") && el.value) return el.value.slice(0, 80);
    if (el.getAttribute("placeholder")) return `[${el.getAttribute("placeholder").slice(0,60)}]`;
    return (el.innerText || el.textContent || "").trim().replace(/\s+/g," ").slice(0, 120);
  }

  function elementCategory(el) {
    const tag  = el.tagName.toLowerCase();
    const role = (el.getAttribute("role") || "").toLowerCase();
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (role === "tab")             return "tab";
    if (role.includes("menu"))      return "menu";
    if (role === "link" || tag === "a")     return "link";
    if (role === "button" || tag === "button") return "button";
    if (role === "checkbox" || type === "checkbox") return "checkbox";
    if (role === "radio"    || type === "radio")    return "radio";
    if (tag === "select")   return "select";
    if (tag === "input")    return "input";
    if (tag === "textarea") return "textarea";
    if (tag === "label")    return "label";
    if (tag === "summary")  return "accordion";
    if (tag === "li")       return "list-item";
    return "other";
  }

  function describeEl(el) {
    const tag  = el.tagName.toLowerCase();
    const role = (el.getAttribute("role") || "").toLowerCase();
    const text = elementText(el) || "—";
    let kind = tag;
    if      (role === "tab")           kind = "Tab";
    else if (role.includes("menu"))    kind = "Menu item";
    else if (role === "link" || tag === "a")      kind = "Link";
    else if (role === "button" || tag === "button") kind = "Button";
    else if (tag === "select")         kind = "Select";
    else if (tag === "input")          kind = `Input[${el.type || "text"}]`;
    else if (tag === "textarea")       kind = "Textarea";
    else if (tag === "label")          kind = "Label";
    else if (tag === "summary")        kind = "Accordion";
    else if (tag === "li")             kind = "List item";
    else                               kind = tag.charAt(0).toUpperCase() + tag.slice(1);

    let desc = `${kind}: '${text}'`;
    if (tag === "a" && el.href) {
      try { desc += ` → ${new URL(el.href, location.href).pathname}`; } catch {}
    }
    if (el.type === "checkbox" || el.type === "radio") desc += ` [${el.checked ? "✓" : "✗"}]`;
    if (tag === "select" && el.selectedOptions.length) desc += ` → '${el.selectedOptions[0].text}'`;
    return desc;
  }

  function elBase(el) {
    const tag = el.tagName.toLowerCase();
    return {
      url:        pageUrl(),
      domain:     pageDomain(),
      page_title: pageTitle(),
      element_tag:         tag,
      element_type:        el.getAttribute("type") || "",
      element_id:          el.id || "",
      element_classes:     typeof el.className === "string" ? el.className.trim() : "",
      element_name:        el.getAttribute("name") || "",
      element_href:        tag === "a" ? (el.href || "") : "",
      element_role:        el.getAttribute("role") || "",
      element_aria_label:  el.getAttribute("aria-label") || "",
      element_placeholder: el.getAttribute("placeholder") || "",
      element_data_testid: el.getAttribute("data-testid") || "",
      element_text:        elementText(el),
      css_path:            cssPath(el),
      element_category:    elementCategory(el),
      element_description: describeEl(el),
      timestamp_browser:   now(),
    };
  }

  function debounce(fn, ms) {
    let t;
    return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  }

  // ─── 1. CLICK EVENTS ────────────────────────────────────────────────────────

  document.addEventListener("click", (e) => {
    const el  = interactiveAncestor(e.target);
    const tag = el.tagName.toLowerCase();
    sendEvent({
      event: "element_click",
      ...elBase(el),
      element_checked:         (tag === "input" && (el.type === "checkbox" || el.type === "radio")) ? el.checked : undefined,
      element_selected_option: tag === "select" && el.selectedOptions.length ? el.selectedOptions[0].text : undefined,
      click_x:        Math.round(e.clientX),
      click_y:        Math.round(e.clientY),
      page_x:         Math.round(e.pageX),
      page_y:         Math.round(e.pageY),
      viewport_width:  window.innerWidth,
      viewport_height: window.innerHeight,
    });
  }, true);

  // ─── 2. FORM SUBMIT ──────────────────────────────────────────────────────────

  document.addEventListener("submit", (e) => {
    const form = e.target;
    const fields = {};
    Array.from(form.elements).forEach(el => {
      if (!el.name) return;
      if (el.type === "password") { fields[el.name] = "[REDACTED]"; return; }
      if ((el.type === "checkbox" || el.type === "radio") && !el.checked) return;
      if (el.tagName === "SELECT") { fields[el.name] = el.selectedOptions.length ? el.selectedOptions[0].text : ""; return; }
      if (el.value) fields[el.name] = el.value.slice(0, 100);
    });
    sendEvent({
      event:        "form_submit",
      url:          pageUrl(), domain: pageDomain(), page_title: pageTitle(),
      element_id:      form.id || "",
      element_name:    form.getAttribute("name") || "",
      element_classes: typeof form.className === "string" ? form.className.trim() : "",
      css_path:        cssPath(form),
      form_action:     form.action || "",
      form_method:     form.method || "get",
      form_field_count: Object.keys(fields).length,
      form_fields_json: JSON.stringify(fields),
      element_category:    "form",
      element_description: `Form submit: '${form.id || form.getAttribute("name") || "form"}' (${Object.keys(fields).length} fields)`,
      timestamp_browser: now(),
    });
  }, true);

  // ─── 3. INPUT CHANGE ─────────────────────────────────────────────────────────

  document.addEventListener("change", (e) => {
    const el  = e.target;
    const tag = el.tagName.toLowerCase();
    if (!["input","select","textarea"].includes(tag) || el.type === "password") return;

    let newValue = "", desc = "";
    if (el.type === "checkbox") {
      newValue = el.checked ? "checked" : "unchecked";
      desc = `Checkbox '${elementText(el)}' → ${newValue}`;
    } else if (el.type === "radio") {
      newValue = el.value;
      desc = `Radio '${elementText(el)}' → selected`;
    } else if (tag === "select") {
      newValue = el.selectedOptions.length ? el.selectedOptions[0].text : "";
      desc = `Select '${el.name || el.id || "dropdown"}' → '${newValue}'`;
    } else {
      newValue = (el.value || "").slice(0, 100);
      desc = `${tag === "textarea" ? "Textarea" : "Input"} '${el.name || el.placeholder || el.id || "field"}' changed`;
    }

    sendEvent({
      event: "form_input_change",
      ...elBase(el),
      element_new_value:   newValue,
      element_checked:     (el.type === "checkbox" || el.type === "radio") ? el.checked : undefined,
      element_description: desc,
    });
  }, true);

  // ─── 4. FOCUS ─────────────────────────────────────────────────────────────────

  let _lastFocus = null;
  document.addEventListener("focusin", (e) => {
    const el  = e.target;
    const tag = el.tagName.toLowerCase();
    if (!["input","select","textarea"].includes(tag) || el.type === "password") return;
    if (_lastFocus === el) return;
    _lastFocus = el;
    sendEvent({
      event: "focus_input",
      ...elBase(el),
      element_description: `Focus: ${tag} '${el.name || el.placeholder || el.id || "field"}'`,
    });
  }, true);

  // ─── 5. DOM MUTATION OBSERVER ─────────────────────────────────────────────────

  const MODAL_PAT   = /modal|dialog|drawer|overlay|lightbox|panel|sidebar|offcanvas|sheet/i;
  const DROP_PAT    = /dropdown|popover|flyout|submenu|autocomplete|suggest/i;
  const TIP_PAT     = /tooltip|hint/i;
  const NOTIF_PAT   = /toast|notification|snackbar|banner|announcement/i;
  const ERROR_PAT   = /error|invalid|warning|danger|fail/i;
  const LOAD_PAT    = /spinner|skeleton|loader|loading/i;
  const SUCCESS_PAT = /success|valid|done|complete|confirm/i;

  function elKey(el) {
    return `${typeof el.className === "string" ? el.className.trim() : ""} ${el.id || ""}`.toLowerCase();
  }
  function isVisible(el) {
    const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
    if (!r || (r.width === 0 && r.height === 0)) return false;
    const s = window.getComputedStyle(el);
    return s.display !== "none" && s.visibility !== "hidden" && s.opacity !== "0";
  }

  function classifyNew(el) {
    if (el.nodeType !== Node.ELEMENT_NODE) return null;
    const k    = elKey(el);
    const tag  = el.tagName.toLowerCase();
    const role = (el.getAttribute("role") || "").toLowerCase();
    const text = () => (el.innerText || "").trim().slice(0, 100);

    if (tag === "dialog" || role === "dialog" || role === "alertdialog")
      return { sub:"modal_open", desc:`Dialog opened: '${el.getAttribute("aria-label") || el.id || "dialog"}'` };
    if (role === "tooltip" || TIP_PAT.test(k))
      return { sub:"tooltip_show", desc:`Tooltip: '${text()}'` };
    if (DROP_PAT.test(k) || role === "listbox")
      return { sub:"dropdown_open", desc:`Dropdown: '${el.id || el.className.split(" ")[0] || "menu"}'` };
    if (NOTIF_PAT.test(k) || role === "status")
      return { sub:"notification_show", desc:`Notification: '${text()}'` };
    if (role === "alert" || role === "alertdialog")
      return { sub:"alert_show", desc:`Alert: '${text()}'` };
    if (ERROR_PAT.test(k) && text())
      return { sub:"error_show", desc:`Error: '${text()}'` };
    if (SUCCESS_PAT.test(k) && text())
      return { sub:"success_show", desc:`Success: '${text()}'` };
    if (LOAD_PAT.test(k))
      return { sub:"loading_start", desc:`Loading: '${el.id || el.className.split(" ")[0] || "loader"}'` };
    if (MODAL_PAT.test(k) && isVisible(el))
      return { sub:"modal_open", desc:`Overlay: '${el.id || el.className.split(" ")[0] || "overlay"}'` };
    return null;
  }

  function classifyRemoved(el) {
    if (el.nodeType !== Node.ELEMENT_NODE) return null;
    const k    = elKey(el);
    const tag  = el.tagName.toLowerCase();
    const role = (el.getAttribute("role") || "").toLowerCase();
    if (tag === "dialog" || role === "dialog" || role === "alertdialog")
      return { sub:"modal_close", desc:`Dialog closed: '${el.id || "dialog"}'` };
    if (MODAL_PAT.test(k))
      return { sub:"modal_close", desc:`Overlay closed: '${el.id || el.className.split(" ")[0]}'` };
    if (LOAD_PAT.test(k))
      return { sub:"loading_end", desc:"Loading ended" };
    if (DROP_PAT.test(k))
      return { sub:"dropdown_close", desc:"Dropdown closed" };
    return null;
  }

  function sendDomEv(sub, el, desc) {
    sendEvent({
      event:           sub,
      url:             pageUrl(), domain: pageDomain(), page_title: pageTitle(),
      element_tag:     el ? el.tagName.toLowerCase() : "",
      element_id:      el ? (el.id || "") : "",
      element_classes: el ? (typeof el.className === "string" ? el.className.trim() : "") : "",
      element_role:    el ? (el.getAttribute("role") || "") : "",
      element_aria_label: el ? (el.getAttribute("aria-label") || "") : "",
      css_path:        el ? cssPath(el) : "",
      element_category:    sub,
      element_description: desc,
      dom_subtype:         sub,
      timestamp_browser:   now(),
    });
  }

  const _seen = new WeakSet();
  new MutationObserver(debounce((muts) => {
    for (const m of muts) {
      for (const node of m.addedNodes) {
        if (_seen.has(node)) continue;
        const info = classifyNew(node);
        if (info) { _seen.add(node); sendDomEv(info.sub, node, info.desc); }
        if (node.querySelectorAll) {
          node.querySelectorAll("[role='dialog'],[role='alertdialog']").forEach(child => {
            if (!_seen.has(child)) {
              _seen.add(child);
              sendDomEv("modal_open", child, `Dialog (nested): '${child.getAttribute("aria-label") || child.id || "dialog"}'`);
            }
          });
        }
      }
      for (const node of m.removedNodes) {
        const info = classifyRemoved(node);
        if (info) sendDomEv(info.sub, node, info.desc);
      }
      if (m.type === "attributes" && m.target) {
        const el  = m.target;
        const tag = el.tagName.toLowerCase();
        if (m.attributeName === "open") {
          if (tag === "dialog") {
            const open = el.hasAttribute("open");
            sendDomEv(open ? "modal_open" : "modal_close", el,
              `<dialog> ${open ? "opened" : "closed"}: '${el.id || "dialog"}'`);
          }
          if (tag === "details") {
            const open = el.hasAttribute("open");
            const s = (el.querySelector("summary")?.innerText || "").trim().slice(0, 80);
            sendDomEv(open ? "accordion_expand" : "accordion_collapse", el,
              `Accordion ${open ? "expanded" : "collapsed"}: '${s}'`);
          }
        }
        if (m.attributeName === "aria-hidden") {
          const k    = elKey(el);
          const role = (el.getAttribute("role") || "").toLowerCase();
          if (role === "dialog" || MODAL_PAT.test(k)) {
            const hidden = el.getAttribute("aria-hidden");
            sendDomEv(
              (hidden === "false" || !hidden) ? "modal_open" : "modal_close",
              el, `Dialog ${hidden === "true" ? "hidden" : "shown"}: '${el.id || el.className.split(" ")[0]}'`
            );
          }
        }
      }
    }
  }, 80)).observe(document.body || document.documentElement, {
    childList: true, subtree: true, attributes: true,
    attributeFilter: ["aria-hidden", "open", "class", "hidden"],
  });

  // ─── 6. SCROLL MILESTONES ────────────────────────────────────────────────────

  const _scrolled = new Set();
  function resetScrollMilestones() { _scrolled.clear(); }

  window.addEventListener("scroll", debounce(() => {
    const top   = window.scrollY || document.documentElement.scrollTop;
    const total = document.documentElement.scrollHeight - window.innerHeight;
    if (total <= 0) return;
    const pct = Math.round((top / total) * 100);
    for (const m of [25, 50, 75, 100]) {
      if (pct >= m && !_scrolled.has(m)) {
        _scrolled.add(m);
        sendEvent({
          event: "scroll_milestone",
          url: pageUrl(), domain: pageDomain(), page_title: pageTitle(),
          scroll_percent:   m,
          scroll_px:        Math.round(top),
          page_height_px:   Math.round(document.documentElement.scrollHeight),
          element_category: "scroll",
          element_description: `Scrolled ${m}% of page`,
          timestamp_browser: now(),
        });
      }
    }
  }, 250), { passive: true });

  // ─── 7. SPA NAVIGATION ───────────────────────────────────────────────────────

  let lastUrl = location.href;

  function onSPANav() {
    resetScrollMilestones();
    setTimeout(() => sendEvent(buildPageContext("page_update")), 300);
  }

  ["pushState","replaceState"].forEach(method => {
    const orig = history[method];
    history[method] = function (...args) {
      const r = orig.apply(this, args);
      if (location.href !== lastUrl) { lastUrl = location.href; onSPANav(); }
      return r;
    };
  });

  window.addEventListener("popstate", () => {
    if (location.href !== lastUrl) { lastUrl = location.href; onSPANav(); }
  });

})();
