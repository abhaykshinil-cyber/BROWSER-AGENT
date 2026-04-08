/**
 * BrowserAgent — Content Script Orchestrator
 *
 * Lightweight coordinator that ties together the three Phase 2 modules:
 *   • annotation.js  — visual overlays         (window.__BrowserAgentAnnotation)
 *   • dom-sensor.js  — DOM scanning & analysis  (window.__BrowserAgentSensor)
 *   • action-runner.js — action execution       (window.__BrowserAgentRunner)
 *
 * This script:
 *   1. Watches for DOM mutations and reports PAGE_CHANGED to background.
 *   2. Listens for high-level messages from the background service worker
 *      and routes them to the correct module.
 *   3. Sends an initial page context snapshot on load.
 *
 * All content scripts are standard IIFEs (not ES modules) because
 * Manifest V3 content scripts do not support module imports.
 */

/* global chrome */

(function BrowserAgentOrchestrator() {
  "use strict";

  // ── Module References ────────────────────────────────────────────

  var sensor     = function () { return window.__BrowserAgentSensor     || {}; };
  var runner     = function () { return window.__BrowserAgentRunner     || {}; };
  var annotation = function () { return window.__BrowserAgentAnnotation || {}; };

  // ── DOM Mutation Observer ────────────────────────────────────────

  var mutationDebounce = null;
  var lastReportedUrl  = window.location.href;

  var observer = new MutationObserver(function () {
    clearTimeout(mutationDebounce);
    mutationDebounce = setTimeout(function () {
      var currentUrl = window.location.href;
      var urlChanged = currentUrl !== lastReportedUrl;
      lastReportedUrl = currentUrl;

      chrome.runtime.sendMessage({
        type: "PAGE_CHANGED",
        payload: {
          url:          currentUrl,
          title:        document.title,
          url_changed:  urlChanged,
        },
      }).catch(function () {});
    }, 500);
  });

  if (document.documentElement) {
    observer.observe(document.documentElement, {
      childList:  true,
      subtree:    true,
      attributes: false,
    });
  }

  // ── URL Polling (for SPA navigations that don't mutate DOM) ──────

  var lastPolledUrl = window.location.href;
  setInterval(function () {
    var current = window.location.href;
    if (current !== lastPolledUrl) {
      lastPolledUrl = current;
      chrome.runtime.sendMessage({
        type: "PAGE_CHANGED",
        payload: {
          url:         current,
          title:       document.title,
          url_changed: true,
        },
      }).catch(function () {});
    }
  }, 1000);

  // ── Message Router ───────────────────────────────────────────────

  chrome.runtime.onMessage.addListener(function (message, _sender, sendResponse) {
    if (!message || !message.type) return false;

    var type = message.type.toUpperCase();

    switch (type) {
      // ── Scan requests → dom-sensor ──────────────────────────
      case "SCAN_PAGE":
      case "SCAN": {
        var scanFn = sensor().buildScanResult;
        if (!scanFn) {
          sendResponse({ success: false, error: "DOM Sensor module not loaded" });
          return true;
        }
        try {
          var scanData = scanFn();
          // Augment with URL and title
          scanData.url   = window.location.href;
          scanData.title = document.title;
          sendResponse({ success: true, data: scanData });
        } catch (err) {
          sendResponse({ success: false, error: err.message });
        }
        return true;
      }

      // ── Execution requests → action-runner ──────────────────
      case "EXECUTE_ACTION": {
        var execFn = runner().executeAction;
        if (!execFn) {
          sendResponse({ success: false, error: "Action Runner module not loaded" });
          return true;
        }
        execFn(message.payload).then(function (result) {
          sendResponse(result);
        }).catch(function (err) {
          sendResponse({ success: false, error: err.message });
        });
        return true; // keep channel open for async
      }

      // ── Page context snapshot (legacy format for background) ──
      case "GET_PAGE_CONTEXT": {
        var sensorMod = sensor();
        var pageCtx = {
          url:               window.location.href,
          title:             document.title,
          body_text:         sensorMod.extractPageText   ? sensorMod.extractPageText()   : "",
          visible_elements:  sensorMod.scanDOM           ? sensorMod.scanDOM()            : [],
          page_type:         sensorMod.detectPageType     ? sensorMod.detectPageType()     : "general",
          screenshot_base64: null,
          tabs:              [],
        };
        sendResponse({ success: true, data: pageCtx });
        return true;
      }

      // ── Phase 9: Content-script heartbeat ────────────────────
      case "PING":
        sendResponse({ pong: true });
        return true;

      // ── Phase 9: Page text for multi-tab context ──────────────
      case "PAGE_INFO": {
        var bodyText = "";
        try {
          var sensorRef = sensor();
          bodyText = sensorRef.extractPageText ? sensorRef.extractPageText() : (document.body ? document.body.innerText.substring(0, 3000) : "");
        } catch (_e) {
          bodyText = document.body ? document.body.innerText.substring(0, 3000) : "";
        }
        sendResponse({ success: true, data: { body_text: bodyText, url: window.location.href, title: document.title } });
        return true;
      }

      // ── Annotation commands (forwarded if not already handled
      //    by annotation.js's own listener) ────────────────────
      case "HIGHLIGHT":
      case "SHOW_BADGE":
      case "CLEAR_ANNOTATIONS":
      case "SHOW_THINKING":
      case "HIDE_THINKING":
        // These are handled by annotation.js — this listener should
        // not intercept them.  Return false to let the next listener
        // handle it.
        return false;
    }

    return false;
  });

  // ── Initial Load Report ──────────────────────────────────────────

  chrome.runtime.sendMessage({
    type: "PAGE_CONTEXT",
    payload: {
      url:   window.location.href,
      title: document.title,
      event: "content_script_loaded",
    },
  }).catch(function () {});

  console.log("[BrowserAgent] Orchestrator loaded on", window.location.href);
})();
