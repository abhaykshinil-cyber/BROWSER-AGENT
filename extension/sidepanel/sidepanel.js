/**
 * BrowserAgent — Side Panel Controller (Phase 8)
 *
 * Master controller that initialises the side panel UI, wires every
 * button, manages tab switching, keeps the status bar + MCQ + memory
 * panels in sync with the reactive state store, and bridges the
 * Chrome extension messaging layer.
 *
 * Loaded by sidepanel.html as an ES module.
 */

/* global chrome */

import {
  setState, getState, onStateChange,
  addLogEntry, clearLog as clearStateLog,
} from "./state-store.js";

import {
  runAgent, stopAgent, pauseAgent, resumeAgent, confirmPlan,
} from "./agent-controller.js";

import { initTeachPanel } from "./teach-panel.js";

import { initTabsPanel, loadTabs as reloadTabsPanel } from "./tabs-panel.js";

import { initMemoryPanel } from "./memory-panel.js";

// ── Configuration ────────────────────────────────────────────────────

let SETTINGS = {
  apiKey:      "",
  model:       "gemini-2.0-flash",
  delay:       800,
  previewMode: true,
  backendUrl:  "http://localhost:8765",
};

let _healthTimer = null;

// ── DOM references (populated in init) ───────────────────────────────

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

// ═══════════════════════════════════════════════════════════════════════
// INITIALISATION
// ═══════════════════════════════════════════════════════════════════════

document.addEventListener("DOMContentLoaded", async () => {
  // 1. Load saved settings
  await loadSettings();

  // 2. Populate settings form
  populateSettingsForm();

  // 3. Health check — run immediately, then every 10 s
  pingBackend();
  _healthTimer = setInterval(pingBackend, 10000);

  // 4. Register event listeners
  registerSettingsListeners();
  registerTabListeners();
  registerAgentListeners();
  registerMCQListeners();
  registerMemoryListeners();
  registerLogListeners();
  registerKeyboardShortcuts();

  // 5. State change listener
  onStateChange(handleStateChange);

  // 6. Inject teach panel
  const teachContainer = $("#teach-panel-container");
  if (teachContainer) initTeachPanel(teachContainer);

  // 7. Inject tabs panel (Phase 9)
  const tabsContainer = $("#tabs-panel-container");
  if (tabsContainer) initTabsPanel(tabsContainer);

  // 8. Inject memory panel (Phase 10)
  const memContainer = $("#memory-panel-container");
  if (memContainer) initMemoryPanel(memContainer);

  // 9. Listen for content-script messages
  chrome.runtime.onMessage.addListener(handleExtensionMessage);

  // 10. Initial data loads
  loadMemoryRules();

  appendLog("Side panel initialised", "info");
});


// ═══════════════════════════════════════════════════════════════════════
// SETTINGS
// ═══════════════════════════════════════════════════════════════════════

async function loadSettings() {
  try {
    const data = await chrome.storage.local.get([
      "apiKey", "model", "delay", "previewMode", "backendUrl",
    ]);
    if (data.apiKey)      SETTINGS.apiKey      = data.apiKey;
    if (data.model)       SETTINGS.model       = data.model;
    if (data.delay)       SETTINGS.delay       = parseInt(data.delay, 10) || 800;
    if (data.previewMode !== undefined) SETTINGS.previewMode = !!data.previewMode;
    if (data.backendUrl)  SETTINGS.backendUrl  = data.backendUrl;
  } catch (e) {
    console.warn("[Settings] Could not load:", e);
  }
}

function populateSettingsForm() {
  const key     = $("#set-api-key");
  const model   = $("#set-model");
  const delay   = $("#set-delay");
  const delVal  = $("#delay-value");
  const preview = $("#set-preview");
  const backend = $("#set-backend");

  if (key)     key.value     = SETTINGS.apiKey;
  if (model)   model.value   = SETTINGS.model;
  if (delay)   delay.value   = SETTINGS.delay;
  if (delVal)  delVal.textContent = SETTINGS.delay;
  if (preview) preview.checked = SETTINGS.previewMode;
  if (backend) backend.value = SETTINGS.backendUrl;
}

function registerSettingsListeners() {
  // Toggle drawer
  const toggle  = $("#settings-toggle");
  const drawer  = $("#settings-drawer");
  if (toggle && drawer) {
    toggle.addEventListener("click", () => {
      drawer.classList.toggle("open");
      toggle.classList.toggle("active");
    });
  }

  // Delay slider live update
  const delay  = $("#set-delay");
  const delVal = $("#delay-value");
  if (delay && delVal) {
    delay.addEventListener("input", () => { delVal.textContent = delay.value; });
  }

  // Save button
  const saveBtn = $("#save-settings-btn");
  if (saveBtn) {
    saveBtn.addEventListener("click", async () => {
      const apiKey     = ($("#set-api-key")?.value || "").trim();
      const model      = $("#set-model")?.value || "gemini-2.0-flash";
      const delayVal   = parseInt($("#set-delay")?.value || "800", 10);
      const previewVal = !!$("#set-preview")?.checked;
      const backendVal = ($("#set-backend")?.value || "http://localhost:8765").trim();

      // Validate API key (Gemini keys start with AIza)
      if (apiKey && !apiKey.startsWith("AIza")) {
        showToast("API key must start with AIza", "error");
        return;
      }

      SETTINGS = { apiKey: apiKey, model, delay: delayVal, previewMode: previewVal, backendUrl: backendVal };

      try {
        await chrome.storage.local.set(SETTINGS);
        showToast("Settings saved ✓", "success");
        pingBackend();
      } catch (e) {
        showToast("Failed to save settings", "error");
      }
    });
  }
}


// ═══════════════════════════════════════════════════════════════════════
// TABS
// ═══════════════════════════════════════════════════════════════════════

function registerTabListeners() {
  $$(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });
}

function switchTab(name) {
  // Update buttons
  $$(".tab-btn").forEach((b) => {
    const isActive = b.dataset.tab === name;
    b.classList.toggle("active", isActive);
    b.setAttribute("aria-selected", isActive);
  });

  // Update panels
  $$(".tab-panel").forEach((p) => {
    p.classList.toggle("active", p.id === `panel-${name}`);
  });

  // On-switch actions
  if (name === "memory") loadMemoryRules();
  if (name === "mcq")    renderMCQPanel();
}


// ═══════════════════════════════════════════════════════════════════════
// AGENT TAB
// ═══════════════════════════════════════════════════════════════════════

function registerAgentListeners() {
  const prompt = $("#prompt-input");

  // Enable Run button when there's text
  if (prompt) {
    prompt.addEventListener("input", () => {
      const hasText = prompt.value.trim().length > 0;
      const btnRun  = $("#btn-run");
      if (btnRun && !getState().running) btnRun.disabled = !hasText;
    });
  }

  // Scan Page
  $("#btn-scan")?.addEventListener("click", handleScan);

  // Run Agent
  $("#btn-run")?.addEventListener("click", handleRun);

  // Pause
  $("#btn-pause")?.addEventListener("click", () => {
    const st = getState();
    if (st.paused) {
      resumeAgent();
      appendLog("Resumed", "info");
    } else {
      pauseAgent();
      appendLog("Paused", "warn");
    }
  });

  // Stop
  $("#btn-stop")?.addEventListener("click", () => {
    stopAgent();
    appendLog("Stopped by user", "err");
  });

  // Confirm / Cancel plan
  $("#btn-confirm")?.addEventListener("click", () => {
    confirmPlan(true);
    $("#confirm-section")?.classList.add("hidden");
  });

  $("#btn-cancel-plan")?.addEventListener("click", () => {
    confirmPlan(false);
    $("#confirm-section")?.classList.add("hidden");
  });
}

async function handleScan() {
  const btnScan = $("#btn-scan");
  if (btnScan) { btnScan.disabled = true; btnScan.textContent = "Scanning…"; }
  setStatus("running", "Scanning page…");

  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tabs || !tabs.length) throw new Error("No active tab");

    const tab = tabs[0];

    // Guard: can't inject into chrome:// or extension pages
    if (!tab.url || tab.url.startsWith("chrome://") || tab.url.startsWith("chrome-extension://") || tab.url.startsWith("about:")) {
      throw new Error("Cannot scan browser internal pages. Navigate to a normal website first.");
    }

    // Helper: try to send SCAN, returns response or null on connection error
    const tryScan = () => new Promise((resolve) => {
      const timer = setTimeout(() => resolve(null), 6000);
      chrome.tabs.sendMessage(tab.id, { type: "SCAN" }, (r) => {
        clearTimeout(timer);
        if (chrome.runtime.lastError) resolve(null);
        else resolve(r);
      });
    });

    let response = await tryScan();

    // If no response, content scripts may not be injected yet — inject them now
    if (!response) {
      appendLog("Content script not ready — injecting…", "info");
      try {
        await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          files: [
            "content/annotation.js",
            "content/dom-sensor.js",
            "content/action-runner.js",
            "content/mcq-detector.js",
            "content/content.js",
          ],
        });
        await chrome.scripting.insertCSS({
          target: { tabId: tab.id },
          files: ["content/content.css"],
        }).catch(() => {});
        // Small wait for scripts to initialise
        await new Promise((r) => setTimeout(r, 500));
        response = await tryScan();
      } catch (injErr) {
        throw new Error(`Could not inject content scripts: ${injErr.message}`);
      }
    }

    if (!response) throw new Error("Content script did not respond after injection. Try refreshing the page.");

    if (response.success && response.data) {
      const data = response.data;
      const questions = data.mcq_questions || data.questions || [];
      setState({ questions });
      appendLog(`Scanned: ${data.url?.substring(0, 60) || "page"} — ${questions.length} question(s)`, "ok");
      setStatus("idle", `Scanned — ${questions.length} question(s) found`);

      if (questions.length > 0) {
        renderMCQPanel();
        switchTab("mcq");
        showToast(`${questions.length} question(s) detected`, "info");
      } else {
        showToast("Page scanned — no MCQ questions found", "info");
      }
    } else {
      appendLog("Scan returned no data", "warn");
      setStatus("idle", "Scan completed (no data)");
    }
  } catch (e) {
    appendLog(`Scan failed: ${e.message}`, "err");
    setStatus("error", "Scan failed");
    showToast(`Scan failed: ${e.message}`, "error");
  } finally {
    if (btnScan) { btnScan.disabled = false; btnScan.textContent = "🔍 Scan Page"; }
  }
}

async function handleRun() {
  const goal = ($("#prompt-input")?.value || "").trim();
  if (!goal) { showToast("Enter a goal first", "error"); return; }
  // API key is optional here — the backend uses its own .env GEMINI_API_KEY.
  // Only warn, don't block, so Pause/Stop buttons still get enabled.
  if (!SETTINGS.apiKey) { showToast("⚠ No API key set — using server key", "warn"); }

  // If preview mode: call /plan first
  if (SETTINGS.previewMode) {
    setStatus("running", "Planning…");
    appendLog("Preview mode: generating plan…", "info");
    // The agent-controller handles preview via confirmation gate
  }

  const settings = {
    require_confirmation: SETTINGS.previewMode,
    context: null,
  };

  // Update button states
  updateRunningButtons(true);

  try {
    await runAgent(goal, settings);
  } catch (e) {
    appendLog(`Run error: ${e.message}`, "err");
    showToast(`Run error: ${e.message}`, "error");
  } finally {
    updateRunningButtons(false);
  }
}

function updateRunningButtons(running) {
  const btnScan  = $("#btn-scan");
  const btnRun   = $("#btn-run");
  const btnPause = $("#btn-pause");
  const btnStop  = $("#btn-stop");

  if (btnScan)  btnScan.disabled  = running;
  if (btnRun)   btnRun.disabled   = running;
  if (btnPause) btnPause.disabled = !running;
  if (btnStop)  btnStop.disabled  = !running;
}


// ═══════════════════════════════════════════════════════════════════════
// MCQ TAB
// ═══════════════════════════════════════════════════════════════════════

function registerMCQListeners() {
  $("#btn-answer-all")?.addEventListener("click", handleAnswerAll);
}

function renderMCQPanel() {
  const state = getState();
  const questions = state.questions || [];
  const container = $("#mcq-questions");
  const summary   = $("#mcq-summary");
  const btnAll    = $("#btn-answer-all");

  if (!container) return;

  if (questions.length === 0) {
    container.innerHTML = '<div class="empty-state"><span class="empty-icon">📝</span><p>No questions detected.<br>Click "Scan Page" on a quiz page.</p></div>';
    if (summary) summary.textContent = "No questions detected";
    if (btnAll)  btnAll.disabled = true;
    return;
  }

  const answered = questions.filter((q) => q.answered).length;
  const total    = questions.length;
  if (summary) summary.textContent = `${total} question(s) detected — ${answered} answered — ${total - answered} remaining`;
  if (btnAll)  btnAll.disabled = answered >= total;

  container.innerHTML = questions.map((q, i) => {
    const qIdx  = q.qIdx ?? q.index ?? i;
    const qType = q.type || "radio";
    const typeName = { radio: "Single", checkbox: "Multi", dropdown: "Dropdown", card: "Custom", custom: "Custom" }[qType] || "Single";
    const cardClass = q.answered ? "q-card done" : "q-card";

    const optionsHTML = (q.options || []).map((opt, oi) => {
      let cls = "option-btn";
      let extra = "";
      if (opt._aiPick)   { cls += " ai-pick"; extra = `<span class="confidence-text">AI: ${Math.round((opt._confidence || 0) * 100)}% confident</span>`; }
      if (opt._selected) { cls += " selected"; }
      return `<button class="${cls}" data-q="${qIdx}" data-opt="${oi}">${esc(opt.text || `Option ${oi + 1}`)}${extra}</button>`;
    }).join("");

    return `
      <div class="${cardClass}" data-qidx="${qIdx}">
        <div class="q-card-top">
          <span class="q-number">Q${qIdx + 1}</span>
          <span class="q-type-badge">${typeName}</span>
        </div>
        <p class="q-text" data-qidx="${qIdx}">${esc(q.text || "")}</p>
        <div class="q-options">${optionsHTML}</div>
      </div>
    `;
  }).join("");

  // Attach option click listeners
  container.querySelectorAll(".option-btn").forEach((btn) => {
    btn.addEventListener("click", () => handleOptionClick(btn));
  });

  // Expand question text on click
  container.querySelectorAll(".q-text").forEach((el) => {
    el.addEventListener("click", () => el.classList.toggle("expanded"));
  });
}

async function handleOptionClick(btn) {
  const qIdx = parseInt(btn.dataset.q, 10);
  const optIdx = parseInt(btn.dataset.opt, 10);
  const state = getState();
  const questions = [...(state.questions || [])];
  const q = questions.find((x) => (x.qIdx ?? x.index ?? 0) === qIdx);
  if (!q) return;

  const opt = (q.options || [])[optIdx];
  if (!opt) return;

  btn.classList.add("selected");
  opt._selected = true;

  // Send select action to content script
  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tabs && tabs.length) {
      chrome.tabs.sendMessage(tabs[0].id, {
        type: "EXECUTE_ACTION",
        payload: {
          action: "SELECT",
          action_type: "SELECT",
          selector: opt.selector || null,
          value: opt.value || opt.text || null,
          text: opt.text || null,
        },
      });
    }
  } catch (e) {
    appendLog(`Option select error: ${e.message}`, "err");
  }

  // Update state
  q.answered = true;
  setState({ questions });
  renderMCQPanel();
  appendLog(`Selected option "${(opt.text || "").substring(0, 40)}" for Q${qIdx + 1}`, "ok");
}

async function handleAnswerAll() {
  const state = getState();
  const questions = state.questions || [];
  if (!questions.length) return;

  const btnAll = $("#btn-answer-all");
  if (btnAll) { btnAll.disabled = true; btnAll.textContent = "Solving…"; }
  appendLog("Sending questions to AI solver…", "ai");

  try {
    const resp = await apiFetch("POST", "/mcq/solve", {
      questions: questions.map((q, i) => ({
        qIdx: q.qIdx ?? q.index ?? i,
        text: q.text || "",
        type: q.type || "radio",
        options: (q.options || []).map((o, oi) => ({ idx: oi, text: o.text || "" })),
        answered: !!q.answered,
      })),
      user_instruction: ($("#prompt-input")?.value || "").trim(),
      context: "",
      page_url: "",
      page_title: "",
    });

    const answers = resp.answers || [];
    const updatedQs = [...questions];

    for (const ans of answers) {
      const q = updatedQs.find((x) => (x.qIdx ?? x.index ?? 0) === ans.question_id);
      if (!q) continue;

      for (const selIdx of (ans.selected_indices || [])) {
        if (q.options && q.options[selIdx]) {
          q.options[selIdx]._aiPick = true;
          q.options[selIdx]._confidence = ans.confidence || 0;
        }
      }
    }

    setState({ questions: updatedQs });
    renderMCQPanel();
    appendLog(`AI solved ${answers.length} question(s)`, "ok");
    showToast(`AI solved ${answers.length} question(s)`, "success");
  } catch (e) {
    appendLog(`AI solve error: ${e.message}`, "err");
    showToast("AI solve failed", "error");
  } finally {
    if (btnAll) { btnAll.disabled = false; btnAll.textContent = "🤖 Answer All with AI"; }
  }
}


// ═══════════════════════════════════════════════════════════════════════
// MEMORY TAB
// ═══════════════════════════════════════════════════════════════════════

function registerMemoryListeners() {
  $("#btn-mem-filter")?.addEventListener("click", () => loadMemoryRules());
}

async function loadMemoryRules() {
  const container = $("#memory-list-container");
  const countEl   = $("#memory-count");
  if (!container) return;

  container.innerHTML = '<p class="empty-state">Loading…</p>';

  const typeFilter   = ($("#mem-type-filter")?.value || "").trim();
  const domainFilter = ($("#mem-domain-filter")?.value || "").trim();

  let url = "/teach/all";
  const params = new URLSearchParams();
  if (typeFilter)   params.set("type", typeFilter);  // note: filtered server-side for user_rule/site only
  if (domainFilter) params.set("domain", domainFilter);
  const qs = params.toString();
  if (qs) url += `?${qs}`;

  try {
    const data = await apiFetch("GET", url);
    const rules = data.rules || [];

    if (countEl) countEl.textContent = `Showing ${rules.length} rule(s)`;

    if (rules.length === 0) {
      container.innerHTML = '<div class="empty-state"><span class="empty-icon">📦</span><p>No memory rules saved yet.<br>Use the Teach tab to add rules.</p></div>';
      return;
    }

    container.innerHTML = rules.map(renderMemoryCard).join("");
  } catch (e) {
    container.innerHTML = `<div class="empty-state">Could not load rules: ${esc(e.message)}</div>`;
    if (countEl) countEl.textContent = "";
  }
}

function renderMemoryCard(rule) {
  const memType = typeof rule.type === "string" ? rule.type : (rule.type || "user_rule");
  const confPct = Math.round((rule.confidence || 0) * 100);

  return `
    <div class="mem-card">
      <div class="mem-card-header">
        <div class="mem-badges">
          <span class="badge badge-type">${esc(memType)}</span>
          <span class="badge badge-scope">${esc(rule.scope || "global")}</span>
          ${rule.domain ? `<span class="badge badge-domain">${esc(rule.domain)}</span>` : ""}
        </div>
      </div>
      <p class="mem-instruction">${esc(rule.instruction || "")}</p>
      <div class="mem-meta">
        <div class="confidence-bar-track" title="Confidence: ${confPct}%">
          <div class="confidence-bar-fill" style="width:${confPct}%"></div>
        </div>
        <span class="mem-counts">✓ ${rule.success_count || 0} &nbsp; ✗ ${rule.failure_count || 0} &nbsp; ${confPct}%</span>
      </div>
    </div>
  `;
}


// ═══════════════════════════════════════════════════════════════════════
// LOG TAB
// ═══════════════════════════════════════════════════════════════════════

function registerLogListeners() {
  $("#btn-clear-log")?.addEventListener("click", () => {
    const container = $("#log-container");
    if (container) container.innerHTML = "";
    clearStateLog();
    appendLog("Log cleared", "info");
  });

  $("#btn-export-log")?.addEventListener("click", exportLog);
}

function appendLog(message, type = "info") {
  const container = $("#log-container");
  if (!container) return;

  const now = new Date();
  const ts  = `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}:${String(now.getSeconds()).padStart(2, "0")}`;

  const line = document.createElement("div");
  line.className = `log-line ${type}`;
  line.innerHTML = `<span class="ts">${ts}</span><span class="msg">${esc(message)}</span>`;
  container.appendChild(line);
  container.scrollTop = container.scrollHeight;
}

function exportLog() {
  const container = $("#log-container");
  if (!container) return;

  const lines = [];
  container.querySelectorAll(".log-line").forEach((el) => {
    const ts  = el.querySelector(".ts")?.textContent || "";
    const msg = el.querySelector(".msg")?.textContent || "";
    lines.push(`[${ts}] ${msg}`);
  });

  const blob = new Blob([lines.join("\n")], { type: "text/plain" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href     = url;
  a.download = "browser-agent-log.txt";
  a.click();
  URL.revokeObjectURL(url);

  showToast("Log exported ✓", "success");
}


// ═══════════════════════════════════════════════════════════════════════
// STATE CHANGE HANDLER
// ═══════════════════════════════════════════════════════════════════════

function handleStateChange(next, prev) {
  // ── Status bar ──
  if (next.running !== prev.running || next.paused !== prev.paused) {
    if (next.running && !next.paused)       setStatus("running", "Running…");
    else if (next.running && next.paused)    setStatus("paused", "Paused");
    else if (!next.running && prev.running)  setStatus("idle", "Done");
    updateRunningButtons(next.running);
  }

  // ── Step counter ──
  const counter = $("#step-counter");
  if (counter) {
    if (next.running && next.totalSteps > 0 && next.currentStep >= 0) {
      counter.textContent = `${next.currentStep + 1} / ${next.totalSteps}`;
      counter.classList.remove("hidden");
    } else {
      counter.classList.add("hidden");
    }
  }

  // ── Progress bar ──
  const progressWrap = $("#progress-wrap");
  const progressFill = $("#progress-fill");
  if (progressWrap && progressFill) {
    if (next.running && next.totalSteps > 0) {
      progressWrap.classList.remove("hidden");
      const pct = Math.round(((next.currentStep + 1) / next.totalSteps) * 100);
      progressFill.style.width = `${pct}%`;
    } else if (!next.running) {
      progressWrap.classList.add("hidden");
      progressFill.style.width = "0%";
    }
  }

  // ── Active task card ──
  const activeTask = $("#active-task");
  const activeText = $("#active-task-text");
  if (activeTask && activeText) {
    if (next.running && next.goal) {
      activeText.textContent = next.goal;
      activeTask.classList.remove("hidden");
    } else if (!next.running) {
      activeTask.classList.add("hidden");
    }
  }

  // ── Confirmation gate ──
  const confirmSection = $("#confirm-section");
  if (confirmSection) {
    if (next.awaitingConfirmation) {
      confirmSection.classList.remove("hidden");
    } else if (prev.awaitingConfirmation) {
      confirmSection.classList.add("hidden");
    }
  }

  // ── Step log (agent tab) ──
  if (next.log !== prev.log && next.log.length > 0) {
    const agentLog = $("#agent-log");
    if (agentLog) {
      agentLog.innerHTML = next.log.map((e) => `
        <div class="step-entry ${e.status}">
          <span class="step-icon">${esc(e.icon)}</span>
          <span class="step-text">${esc(e.text)}</span>
        </div>
      `).join("");
    }

    // Also mirror to the Log tab
    const latest = next.log[0];
    if (latest && (!prev.log.length || prev.log[0]?.id !== latest.id)) {
      const typeMap = { success: "ok", error: "err", warning: "warn", pending: "info" };
      appendLog(latest.text, typeMap[latest.status] || "info");
    }
  }

  // ── MCQ questions ──
  if (next.questions !== prev.questions) {
    renderMCQPanel();
  }

  // ── Connection ──
  if (next.connected !== prev.connected) {
    updateConnectionBadge(next.connected);
  }

  // ── Pause button text ──
  const btnPause = $("#btn-pause");
  if (btnPause) {
    btnPause.textContent = next.paused ? "▶ Resume" : "⏸ Pause";
  }
}


// ═══════════════════════════════════════════════════════════════════════
// HEALTH CHECK
// ═══════════════════════════════════════════════════════════════════════

async function pingBackend() {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 2000);
    const resp = await fetch(`${SETTINGS.backendUrl}/health`, { signal: controller.signal });
    clearTimeout(timer);

    if (resp.ok) {
      setState({ connected: true });
      updateConnectionBadge(true);
    } else {
      setState({ connected: false });
      updateConnectionBadge(false);
    }
  } catch (e) {
    setState({ connected: false });
    updateConnectionBadge(false);
  }
}

function updateConnectionBadge(online) {
  const badge = $("#conn-badge");
  const label = $("#conn-label");
  if (!badge) return;

  badge.classList.toggle("online", online);
  badge.classList.toggle("offline", !online);
  if (label) label.textContent = online ? "Connected" : "Offline";
}


// ═══════════════════════════════════════════════════════════════════════
// STATUS BAR
// ═══════════════════════════════════════════════════════════════════════

function setStatus(dotClass, text) {
  const dot  = $("#status-dot");
  const txt  = $("#status-text");
  if (dot) {
    dot.className = `status-dot ${dotClass}`;
  }
  if (txt) txt.textContent = text;
}


// ═══════════════════════════════════════════════════════════════════════
// TOASTS
// ═══════════════════════════════════════════════════════════════════════

function showToast(message, type = "info", durationMs = 3000) {
  const container = $("#toast-container");
  if (!container) return;

  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  container.appendChild(toast);

  setTimeout(() => {
    toast.classList.add("fade-out");
    setTimeout(() => toast.remove(), 220);
  }, durationMs);
}


// ═══════════════════════════════════════════════════════════════════════
// KEYBOARD SHORTCUTS
// ═══════════════════════════════════════════════════════════════════════

function registerKeyboardShortcuts() {
  document.addEventListener("keydown", (e) => {
    // Ctrl+Enter in prompt → Run
    if (e.ctrlKey && e.key === "Enter") {
      const prompt = $("#prompt-input");
      if (document.activeElement === prompt && !getState().running) {
        e.preventDefault();
        handleRun();
      }
    }

    // Escape → Stop
    if (e.key === "Escape" && getState().running) {
      stopAgent();
      appendLog("Stopped via Escape", "err");
    }
  });
}


// ═══════════════════════════════════════════════════════════════════════
// EXTENSION MESSAGING
// ═══════════════════════════════════════════════════════════════════════

function handleExtensionMessage(message, _sender, _sendResponse) {
  if (!message || !message.type) return;

  switch (message.type) {
    case "SCAN_RESULT":
      if (message.data) {
        const qs = message.data.mcq_questions || message.data.questions || [];
        setState({ questions: qs });
        if (qs.length > 0) renderMCQPanel();
      }
      break;

    case "MCQ_DETECTED":
      if (message.questions) {
        setState({ questions: message.questions });
        renderMCQPanel();
        showToast(`${message.questions.length} question(s) detected`, "info");
      }
      break;

    case "STEP_RESULT":
      if (message.result) {
        const r = message.result;
        const icon = r.success ? "✓" : "✕";
        const status = r.success ? "success" : "error";
        addLogEntry(status, icon, r.action_taken || r.error || "Step result");
      }
      break;

    // Phase 9: tab changes from background
    case "TAB_CHANGED":
      reloadTabsPanel();
      break;
  }
}


// ═══════════════════════════════════════════════════════════════════════
// UTILITIES
// ═══════════════════════════════════════════════════════════════════════

function esc(str) {
  const el = document.createElement("span");
  el.textContent = str || "";
  return el.innerHTML;
}

async function apiFetch(method, path, body = null) {
  const opts = {
    method,
    headers: { "Content-Type": "application/json" },
  };
  if (body !== null) opts.body = JSON.stringify(body);

  const resp = await fetch(`${SETTINGS.backendUrl}${path}`, opts);
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`${resp.status}: ${text.slice(0, 200)}`);
  }
  return resp.json();
}
