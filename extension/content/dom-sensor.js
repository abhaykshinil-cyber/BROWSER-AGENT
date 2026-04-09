/**
 * BrowserAgent — DOM Sensor
 *
 * A page observer injected into every tab.  Scans for interactive
 * elements, detects page types and MCQ/navigation patterns, and
 * returns a structured snapshot on demand via chrome.runtime messages.
 *
 * Message protocol:
 *   Incoming:  { type: "SCAN" }
 *   Response:  { page_type, dom_snapshot, mcq_questions, navigation_buttons, page_text }
 *
 * Pure vanilla JS — no external dependencies.
 */

/* global chrome */

(function BrowserAgentDomSensor() {
  "use strict";

  // ── Constants ────────────────────────────────────────────────────

  const MAX_SNAPSHOT_ELEMENTS = 80;
  const MAX_PAGE_TEXT_CHARS   = 20000;

  /**
   * CSS selector covering every element the agent should be aware of.
   */
  const INTERACTIVE_SELECTOR = [
    "a[href]",
    "button",
    "input",
    "textarea",
    "select",
    "option",
    "label",
    "[role='button']",
    "[role='link']",
    "[role='radio']",
    "[role='checkbox']",
    "[role='option']",
    "[role='tab']",
    "[role='menuitem']",
    "[role='switch']",
    "[role='combobox']",
    "[onclick]",
    "[tabindex]",
    "[contenteditable='true']",
  ].join(", ");

  /**
   * Patterns for detecting quiz-option elements by class name.
   */
  const MCQ_CLASS_RE = /option|choice|answer|alternative|mcq|quiz-option/i;

  /**
   * Nav-button text patterns (case-insensitive).
   */
  const NAV_NEXT_RE     = /^(next|continue|proceed|forward|→|›|>>|go\s*to\s*next)$/i;
  const NAV_SUBMIT_RE   = /^(submit|finish|done|complete|send|confirm|save|grade|check)$/i;
  const NAV_BACK_RE     = /^(back|previous|←|‹|<<|go\s*back|return)$/i;

  // ── Selector Generator ───────────────────────────────────────────

  /**
   * Generate a robust CSS selector for `el`.
   * Priority: #id  →  [data-*] attribute  →  nth-of-type path.
   */
  function generateSelector(el) {
    // 1. Unique ID
    if (el.id) {
      return "#" + CSS.escape(el.id);
    }

    // 2. data-* attributes (testid, cy, qa, automation-id, name)
    var dataAttrs = ["data-testid", "data-cy", "data-qa", "data-automation-id", "data-name", "data-id"];
    for (var i = 0; i < dataAttrs.length; i++) {
      var val = el.getAttribute(dataAttrs[i]);
      if (val) {
        return "[" + dataAttrs[i] + '="' + CSS.escape(val) + '"]';
      }
    }

    // 3. Build an nth-of-type path up from the element
    return buildNthOfTypePath(el);
  }

  /**
   * Walk up the DOM to produce a selector like
   *   body > div:nth-of-type(2) > form > input:nth-of-type(3)
   */
  function buildNthOfTypePath(el) {
    var parts = [];
    var node = el;

    while (node && node !== document.documentElement && node !== document.body) {
      var tag = node.tagName.toLowerCase();
      var parent = node.parentElement;

      if (node.id) {
        parts.unshift("#" + CSS.escape(node.id));
        break;
      }

      if (parent) {
        var siblings = parent.children;
        var sameTag = [];
        for (var s = 0; s < siblings.length; s++) {
          if (siblings[s].tagName === node.tagName) {
            sameTag.push(siblings[s]);
          }
        }
        if (sameTag.length > 1) {
          var idx = sameTag.indexOf(node) + 1;
          parts.unshift(tag + ":nth-of-type(" + idx + ")");
        } else {
          parts.unshift(tag);
        }
      } else {
        parts.unshift(tag);
      }

      node = parent;
    }

    if (parts.length === 0) {
      return el.tagName.toLowerCase();
    }

    // Prefix with body if the chain doesn't start with an #id
    if (parts[0].charAt(0) !== "#") {
      parts.unshift("body");
    }

    return parts.join(" > ");
  }

  // ── Visibility & Geometry ────────────────────────────────────────

  function isElementVisible(el) {
    if (el.offsetParent === null && el.tagName !== "BODY" && el.tagName !== "HTML") {
      // hidden via display:none or similar
      var style = getComputedStyle(el);
      if (style.display === "none" || style.visibility === "hidden") return false;
      if (style.position !== "fixed" && style.position !== "sticky") return false;
    }
    var rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) return false;
    return true;
  }

  function getPosition(el) {
    var rect = el.getBoundingClientRect();
    return {
      x: Math.round(rect.x),
      y: Math.round(rect.y),
      w: Math.round(rect.width),
      h: Math.round(rect.height),
    };
  }

  // ── Element Snapshot Builder ─────────────────────────────────────

  function snapshotElement(el) {
    var tag  = el.tagName.toLowerCase();
    var type = el.getAttribute("type") || "";
    var role = el.getAttribute("role") || "";

    var text = "";
    if (tag === "input" || tag === "textarea") {
      text = el.placeholder || el.getAttribute("aria-label") || el.name || "";
    } else {
      text = (el.innerText || el.textContent || "").trim();
    }
    text = text.slice(0, 200);

    var classes = Array.from(el.classList).join(" ");

    return {
      tag:         tag,
      type:        type,
      id:          el.id || "",
      name:        el.name || el.getAttribute("name") || "",
      classes:     classes,
      text:        text,
      placeholder: el.placeholder || "",
      value:       (tag === "input" || tag === "textarea" || tag === "select")
                     ? (el.value || "").slice(0, 200)
                     : "",
      selector:    generateSelector(el),
      role:        role,
      position:    getPosition(el),
      visible:     isElementVisible(el),
      enabled:     !el.disabled && !el.getAttribute("aria-disabled"),
    };
  }

  // ── Interactivity Score (for ranking / capping at 80) ────────────

  /**
   * Higher score = more likely to be useful to the agent.
   * Directly interactive elements score highest; decorative labels lowest.
   */
  function interactivityScore(snap) {
    var score = 0;
    var tag = snap.tag;

    // Base tag scores
    if (tag === "button")   score += 10;
    if (tag === "input")    score += 10;
    if (tag === "textarea") score += 10;
    if (tag === "select")   score += 10;
    if (tag === "a")        score += 8;
    if (tag === "option")   score += 3;
    if (tag === "label")    score += 2;

    // Role-based boosts
    var roleBoosts = { button: 9, radio: 9, checkbox: 9, link: 7, tab: 7, option: 5, switch: 8 };
    if (snap.role && roleBoosts[snap.role]) score += roleBoosts[snap.role];

    // Visible + enabled boosts
    if (snap.visible) score += 5;
    if (snap.enabled) score += 3;

    // In-viewport boost
    var pos = snap.position;
    if (pos.y >= 0 && pos.y < window.innerHeight && pos.x >= 0 && pos.x < window.innerWidth) {
      score += 6;
    }

    // Has text → more useful
    if (snap.text.length > 0) score += 2;

    return score;
  }

  // ── Full DOM Scan ────────────────────────────────────────────────

  function scanDOM() {
    var elements = document.querySelectorAll(INTERACTIVE_SELECTOR);
    var snapshots = [];
    var selectorSet = new Set();

    for (var i = 0; i < elements.length; i++) {
      var el = elements[i];
      var snap = snapshotElement(el);

      // De-duplicate by selector
      if (selectorSet.has(snap.selector)) continue;
      selectorSet.add(snap.selector);

      snap._score = interactivityScore(snap);
      snapshots.push(snap);
    }

    // Sort by score descending, then cap at MAX_SNAPSHOT_ELEMENTS
    snapshots.sort(function (a, b) { return b._score - a._score; });
    snapshots = snapshots.slice(0, MAX_SNAPSHOT_ELEMENTS);

    // Strip internal score before returning
    for (var j = 0; j < snapshots.length; j++) {
      delete snapshots[j]._score;
    }

    return snapshots;
  }

  // ── Page Text Extraction ─────────────────────────────────────────

  function extractPageText() {
    var body = document.body;
    if (!body) return "";

    var walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT, {
      acceptNode: function (node) {
        var parent = node.parentElement;
        if (!parent) return NodeFilter.FILTER_REJECT;
        var parentTag = parent.tagName;
        if (parentTag === "SCRIPT" || parentTag === "STYLE" ||
            parentTag === "NOSCRIPT" || parentTag === "SVG" ||
            parentTag === "TEMPLATE") {
          return NodeFilter.FILTER_REJECT;
        }
        // Skip hidden containers
        if (parent.offsetParent === null && parentTag !== "BODY" && parentTag !== "HTML") {
          var pStyle = getComputedStyle(parent);
          if (pStyle.display === "none" || pStyle.visibility === "hidden") {
            return NodeFilter.FILTER_REJECT;
          }
        }
        if (node.textContent.trim().length === 0) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      },
    });

    var chunks = [];
    var total  = 0;

    while (walker.nextNode()) {
      var text = walker.currentNode.textContent.trim();
      if (text) {
        chunks.push(text);
        total += text.length;
        if (total > MAX_PAGE_TEXT_CHARS) break;
      }
    }

    return chunks.join(" ");
  }

  // ── Page Type Detection ──────────────────────────────────────────

  /**
   * Return one of: "quiz", "form", "login", "article", "search",
   * "ecommerce", or "general".
   */
  function detectPageType() {
    var url   = window.location.href.toLowerCase();
    var title = (document.title || "").toLowerCase();
    var body  = document.body;
    if (!body) return "general";

    // Gather some quick signals
    var inputs       = body.querySelectorAll("input, textarea, select");
    var passwordFields = body.querySelectorAll('input[type="password"]');
    var radios       = body.querySelectorAll('input[type="radio"]');
    var checkboxes   = body.querySelectorAll('input[type="checkbox"]');
    var forms        = body.querySelectorAll("form");
    var articles     = body.querySelectorAll("article, [role='article']");
    var prices       = body.querySelectorAll("[class*='price'], [class*='Price'], [data-price]");
    var carts        = body.querySelectorAll("[class*='cart'], [class*='Cart'], [data-cart]");
    var searchInputs = body.querySelectorAll('input[type="search"], input[name*="search"], input[name*="query"], input[name="q"]');
    var roleRadios   = body.querySelectorAll('[role="radio"]');
    var roleChecks   = body.querySelectorAll('[role="checkbox"]');
    var mcqDivs      = body.querySelectorAll('[class*="option"], [class*="choice"], [class*="answer"]');

    // Login
    if (passwordFields.length > 0 && inputs.length <= 5) return "login";
    if (url.includes("/login") || url.includes("/signin") || url.includes("/sign-in")) return "login";

    // Quiz / MCQ
    var radioGroupCount = countRadioGroups(radios);
    var totalQuizSignals = radioGroupCount + roleRadios.length + roleChecks.length;
    if (totalQuizSignals >= 2) return "quiz";
    if (mcqDivs.length >= 3 && (url.includes("quiz") || url.includes("exam") || url.includes("test") || url.includes("assessment"))) return "quiz";
    if (url.includes("/quiz") || url.includes("/exam") || title.includes("quiz") || title.includes("exam")) return "quiz";

    // Search
    if (searchInputs.length > 0 || url.includes("/search") || url.includes("?q=")) return "search";

    // Ecommerce
    if (prices.length >= 2 || carts.length > 0) return "ecommerce";
    if (url.includes("/product") || url.includes("/shop") || url.includes("/cart") || url.includes("/checkout")) return "ecommerce";

    // Form (non-login, multiple inputs)
    if (forms.length > 0 && inputs.length >= 3) return "form";

    // Article
    if (articles.length > 0) return "article";
    var longText = (body.innerText || "").length;
    if (longText > 3000 && inputs.length < 3) return "article";

    return "general";
  }

  function countRadioGroups(radios) {
    var groups = new Set();
    for (var i = 0; i < radios.length; i++) {
      var name = radios[i].name || radios[i].getAttribute("name");
      if (name) groups.add(name);
    }
    return groups.size;
  }

  // ── MCQ Pattern Detection ───────────────────────────────────────

  /**
   * Detect multiple-choice question patterns on the page.
   * Returns an array of question objects:
   *   { question_text, question_selector, options: [{ text, selector, type, selected }] }
   */
  function detectMCQQuestions() {
    var questions = [];

    // Strategy 1: Radio-button groups (native <input type="radio">)
    var radioGroups = {};
    var radios = document.querySelectorAll('input[type="radio"]');
    for (var r = 0; r < radios.length; r++) {
      var radio = radios[r];
      var groupName = radio.name || "__unnamed_" + r;
      if (!radioGroups[groupName]) radioGroups[groupName] = [];
      radioGroups[groupName].push(radio);
    }

    for (var gName in radioGroups) {
      if (!radioGroups.hasOwnProperty(gName)) continue;
      var group = radioGroups[gName];
      if (group.length < 2) continue;

      var questionText = findQuestionTextForGroup(group[0]);
      var options = group.map(function (rb) {
        var label = findLabelFor(rb);
        return {
          text:     label,
          selector: generateSelector(rb),
          type:     "radio",
          selected: rb.checked,
        };
      });

      questions.push({
        question_text:     questionText,
        question_selector: generateSelector(group[0].closest("fieldset, .question, [class*='question']") || group[0].parentElement),
        options:           options,
      });
    }

    // Strategy 2: Checkbox groups inside a common container
    var checkboxes = document.querySelectorAll('input[type="checkbox"]');
    var cbGroups = groupByParent(checkboxes, 3);
    for (var ci = 0; ci < cbGroups.length; ci++) {
      var cbGroup = cbGroups[ci];
      var qText = findQuestionTextForGroup(cbGroup[0]);
      var cbOpts = cbGroup.map(function (cb) {
        return {
          text:     findLabelFor(cb),
          selector: generateSelector(cb),
          type:     "checkbox",
          selected: cb.checked,
        };
      });
      questions.push({
        question_text:     qText,
        question_selector: generateSelector(cbGroup[0].parentElement),
        options:           cbOpts,
      });
    }

    // Strategy 3: ARIA role="radio" / role="checkbox"
    var roleRadios = document.querySelectorAll('[role="radio"]');
    var roleGroups = groupByParent(roleRadios, 2);
    for (var ri = 0; ri < roleGroups.length; ri++) {
      var rg = roleGroups[ri];
      var rqText = findQuestionTextForGroup(rg[0]);
      var rOpts = rg.map(function (el) {
        return {
          text:     (el.innerText || el.textContent || "").trim().slice(0, 200),
          selector: generateSelector(el),
          type:     "role-radio",
          selected: el.getAttribute("aria-checked") === "true",
        };
      });
      questions.push({
        question_text:     rqText,
        question_selector: generateSelector(rg[0].parentElement),
        options:           rOpts,
      });
    }

    // Strategy 4: Div-based options detected by class patterns
    var optionDivs = document.querySelectorAll(
      '[class*="option"], [class*="choice"], [class*="answer"], [class*="alternative"], [class*="mcq"]'
    );
    var divGroups = groupByParent(optionDivs, 2);
    for (var di = 0; di < divGroups.length; di++) {
      var dg = divGroups[di];
      // Skip if already captured by radio/checkbox strategies
      var alreadyCaptured = false;
      for (var qi = 0; qi < questions.length; qi++) {
        if (questions[qi].question_selector === generateSelector(dg[0].parentElement)) {
          alreadyCaptured = true;
          break;
        }
      }
      if (alreadyCaptured) continue;

      var dqText = findQuestionTextForGroup(dg[0]);
      var dOpts = dg.map(function (div) {
        return {
          text:     (div.innerText || div.textContent || "").trim().slice(0, 200),
          selector: generateSelector(div),
          type:     "div-option",
          selected: div.classList.contains("selected") || div.classList.contains("active") ||
                    div.getAttribute("aria-selected") === "true",
        };
      });
      questions.push({
        question_text:     dqText,
        question_selector: generateSelector(dg[0].parentElement),
        options:           dOpts,
      });
    }

    return questions;
  }

  /**
   * Walk up from the first option element to find question text.
   */
  function findQuestionTextForGroup(firstOption) {
    // Look for a preceding heading, legend, label, or generic question container
    var parent = firstOption.parentElement;
    var searchContainers = [
      parent ? parent.closest("fieldset") : null,
      parent ? parent.closest("[class*='question']") : null,
      parent ? parent.closest("[class*='Question']") : null,
      parent,
    ];

    for (var i = 0; i < searchContainers.length; i++) {
      var container = searchContainers[i];
      if (!container) continue;

      // Legend inside fieldset
      var legend = container.querySelector("legend");
      if (legend) return legend.innerText.trim().slice(0, 300);

      // Preceding heading or label
      var heading = container.querySelector("h1, h2, h3, h4, h5, h6, .question-text, [class*='question-title'], label");
      if (heading) return heading.innerText.trim().slice(0, 300);
    }

    // Try previous sibling
    if (parent) {
      var prev = parent.previousElementSibling;
      if (prev) {
        var prevText = (prev.innerText || "").trim();
        if (prevText.length > 5 && prevText.length < 500) return prevText;
      }
    }

    return "";
  }

  /**
   * Find the visible label text for an input element.
   */
  function findLabelFor(el) {
    // <label for="id">
    if (el.id) {
      var label = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (label) return label.innerText.trim().slice(0, 200);
    }

    // Wrapping <label>
    var parent = el.closest("label");
    if (parent) {
      // Clone, remove the input, take remaining text
      var clone = parent.cloneNode(true);
      var inputs = clone.querySelectorAll("input, select, textarea");
      for (var i = 0; i < inputs.length; i++) inputs[i].remove();
      var remaining = clone.innerText.trim();
      if (remaining) return remaining.slice(0, 200);
    }

    // Adjacent text node or sibling
    var next = el.nextSibling;
    if (next && next.nodeType === Node.TEXT_NODE && next.textContent.trim()) {
      return next.textContent.trim().slice(0, 200);
    }
    var nextEl = el.nextElementSibling;
    if (nextEl && (nextEl.tagName === "SPAN" || nextEl.tagName === "LABEL")) {
      return nextEl.innerText.trim().slice(0, 200);
    }

    // aria-label / value
    return el.getAttribute("aria-label") || el.value || "";
  }

  /**
   * Group elements by their nearest common parent.
   * Only return groups with at least `minGroupSize` members.
   */
  function groupByParent(elements, minGroupSize) {
    var parentMap = new Map();
    for (var i = 0; i < elements.length; i++) {
      var p = elements[i].parentElement;
      if (!p) continue;
      if (!parentMap.has(p)) parentMap.set(p, []);
      parentMap.get(p).push(elements[i]);
    }

    var groups = [];
    parentMap.forEach(function (children) {
      if (children.length >= minGroupSize) {
        groups.push(children);
      }
    });
    return groups;
  }

  // ── Navigation Button Detection ──────────────────────────────────

  /**
   * Find all navigation-style buttons on the page.
   * Returns { next: [...], submit: [...], back: [...] }.
   */
  function detectNavigationButtons() {
    var result = { next: [], submit: [], back: [] };
    var candidates = document.querySelectorAll(
      'button, input[type="submit"], input[type="button"], a[href], [role="button"]'
    );

    for (var i = 0; i < candidates.length; i++) {
      var el   = candidates[i];
      var text = (el.innerText || el.value || el.getAttribute("aria-label") || "").trim();
      if (!text) continue;

      var selector = generateSelector(el);
      var entry = { text: text.slice(0, 100), selector: selector };

      if (NAV_NEXT_RE.test(text))   result.next.push(entry);
      if (NAV_SUBMIT_RE.test(text)) result.submit.push(entry);
      if (NAV_BACK_RE.test(text))   result.back.push(entry);
    }

    return result;
  }

  // ── Public API (via message listener) ────────────────────────────

  /**
   * Build the complete scan result payload.
   */
  function buildScanResult() {
    return {
      page_type:          detectPageType(),
      dom_snapshot:        scanDOM(),
      mcq_questions:       detectMCQQuestions(),
      navigation_buttons:  detectNavigationButtons(),
      page_text:           extractPageText(),
    };
  }

  // ── Expose for sibling scripts ───────────────────────────────────
  // NOTE: SCAN messages are handled by content.js (which augments the
  // result with url/title before responding). dom-sensor exposes its
  // API via window.__BrowserAgentSensor for content.js to call.

  window.__BrowserAgentSensor = {
    scanDOM:                  scanDOM,
    extractPageText:          extractPageText,
    detectPageType:           detectPageType,
    detectMCQQuestions:       detectMCQQuestions,
    detectNavigationButtons:  detectNavigationButtons,
    buildScanResult:          buildScanResult,
    generateSelector:         generateSelector,
  };

  console.log("[BrowserAgent:DomSensor] Loaded on", window.location.href);
})();
