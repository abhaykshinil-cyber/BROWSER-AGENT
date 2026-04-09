/**
 * BrowserAgent — MCQ Detector (Phase 6)
 *
 * Advanced question detection that builds on dom-sensor.js.  Handles
 * shadow DOM, iframes, React/Vue controlled components, and
 * non-standard quiz layouts (card-based, custom widgets, etc.).
 *
 * Returns a rich Question[] array sorted by visual position (top→bottom).
 *
 * Question shape:
 *   { id, index, text, type, options, answered, confidence,
 *     selector, containerSelector }
 *
 * type: "radio" | "checkbox" | "dropdown" | "card" | "custom"
 *
 * Exposed via window.__BrowserAgentMCQ.detectQuestions()
 *
 * Pure vanilla JS — no external dependencies.
 */

/* global chrome */

(function BrowserAgentMCQDetector() {
  "use strict";

  var sensor = function () { return window.__BrowserAgentSensor || {}; };

  var _qIdSeq = 0;

  // ── Question Builder ─────────────────────────────────────────────

  function makeQuestion(overrides) {
    _qIdSeq++;
    return {
      id:                "q_" + _qIdSeq,
      index:             overrides.index != null ? overrides.index : _qIdSeq - 1,
      text:              overrides.text              || "",
      type:              overrides.type              || "custom",
      options:           overrides.options            || [],
      answered:          overrides.answered           || false,
      confidence:        overrides.confidence != null ? overrides.confidence : 0.5,
      selector:          overrides.selector           || "",
      containerSelector: overrides.containerSelector  || "",
      _y:                overrides._y != null         ? overrides._y : 99999,
    };
  }

  function makeOption(text, selector, value, selected, type) {
    return {
      text:     (text || "").trim().slice(0, 300),
      selector: selector || "",
      value:    value    || "",
      selected: !!selected,
      type:     type     || "unknown",
    };
  }

  // ── Selector & Visibility (use sensor if available) ──────────────

  function genSel(el) {
    var gen = sensor().generateSelector;
    return gen ? gen(el) : _fallbackSelector(el);
  }

  function _fallbackSelector(el) {
    if (el.id) return "#" + CSS.escape(el.id);
    var path = [];
    var n = el;
    while (n && n !== document.body && n !== document.documentElement) {
      var tag = n.tagName.toLowerCase();
      if (n.id) { path.unshift("#" + CSS.escape(n.id)); break; }
      var parent = n.parentElement;
      if (parent) {
        var sibs = parent.children;
        var same = [];
        for (var s = 0; s < sibs.length; s++) {
          if (sibs[s].tagName === n.tagName) same.push(sibs[s]);
        }
        if (same.length > 1) {
          tag += ":nth-of-type(" + (same.indexOf(n) + 1) + ")";
        }
      }
      path.unshift(tag);
      n = parent;
    }
    return (path[0] && path[0][0] !== "#" ? "body > " : "") + path.join(" > ");
  }

  function isVis(el) {
    if (!el) return false;
    var r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return false;
    var s = getComputedStyle(el);
    if (s.display === "none" || s.visibility === "hidden" || s.opacity === "0") return false;
    return true;
  }

  function topY(el) {
    if (!el) return 99999;
    return el.getBoundingClientRect().top;
  }

  // ── Label finders ────────────────────────────────────────────────

  function findLabel(el) {
    if (el.id) {
      var lab = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (lab) return textOf(lab, true);
    }
    var wrap = el.closest("label");
    if (wrap) return textOf(wrap, true);
    var next = el.nextSibling;
    if (next && next.nodeType === 3 && next.textContent.trim()) return next.textContent.trim().slice(0, 300);
    var nextEl = el.nextElementSibling;
    if (nextEl && (nextEl.tagName === "SPAN" || nextEl.tagName === "LABEL" || nextEl.tagName === "DIV" || nextEl.tagName === "P")) {
      return textOf(nextEl, false);
    }
    return el.getAttribute("aria-label") || el.value || "";
  }

  function textOf(el, excludeInputs) {
    if (!el) return "";
    if (excludeInputs) {
      var c = el.cloneNode(true);
      var rm = c.querySelectorAll("input, select, textarea, svg");
      for (var i = 0; i < rm.length; i++) rm[i].remove();
      return (c.innerText || c.textContent || "").trim().slice(0, 300);
    }
    return (el.innerText || el.textContent || "").trim().slice(0, 300);
  }

  /**
   * Walk up from the first option element to find the question text.
   * Tries multiple strategies: <legend>, heading tags, .question-text,
   * previous sibling, aria-label on container, data-question attribute.
   */
  function findQuestionText(firstOption) {
    var parent = firstOption.parentElement;
    var containers = [
      parent ? parent.closest("fieldset") : null,
      parent ? parent.closest("[class*='question']") : null,
      parent ? parent.closest("[class*='Question']") : null,
      parent ? parent.closest("[data-question]") : null,
      parent ? parent.closest("[role='radiogroup']") : null,
      parent ? parent.closest("[role='group']") : null,
      parent ? parent.closest("form") : null,
      parent,
    ];

    for (var i = 0; i < containers.length; i++) {
      var c = containers[i];
      if (!c) continue;

      // data-question attribute
      var dq = c.getAttribute("data-question") || c.getAttribute("data-question-text");
      if (dq) return dq.trim().slice(0, 500);

      // aria-label / aria-labelledby
      var al = c.getAttribute("aria-label");
      if (al && al.length > 10) return al.trim().slice(0, 500);
      var alb = c.getAttribute("aria-labelledby");
      if (alb) {
        var refEl = document.getElementById(alb);
        if (refEl) return textOf(refEl, false);
      }

      // <legend>
      var legend = c.querySelector("legend");
      if (legend) return textOf(legend, false);

      // Heading or question-text
      var heading = c.querySelector(
        "h1, h2, h3, h4, h5, h6, .question-text, .question-title, " +
        "[class*='question-text'], [class*='question-title'], " +
        "[class*='questionText'], [class*='questionTitle'], " +
        "[class*='stem'], .stem, [class*='prompt'], .prompt"
      );
      if (heading) return textOf(heading, false);

      // <label> that isn't wrapping an option
      var topLabel = c.querySelector("label");
      if (topLabel && !topLabel.querySelector("input")) {
        var lt = textOf(topLabel, false);
        if (lt.length > 10) return lt;
      }
    }

    // Previous sibling text
    if (parent) {
      var prev = parent.previousElementSibling;
      while (prev) {
        var pt = textOf(prev, false);
        if (pt.length > 5 && pt.length < 800) return pt;
        prev = prev.previousElementSibling;
      }
    }

    return "";
  }

  // ── Answered detection ───────────────────────────────────────────

  function anyOptionSelected(options) {
    for (var i = 0; i < options.length; i++) {
      if (options[i].selected) return true;
    }
    return false;
  }

  // ── Confidence scoring ───────────────────────────────────────────

  function scoreConfidence(qText, options, type) {
    var c = 0.3; // base

    if (qText.length > 10) c += 0.15;
    if (qText.length > 30) c += 0.1;
    if (/\?/.test(qText)) c += 0.1;
    if (options.length >= 2 && options.length <= 8) c += 0.15;
    if (type === "radio" || type === "checkbox") c += 0.1;
    if (type === "dropdown") c += 0.05;

    // Check all options have text
    var allHaveText = true;
    for (var i = 0; i < options.length; i++) {
      if (!options[i].text || options[i].text.length < 1) { allHaveText = false; break; }
    }
    if (allHaveText) c += 0.1;

    return Math.min(1.0, Math.round(c * 100) / 100);
  }

  // ── Strategy 1: Native Radio Groups ──────────────────────────────

  function detectRadioQuestions(results, seenContainers) {
    var radios = document.querySelectorAll('input[type="radio"]');
    var groups = {};
    for (var i = 0; i < radios.length; i++) {
      var r = radios[i];
      if (!isVis(r) && !isVis(r.closest("label"))) continue;
      var name = r.name || "__anon_" + i;
      if (!groups[name]) groups[name] = [];
      groups[name].push(r);
    }

    for (var gName in groups) {
      var grp = groups[gName];
      if (grp.length < 2) continue;

      var containerEl = grp[0].closest("fieldset, [class*='question'], [role='radiogroup'], [role='group']") || grp[0].parentElement;
      var containerSel = genSel(containerEl);
      if (seenContainers.has(containerSel)) continue;
      seenContainers.add(containerSel);

      var qText = findQuestionText(grp[0]);
      var opts = [];
      for (var j = 0; j < grp.length; j++) {
        opts.push(makeOption(
          findLabel(grp[j]),
          genSel(grp[j]),
          grp[j].value,
          grp[j].checked,
          "radio"
        ));
      }

      results.push(makeQuestion({
        text: qText,
        type: "radio",
        options: opts,
        answered: anyOptionSelected(opts),
        confidence: scoreConfidence(qText, opts, "radio"),
        selector: genSel(grp[0]),
        containerSelector: containerSel,
        _y: topY(containerEl),
      }));
    }
  }

  // ── Strategy 2: Native Checkbox Groups ───────────────────────────

  function detectCheckboxQuestions(results, seenContainers) {
    var checks = document.querySelectorAll('input[type="checkbox"]');
    var parentMap = new Map();

    for (var i = 0; i < checks.length; i++) {
      var cb = checks[i];
      if (!isVis(cb) && !isVis(cb.closest("label"))) continue;
      var p = cb.closest("fieldset, [class*='question'], [role='group']") || cb.parentElement;
      if (!p) continue;
      if (!parentMap.has(p)) parentMap.set(p, []);
      parentMap.get(p).push(cb);
    }

    parentMap.forEach(function (grp, container) {
      if (grp.length < 2) return;
      var containerSel = genSel(container);
      if (seenContainers.has(containerSel)) return;
      seenContainers.add(containerSel);

      var qText = findQuestionText(grp[0]);
      var opts = [];
      for (var j = 0; j < grp.length; j++) {
        opts.push(makeOption(
          findLabel(grp[j]),
          genSel(grp[j]),
          grp[j].value,
          grp[j].checked,
          "checkbox"
        ));
      }

      results.push(makeQuestion({
        text: qText,
        type: "checkbox",
        options: opts,
        answered: anyOptionSelected(opts),
        confidence: scoreConfidence(qText, opts, "checkbox"),
        selector: genSel(grp[0]),
        containerSelector: containerSel,
        _y: topY(container),
      }));
    });
  }

  // ── Strategy 3: <select> Dropdowns ───────────────────────────────

  function detectDropdownQuestions(results, seenContainers) {
    var selects = document.querySelectorAll("select");

    for (var i = 0; i < selects.length; i++) {
      var sel = selects[i];
      if (!isVis(sel)) continue;
      var options = sel.querySelectorAll("option");
      if (options.length < 2) continue;

      var containerSel = genSel(sel.parentElement || sel);
      if (seenContainers.has(containerSel)) continue;
      seenContainers.add(containerSel);

      var qText = "";
      // Label for the <select>
      if (sel.id) {
        var lab = document.querySelector('label[for="' + CSS.escape(sel.id) + '"]');
        if (lab) qText = textOf(lab, false);
      }
      if (!qText) {
        var prev = sel.previousElementSibling;
        if (prev) qText = textOf(prev, false);
      }
      if (!qText) qText = sel.getAttribute("aria-label") || sel.name || "";

      var opts = [];
      for (var j = 0; j < options.length; j++) {
        var opt = options[j];
        if (!opt.value && !opt.textContent.trim()) continue; // skip empty <option>
        opts.push(makeOption(
          opt.textContent.trim(),
          genSel(opt),
          opt.value,
          opt.selected,
          "dropdown"
        ));
      }

      results.push(makeQuestion({
        text: qText,
        type: "dropdown",
        options: opts,
        answered: sel.value !== "" && sel.selectedIndex > 0,
        confidence: scoreConfidence(qText, opts, "dropdown"),
        selector: genSel(sel),
        containerSelector: containerSel,
        _y: topY(sel),
      }));
    }
  }

  // ── Strategy 4: ARIA role="radio" / role="checkbox" ──────────────

  function detectAriaRoleQuestions(results, seenContainers) {
    var roleEls = document.querySelectorAll(
      '[role="radio"], [role="checkbox"], [role="option"]'
    );
    var parentMap = new Map();

    for (var i = 0; i < roleEls.length; i++) {
      var el = roleEls[i];
      if (!isVis(el)) continue;
      // Skip native inputs already handled
      if (el.tagName === "INPUT") continue;

      var p = el.closest("[role='radiogroup'], [role='listbox'], [role='group'], [class*='question']") || el.parentElement;
      if (!p) continue;
      if (!parentMap.has(p)) parentMap.set(p, []);
      parentMap.get(p).push(el);
    }

    parentMap.forEach(function (grp, container) {
      if (grp.length < 2) return;
      var containerSel = genSel(container);
      if (seenContainers.has(containerSel)) return;
      seenContainers.add(containerSel);

      var role = grp[0].getAttribute("role");
      var qText = findQuestionText(grp[0]);
      var opts = [];

      for (var j = 0; j < grp.length; j++) {
        var el = grp[j];
        var selected = el.getAttribute("aria-checked") === "true" ||
                       el.getAttribute("aria-selected") === "true" ||
                       el.classList.contains("selected") ||
                       el.classList.contains("active");

        opts.push(makeOption(
          textOf(el, false),
          genSel(el),
          el.getAttribute("data-value") || el.getAttribute("value") || "",
          selected,
          role === "checkbox" ? "checkbox" : "radio"
        ));
      }

      results.push(makeQuestion({
        text: qText,
        type: role === "checkbox" ? "checkbox" : "radio",
        options: opts,
        answered: anyOptionSelected(opts),
        confidence: scoreConfidence(qText, opts, "radio"),
        selector: genSel(grp[0]),
        containerSelector: containerSel,
        _y: topY(container),
      }));
    });
  }

  // ── Strategy 5: Card / Div-based Options ─────────────────────────

  function detectCardQuestions(results, seenContainers) {
    // Match elements whose class suggests they're quiz options
    var candidates = document.querySelectorAll(
      '[class*="option"], [class*="Option"], [class*="choice"], [class*="Choice"], ' +
      '[class*="answer"], [class*="Answer"], [class*="alternative"], ' +
      '[class*="mcq"], [class*="quiz-option"], [class*="quiz_option"], ' +
      '[data-option], [data-choice], [data-answer]'
    );

    var parentMap = new Map();
    for (var i = 0; i < candidates.length; i++) {
      var el = candidates[i];
      if (!isVis(el)) continue;
      if (el.tagName === "INPUT" || el.tagName === "SELECT" || el.tagName === "OPTION") continue;

      // Heuristic: skip if the element is too large (probably a container, not an option)
      var rect = el.getBoundingClientRect();
      if (rect.height > 300 || rect.width > 800) continue;

      var p = el.parentElement;
      if (!p) continue;
      if (!parentMap.has(p)) parentMap.set(p, []);
      parentMap.get(p).push(el);
    }

    parentMap.forEach(function (grp, container) {
      if (grp.length < 2 || grp.length > 12) return;
      var containerSel = genSel(container);
      if (seenContainers.has(containerSel)) return;
      seenContainers.add(containerSel);

      var qText = findQuestionText(grp[0]);
      var opts = [];

      for (var j = 0; j < grp.length; j++) {
        var el = grp[j];
        var selected = el.classList.contains("selected") ||
                       el.classList.contains("active") ||
                       el.classList.contains("checked") ||
                       el.getAttribute("aria-checked") === "true" ||
                       el.getAttribute("aria-selected") === "true" ||
                       el.getAttribute("data-selected") === "true";

        opts.push(makeOption(
          textOf(el, false),
          genSel(el),
          el.getAttribute("data-value") || el.getAttribute("data-option") || "",
          selected,
          "card"
        ));
      }

      results.push(makeQuestion({
        text: qText,
        type: "card",
        options: opts,
        answered: anyOptionSelected(opts),
        confidence: scoreConfidence(qText, opts, "custom") - 0.1, // slight penalty for div-based
        selector: genSel(grp[0]),
        containerSelector: containerSel,
        _y: topY(container),
      }));
    });
  }

  // ── Strategy 6: React / Vue Controlled Components ────────────────

  function detectReactVueQuestions(results, seenContainers) {
    // Look for elements with React/Vue data attributes that look like options
    var reactSelectors = [
      "[data-reactid]",
      "[data-reactroot]",
      "[class*='MuiRadio']",
      "[class*='MuiCheckbox']",
      "[class*='v-radio']",
      "[class*='el-radio']",
      "[class*='ant-radio']",
      "[class*='ant-checkbox']",
      "[class*='chakra-radio']",
      "[class*='chakra-checkbox']",
    ];

    for (var si = 0; si < reactSelectors.length; si++) {
      var els = document.querySelectorAll(reactSelectors[si]);
      if (els.length < 2) continue;

      var parentMap = new Map();
      for (var i = 0; i < els.length; i++) {
        var el = els[i];
        if (!isVis(el)) continue;
        var p = el.closest("[class*='question'], [class*='group'], [role='radiogroup'], fieldset") || el.parentElement;
        if (!p) continue;
        if (!parentMap.has(p)) parentMap.set(p, []);
        parentMap.get(p).push(el);
      }

      parentMap.forEach(function (grp, container) {
        if (grp.length < 2) return;
        var containerSel = genSel(container);
        if (seenContainers.has(containerSel)) return;
        seenContainers.add(containerSel);

        var qText = findQuestionText(grp[0]);
        var isCheckbox = reactSelectors[si].indexOf("Checkbox") !== -1 || reactSelectors[si].indexOf("checkbox") !== -1;

        var opts = [];
        for (var j = 0; j < grp.length; j++) {
          var el = grp[j];
          // Try to find the text in a sibling or child span
          var optText = textOf(el, true) || textOf(el.parentElement, true);
          var selected = el.classList.contains("Mui-checked") ||
                         el.classList.contains("is-checked") ||
                         el.classList.contains("checked") ||
                         el.querySelector("input:checked") !== null ||
                         el.getAttribute("aria-checked") === "true";

          var innerInput = el.querySelector("input");
          opts.push(makeOption(
            optText,
            genSel(el),
            innerInput ? innerInput.value : el.getAttribute("data-value") || "",
            selected,
            isCheckbox ? "checkbox" : "radio"
          ));
        }

        results.push(makeQuestion({
          text: qText,
          type: isCheckbox ? "checkbox" : "radio",
          options: opts,
          answered: anyOptionSelected(opts),
          confidence: scoreConfidence(qText, opts, "radio") - 0.05,
          selector: genSel(grp[0]),
          containerSelector: containerSel,
          _y: topY(container),
        }));
      });
    }
  }

  // ── Strategy 7: Shadow DOM ───────────────────────────────────────

  function detectShadowDOMQuestions(results, seenContainers) {
    // Find all elements with a shadowRoot
    var allEls = document.querySelectorAll("*");
    for (var i = 0; i < allEls.length; i++) {
      var host = allEls[i];
      if (!host.shadowRoot) continue;

      try {
        var shadowRadios = host.shadowRoot.querySelectorAll('input[type="radio"]');
        var shadowChecks = host.shadowRoot.querySelectorAll('input[type="checkbox"]');
        var shadowRoles  = host.shadowRoot.querySelectorAll('[role="radio"], [role="checkbox"]');

        // Process radios
        var rGroups = {};
        for (var r = 0; r < shadowRadios.length; r++) {
          var name = shadowRadios[r].name || "__shadow_" + r;
          if (!rGroups[name]) rGroups[name] = [];
          rGroups[name].push(shadowRadios[r]);
        }
        for (var gn in rGroups) {
          var grp = rGroups[gn];
          if (grp.length < 2) continue;
          var containerSel = genSel(host);
          if (seenContainers.has(containerSel)) continue;
          seenContainers.add(containerSel);

          var qText = findQuestionText(grp[0]) || host.getAttribute("aria-label") || "";
          var opts = [];
          for (var j = 0; j < grp.length; j++) {
            // Shadow DOM: generate a selector the browser can actually use.
            // ">>>" is not valid for document.querySelector; use the element's
            // own id if available, otherwise fall back to the shadow host selector.
            var shadowInputSel = grp[j].id
              ? "#" + grp[j].id
              : genSel(host);
            opts.push(makeOption(
              findLabel(grp[j]),
              shadowInputSel,
              grp[j].value,
              grp[j].checked,
              "radio"
            ));
          }
          results.push(makeQuestion({
            text: qText,
            type: "radio",
            options: opts,
            answered: anyOptionSelected(opts),
            confidence: scoreConfidence(qText, opts, "radio") - 0.1,
            selector: genSel(host),
            containerSelector: containerSel,
            _y: topY(host),
          }));
        }
      } catch (e) {
        // Closed shadow root — skip
      }
    }
  }

  // ── Strategy 8: Iframes ──────────────────────────────────────────

  function detectIframeQuestions(results, seenContainers) {
    var iframes = document.querySelectorAll("iframe");
    for (var i = 0; i < iframes.length; i++) {
      try {
        var doc = iframes[i].contentDocument;
        if (!doc || !doc.body) continue;

        var radios = doc.querySelectorAll('input[type="radio"]');
        var groups = {};
        for (var r = 0; r < radios.length; r++) {
          var name = radios[r].name || "__iframe_" + r;
          if (!groups[name]) groups[name] = [];
          groups[name].push(radios[r]);
        }

        var iframeSelector = genSel(iframes[i]);

        for (var gn in groups) {
          var grp = groups[gn];
          if (grp.length < 2) continue;
          // Use the iframe's own selector as container — ">>>" is invalid CSS.
          var containerSel = iframeSelector + "__group_" + gn;
          if (seenContainers.has(containerSel)) continue;
          seenContainers.add(containerSel);

          var qText = "";
          var parent = grp[0].closest("fieldset, [class*='question']");
          if (parent) {
            var h = parent.querySelector("legend, h1, h2, h3, h4, h5, h6, .question-text");
            if (h) qText = (h.innerText || "").trim();
          }

          var opts = [];
          for (var j = 0; j < grp.length; j++) {
            var lab = "";
            if (grp[j].id) {
              var labEl = doc.querySelector('label[for="' + grp[j].id + '"]');
              if (labEl) lab = (labEl.innerText || "").trim();
            }
            if (!lab) lab = grp[j].value || "";

            // Iframe content isn't accessible via document.querySelector from
            // the parent frame; store the best available id-based selector.
            var iframeInputSel = grp[j].id
              ? "#" + grp[j].id
              : "input[name='" + gn + "'][value='" + grp[j].value + "']";
            opts.push(makeOption(
              lab,
              iframeInputSel,
              grp[j].value,
              grp[j].checked,
              "radio"
            ));
          }

          results.push(makeQuestion({
            text: qText,
            type: "radio",
            options: opts,
            answered: anyOptionSelected(opts),
            confidence: scoreConfidence(qText, opts, "radio") - 0.15,
            selector: iframeSelector,
            containerSelector: containerSel,
            _y: topY(iframes[i]),
          }));
        }
      } catch (e) {
        // Cross-origin iframe — skip
      }
    }
  }

  // ── Master Detection ─────────────────────────────────────────────

  /**
   * Run all detection strategies and return a deduplicated, sorted
   * array of Question objects.
   *
   * @returns {Array<Object>}  Question[]
   */
  function detectQuestions() {
    _qIdSeq = 0;
    var results = [];
    var seenContainers = new Set();

    // Run strategies in priority order (higher-confidence first)
    detectRadioQuestions(results, seenContainers);
    detectCheckboxQuestions(results, seenContainers);
    detectDropdownQuestions(results, seenContainers);
    detectAriaRoleQuestions(results, seenContainers);
    detectCardQuestions(results, seenContainers);
    detectReactVueQuestions(results, seenContainers);
    detectShadowDOMQuestions(results, seenContainers);
    detectIframeQuestions(results, seenContainers);

    // Sort by visual position (top to bottom)
    results.sort(function (a, b) { return a._y - b._y; });

    // Assign final indices and strip internal _y
    for (var i = 0; i < results.length; i++) {
      results[i].index = i;
      delete results[i]._y;
    }

    return results;
  }

  // ── Message Handler ──────────────────────────────────────────────

  chrome.runtime.onMessage.addListener(function (message, _sender, sendResponse) {
    if (!message) return false;
    var type = (message.type || "").toUpperCase();

    if (type === "DETECT_MCQ" || type === "DETECT_QUESTIONS") {
      try {
        var questions = detectQuestions();
        sendResponse({ success: true, questions: questions });
      } catch (err) {
        sendResponse({ success: false, error: err.message, questions: [] });
      }
      return true;
    }

    if (type === "SELECT_ANSWERS") {
      // Receive answers and select them — applyAnswers is async
      var answers = (message.payload || message).answers || [];
      applyAnswers(answers).then(function (applied) {
        sendResponse({ success: true, applied: applied });
      }).catch(function (err) {
        sendResponse({ success: false, error: err.message, applied: [] });
      });
      return true; // keep channel open for async
    }

    return false;
  });

  // ── Answer Application ───────────────────────────────────────────

  /**
   * Apply a set of answers to the detected questions.
   * Each answer: { question_id, selected_indices: [int] }
   *
   * @param {Array} answers
   * @returns {Array} Results of each application.
   */
  async function applyAnswers(answers) {
    var questions = detectQuestions();
    var qMap = {};
    for (var i = 0; i < questions.length; i++) {
      qMap[questions[i].id] = questions[i];
      qMap["q_" + questions[i].index] = questions[i];  // also index-based
      qMap[String(questions[i].index)] = questions[i];
    }

    var results = [];

    for (var a = 0; a < answers.length; a++) {
      var ans = answers[a];
      var qId = ans.question_id || ans.qIdx;
      var q = qMap[qId] || qMap["q_" + qId] || qMap[String(qId)];

      if (!q) {
        // Try by index
        if (typeof qId === "number" && qId >= 0 && qId < questions.length) {
          q = questions[qId];
        }
      }

      if (!q) {
        results.push({ question_id: qId, success: false, error: "Question not found" });
        continue;
      }

      var indices = ans.selected_indices || ans.selected || [];
      var clickResults = [];

      for (var si = 0; si < indices.length; si++) {
        var optIdx = indices[si];
        if (optIdx < 0 || optIdx >= q.options.length) {
          clickResults.push({ index: optIdx, success: false, error: "Option index out of range" });
          continue;
        }

        var opt = q.options[optIdx];
        var el = null;
        try {
          el = document.querySelector(opt.selector);
        } catch (e) {}

        if (!el) {
          clickResults.push({ index: optIdx, success: false, error: "Option element not found" });
          continue;
        }

        // Use the action-runner's dispatching if available, else click directly
        var runner = window.__BrowserAgentRunner;
        var clickOk = false;
        if (runner && runner.executeAction) {
          // executeAction returns a Promise — we must resolve it before
          // recording success, otherwise we always report success prematurely.
          var actionResult = null;
          try {
            actionResult = await runner.executeAction({
              action_type: "SELECT",
              selector: opt.selector,
              text: opt.text,
              value: opt.value,
            });
            clickOk = actionResult && actionResult.success !== false;
          } catch (_e) { clickOk = false; }
        } else {
          el.click();
          if (el.tagName === "INPUT") {
            el.checked = true;
            el.dispatchEvent(new Event("input", { bubbles: true }));
            el.dispatchEvent(new Event("change", { bubbles: true }));
          }
          clickOk = true;
        }

        clickResults.push({ index: optIdx, success: clickOk, text: opt.text });
      }

      results.push({
        question_id: q.id,
        index: q.index,
        success: clickResults.every(function (r) { return r.success; }),
        clicks: clickResults,
      });
    }

    return results;
  }

  // ── Expose API ───────────────────────────────────────────────────

  window.__BrowserAgentMCQ = {
    detectQuestions: detectQuestions,
    applyAnswers:    applyAnswers,
  };

  console.log("[BrowserAgent:MCQDetector] Loaded on", window.location.href);
})();
