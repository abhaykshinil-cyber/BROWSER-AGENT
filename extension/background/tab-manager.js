/**
 * BrowserAgent — Tab Manager (Phase 9)
 *
 * Complete multi-tab management for the background service worker.
 * Provides: listing, scanning, context building, tab lifecycle,
 * screenshot capture, content-script injection, and tab-change
 * monitoring.
 *
 * Runs in the Manifest V3 service worker (module context).
 */

/* global chrome */

// ── Constants ────────────────────────────────────────────────────────

const PING_TIMEOUT_MS        = 500;
const SCAN_TIMEOUT_MS        = 5000;
const PAGE_TEXT_MAX_CHARS     = 3000;
const DOM_SUMMARY_MAX_ELEMS  = 20;
const MAX_SUPPORTING_TABS    = 5;

const SKIP_URL_PREFIXES = [
  "chrome://",
  "chrome-extension://",
  "about:",
  "edge://",
  "brave://",
  "devtools://",
  "view-source:",
];

const CONTENT_SCRIPTS = [
  "content/annotation.js",
  "content/dom-sensor.js",
  "content/action-runner.js",
  "content/mcq-detector.js",
  "content/content.js",
];

// ── Tab Listing ──────────────────────────────────────────────────────

/**
 * Get all open browser tabs with basic metadata.
 * Excludes internal browser URLs.  Active tab sorted first,
 * then by lastAccessed descending.
 *
 * @returns {Promise<Array<{
 *   tabId: number, title: string, url: string,
 *   active: boolean, pinned: boolean, audible: boolean,
 *   favIconUrl: string, windowId: number
 * }>>}
 */
export async function getAllOpenTabs() {
  const raw = await chrome.tabs.query({});

  const filtered = raw.filter((t) => {
    if (!t.url) return false;
    return !SKIP_URL_PREFIXES.some((p) => t.url.startsWith(p));
  });

  const mapped = filtered.map((t) => ({
    tabId:      t.id,
    title:      t.title || "",
    url:        t.url || "",
    active:     !!t.active,
    pinned:     !!t.pinned,
    audible:    !!t.audible,
    favIconUrl: t.favIconUrl || "",
    windowId:   t.windowId,
    _lastAccessed: t.lastAccessed || 0,
  }));

  // Sort: active first, then most-recently-accessed
  mapped.sort((a, b) => {
    if (a.active && !b.active) return -1;
    if (!a.active && b.active) return 1;
    return (b._lastAccessed || 0) - (a._lastAccessed || 0);
  });

  // Strip internal sort key
  return mapped.map(({ _lastAccessed, ...rest }) => rest);
}

// ── Tab DOM Summary ──────────────────────────────────────────────────

/**
 * Get a compact DOM summary from a tab's content script.
 *
 * @param {number} tabId
 * @returns {Promise<{
 *   tabId: number, title: string, url: string,
 *   page_type: string, question_count: number,
 *   interactive_element_count: number,
 *   dom_snapshot_short: Array, error?: string
 * }>}
 */
export async function getTabDOMSummary(tabId) {
  // Baseline info from chrome.tabs API
  let tabInfo;
  try {
    tabInfo = await chrome.tabs.get(tabId);
  } catch (e) {
    return { tabId, title: "", url: "", page_type: "unknown", question_count: 0, interactive_element_count: 0, dom_snapshot_short: [], error: "Tab not found" };
  }

  const base = {
    tabId,
    title:      tabInfo.title || "",
    url:        tabInfo.url || "",
    page_type:  "general",
    question_count: 0,
    interactive_element_count: 0,
    dom_snapshot_short: [],
  };

  // Skip restricted URLs
  if (SKIP_URL_PREFIXES.some((p) => (tabInfo.url || "").startsWith(p))) {
    return { ...base, error: "Restricted URL — cannot access" };
  }

  // Try scanning via content script
  let response = await _sendToTabSafe(tabId, { type: "SCAN" });

  // If content script not responding, inject and retry
  if (!response || !response.success) {
    const injected = await injectContentScriptIfNeeded(tabId);
    if (injected) {
      // Brief wait for scripts to initialise
      await _sleep(300);
      response = await _sendToTabSafe(tabId, { type: "SCAN" });
    }
  }

  if (!response || !response.success || !response.data) {
    return { ...base, error: "Could not access tab" };
  }

  const data = response.data;
  const snapshot = Array.isArray(data.dom_snapshot) ? data.dom_snapshot : [];
  const questions = Array.isArray(data.mcq_questions) ? data.mcq_questions : [];

  return {
    tabId,
    title:                     data.title || tabInfo.title || "",
    url:                       data.url || tabInfo.url || "",
    page_type:                 data.page_type || "general",
    question_count:            questions.length,
    interactive_element_count: snapshot.length,
    dom_snapshot_short:        snapshot.slice(0, DOM_SUMMARY_MAX_ELEMS),
  };
}

// ── Tab Page Text ────────────────────────────────────────────────────

/**
 * Get the body text from a tab's content script.
 *
 * @param {number} tabId
 * @returns {Promise<string>}
 */
export async function getTabPageText(tabId) {
  let response = await _sendToTabSafe(tabId, { type: "GET_PAGE_CONTEXT" });

  if (!response || !response.success) {
    const injected = await injectContentScriptIfNeeded(tabId);
    if (injected) {
      await _sleep(300);
      response = await _sendToTabSafe(tabId, { type: "GET_PAGE_CONTEXT" });
    }
  }

  if (!response || !response.success || !response.data) return "";

  const text = response.data.body_text || "";
  return text.substring(0, PAGE_TEXT_MAX_CHARS);
}

// ── Build Full Multi-Tab Context ─────────────────────────────────────

/**
 * Build a complete multi-tab context for the planner.
 *
 * @param {number} activeTabId
 * @returns {Promise<{
 *   active_tab: object,
 *   supporting_tabs: object[],
 *   total_tabs_open: number
 * }>}
 */
export async function buildFullContext(activeTabId) {
  const allTabs = await getAllOpenTabs();

  // Full DOM summary for the active tab
  const activeTab = await getTabDOMSummary(activeTabId);

  // Get page text for up to MAX_SUPPORTING_TABS other tabs
  const otherTabs = allTabs.filter((t) => t.tabId !== activeTabId);
  const recentTabs = otherTabs.slice(0, MAX_SUPPORTING_TABS);

  const supportingTabs = [];
  for (const tab of recentTabs) {
    try {
      const summary = await getTabDOMSummary(tab.tabId);
      const text = await getTabPageText(tab.tabId);
      supportingTabs.push({
        ...summary,
        body_text_preview: text.substring(0, 1000),
      });
    } catch (e) {
      supportingTabs.push({
        tabId:   tab.tabId,
        title:   tab.title,
        url:     tab.url,
        error:   e.message || "Failed to scan",
        body_text_preview: "",
      });
    }
  }

  return {
    active_tab:      activeTab,
    supporting_tabs: supportingTabs,
    total_tabs_open: allTabs.length,
  };
}

// ── Watch Tab Changes ────────────────────────────────────────────────

/**
 * Register listeners for tab lifecycle events.
 *
 * @param {function({event_type: string, tab: object}): void} callback
 */
export function watchTabChanges(callback) {
  chrome.tabs.onCreated.addListener((tab) => {
    callback({ event_type: "created", tab: _tabInfo(tab) });
  });

  chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
    if (changeInfo.status === "complete" || changeInfo.title) {
      callback({ event_type: "updated", tab: _tabInfo(tab) });
    }
  });

  chrome.tabs.onRemoved.addListener((tabId, removeInfo) => {
    callback({
      event_type: "removed",
      tab: { tabId, windowId: removeInfo.windowId },
    });
  });

  chrome.tabs.onActivated.addListener((activeInfo) => {
    chrome.tabs.get(activeInfo.tabId, (tab) => {
      if (chrome.runtime.lastError) {
        callback({ event_type: "activated", tab: { tabId: activeInfo.tabId } });
        return;
      }
      callback({ event_type: "activated", tab: _tabInfo(tab) });
    });
  });
}

// ── Tab Lifecycle ────────────────────────────────────────────────────

/**
 * Switch to (activate) a specific tab and focus its window.
 *
 * @param {number} tabId
 * @returns {Promise<void>}
 */
export async function switchToTab(tabId) {
  try {
    const tab = await chrome.tabs.update(tabId, { active: true });
    if (tab && tab.windowId) {
      await chrome.windows.update(tab.windowId, { focused: true });
    }
  } catch (e) {
    console.warn("[TabManager] switchToTab failed:", e.message);
  }
}

/**
 * Open a URL in a new tab.
 *
 * @param {string}  url
 * @param {boolean} background  If true, don't activate the new tab.
 * @returns {Promise<chrome.tabs.Tab>}
 */
export async function openUrl(url, background = false) {
  return chrome.tabs.create({ url, active: !background });
}

/**
 * Close a tab by its ID.
 *
 * @param {number} tabId
 * @returns {Promise<void>}
 */
export async function closeTab(tabId) {
  try {
    await chrome.tabs.remove(tabId);
  } catch (e) {
    console.warn("[TabManager] closeTab failed:", e.message);
  }
}

// ── Screenshot ───────────────────────────────────────────────────────

/**
 * Capture a JPEG screenshot of a specific tab.
 *
 * If the tab is not currently active, it is temporarily activated,
 * the screenshot is taken, and the originally active tab is restored.
 *
 * @param {number} tabId
 * @returns {Promise<string|null>}  Base64 JPEG string or null.
 */
export async function captureTabScreenshot(tabId) {
  try {
    const targetTab = await chrome.tabs.get(tabId);
    if (!targetTab || !targetTab.windowId) return null;

    // Remember the currently active tab in the target's window
    const [currentActive] = await chrome.tabs.query({
      active: true,
      windowId: targetTab.windowId,
    });
    const needsSwitch = currentActive && currentActive.id !== tabId;

    // Activate target tab if needed
    if (needsSwitch) {
      await chrome.tabs.update(tabId, { active: true });
      await _sleep(200);  // let the tab render
    }

    const dataUrl = await chrome.tabs.captureVisibleTab(targetTab.windowId, {
      format: "jpeg",
      quality: 70,
    });

    // Restore original active tab
    if (needsSwitch && currentActive) {
      await chrome.tabs.update(currentActive.id, { active: true });
    }

    if (dataUrl && dataUrl.startsWith("data:")) {
      return dataUrl.split(",")[1] || null;
    }
    return dataUrl || null;
  } catch (e) {
    console.warn("[TabManager] captureTabScreenshot failed:", e.message);
    return null;
  }
}

// ── Content Script Injection ─────────────────────────────────────────

/**
 * Check if a content script is present on the tab and inject if not.
 *
 * @param {number} tabId
 * @returns {Promise<boolean>}  True if script is (now) present.
 */
export async function injectContentScriptIfNeeded(tabId) {
  // Quick ping to check if the content script is already there
  const alive = await _pingContentScript(tabId);
  if (alive) return true;

  // Verify we can script this tab
  let tabInfo;
  try {
    tabInfo = await chrome.tabs.get(tabId);
  } catch (e) {
    return false;
  }

  if (!tabInfo.url || SKIP_URL_PREFIXES.some((p) => tabInfo.url.startsWith(p))) {
    return false;
  }

  // Inject all content scripts
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      files: CONTENT_SCRIPTS,
    });
    // Wait for initialisation
    await _sleep(200);
    return true;
  } catch (e) {
    console.warn("[TabManager] Injection failed for tab", tabId, ":", e.message);
    return false;
  }
}

// ── Internal Helpers ─────────────────────────────────────────────────

/**
 * Send a message to a tab's content script, returning null on any error.
 */
function _sendToTabSafe(tabId, message) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(null), SCAN_TIMEOUT_MS);
    try {
      chrome.tabs.sendMessage(tabId, message, (response) => {
        clearTimeout(timer);
        if (chrome.runtime.lastError) {
          resolve(null);
          return;
        }
        resolve(response);
      });
    } catch (e) {
      clearTimeout(timer);
      resolve(null);
    }
  });
}

/**
 * Ping the content script on a tab within PING_TIMEOUT_MS.
 * Returns true if the script responds.
 */
function _pingContentScript(tabId) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(false), PING_TIMEOUT_MS);
    try {
      chrome.tabs.sendMessage(tabId, { type: "PING" }, (response) => {
        clearTimeout(timer);
        if (chrome.runtime.lastError) {
          resolve(false);
          return;
        }
        resolve(true);
      });
    } catch (e) {
      clearTimeout(timer);
      resolve(false);
    }
  });
}

/**
 * Convert a chrome.tabs.Tab to a compact TabInfo object.
 */
function _tabInfo(tab) {
  return {
    tabId:      tab.id,
    title:      tab.title || "",
    url:        tab.url || "",
    active:     !!tab.active,
    pinned:     !!tab.pinned,
    audible:    !!tab.audible,
    favIconUrl: tab.favIconUrl || "",
    windowId:   tab.windowId,
  };
}

function _sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}
