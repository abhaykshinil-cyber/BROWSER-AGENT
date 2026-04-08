/**
 * BrowserAgent — Action Runner
 *
 * Executes atomic browser actions dispatched by the background service
 * worker and returns structured results.  Relies on
 * window.__BrowserAgentSensor (dom-sensor.js) for selector generation
 * and window.__BrowserAgentAnnotation (annotation.js) for visual feedback.
 *
 * Supported action types:
 *   CLICK · TYPE · SELECT · SCROLL · EXTRACT · WAIT ·
 *   NAVIGATE_NEXT · SUBMIT
 *
 * Every action returns:
 *   { success:bool, action_taken:string, selector_used:string,
 *     element_text:string, error:string|null }
 *
 * Pure vanilla JS — no external dependencies.
 */

/* global chrome */

(function BrowserAgentActionRunner() {
  "use strict";

  // ── Helpers ──────────────────────────────────────────────────────

  var annotation = function () { return window.__BrowserAgentAnnotation || {}; };
  var sensor     = function () { return window.__BrowserAgentSensor     || {}; };

  /**
   * Standard result envelope returned by every action.
   */
  function makeResult(success, action_taken, selector_used, element_text, error) {
    return {
      success:       !!success,
      action_taken:  action_taken || "",
      selector_used: selector_used || "",
      element_text:  element_text || "",
      error:         error || null,
    };
  }

  // ── Element Resolution ───────────────────────────────────────────

  /**
   * Find an element using the provided selector string.  Falls back
   * to a text-match search against all interactive elements if the
   * CSS selector fails.
   */
  function findElement(selector, textFallback) {
    if (selector) {
      try {
        var el = document.querySelector(selector);
        if (el) return { el: el, usedSelector: selector };
      } catch (_) { /* invalid selector — fall through */ }
    }

    if (textFallback) {
      var gen = sensor().generateSelector;
      var interactive = document.querySelectorAll(
        'a[href], button, input, textarea, select, [role="button"], ' +
        '[role="radio"], [role="checkbox"], [role="option"], [onclick], [tabindex]'
      );
      // Try exact match first, then includes
      for (var pass = 0; pass < 2; pass++) {
        for (var i = 0; i < interactive.length; i++) {
          var candidate = interactive[i];
          var cText = (candidate.innerText || candidate.value ||
                       candidate.getAttribute("aria-label") || "").trim();
          if (pass === 0 && cText === textFallback) {
            return { el: candidate, usedSelector: gen ? gen(candidate) : selector };
          }
          if (pass === 1 && cText.toLowerCase().includes(textFallback.toLowerCase())) {
            return { el: candidate, usedSelector: gen ? gen(candidate) : selector };
          }
        }
      }
    }

    return null;
  }

  /**
   * Scroll an element into view and flash-highlight it.
   */
  function prepareElement(el, highlightColor) {
    el.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
    var anno = annotation();
    if (anno.highlightElement) {
      var gen = sensor().generateSelector;
      var sel = gen ? gen(el) : "";
      if (sel) anno.highlightElement(sel, highlightColor || "#4f98a3", 1200);
    }
  }

  /**
   * Observe whether the page URL or DOM meaningfully changes within
   * a short window after an action.
   */
  function detectPageChange(beforeUrl, timeoutMs) {
    return new Promise(function (resolve) {
      var changed = false;
      var observer = new MutationObserver(function (mutations) {
        if (mutations.length > 0) changed = true;
      });
      observer.observe(document.body || document.documentElement, {
        childList: true,
        subtree: true,
      });

      setTimeout(function () {
        observer.disconnect();
        if (window.location.href !== beforeUrl) changed = true;
        resolve(changed);
      }, timeoutMs || 400);
    });
  }

  // ── Action: CLICK ────────────────────────────────────────────────

  async function doClick(payload) {
    var target = findElement(payload.selector, payload.text);
    if (!target) {
      return makeResult(false, "CLICK failed", payload.selector, "", "Element not found: " + (payload.selector || payload.text));
    }

    var el  = target.el;
    var sel = target.usedSelector;
    var elText = (el.innerText || el.value || "").trim().slice(0, 120);

    prepareElement(el, "#4f98a3");
    await sleep(200); // let scroll settle

    var beforeUrl = window.location.href;

    // Dispatch full mouse-event sequence
    dispatchMouseSequence(el);
    el.click();

    var changed = await detectPageChange(beforeUrl, 500);

    return makeResult(
      true,
      "Clicked " + el.tagName.toLowerCase() + (elText ? ' "' + elText.slice(0, 60) + '"' : ""),
      sel,
      elText,
      null
    );
  }

  function dispatchMouseSequence(el) {
    var rect = el.getBoundingClientRect();
    var cx = rect.x + rect.width / 2;
    var cy = rect.y + rect.height / 2;
    var common = { bubbles: true, cancelable: true, view: window, clientX: cx, clientY: cy };

    el.dispatchEvent(new MouseEvent("mouseenter",  common));
    el.dispatchEvent(new MouseEvent("mouseover",   common));
    el.dispatchEvent(new MouseEvent("mousemove",   common));
    el.dispatchEvent(new MouseEvent("mousedown",   Object.assign({}, common, { button: 0 })));
    el.dispatchEvent(new MouseEvent("mouseup",     Object.assign({}, common, { button: 0 })));
    el.dispatchEvent(new MouseEvent("click",       Object.assign({}, common, { button: 0 })));
  }

  // ── Action: TYPE ─────────────────────────────────────────────────

  async function doType(payload) {
    var target = findElement(payload.selector, payload.text);
    if (!target) {
      return makeResult(false, "TYPE failed", payload.selector, "", "Element not found: " + (payload.selector || payload.text));
    }

    var el  = target.el;
    var sel = target.usedSelector;
    var value = payload.value || "";

    prepareElement(el, "#4f98a3");
    await sleep(150);

    el.focus();

    // Clear existing content
    if (el.tagName === "INPUT" || el.tagName === "TEXTAREA") {
      el.value = "";
      el.dispatchEvent(new Event("input",  { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    } else if (el.getAttribute("contenteditable") === "true") {
      el.textContent = "";
    }

    // Type character by character
    for (var i = 0; i < value.length; i++) {
      var ch = value[i];

      el.dispatchEvent(new KeyboardEvent("keydown",  { key: ch, bubbles: true }));
      el.dispatchEvent(new KeyboardEvent("keypress", { key: ch, bubbles: true }));

      if (el.tagName === "INPUT" || el.tagName === "TEXTAREA") {
        el.value += ch;
      } else if (el.getAttribute("contenteditable") === "true") {
        el.textContent += ch;
      }

      el.dispatchEvent(new InputEvent("input", {
        bubbles: true,
        inputType: "insertText",
        data: ch,
      }));
      el.dispatchEvent(new KeyboardEvent("keyup", { key: ch, bubbles: true }));

      await sleep(40);
    }

    el.dispatchEvent(new Event("change", { bubbles: true }));

    var anno = annotation();
    if (anno.showActionBadge) {
      anno.showActionBadge(sel, 'Typed "' + value.slice(0, 20) + '"');
    }

    return makeResult(
      true,
      'Typed "' + value.slice(0, 50) + '" into ' + el.tagName.toLowerCase(),
      sel,
      value,
      null
    );
  }

  // ── Action: SELECT ───────────────────────────────────────────────

  async function doSelect(payload) {
    /*
     * SELECT handles heterogeneous selection interfaces:
     *   1. Native <input type="radio">  – by selector or (name + value)
     *   2. Native <input type="checkbox"> – same
     *   3. Native <select> element       – set value property
     *   4. Div-based custom options      – click the matching div
     *   5. ARIA role="radio" / "checkbox" – click the element
     */

    var sel = payload.selector;
    var name = payload.name;
    var value = payload.value || "";
    var text = payload.text || "";
    var el = null;
    var usedSelector = sel;

    // ─ 1–2: Radio / Checkbox by name + value ────────────────────
    if (name && value) {
      var candidates = document.querySelectorAll(
        'input[name="' + CSS.escape(name) + '"]'
      );
      for (var i = 0; i < candidates.length; i++) {
        if (candidates[i].value === value) {
          el = candidates[i];
          var gen = sensor().generateSelector;
          usedSelector = gen ? gen(el) : 'input[name="' + name + '"][value="' + value + '"]';
          break;
        }
      }
    }

    // ─ Direct selector ──────────────────────────────────────────
    if (!el && sel) {
      try { el = document.querySelector(sel); } catch (_) {}
    }

    // ─ Text fallback ────────────────────────────────────────────
    if (!el && text) {
      var found = findElement(null, text);
      if (found) { el = found.el; usedSelector = found.usedSelector; }
    }

    if (!el) {
      return makeResult(false, "SELECT failed", sel, "", "Element not found for selection");
    }

    var tagName = el.tagName.toLowerCase();
    var elType  = (el.getAttribute("type") || "").toLowerCase();
    var elRole  = (el.getAttribute("role") || "").toLowerCase();
    var elText  = (el.innerText || el.value || "").trim().slice(0, 120);

    prepareElement(el, "#4f98a3");
    await sleep(150);

    // ─ Native <select> ──────────────────────────────────────────
    if (tagName === "select") {
      el.value = value;
      el.dispatchEvent(new Event("input",  { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return makeResult(true, "Selected option \"" + value + "\" in <select>", usedSelector, value, null);
    }

    // ─ Radio / Checkbox ─────────────────────────────────────────
    if (tagName === "input" && (elType === "radio" || elType === "checkbox")) {
      if (elType === "radio") {
        el.checked = true;
      } else {
        el.checked = !el.checked; // toggle checkbox
      }
      el.dispatchEvent(new Event("input",  { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      dispatchMouseSequence(el);
      el.click();
      return makeResult(true, "Selected " + elType + ' "' + elText + '"', usedSelector, elText, null);
    }

    // ─ ARIA role radio/checkbox ─────────────────────────────────
    if (elRole === "radio" || elRole === "checkbox" || elRole === "option") {
      dispatchMouseSequence(el);
      el.click();
      return makeResult(true, "Selected " + elRole + ' "' + elText + '"', usedSelector, elText, null);
    }

    // ─ Div-based option ─────────────────────────────────────────
    dispatchMouseSequence(el);
    el.click();
    return makeResult(true, "Selected div-option \"" + elText.slice(0, 60) + '"', usedSelector, elText, null);
  }

  // ── Action: SCROLL ───────────────────────────────────────────────

  async function doScroll(payload) {
    var direction = (payload.direction || "down").toLowerCase();
    var amount    = parseInt(payload.amount, 10) || 500;
    var selector  = payload.selector;

    // Scroll a specific element into view
    if (selector) {
      try {
        var el = document.querySelector(selector);
        if (el) {
          el.scrollIntoView({ behavior: "smooth", block: "center" });
          return makeResult(true, "Scrolled element into view", selector, "", null);
        }
      } catch (_) {}
    }

    var dx = 0, dy = 0;
    switch (direction) {
      case "up":    dy = -amount; break;
      case "down":  dy =  amount; break;
      case "left":  dx = -amount; break;
      case "right": dx =  amount; break;
      case "top":
        window.scrollTo({ top: 0, behavior: "smooth" });
        return makeResult(true, "Scrolled to top of page", "", "", null);
      case "bottom":
        window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
        return makeResult(true, "Scrolled to bottom of page", "", "", null);
      default:
        dy = amount;
    }

    window.scrollBy({ left: dx, top: dy, behavior: "smooth" });
    return makeResult(
      true,
      "Scrolled " + direction + " by " + amount + "px",
      "",
      "",
      null
    );
  }

  // ── Action: EXTRACT ──────────────────────────────────────────────

  function doExtract(payload) {
    var selector = payload.selector;
    var el;

    if (selector) {
      try { el = document.querySelector(selector); } catch (_) {}
    }
    if (!el) el = document.body;

    var extracted = (el.innerText || el.textContent || "").trim().slice(0, 8000);

    return makeResult(
      true,
      "Extracted " + extracted.length + " characters",
      selector || "body",
      extracted,
      null
    );
  }

  // ── Action: WAIT ─────────────────────────────────────────────────

  function doWait(payload) {
    var ms = parseInt(payload.duration || payload.value || payload.ms, 10) || 1000;
    return new Promise(function (resolve) {
      setTimeout(function () {
        resolve(makeResult(true, "Waited " + ms + "ms", "", "", null));
      }, ms);
    });
  }

  // ── Action: NAVIGATE_NEXT ────────────────────────────────────────

  async function doNavigateNext(payload) {
    var navButtons = (sensor().detectNavigationButtons || function () { return { next: [] }; })();
    var candidates = navButtons.next || [];

    // Also try matching by user-provided text
    if (payload.text) {
      var extra = findElement(null, payload.text);
      if (extra) {
        candidates.unshift({ text: payload.text, selector: extra.usedSelector, _el: extra.el });
      }
    }

    // If a specific selector was provided, try that first
    if (payload.selector) {
      try {
        var directEl = document.querySelector(payload.selector);
        if (directEl) {
          prepareElement(directEl, "#4f98a3");
          await sleep(200);
          dispatchMouseSequence(directEl);
          directEl.click();
          var t = (directEl.innerText || "").trim().slice(0, 80);
          return makeResult(true, 'Clicked next button: "' + t + '"', payload.selector, t, null);
        }
      } catch (_) {}
    }

    if (candidates.length === 0) {
      // Fallback: broaden search with regex
      var allBtns = document.querySelectorAll('button, a[href], input[type="submit"], input[type="button"], [role="button"]');
      var nextRe = /next|continue|proceed|forward|→|›|>>/i;
      for (var b = 0; b < allBtns.length; b++) {
        var btnText = (allBtns[b].innerText || allBtns[b].value || "").trim();
        if (nextRe.test(btnText)) {
          var gen = sensor().generateSelector;
          candidates.push({
            text: btnText,
            selector: gen ? gen(allBtns[b]) : "",
            _el: allBtns[b],
          });
        }
      }
    }

    if (candidates.length === 0) {
      return makeResult(false, "NAVIGATE_NEXT failed", "", "", "No next/continue button found on page");
    }

    // Click the first (most relevant) candidate
    var best = candidates[0];
    var el = best._el || null;
    if (!el && best.selector) {
      try { el = document.querySelector(best.selector); } catch (_) {}
    }

    if (!el) {
      return makeResult(false, "NAVIGATE_NEXT failed", best.selector, "", "Next button found but could not be clicked");
    }

    prepareElement(el, "#4f98a3");
    await sleep(200);
    dispatchMouseSequence(el);
    el.click();

    return makeResult(
      true,
      'Clicked next button: "' + best.text.slice(0, 60) + '"',
      best.selector,
      best.text.slice(0, 120),
      null
    );
  }

  // ── Action: SUBMIT ───────────────────────────────────────────────

  async function doSubmit(payload) {
    var navButtons = (sensor().detectNavigationButtons || function () { return { submit: [] }; })();
    var candidates = navButtons.submit || [];

    // Specific selector first
    if (payload.selector) {
      try {
        var directEl = document.querySelector(payload.selector);
        if (directEl) {
          prepareElement(directEl, "#4f98a3");
          await sleep(200);
          dispatchMouseSequence(directEl);
          directEl.click();
          var t = (directEl.innerText || directEl.value || "").trim().slice(0, 80);
          return makeResult(true, 'Clicked submit button: "' + t + '"', payload.selector, t, null);
        }
      } catch (_) {}
    }

    // Broaden search if no candidates
    if (candidates.length === 0) {
      var allBtns = document.querySelectorAll(
        'button, input[type="submit"], input[type="button"], [role="button"], a[href]'
      );
      var submitRe = /submit|finish|done|complete|send|confirm|save|grade|check/i;
      for (var b = 0; b < allBtns.length; b++) {
        var btnText = (allBtns[b].innerText || allBtns[b].value || "").trim();
        if (submitRe.test(btnText)) {
          var gen = sensor().generateSelector;
          candidates.push({
            text: btnText,
            selector: gen ? gen(allBtns[b]) : "",
            _el: allBtns[b],
          });
        }
      }
    }

    // Try form.requestSubmit() as a fallback
    if (candidates.length === 0) {
      var form = document.querySelector("form");
      if (form) {
        try {
          form.requestSubmit();
          return makeResult(true, "Submitted form via requestSubmit()", "form", "", null);
        } catch (_) {
          form.submit();
          return makeResult(true, "Submitted form via submit()", "form", "", null);
        }
      }
      return makeResult(false, "SUBMIT failed", "", "", "No submit button or form found");
    }

    var best = candidates[0];
    var el = best._el || null;
    if (!el && best.selector) {
      try { el = document.querySelector(best.selector); } catch (_) {}
    }

    if (!el) {
      return makeResult(false, "SUBMIT failed", best.selector, "", "Submit button found but could not be clicked");
    }

    prepareElement(el, "#4f98a3");
    await sleep(200);
    dispatchMouseSequence(el);
    el.click();

    return makeResult(
      true,
      'Clicked submit button: "' + best.text.slice(0, 60) + '"',
      best.selector,
      best.text.slice(0, 120),
      null
    );
  }

  // ── Action Dispatcher ────────────────────────────────────────────

  /**
   * Route an incoming action payload to the correct handler.
   * Always returns a result envelope.
   */
  async function executeAction(payload) {
    var actionType = (payload.action || payload.action_type || payload.type || "").toUpperCase();

    var anno = annotation();
    if (anno.showAgentThinking) anno.showAgentThinking();

    var result;
    try {
      switch (actionType) {
        case "CLICK":          result = await doClick(payload);        break;
        case "TYPE":           result = await doType(payload);         break;
        case "SELECT":         result = await doSelect(payload);       break;
        case "SCROLL":         result = await doScroll(payload);       break;
        case "EXTRACT":        result = doExtract(payload);            break;
        case "WAIT":           result = await doWait(payload);         break;
        case "NAVIGATE_NEXT":  result = await doNavigateNext(payload); break;
        case "SUBMIT":         result = await doSubmit(payload);       break;
        default:
          result = makeResult(false, "Unknown action: " + actionType, "", "", "Unsupported action type: " + actionType);
      }
    } catch (err) {
      result = makeResult(false, "Action " + actionType + " threw", "", "", err.message || String(err));
    }

    if (anno.hideAgentThinking) anno.hideAgentThinking();

    return result;
  }

  // ── Message Handler ──────────────────────────────────────────────

  chrome.runtime.onMessage.addListener(function (message, _sender, sendResponse) {
    if (!message) return false;

    var type = (message.type || "").toUpperCase();

    // Accept both "EXECUTE_ACTION" envelope and direct action types
    if (type === "EXECUTE_ACTION" && message.payload) {
      executeAction(message.payload).then(function (result) {
        sendResponse(result);
      });
      return true; // keep channel open for async
    }

    // Direct action types: CLICK, TYPE, SELECT, …
    var directActions = ["CLICK", "TYPE", "SELECT", "SCROLL", "EXTRACT", "WAIT", "NAVIGATE_NEXT", "SUBMIT"];
    if (directActions.indexOf(type) !== -1) {
      var actionPayload = Object.assign({}, message.payload || message, { action_type: type });
      executeAction(actionPayload).then(function (result) {
        sendResponse(result);
      });
      return true;
    }

    return false;
  });

  // ── Utilities ────────────────────────────────────────────────────

  function sleep(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  // ── Expose for sibling scripts ───────────────────────────────────

  window.__BrowserAgentRunner = {
    executeAction: executeAction,
  };

  console.log("[BrowserAgent:ActionRunner] Loaded on", window.location.href);
})();
