/**
 * BrowserAgent — Annotation Overlay
 *
 * Visual helper layer injected into every page.  Provides highlight
 * rings, floating action badges, and an "Agent is working…" indicator.
 *
 * All DOM nodes created by this module live inside a dedicated shadow-
 * DOM host (<browseragent-overlay>) to avoid style collisions with
 * the host page.
 *
 * Public API (via window.__BrowserAgentAnnotation):
 *   highlightElement(selector, color, durationMs)
 *   showActionBadge(selector, text)
 *   clearAllAnnotations()
 *   showAgentThinking()
 *   hideAgentThinking()
 *
 * Pure vanilla JS — no external dependencies.
 */

/* global chrome */

(function BrowserAgentAnnotation() {
  "use strict";

  // ── Shadow-DOM Host ──────────────────────────────────────────────

  var HOST_TAG = "browseragent-overlay";
  var host = document.createElement(HOST_TAG);
  host.style.cssText = "position:fixed;top:0;left:0;width:0;height:0;z-index:2147483647;pointer-events:none;";
  (document.body || document.documentElement).appendChild(host);

  var shadow = host.attachShadow({ mode: "open" });

  // Inject internal styles
  var styleEl = document.createElement("style");
  styleEl.textContent = [
    /* ── Highlight Ring ─────────────────────────────────────────── */
    ".ba-highlight {",
    "  position: fixed;",
    "  pointer-events: none;",
    "  border-radius: 4px;",
    "  box-sizing: border-box;",
    "  transition: opacity 0.2s ease, outline-color 0.2s ease;",
    "  z-index: 2147483646;",
    "}",

    /* ── Action Badge ───────────────────────────────────────────── */
    ".ba-badge {",
    "  position: fixed;",
    "  pointer-events: none;",
    "  padding: 4px 10px;",
    "  border-radius: 6px;",
    "  font: 600 11px/1.3 'Inter', 'Segoe UI', system-ui, sans-serif;",
    "  color: #fff;",
    "  background: rgba(30, 27, 75, 0.92);",
    "  box-shadow: 0 2px 10px rgba(0,0,0,0.3);",
    "  white-space: nowrap;",
    "  max-width: 260px;",
    "  overflow: hidden;",
    "  text-overflow: ellipsis;",
    "  opacity: 0;",
    "  transform: translateY(4px);",
    "  transition: opacity 0.18s ease, transform 0.18s ease;",
    "  z-index: 2147483647;",
    "}",
    ".ba-badge.visible {",
    "  opacity: 1;",
    "  transform: translateY(0);",
    "}",

    /* ── Agent Thinking Overlay ──────────────────────────────────── */
    ".ba-thinking {",
    "  position: fixed;",
    "  top: 16px;",
    "  right: 16px;",
    "  display: flex;",
    "  align-items: center;",
    "  gap: 8px;",
    "  padding: 8px 16px;",
    "  border-radius: 10px;",
    "  background: rgba(15, 14, 23, 0.90);",
    "  border: 1px solid rgba(99, 102, 241, 0.35);",
    "  box-shadow: 0 4px 20px rgba(0,0,0,0.35), 0 0 30px rgba(99,102,241,0.12);",
    "  font: 500 12px/1.4 'Inter', 'Segoe UI', system-ui, sans-serif;",
    "  color: #e0e7ff;",
    "  z-index: 2147483647;",
    "  opacity: 0;",
    "  transform: translateY(-8px);",
    "  transition: opacity 0.25s ease, transform 0.25s ease;",
    "  pointer-events: none;",
    "}",
    ".ba-thinking.visible {",
    "  opacity: 1;",
    "  transform: translateY(0);",
    "}",
    ".ba-thinking-dot {",
    "  width: 8px;",
    "  height: 8px;",
    "  border-radius: 50%;",
    "  background: #6366f1;",
    "  animation: ba-pulse 1.4s ease-in-out infinite;",
    "}",
    ".ba-thinking-dot:nth-child(2) { animation-delay: 0.2s; }",
    ".ba-thinking-dot:nth-child(3) { animation-delay: 0.4s; }",
    "@keyframes ba-pulse {",
    "  0%, 80%, 100% { opacity: 0.25; transform: scale(0.8); }",
    "  40% { opacity: 1; transform: scale(1.1); }",
    "}",
  ].join("\n");
  shadow.appendChild(styleEl);

  // Container for all dynamic overlays
  var container = document.createElement("div");
  container.id = "ba-root";
  shadow.appendChild(container);

  // ── Internal State ───────────────────────────────────────────────

  var activeHighlights = [];   // [{ node, timer }]
  var activeBadges     = [];   // [{ node, timer }]
  var thinkingNode     = null;

  // ── highlightElement ─────────────────────────────────────────────

  /**
   * Draw a coloured outline ring around the element matched by `selector`.
   *
   * @param {string} selector   CSS selector for the target element.
   * @param {string} color      Outline colour (CSS value).  Default: "#4f98a3".
   * @param {number} durationMs How long to show the highlight.  Default: 1200ms.
   */
  function highlightElement(selector, color, durationMs) {
    color      = color      || "#4f98a3";
    durationMs = durationMs || 1200;

    var el;
    try { el = document.querySelector(selector); } catch (_) { return; }
    if (!el) return;

    var rect = el.getBoundingClientRect();

    var ring = document.createElement("div");
    ring.className = "ba-highlight";
    ring.style.cssText = [
      "top:"    + (rect.top  - 3) + "px;",
      "left:"   + (rect.left - 3) + "px;",
      "width:"  + (rect.width  + 6) + "px;",
      "height:" + (rect.height + 6) + "px;",
      "outline: 3px solid " + color + ";",
      "outline-offset: 0px;",
    ].join(" ");

    container.appendChild(ring);

    var timer = setTimeout(function () {
      ring.style.opacity = "0";
      setTimeout(function () { ring.remove(); }, 250);
    }, durationMs);

    activeHighlights.push({ node: ring, timer: timer });
  }

  // ── showActionBadge ──────────────────────────────────────────────

  /**
   * Show a small floating badge near the target element.
   *
   * @param {string} selector  CSS selector for the target element.
   * @param {string} text      Badge text (e.g. 'Clicked', 'Typed "hello"').
   */
  function showActionBadge(selector, text) {
    var el;
    try { el = document.querySelector(selector); } catch (_) { return; }
    if (!el) return;

    var rect = el.getBoundingClientRect();

    var badge = document.createElement("div");
    badge.className = "ba-badge";
    badge.textContent = text || "";

    // Position above the element, centred horizontally
    var badgeTop  = Math.max(4, rect.top - 30);
    var badgeLeft = rect.left + rect.width / 2;

    badge.style.top  = badgeTop  + "px";
    badge.style.left = badgeLeft + "px";
    badge.style.transform = "translate(-50%, 0) translateY(4px)";

    container.appendChild(badge);

    // Trigger entrance animation on the next frame
    requestAnimationFrame(function () {
      badge.classList.add("visible");
      badge.style.transform = "translate(-50%, 0) translateY(0)";
    });

    var timer = setTimeout(function () {
      badge.classList.remove("visible");
      badge.style.opacity = "0";
      badge.style.transform = "translate(-50%, 0) translateY(-4px)";
      setTimeout(function () { badge.remove(); }, 250);
    }, 2000);

    activeBadges.push({ node: badge, timer: timer });
  }

  // ── clearAllAnnotations ──────────────────────────────────────────

  /**
   * Remove every highlight, badge, and the thinking indicator.
   */
  function clearAllAnnotations() {
    var i;
    for (i = 0; i < activeHighlights.length; i++) {
      clearTimeout(activeHighlights[i].timer);
      activeHighlights[i].node.remove();
    }
    activeHighlights = [];

    for (i = 0; i < activeBadges.length; i++) {
      clearTimeout(activeBadges[i].timer);
      activeBadges[i].node.remove();
    }
    activeBadges = [];

    hideAgentThinking();
  }

  // ── showAgentThinking ────────────────────────────────────────────

  /**
   * Show a subtle pulsing "Agent is working…" indicator in the
   * top-right corner of the viewport.
   */
  function showAgentThinking() {
    if (thinkingNode) return; // already visible

    var overlay = document.createElement("div");
    overlay.className = "ba-thinking";
    overlay.innerHTML = [
      '<span class="ba-thinking-dot"></span>',
      '<span class="ba-thinking-dot"></span>',
      '<span class="ba-thinking-dot"></span>',
      '<span style="margin-left:4px;">Agent is working\u2026</span>',
    ].join("");

    container.appendChild(overlay);

    requestAnimationFrame(function () {
      overlay.classList.add("visible");
    });

    thinkingNode = overlay;
  }

  // ── hideAgentThinking ────────────────────────────────────────────

  /**
   * Remove the "Agent is working…" indicator.
   */
  function hideAgentThinking() {
    if (!thinkingNode) return;

    var node = thinkingNode;
    thinkingNode = null;

    node.classList.remove("visible");
    node.style.opacity = "0";
    node.style.transform = "translateY(-8px)";

    setTimeout(function () { node.remove(); }, 300);
  }

  // ── Message Handler ──────────────────────────────────────────────

  chrome.runtime.onMessage.addListener(function (message, _sender, sendResponse) {
    if (!message) return false;
    var type = (message.type || "").toUpperCase();

    switch (type) {
      case "HIGHLIGHT":
        highlightElement(
          message.selector || (message.payload && message.payload.selector),
          message.color    || (message.payload && message.payload.color),
          message.duration || (message.payload && message.payload.duration)
        );
        sendResponse({ success: true });
        return true;

      case "SHOW_BADGE":
        showActionBadge(
          message.selector || (message.payload && message.payload.selector),
          message.text     || (message.payload && message.payload.text)
        );
        sendResponse({ success: true });
        return true;

      case "CLEAR_ANNOTATIONS":
        clearAllAnnotations();
        sendResponse({ success: true });
        return true;

      case "SHOW_THINKING":
        showAgentThinking();
        sendResponse({ success: true });
        return true;

      case "HIDE_THINKING":
        hideAgentThinking();
        sendResponse({ success: true });
        return true;
    }

    return false;
  });

  // ── Expose Public API ────────────────────────────────────────────

  window.__BrowserAgentAnnotation = {
    highlightElement:    highlightElement,
    showActionBadge:     showActionBadge,
    clearAllAnnotations: clearAllAnnotations,
    showAgentThinking:   showAgentThinking,
    hideAgentThinking:   hideAgentThinking,
  };

  console.log("[BrowserAgent:Annotation] Loaded on", window.location.href);
})();
