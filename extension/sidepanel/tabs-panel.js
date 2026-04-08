/**
 * BrowserAgent — Tabs Panel (Phase 9)
 *
 * Collapsible "Open Tabs" widget injected into the Agent tab of the
 * side panel.  Shows all open browser tabs, enables switching between
 * them, and displays cross-tab facts when available.
 *
 * Uses the same CSS variables as sidepanel.css (Phase 8).
 */

/* global chrome */

const API_BASE = "http://localhost:8765";

// ── Helpers ──────────────────────────────────────────────────────────

function esc(str) {
  const el = document.createElement("span");
  el.textContent = str || "";
  return el.innerHTML;
}

function domainOf(url) {
  try {
    return new URL(url).hostname;
  } catch (_) {
    return url ? url.substring(0, 30) : "";
  }
}

// ── State ────────────────────────────────────────────────────────────

let _tabs = [];
let _crossTabFacts = [];
let _crossTabEnabled = true;
let _collapsed = true;
let _container = null;

// ── Public API ───────────────────────────────────────────────────────

/**
 * Initialise the tabs panel inside the given container element.
 * Call this once on DOMContentLoaded.
 *
 * @param {HTMLElement} containerElement
 */
export function initTabsPanel(containerElement) {
  _container = containerElement;
  _render();
  loadTabs();
}

/**
 * Get whether cross-tab context is enabled.
 * @returns {boolean}
 */
export function isCrossTabEnabled() {
  return _crossTabEnabled;
}

/**
 * Get the currently loaded tabs.
 * @returns {Array}
 */
export function getLoadedTabs() {
  return _tabs;
}

/**
 * Get the current cross-tab facts.
 * @returns {Array<string>}
 */
export function getCrossTabFacts() {
  return _crossTabFacts;
}

// ── Tab Loading ──────────────────────────────────────────────────────

export async function loadTabs() {
  try {
    const response = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("Timeout")), 3000);
      chrome.runtime.sendMessage({ type: "GET_ALL_TABS" }, (r) => {
        clearTimeout(timer);
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
          return;
        }
        resolve(r);
      });
    });

    if (response && Array.isArray(response.tabs)) {
      _tabs = response.tabs;
    } else if (response && Array.isArray(response)) {
      _tabs = response;
    } else {
      // Fallback: query tabs directly (works from side panel context)
      _tabs = await _queryTabsDirect();
    }
  } catch (e) {
    console.warn("[TabsPanel] loadTabs failed:", e.message);
    _tabs = await _queryTabsDirect();
  }

  renderTabList();
}

async function _queryTabsDirect() {
  try {
    const raw = await chrome.tabs.query({});
    return raw
      .filter((t) => t.url && !t.url.startsWith("chrome://") && !t.url.startsWith("chrome-extension://"))
      .map((t) => ({
        tabId:      t.id,
        title:      t.title || "",
        url:        t.url || "",
        active:     !!t.active,
        pinned:     !!t.pinned,
        favIconUrl: t.favIconUrl || "",
        windowId:   t.windowId,
      }));
  } catch (_) {
    return [];
  }
}

// ── Rendering ────────────────────────────────────────────────────────

function _render() {
  if (!_container) return;

  _container.innerHTML = `
    <div class="tp-section">
      <button class="tp-header" id="tp-toggle">
        <span class="tp-arrow">▶</span>
        <span class="tp-header-text">Open Tabs (<span id="tp-count">0</span>)</span>
      </button>

      <div class="tp-body" id="tp-body">
        <div class="tp-controls">
          <label class="tp-toggle-label">
            <input type="checkbox" id="tp-cross-tab" checked>
            <span>Use Cross-Tab Context</span>
          </label>
          <button class="btn btn-ghost btn-sm" id="tp-refresh">↻ Refresh</button>
        </div>

        <div class="tp-list" id="tp-list"></div>

        <div class="tp-facts-section" id="tp-facts-section" style="display:none;">
          <button class="tp-facts-toggle" id="tp-facts-toggle">
            <span class="tp-arrow tp-facts-arrow">▶</span>
            <span>Facts from other tabs</span>
          </button>
          <div class="tp-facts-body" id="tp-facts-body"></div>
        </div>
      </div>
    </div>
  `;

  // Inject styles (scoped to .tp- prefix)
  _injectStyles();

  // ── Event listeners
  const toggle = _container.querySelector("#tp-toggle");
  const body   = _container.querySelector("#tp-body");
  const arrow  = toggle?.querySelector(".tp-arrow");

  toggle?.addEventListener("click", () => {
    _collapsed = !_collapsed;
    if (body) body.style.display = _collapsed ? "none" : "block";
    if (arrow) arrow.textContent = _collapsed ? "▶" : "▼";
  });

  // Cross-tab toggle
  const crossCheck = _container.querySelector("#tp-cross-tab");
  crossCheck?.addEventListener("change", () => {
    _crossTabEnabled = crossCheck.checked;
  });

  // Refresh
  _container.querySelector("#tp-refresh")?.addEventListener("click", () => loadTabs());

  // Facts toggle
  const factsToggle = _container.querySelector("#tp-facts-toggle");
  const factsBody   = _container.querySelector("#tp-facts-body");
  const factsArrow  = factsToggle?.querySelector(".tp-facts-arrow");
  let factsOpen = false;

  factsToggle?.addEventListener("click", () => {
    factsOpen = !factsOpen;
    if (factsBody) factsBody.style.display = factsOpen ? "block" : "none";
    if (factsArrow) factsArrow.textContent = factsOpen ? "▼" : "▶";
  });

  // Initialize collapsed state
  if (body) body.style.display = "none";
}

function renderTabList() {
  const list    = _container?.querySelector("#tp-list");
  const countEl = _container?.querySelector("#tp-count");
  if (!list) return;

  if (countEl) countEl.textContent = _tabs.length;

  if (_tabs.length === 0) {
    list.innerHTML = '<div class="tp-empty">No tabs detected</div>';
    return;
  }

  list.innerHTML = _tabs.map((tab) => {
    const domain = domainOf(tab.url);
    const title  = (tab.title || "Untitled").substring(0, 40);
    const isActive = tab.active;

    const favicon = tab.favIconUrl
      ? `<img class="tp-favicon" src="${esc(tab.favIconUrl)}" alt="" onerror="this.style.display='none'">`
      : '<span class="tp-favicon-default">○</span>';

    return `
      <div class="tp-tab-card ${isActive ? "tp-tab-active" : ""}" data-tabid="${tab.tabId}">
        <div class="tp-tab-left">
          ${favicon}
          <div class="tp-tab-info">
            <span class="tp-tab-title">${esc(title)}</span>
            <span class="tp-tab-domain">${esc(domain)}</span>
          </div>
        </div>
        <div class="tp-tab-right">
          ${isActive ? '<span class="tp-active-badge">Active</span>' : `<button class="tp-switch-btn" data-tabid="${tab.tabId}">Switch</button>`}
        </div>
      </div>
    `;
  }).join("");

  // Attach switch listeners
  list.querySelectorAll(".tp-switch-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      onTabSwitch(parseInt(btn.dataset.tabid, 10));
    });
  });
}

/**
 * Render cross-tab facts extracted by the server.
 *
 * @param {string[]} facts
 */
export function renderCrossTabFacts(facts) {
  _crossTabFacts = facts || [];

  const section = _container?.querySelector("#tp-facts-section");
  const body    = _container?.querySelector("#tp-facts-body");
  if (!section || !body) return;

  if (_crossTabFacts.length === 0) {
    section.style.display = "none";
    return;
  }

  section.style.display = "block";
  body.innerHTML = _crossTabFacts.map((fact) => {
    // Parse "From [Tab Title]: sentence"
    const match = fact.match(/^From \[(.+?)\]: (.+)$/);
    const source = match ? match[1] : "Other tab";
    const text   = match ? match[2] : fact;

    return `
      <div class="tp-fact">
        <span class="tp-fact-source">${esc(source)}</span>
        <span class="tp-fact-text">"${esc(text)}"</span>
      </div>
    `;
  }).join("");
}

// ── Tab Switching ────────────────────────────────────────────────────

async function onTabSwitch(tabId) {
  try {
    // Try via background message first
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("Timeout")), 2000);
      chrome.runtime.sendMessage({ type: "SWITCH_TAB", payload: { tabId } }, (r) => {
        clearTimeout(timer);
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
          return;
        }
        resolve(r);
      });
    });
  } catch (_) {
    // Direct fallback
    try {
      await chrome.tabs.update(tabId, { active: true });
      const tab = await chrome.tabs.get(tabId);
      if (tab?.windowId) {
        await chrome.windows.update(tab.windowId, { focused: true });
      }
    } catch (e) {
      console.warn("[TabsPanel] switchToTab failed:", e.message);
    }
  }

  // Refresh the list after a short delay
  setTimeout(() => loadTabs(), 300);

  // Highlight the new active tab
  highlightActiveTab(tabId);
}

/**
 * Visually highlight the card for a specific tabId.
 *
 * @param {number} tabId
 */
export function highlightActiveTab(tabId) {
  if (!_container) return;

  _container.querySelectorAll(".tp-tab-card").forEach((card) => {
    const cardTabId = parseInt(card.dataset.tabid, 10);
    card.classList.toggle("tp-tab-active", cardTabId === tabId);
  });
}

// ── Scoped Styles ────────────────────────────────────────────────────

function _injectStyles() {
  if (document.getElementById("tp-styles")) return;

  const style = document.createElement("style");
  style.id = "tp-styles";
  style.textContent = `
    .tp-section {
      border: 1px solid var(--border, #393836);
      border-radius: var(--radius-md, 6px);
      overflow: hidden;
      background: var(--surf, #1c1b19);
    }

    .tp-header {
      display: flex;
      align-items: center;
      gap: 6px;
      width: 100%;
      padding: 7px 10px;
      background: none;
      border: none;
      color: var(--muted, #797876);
      font: inherit;
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.3px;
      cursor: pointer;
      text-align: left;
    }
    .tp-header:hover { color: var(--text, #cdccca); }

    .tp-arrow {
      font-size: 9px;
      transition: transform 0.15s ease;
      width: 10px;
      text-align: center;
    }

    .tp-body {
      padding: 8px 10px;
      display: flex;
      flex-direction: column;
      gap: 8px;
      border-top: 1px solid var(--border, #393836);
    }

    .tp-controls {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }

    .tp-toggle-label {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 11px;
      color: var(--muted, #797876);
      cursor: pointer;
    }
    .tp-toggle-label input[type="checkbox"] {
      accent-color: var(--teal, #4f98a3);
      width: 13px; height: 13px;
      cursor: pointer;
    }

    .tp-list {
      display: flex;
      flex-direction: column;
      gap: 4px;
      max-height: 200px;
      overflow-y: auto;
    }

    .tp-tab-card {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 5px 8px;
      border-radius: var(--radius-sm, 4px);
      border-left: 2px solid transparent;
      transition: all 0.15s ease;
      cursor: default;
    }
    .tp-tab-card:hover {
      background: var(--surf2, #201f1d);
    }
    .tp-tab-card.tp-tab-active {
      border-left-color: var(--teal, #4f98a3);
      background: rgba(79,152,163,0.06);
    }

    .tp-tab-left {
      display: flex;
      align-items: center;
      gap: 6px;
      overflow: hidden;
      flex: 1;
      min-width: 0;
    }

    .tp-favicon {
      width: 14px; height: 14px;
      border-radius: 2px;
      flex-shrink: 0;
      object-fit: contain;
    }
    .tp-favicon-default {
      width: 14px; height: 14px;
      display: flex; align-items: center; justify-content: center;
      font-size: 10px;
      color: var(--faint, #5a5957);
      flex-shrink: 0;
    }

    .tp-tab-info {
      display: flex;
      flex-direction: column;
      overflow: hidden;
      min-width: 0;
    }

    .tp-tab-title {
      font-size: 11px;
      color: var(--text, #cdccca);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .tp-tab-domain {
      font-size: 9px;
      color: var(--faint, #5a5957);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .tp-tab-right {
      flex-shrink: 0;
      margin-left: 6px;
    }

    .tp-active-badge {
      font-size: 9px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.3px;
      color: var(--teal, #4f98a3);
      background: rgba(79,152,163,0.12);
      padding: 2px 6px;
      border-radius: 9999px;
    }

    .tp-switch-btn {
      font-size: 10px;
      padding: 2px 8px;
      border: 1px solid var(--border, #393836);
      border-radius: var(--radius-sm, 4px);
      background: none;
      color: var(--muted, #797876);
      cursor: pointer;
      transition: all 0.15s ease;
      font-family: inherit;
    }
    .tp-switch-btn:hover {
      border-color: var(--teal, #4f98a3);
      color: var(--teal, #4f98a3);
      background: rgba(79,152,163,0.06);
    }

    .tp-empty {
      font-size: 11px;
      color: var(--faint, #5a5957);
      text-align: center;
      padding: 10px 0;
    }

    /* Facts section */
    .tp-facts-section {
      border-top: 1px solid var(--border, #393836);
      padding-top: 6px;
    }

    .tp-facts-toggle {
      display: flex;
      align-items: center;
      gap: 5px;
      width: 100%;
      padding: 4px 0;
      background: none;
      border: none;
      color: var(--muted, #797876);
      font: inherit;
      font-size: 10px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.3px;
      cursor: pointer;
      text-align: left;
    }
    .tp-facts-toggle:hover { color: var(--text, #cdccca); }

    .tp-facts-body {
      display: none;
      flex-direction: column;
      gap: 4px;
      padding-top: 4px;
    }

    .tp-fact {
      display: flex;
      flex-direction: column;
      gap: 1px;
      padding: 4px 8px;
      border-left: 2px solid var(--gold, #e8af34);
      font-size: 10px;
    }

    .tp-fact-source {
      color: var(--gold, #e8af34);
      font-weight: 600;
      font-size: 9px;
      text-transform: uppercase;
      letter-spacing: 0.2px;
    }

    .tp-fact-text {
      color: var(--muted, #797876);
      font-style: italic;
      line-height: 1.4;
    }
  `;

  document.head.appendChild(style);
}
