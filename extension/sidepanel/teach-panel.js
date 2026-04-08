/**
 * BrowserAgent — Teach Panel UI (Phase 7)
 *
 * Vanilla JS module for the Teach tab in the Chrome extension side panel.
 * Handles: teaching prompt entry, rule preview, rule CRUD, conflict display.
 *
 * Uses the REST API:
 *   GET  /teach/preview  — preview parsed rule
 *   POST /teach          — save a rule
 *   GET  /teach/all      — list all rules
 *   DELETE /teach/{id}   — delete a rule
 *   PATCH /teach/{id}    — update a rule
 *   GET  /teach/conflicts — check for conflicts
 *
 * Styles use CSS variables from sidepanel.css.
 */

/* global chrome */

const API_BASE = "http://localhost:8765";

// ── Helpers ──────────────────────────────────────────────────────────

function esc(str) {
  const el = document.createElement("span");
  el.textContent = str || "";
  return el.innerHTML;
}

async function api(method, path, body = null) {
  const opts = {
    method,
    headers: { "Content-Type": "application/json" },
  };
  if (body !== null) opts.body = JSON.stringify(body);
  const resp = await fetch(`${API_BASE}${path}`, opts);
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`${resp.status}: ${text.slice(0, 200)}`);
  }
  return resp.json();
}

function showToast(container, message, type = "success") {
  const toast = document.createElement("div");
  toast.className = `teach-toast teach-toast--${type}`;
  toast.textContent = message;
  container.prepend(toast);
  setTimeout(() => toast.remove(), 3500);
}

// ── Init ─────────────────────────────────────────────────────────────

export function initTeachPanel(containerElement) {
  containerElement.innerHTML = `
    <!-- Teach Input -->
    <section class="teach-section">
      <div class="input-group">
        <label for="teach-prompt" class="input-label">Teach the Agent</label>
        <textarea
          id="teach-prompt"
          class="task-textarea"
          placeholder="e.g. On Coursera, always wait 2 seconds after clicking Next before scanning the page"
          rows="3"
        ></textarea>
      </div>

      <div class="teach-selectors">
        <div class="input-group teach-select-group">
          <label for="teach-scope-select" class="input-label">Scope</label>
          <select id="teach-scope-select" class="teach-select">
            <option value="global">Global</option>
            <option value="domain">This Domain</option>
            <option value="page_pattern">Page Pattern</option>
          </select>
        </div>
        <div class="input-group teach-select-group">
          <label for="teach-task-select" class="input-label">Task Type</label>
          <select id="teach-task-select" class="teach-select">
            <option value="general">General</option>
            <option value="mcq">MCQ</option>
            <option value="form">Form Filling</option>
            <option value="navigation">Navigation</option>
          </select>
        </div>
      </div>

      <div class="teach-actions">
        <button id="teach-preview-btn" class="btn btn-ghost" disabled>
          <span class="btn-icon">👁</span> Preview Rule
        </button>
        <button id="teach-save-btn" class="btn btn-primary" disabled>
          <span class="btn-icon">💾</span> Save Rule
        </button>
      </div>
    </section>

    <!-- Preview Section (hidden until Preview clicked) -->
    <section id="teach-preview-section" class="teach-section teach-hidden">
      <div class="teach-section-header">
        <h3 class="teach-section-title">Preview</h3>
      </div>
      <div id="teach-preview-content"></div>
      <div id="teach-preview-warnings"></div>
      <div id="teach-preview-similar"></div>
    </section>

    <!-- Saved Rules -->
    <section class="teach-section">
      <div class="teach-section-header">
        <h3 class="teach-section-title">Saved Rules</h3>
        <button id="teach-refresh-btn" class="btn btn-ghost btn-sm">↻ Refresh</button>
      </div>
      <div id="teach-rules-list"></div>
    </section>

    <!-- Conflicts -->
    <section id="teach-conflicts-section" class="teach-section teach-hidden">
      <div class="teach-section-header">
        <h3 class="teach-section-title" style="color: var(--error);">⚠ Conflicts</h3>
      </div>
      <div id="teach-conflicts-list"></div>
    </section>
  `;

  // ── DOM refs
  const prompt = containerElement.querySelector("#teach-prompt");
  const scopeSelect = containerElement.querySelector("#teach-scope-select");
  const taskSelect = containerElement.querySelector("#teach-task-select");
  const previewBtn = containerElement.querySelector("#teach-preview-btn");
  const saveBtn = containerElement.querySelector("#teach-save-btn");
  const refreshBtn = containerElement.querySelector("#teach-refresh-btn");
  const previewSection = containerElement.querySelector("#teach-preview-section");
  const previewContent = containerElement.querySelector("#teach-preview-content");
  const previewWarnings = containerElement.querySelector("#teach-preview-warnings");
  const previewSimilar = containerElement.querySelector("#teach-preview-similar");
  const rulesList = containerElement.querySelector("#teach-rules-list");
  const conflictsSection = containerElement.querySelector("#teach-conflicts-section");
  const conflictsList = containerElement.querySelector("#teach-conflicts-list");

  let _lastParsed = null;

  // ── Input validation
  prompt.addEventListener("input", () => {
    const hasText = prompt.value.trim().length > 0;
    previewBtn.disabled = !hasText;
    saveBtn.disabled = !hasText;
  });

  // ── Preview
  previewBtn.addEventListener("click", async () => {
    const text = prompt.value.trim();
    if (!text) return;
    previewBtn.disabled = true;
    previewBtn.textContent = "Parsing…";
    try {
      await previewRule(
        text,
        scopeSelect.value,
        taskSelect.value,
        previewSection,
        previewContent,
        previewWarnings,
        previewSimilar
      );
    } catch (err) {
      showToast(containerElement, `Preview failed: ${err.message}`, "error");
    } finally {
      previewBtn.disabled = false;
      previewBtn.innerHTML = '<span class="btn-icon">👁</span> Preview Rule';
    }
  });

  // ── Save
  saveBtn.addEventListener("click", async () => {
    const text = prompt.value.trim();
    if (!text) return;
    saveBtn.disabled = true;
    saveBtn.textContent = "Saving…";
    try {
      await saveRule(
        text,
        scopeSelect.value,
        taskSelect.value,
        containerElement,
        rulesList
      );
      prompt.value = "";
      previewBtn.disabled = true;
      saveBtn.disabled = true;
      previewSection.classList.add("teach-hidden");
    } catch (err) {
      showToast(containerElement, `Save failed: ${err.message}`, "error");
    } finally {
      saveBtn.disabled = false;
      saveBtn.innerHTML = '<span class="btn-icon">💾</span> Save Rule';
    }
  });

  // ── Refresh
  refreshBtn.addEventListener("click", () => {
    loadAllRules(rulesList, containerElement);
    loadConflicts(conflictsSection, conflictsList);
  });

  // ── Initial load
  loadAllRules(rulesList, containerElement);
  loadConflicts(conflictsSection, conflictsList);
}


// ── Preview Rule ─────────────────────────────────────────────────────

async function previewRule(
  promptText, scope, taskType,
  previewSection, previewContent, previewWarnings, previewSimilar
) {
  const params = new URLSearchParams({ text: promptText });
  if (scope && scope !== "global") params.append("scope", scope);

  const data = await api("GET", `/teach/preview?${params}`);
  const parsed = data.parsed;
  const validation = data.validation || {};
  const similar = data.similar_rules || [];

  // Show the preview section
  previewSection.classList.remove("teach-hidden");

  // Render parsed rule card
  previewContent.innerHTML = renderRuleCard(parsed, { showActions: false });

  // Render warnings
  const warns = validation.warnings || [];
  if (warns.length > 0) {
    previewWarnings.innerHTML = warns
      .map((w) => `<div class="teach-warning">⚠ ${esc(w)}</div>`)
      .join("");
  } else {
    previewWarnings.innerHTML = '<div class="teach-success">✓ Validation passed</div>';
  }

  // Render similar rules
  if (similar.length > 0) {
    previewSimilar.innerHTML =
      '<div class="teach-similar-header">Similar existing rules:</div>' +
      similar.map((r) => renderRuleCard(r, { compact: true })).join("");
  } else {
    previewSimilar.innerHTML = "";
  }
}


// ── Save Rule ────────────────────────────────────────────────────────

async function saveRule(promptText, scope, taskType, container, rulesList) {
  const payload = {
    raw_text: promptText,
    scope: scope || "global",
    task_type: taskType || "general",
    domain: null,
    trigger: null,
    preferred_behavior: null,
    avoid_behavior: null,
    priority: 5,
  };

  const data = await api("POST", "/teach", payload);

  if (data.saved) {
    showToast(container, "Rule saved ✓", "success");
    // Prepend the new card
    const card = document.createElement("div");
    card.innerHTML = renderRuleCard(data.memory_item);
    if (rulesList.firstChild) {
      rulesList.insertBefore(card.firstElementChild, rulesList.firstChild);
    } else {
      rulesList.innerHTML = "";
      rulesList.appendChild(card.firstElementChild);
    }
    _attachCardListeners(rulesList, container);
  } else {
    // Duplicate or validation failure
    const warns = data.warnings || [];
    const msg = warns.length > 0 ? warns[0] : "Rule was not saved";
    showToast(container, msg, "warning");
  }
}


// ── Load All Rules ───────────────────────────────────────────────────

async function loadAllRules(rulesList, container) {
  rulesList.innerHTML = '<p class="empty-state">Loading…</p>';

  try {
    const data = await api("GET", "/teach/all");
    const rules = data.rules || [];

    if (rules.length === 0) {
      rulesList.innerHTML =
        '<p class="empty-state">No rules yet. Teach the agent something!</p>';
      return;
    }

    rulesList.innerHTML = rules.map((r) => renderRuleCard(r)).join("");
    _attachCardListeners(rulesList, container);
  } catch (err) {
    rulesList.innerHTML =
      `<p class="empty-state">Could not load rules: ${esc(err.message)}</p>`;
  }
}


// ── Delete Rule ──────────────────────────────────────────────────────

async function deleteRule(memoryId, rulesList, container) {
  if (!confirm("Delete this rule?")) return;

  try {
    await api("DELETE", `/teach/${memoryId}`);
    const card = rulesList.querySelector(`[data-rule-id="${memoryId}"]`);
    if (card) card.remove();
    showToast(container, "Rule deleted", "success");

    // Check if list is now empty
    if (!rulesList.children.length) {
      rulesList.innerHTML =
        '<p class="empty-state">No rules yet. Teach the agent something!</p>';
    }
  } catch (err) {
    showToast(container, `Delete failed: ${err.message}`, "error");
  }
}


// ── Edit Rule ────────────────────────────────────────────────────────

function editRule(memoryId, currentInstruction, rulesList, container) {
  const card = rulesList.querySelector(`[data-rule-id="${memoryId}"]`);
  if (!card) return;

  const origHTML = card.innerHTML;

  card.innerHTML = `
    <div class="teach-edit-form">
      <textarea class="task-textarea teach-edit-textarea" rows="3">${esc(currentInstruction)}</textarea>
      <div class="teach-edit-actions">
        <button class="btn btn-primary btn-sm teach-edit-save">Save</button>
        <button class="btn btn-ghost btn-sm teach-edit-cancel">Cancel</button>
      </div>
    </div>
  `;

  const textarea = card.querySelector(".teach-edit-textarea");
  const saveEditBtn = card.querySelector(".teach-edit-save");
  const cancelBtn = card.querySelector(".teach-edit-cancel");

  cancelBtn.addEventListener("click", () => {
    card.innerHTML = origHTML;
    _attachCardListeners(rulesList, container);
  });

  saveEditBtn.addEventListener("click", async () => {
    const newInstruction = textarea.value.trim();
    if (!newInstruction) return;

    saveEditBtn.disabled = true;
    saveEditBtn.textContent = "Saving…";

    try {
      const updated = await api("PATCH", `/teach/${memoryId}`, {
        instruction: newInstruction,
      });

      // Replace the card with updated content
      const wrapper = document.createElement("div");
      wrapper.innerHTML = renderRuleCard(updated);
      card.replaceWith(wrapper.firstElementChild);
      _attachCardListeners(rulesList, container);
      showToast(container, "Rule updated ✓", "success");
    } catch (err) {
      showToast(container, `Update failed: ${err.message}`, "error");
      card.innerHTML = origHTML;
      _attachCardListeners(rulesList, container);
    }
  });
}


// ── Render Rule Card ─────────────────────────────────────────────────

function renderRuleCard(rule, opts = {}) {
  const compact = opts.compact || false;
  const showActions = opts.showActions !== false;

  const memType = typeof rule.type === "object" ? rule.type : rule.type || "user_rule";
  const confidencePct = Math.round((rule.confidence || 0) * 100);
  const successCount = rule.success_count || 0;
  const failureCount = rule.failure_count || 0;

  // Scope badge colour
  let scopeClass = "teach-badge--global";
  if (rule.scope === "domain") scopeClass = "teach-badge--domain";
  if (rule.scope === "page_pattern") scopeClass = "teach-badge--page";

  // Triggers
  const triggers = rule.trigger_conditions || [];
  const preferred = rule.preferred_actions || [];
  const avoid = rule.avoid_actions || [];

  let actionsHTML = "";
  if (showActions) {
    actionsHTML = `
      <div class="teach-card-actions">
        <button class="btn btn-ghost btn-sm teach-edit-btn" data-id="${esc(rule.memory_id)}" data-instruction="${esc(rule.instruction)}">
          ✏ Edit
        </button>
        <button class="btn btn-ghost btn-sm teach-delete-btn" data-id="${esc(rule.memory_id)}">
          ✕ Delete
        </button>
      </div>
    `;
  }

  let detailsHTML = "";
  if (!compact) {
    detailsHTML = `
      ${triggers.length > 0 ? `
        <details class="teach-card-details">
          <summary class="teach-card-details-summary">Triggers (${triggers.length})</summary>
          <ul class="teach-card-list">
            ${triggers.map((t) => `<li>${esc(t)}</li>`).join("")}
          </ul>
        </details>
      ` : ""}
      ${preferred.length > 0 ? `
        <details class="teach-card-details">
          <summary class="teach-card-details-summary">Preferred actions (${preferred.length})</summary>
          <ul class="teach-card-list">
            ${preferred.map((a) => `<li>${esc(a)}</li>`).join("")}
          </ul>
        </details>
      ` : ""}
      ${avoid.length > 0 ? `
        <details class="teach-card-details">
          <summary class="teach-card-details-summary">Avoid actions (${avoid.length})</summary>
          <ul class="teach-card-list teach-card-list--avoid">
            ${avoid.map((a) => `<li>${esc(a)}</li>`).join("")}
          </ul>
        </details>
      ` : ""}
    `;
  }

  return `
    <div class="teach-rule-card" data-rule-id="${esc(rule.memory_id)}">
      <div class="teach-card-header">
        <div class="teach-card-badges">
          <span class="teach-badge teach-badge--type">${esc(memType)}</span>
          <span class="teach-badge ${scopeClass}">${esc(rule.scope)}</span>
          ${rule.domain ? `<span class="teach-badge teach-badge--domain">${esc(rule.domain)}</span>` : ""}
        </div>
        ${actionsHTML}
      </div>
      <p class="teach-card-instruction">${esc(rule.instruction)}</p>
      <div class="teach-card-meta">
        <div class="teach-confidence-bar" title="Confidence: ${confidencePct}%">
          <div class="teach-confidence-fill" style="width: ${confidencePct}%"></div>
          <span class="teach-confidence-label">${confidencePct}%</span>
        </div>
        <span class="teach-card-counts">✓ ${successCount} &nbsp;✗ ${failureCount}</span>
      </div>
      ${detailsHTML}
    </div>
  `;
}


// ── Conflicts ────────────────────────────────────────────────────────

async function loadConflicts(conflictsSection, conflictsList) {
  try {
    const data = await api("GET", "/teach/conflicts");
    const conflicts = data.conflicts || [];

    if (conflicts.length === 0) {
      conflictsSection.classList.add("teach-hidden");
      return;
    }

    conflictsSection.classList.remove("teach-hidden");
    conflictsList.innerHTML = conflicts
      .map(
        (c) => `
        <div class="teach-conflict-pair">
          <div class="teach-conflict-reason">⚠ ${esc(c.reason)}</div>
          <div class="teach-conflict-rules">
            <div class="teach-conflict-rule">
              <span class="teach-conflict-label">Rule A:</span>
              ${esc(c.rule_a.instruction || "")}
            </div>
            <div class="teach-conflict-rule">
              <span class="teach-conflict-label">Rule B:</span>
              ${esc(c.rule_b.instruction || "")}
            </div>
          </div>
        </div>
      `
      )
      .join("");
  } catch (err) {
    conflictsSection.classList.add("teach-hidden");
  }
}


// ── Card Listener Attacher ───────────────────────────────────────────

function _attachCardListeners(rulesList, container) {
  rulesList.querySelectorAll(".teach-delete-btn").forEach((btn) => {
    btn.onclick = () => deleteRule(btn.dataset.id, rulesList, container);
  });
  rulesList.querySelectorAll(".teach-edit-btn").forEach((btn) => {
    btn.onclick = () =>
      editRule(btn.dataset.id, btn.dataset.instruction, rulesList, container);
  });
}
