/**
 * BrowserAgent — Memory Panel (Phase 10)
 *
 * Complete Memory tab UI module with two sub-tabs:
 *   • Rules — memory rule cards with inline edit, delete, test
 *   • Site Profiles — per-domain profile cards
 *
 * Includes export/import for full memory backup & restore.
 *
 * Uses the same CSS variables as sidepanel.css.
 */

/* global chrome */

const API_BASE = "http://localhost:8765";

// ── Helpers ──────────────────────────────────────────────────────────

function esc(str) {
  const el = document.createElement("span");
  el.textContent = str || "";
  return el.innerHTML;
}

function showToast(msg, type = "info") {
  const container = document.getElementById("toast-container");
  if (!container) return;
  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.textContent = msg;
  container.appendChild(toast);
  setTimeout(() => toast.classList.add("fade-out"), 3000);
  setTimeout(() => toast.remove(), 3500);
}

// ── State ────────────────────────────────────────────────────────────

let _container = null;
let _rules = [];
let _profiles = [];
let _activeSubTab = "rules";
let _backendUrl = API_BASE;

// ── Public API ───────────────────────────────────────────────────────

/**
 * Initialise the memory panel inside the given container.
 * @param {HTMLElement} containerElement
 */
export function initMemoryPanel(containerElement) {
  _container = containerElement;

  // Try to load settings for backend URL
  try {
    chrome.storage?.local?.get(["settings"], (d) => {
      if (d?.settings?.backendUrl) _backendUrl = d.settings.backendUrl;
    });
  } catch (_) {}

  _render();
  loadRules();
}

// ── Root Render ──────────────────────────────────────────────────────

function _render() {
  if (!_container) return;

  _container.innerHTML = `
    <div class="mp-wrapper">
      <!-- Top Bar -->
      <div class="mp-topbar">
        <span class="mp-count" id="mp-count">0 rules</span>
        <div class="mp-topbar-actions">
          <button class="btn btn-ghost btn-sm" id="mp-export">⬇ Export</button>
          <button class="btn btn-ghost btn-sm" id="mp-import">⬆ Import</button>
          <input type="file" id="mp-import-file" accept=".json" style="display:none">
        </div>
      </div>

      <!-- Filter Section -->
      <div class="mp-filters">
        <select class="setting-input filter-select" id="mp-type-filter">
          <option value="">All types</option>
          <option value="user_rule">User Rules</option>
          <option value="site">Site Rules</option>
          <option value="semantic">Semantic</option>
          <option value="episodic">Episodic</option>
        </select>
        <input type="text" class="setting-input filter-input" id="mp-domain-filter" placeholder="Domain…">
        <button class="btn btn-ghost btn-sm" id="mp-apply-filter">Apply</button>
      </div>

      <!-- Sub-Tabs -->
      <div class="mp-subtabs">
        <button class="mp-subtab active" data-subtab="rules">Rules</button>
        <button class="mp-subtab" data-subtab="profiles">Site Profiles</button>
      </div>

      <!-- Rules Container -->
      <div class="mp-panel" id="mp-rules-panel">
        <div class="mp-list" id="mp-rules-list">
          <div class="mp-empty">
            <span class="empty-icon">📦</span>
            <p>No memory rules saved yet.<br>Use the Teach tab to add rules.</p>
          </div>
        </div>
      </div>

      <!-- Site Profiles Container -->
      <div class="mp-panel hidden" id="mp-profiles-panel">
        <div class="mp-list" id="mp-profiles-list">
          <div class="mp-empty">
            <span class="empty-icon">🌐</span>
            <p>No site profiles yet.<br>Run the agent on a website to generate one.</p>
          </div>
        </div>
      </div>
    </div>
  `;

  _injectStyles();
  _bindEvents();
}

function _bindEvents() {
  // Sub-tabs
  _container.querySelectorAll(".mp-subtab").forEach((btn) => {
    btn.addEventListener("click", () => {
      _activeSubTab = btn.dataset.subtab;
      _container.querySelectorAll(".mp-subtab").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");

      const rulesPanel = _container.querySelector("#mp-rules-panel");
      const profilesPanel = _container.querySelector("#mp-profiles-panel");
      if (_activeSubTab === "rules") {
        rulesPanel?.classList.remove("hidden");
        profilesPanel?.classList.add("hidden");
      } else {
        rulesPanel?.classList.add("hidden");
        profilesPanel?.classList.remove("hidden");
        loadSiteProfiles();
      }
    });
  });

  // Export
  _container.querySelector("#mp-export")?.addEventListener("click", exportMemory);

  // Import
  _container.querySelector("#mp-import")?.addEventListener("click", () => {
    _container.querySelector("#mp-import-file")?.click();
  });
  _container.querySelector("#mp-import-file")?.addEventListener("change", (e) => {
    if (e.target.files?.[0]) importMemory(e.target.files[0]);
  });

  // Filters
  _container.querySelector("#mp-apply-filter")?.addEventListener("click", () => {
    const type = _container.querySelector("#mp-type-filter")?.value || "";
    const domain = _container.querySelector("#mp-domain-filter")?.value || "";
    loadRules({ type, domain });
  });
}

// ── Rules ────────────────────────────────────────────────────────────

export async function loadRules(filters = {}) {
  try {
    let url = `${_backendUrl}/teach/all`;
    const params = new URLSearchParams();
    if (filters.type) params.set("type", filters.type);
    if (filters.domain) params.set("domain", filters.domain);
    const qs = params.toString();
    if (qs) url += `?${qs}`;

    // Quick 2 s timeout — if server isn't up yet this fails fast & silently
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 2000);
    let resp;
    try {
      resp = await fetch(url, { signal: controller.signal });
    } finally {
      clearTimeout(timer);
    }
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    _rules = Array.isArray(data) ? data : data.rules || data.items || [];
  } catch (e) {
    // Suppress noise when the backend simply isn't running yet
    if (e.name !== "AbortError" && !e.message?.includes("fetch")) {
      console.warn("[MemoryPanel] loadRules failed:", e.message);
    }
    _rules = [];
  }

  _renderRules();
}

function _renderRules() {
  const list = _container?.querySelector("#mp-rules-list");
  const countEl = _container?.querySelector("#mp-count");
  if (!list) return;

  if (countEl) countEl.textContent = `${_rules.length} rule${_rules.length !== 1 ? "s" : ""}`;

  if (_rules.length === 0) {
    list.innerHTML = `
      <div class="mp-empty">
        <span class="empty-icon">📦</span>
        <p>No memory rules found.</p>
      </div>`;
    return;
  }

  list.innerHTML = _rules.map((rule) => renderRuleCard(rule)).join("");

  // Attach event listeners
  list.querySelectorAll(".mp-card").forEach((card) => {
    const mid = card.dataset.memoryid;

    // Toggle details on click
    card.querySelector(".mp-card-body")?.addEventListener("click", () => {
      const details = card.querySelector(".mp-card-details");
      if (details) details.classList.toggle("hidden");
    });

    // Edit button
    card.querySelector(".mp-btn-edit")?.addEventListener("click", (e) => {
      e.stopPropagation();
      const rule = _rules.find(r => (r.memory_id || r.id) === mid);
      if (rule) editRule(mid, rule, card);
    });

    // Delete button
    card.querySelector(".mp-btn-delete")?.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteRule(mid);
    });

    // Test button
    card.querySelector(".mp-btn-test")?.addEventListener("click", (e) => {
      e.stopPropagation();
      testRule(mid);
    });
  });
}

function renderRuleCard(rule) {
  const mid = rule.memory_id || rule.id || "";
  const instruction = rule.instruction || "";
  const type = rule.type || "semantic";
  const scope = rule.scope || "global";
  const domain = rule.domain || "";
  const confidence = typeof rule.confidence === "number" ? rule.confidence : 1.0;
  const confPct = Math.round(confidence * 100);
  const successCount = rule.success_count || 0;
  const failureCount = rule.failure_count || 0;
  const triggers = rule.trigger_conditions || [];
  const preferred = rule.preferred_actions || [];
  const avoid = rule.avoid_actions || [];

  // Type badge color
  const typeColors = {
    user_rule: "var(--teal, #4f98a3)",
    site: "var(--gold, #e8af34)",
    semantic: "var(--muted, #797876)",
    episodic: "var(--faint, #5a5957)",
  };
  const typeColor = typeColors[type] || typeColors.semantic;

  // Scope display
  const scopeLabels = { global: "Global", domain: "Domain", page_pattern: "Page Pattern" };
  const scopeLabel = scopeLabels[scope] || scope;

  // Triggers tags
  const triggerTags = triggers.map(t => `<span class="mp-tag">${esc(t)}</span>`).join("");
  const preferredTags = preferred.map(t => `<span class="mp-tag mp-tag-green">${esc(t)}</span>`).join("");
  const avoidTags = avoid.map(t => `<span class="mp-tag mp-tag-red">${esc(t)}</span>`).join("");

  return `
    <div class="mp-card" data-memoryid="${esc(mid)}">
      <div class="mp-card-body">
        <div class="mp-card-header">
          <span class="mp-type-badge" style="color:${typeColor};border-color:${typeColor}">${esc(type.replace("_", " "))}</span>
          <span class="mp-scope-badge">${esc(scopeLabel)}</span>
          ${domain ? `<span class="mp-domain-pill">${esc(domain)}</span>` : ""}
        </div>
        <p class="mp-instruction">${esc(instruction)}</p>
        <div class="mp-conf-row">
          <div class="mp-conf-track"><div class="mp-conf-fill" style="width:${confPct}%"></div></div>
          <span class="mp-conf-pct">${confPct}%</span>
        </div>
        <div class="mp-stats">
          <span class="mp-stat-ok">✓ ${successCount}</span>
          <span class="mp-stat-fail">✗ ${failureCount}</span>
        </div>
      </div>

      <div class="mp-card-details hidden">
        ${triggers.length ? `<div class="mp-detail-section"><span class="mp-detail-label">Triggers:</span><div class="mp-tags">${triggerTags}</div></div>` : ""}
        ${preferred.length ? `<div class="mp-detail-section"><span class="mp-detail-label">Preferred:</span><div class="mp-tags">${preferredTags}</div></div>` : ""}
        ${avoid.length ? `<div class="mp-detail-section"><span class="mp-detail-label">Avoid:</span><div class="mp-tags">${avoidTags}</div></div>` : ""}
      </div>

      <div class="mp-card-actions">
        <button class="mp-action-btn mp-btn-edit" title="Edit">✏️</button>
        <button class="mp-action-btn mp-btn-delete" title="Delete">🗑️</button>
        <button class="mp-action-btn mp-btn-test" title="Test on next run">▶️</button>
      </div>
    </div>
  `;
}

// ── Edit / Delete / Test ─────────────────────────────────────────────

function editRule(memoryId, currentData, cardEl) {
  const instruction = currentData.instruction || "";
  const confidence = typeof currentData.confidence === "number"
    ? Math.round(currentData.confidence * 100) : 100;

  cardEl.innerHTML = `
    <div class="mp-edit-form">
      <label class="mp-edit-label">Instruction</label>
      <textarea class="mp-edit-textarea" id="mp-edit-inst" rows="3">${esc(instruction)}</textarea>
      <label class="mp-edit-label">Confidence (%)</label>
      <input type="number" class="mp-edit-input" id="mp-edit-conf" min="0" max="100" value="${confidence}">
      <div class="mp-edit-actions">
        <button class="btn btn-primary btn-sm" id="mp-edit-save">Save</button>
        <button class="btn btn-ghost btn-sm" id="mp-edit-cancel">Cancel</button>
      </div>
    </div>
  `;

  cardEl.querySelector("#mp-edit-save")?.addEventListener("click", async () => {
    const newInst = cardEl.querySelector("#mp-edit-inst")?.value || instruction;
    const newConf = parseInt(cardEl.querySelector("#mp-edit-conf")?.value || "100", 10) / 100;

    try {
      const resp = await fetch(`${_backendUrl}/teach/${memoryId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ instruction: newInst, confidence: Math.min(Math.max(newConf, 0), 1) }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      showToast("Rule updated", "success");
      loadRules();
    } catch (e) {
      showToast(`Update failed: ${e.message}`, "error");
    }
  });

  cardEl.querySelector("#mp-edit-cancel")?.addEventListener("click", () => loadRules());
}

async function deleteRule(memoryId) {
  if (!confirm("Delete this rule? This cannot be undone.")) return;

  try {
    const resp = await fetch(`${_backendUrl}/teach/${memoryId}`, {
      method: "DELETE",
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

    // Fade out animation
    const card = _container?.querySelector(`.mp-card[data-memoryid="${memoryId}"]`);
    if (card) {
      card.style.transition = "opacity 0.3s, transform 0.3s";
      card.style.opacity = "0";
      card.style.transform = "translateX(20px)";
      setTimeout(() => {
        card.remove();
        _rules = _rules.filter(r => (r.memory_id || r.id) !== memoryId);
        const countEl = _container?.querySelector("#mp-count");
        if (countEl) countEl.textContent = `${_rules.length} rule${_rules.length !== 1 ? "s" : ""}`;
      }, 300);
    }

    showToast("Rule deleted", "success");
  } catch (e) {
    showToast(`Delete failed: ${e.message}`, "error");
  }
}

function testRule(memoryId) {
  if (!confirm("This rule will be applied to the next agent run. Confirm?")) return;

  try {
    chrome.storage.local.set({ forced_rule_for_next_run: memoryId }, () => {
      showToast("Rule will be tested on next run", "info");
    });
  } catch (_) {
    showToast("Rule will be tested on next run", "info");
  }
}

// ── Site Profiles ────────────────────────────────────────────────────

async function loadSiteProfiles() {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 2000);
    let resp;
    try {
      resp = await fetch(`${_backendUrl}/memory/site-profiles`, { signal: controller.signal });
    } finally {
      clearTimeout(timer);
    }
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    _profiles = Array.isArray(data) ? data : data.profiles || [];
  } catch (e) {
    if (e.name !== "AbortError" && !e.message?.includes("fetch")) {
      console.warn("[MemoryPanel] loadSiteProfiles failed:", e.message);
    }
    _profiles = [];
  }

  _renderProfiles();
}

function _renderProfiles() {
  const list = _container?.querySelector("#mp-profiles-list");
  if (!list) return;

  if (_profiles.length === 0) {
    list.innerHTML = `
      <div class="mp-empty">
        <span class="empty-icon">🌐</span>
        <p>No site profiles yet.<br>Run the agent on a website to generate one.</p>
      </div>`;
    return;
  }

  list.innerHTML = _profiles.map((p) => renderSiteProfileCard(p)).join("");

  // Delete listeners
  list.querySelectorAll(".mp-profile-delete").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      _deleteSiteProfile(btn.dataset.domain);
    });
  });

  // Toggle details
  list.querySelectorAll(".mp-profile-card").forEach((card) => {
    card.querySelector(".mp-profile-body")?.addEventListener("click", () => {
      card.querySelector(".mp-profile-details")?.classList.toggle("hidden");
    });
  });
}

function renderSiteProfileCard(profile) {
  const domain = profile.domain || "";
  const totalRuns = profile.total_runs || 0;
  const successRate = typeof profile.success_rate === "number"
    ? Math.round(profile.success_rate * 100) : 0;
  const lastUpdated = profile.last_updated || "";
  const mcqSelectors = profile.mcq_selectors || [];
  const nextPatterns = profile.next_button_patterns || [];
  const submitPatterns = profile.submit_button_patterns || [];
  const notes = profile.notes || "";

  const mcqTags = mcqSelectors.map(s => `<code class="mp-code-tag">${esc(s)}</code>`).join(" ");
  const nextTags = nextPatterns.map(s => `<span class="mp-tag">${esc(s)}</span>`).join(" ");
  const submitTags = submitPatterns.map(s => `<span class="mp-tag mp-tag-red">${esc(s)}</span>`).join(" ");

  const dateStr = lastUpdated ? new Date(lastUpdated).toLocaleDateString() : "—";

  return `
    <div class="mp-profile-card" data-domain="${esc(domain)}">
      <div class="mp-profile-body">
        <div class="mp-profile-header">
          <span class="mp-profile-domain">${esc(domain)}</span>
          <button class="mp-action-btn mp-profile-delete" data-domain="${esc(domain)}" title="Delete profile">🗑️</button>
        </div>
        <div class="mp-profile-stats">
          <span>${totalRuns} run${totalRuns !== 1 ? "s" : ""}</span>
          <span class="mp-sep">•</span>
          <span>${successRate}% success</span>
          <span class="mp-sep">•</span>
          <span>Updated ${dateStr}</span>
        </div>
      </div>

      <div class="mp-profile-details hidden">
        ${mcqSelectors.length ? `<div class="mp-detail-section"><span class="mp-detail-label">MCQ selectors:</span><div class="mp-tags">${mcqTags}</div></div>` : ""}
        ${nextPatterns.length ? `<div class="mp-detail-section"><span class="mp-detail-label">Next buttons:</span><div class="mp-tags">${nextTags}</div></div>` : ""}
        ${submitPatterns.length ? `<div class="mp-detail-section"><span class="mp-detail-label">Submit buttons:</span><div class="mp-tags">${submitTags}</div></div>` : ""}
        ${notes ? `<div class="mp-detail-section"><span class="mp-detail-label">Notes:</span><p class="mp-notes-text">${esc(notes)}</p></div>` : ""}
      </div>
    </div>
  `;
}

async function _deleteSiteProfile(domain) {
  if (!confirm(`Delete profile for ${domain}?`)) return;
  try {
    const resp = await fetch(`${_backendUrl}/memory/site-profiles/${encodeURIComponent(domain)}`, {
      method: "DELETE",
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    showToast(`Profile for ${domain} deleted`, "success");
    loadSiteProfiles();
  } catch (e) {
    showToast(`Delete failed: ${e.message}`, "error");
  }
}

// ── Export / Import ──────────────────────────────────────────────────

async function exportMemory() {
  try {
    showToast("Exporting memory…", "info");

    const [rulesResp, profilesResp] = await Promise.allSettled([
      fetch(`${_backendUrl}/teach/all`, { signal: AbortSignal.timeout(5000) }),
      fetch(`${_backendUrl}/memory/site-profiles`, { signal: AbortSignal.timeout(5000) }),
    ]);

    let rules = [];
    let profiles = [];

    if (rulesResp.status === "fulfilled" && rulesResp.value.ok) {
      const data = await rulesResp.value.json();
      rules = Array.isArray(data) ? data : data.rules || data.items || [];
    }

    if (profilesResp.status === "fulfilled" && profilesResp.value.ok) {
      const data = await profilesResp.value.json();
      profiles = Array.isArray(data) ? data : data.profiles || [];
    }

    const exportData = {
      rules,
      site_profiles: profiles,
      exported_at: new Date().toISOString(),
      version: "1.0",
    };

    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "browser-agent-memory.json";
    a.click();
    URL.revokeObjectURL(url);

    showToast(`Exported ${rules.length} rules, ${profiles.length} profiles`, "success");
  } catch (e) {
    showToast(`Export failed: ${e.message}`, "error");
  }
}

async function importMemory(file) {
  try {
    const text = await file.text();
    const data = JSON.parse(text);

    if (!data.rules || !Array.isArray(data.rules)) {
      showToast("Invalid file: missing 'rules' array", "error");
      return;
    }

    const total = data.rules.length;
    let imported = 0;
    let errors = 0;

    for (let i = 0; i < total; i++) {
      const rule = data.rules[i];
      try {
        const resp = await fetch(`${_backendUrl}/teach`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            raw_text: rule.instruction || "",
            domain: rule.domain || null,
            scope: rule.scope || "global",
            priority: 5,
          }),
        });
        if (resp.ok) {
          imported++;
        } else {
          errors++;
        }
      } catch (_) {
        errors++;
      }

      // Progress update every 5 rules
      if ((i + 1) % 5 === 0 || i === total - 1) {
        const countEl = _container?.querySelector("#mp-count");
        if (countEl) countEl.textContent = `Importing ${i + 1} of ${total}…`;
      }
    }

    showToast(`Imported ${imported} rules successfully${errors > 0 ? `, ${errors} failed` : ""}`, imported > 0 ? "success" : "error");
    loadRules();
  } catch (e) {
    showToast(`Import failed: ${e.message}`, "error");
  }
}

// ── Scoped Styles ────────────────────────────────────────────────────

function _injectStyles() {
  if (document.getElementById("mp-styles")) return;

  const style = document.createElement("style");
  style.id = "mp-styles";
  style.textContent = `
    .mp-wrapper {
      display: flex;
      flex-direction: column;
      gap: 8px;
      height: 100%;
    }

    .mp-topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 4px 0;
    }
    .mp-count {
      font-size: 11px;
      color: var(--muted, #797876);
      font-weight: 600;
    }
    .mp-topbar-actions {
      display: flex;
      gap: 4px;
    }

    .mp-filters {
      display: flex;
      gap: 4px;
      align-items: center;
    }
    .mp-filters .filter-select,
    .mp-filters .filter-input {
      font-size: 11px;
      padding: 3px 6px;
      flex: 1;
      min-width: 0;
    }

    .mp-subtabs {
      display: flex;
      border-bottom: 1px solid var(--border, #393836);
    }
    .mp-subtab {
      flex: 1;
      padding: 6px 0;
      background: none;
      border: none;
      border-bottom: 2px solid transparent;
      color: var(--muted, #797876);
      font: inherit;
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.3px;
      cursor: pointer;
      text-align: center;
      transition: color 0.15s, border-color 0.15s;
    }
    .mp-subtab:hover { color: var(--text, #cdccca); }
    .mp-subtab.active {
      color: var(--teal, #4f98a3);
      border-bottom-color: var(--teal, #4f98a3);
    }

    .mp-panel {
      flex: 1;
      overflow-y: auto;
    }
    .mp-panel.hidden { display: none; }

    .mp-list {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }

    .mp-empty {
      text-align: center;
      padding: 30px 10px;
      color: var(--faint, #5a5957);
      font-size: 12px;
    }
    .mp-empty .empty-icon {
      font-size: 28px;
      display: block;
      margin-bottom: 8px;
    }

    /* Rule Card */
    .mp-card {
      border: 1px solid var(--border, #393836);
      border-radius: var(--radius-md, 6px);
      background: var(--surf, #1c1b19);
      overflow: hidden;
      transition: border-color 0.15s;
    }
    .mp-card:hover {
      border-color: rgba(79,152,163,0.3);
    }

    .mp-card-body {
      padding: 8px 10px;
      cursor: pointer;
    }

    .mp-card-header {
      display: flex;
      align-items: center;
      gap: 4px;
      flex-wrap: wrap;
      margin-bottom: 4px;
    }

    .mp-type-badge {
      font-size: 9px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.3px;
      padding: 1px 5px;
      border: 1px solid;
      border-radius: 9999px;
    }
    .mp-scope-badge {
      font-size: 9px;
      color: var(--faint, #5a5957);
      background: rgba(255,255,255,0.04);
      padding: 1px 5px;
      border-radius: 9999px;
    }
    .mp-domain-pill {
      font-size: 9px;
      color: var(--muted, #797876);
      background: rgba(255,255,255,0.04);
      padding: 1px 6px;
      border-radius: 9999px;
    }

    .mp-instruction {
      font-size: 12px;
      color: var(--text, #cdccca);
      font-weight: 600;
      line-height: 1.4;
      margin: 0 0 6px 0;
    }

    .mp-conf-row {
      display: flex;
      align-items: center;
      gap: 6px;
      margin-bottom: 4px;
    }
    .mp-conf-track {
      flex: 1;
      height: 4px;
      background: rgba(255,255,255,0.06);
      border-radius: 2px;
      overflow: hidden;
    }
    .mp-conf-fill {
      height: 100%;
      background: var(--teal, #4f98a3);
      border-radius: 2px;
      transition: width 0.3s ease;
    }
    .mp-conf-pct {
      font-size: 10px;
      color: var(--muted, #797876);
      min-width: 28px;
      text-align: right;
    }

    .mp-stats {
      display: flex;
      gap: 8px;
      font-size: 10px;
    }
    .mp-stat-ok { color: var(--green, #6daa45); }
    .mp-stat-fail { color: var(--red, #dd6974); }

    /* Details */
    .mp-card-details {
      padding: 6px 10px;
      border-top: 1px solid var(--border, #393836);
      background: rgba(0,0,0,0.15);
    }
    .mp-card-details.hidden { display: none; }

    .mp-detail-section {
      margin-bottom: 4px;
    }
    .mp-detail-label {
      font-size: 9px;
      color: var(--faint, #5a5957);
      text-transform: uppercase;
      letter-spacing: 0.2px;
      display: block;
      margin-bottom: 2px;
    }
    .mp-tags {
      display: flex;
      flex-wrap: wrap;
      gap: 3px;
    }
    .mp-tag {
      font-size: 10px;
      padding: 1px 5px;
      border-radius: 3px;
      background: rgba(79,152,163,0.1);
      color: var(--teal, #4f98a3);
      border: 1px solid rgba(79,152,163,0.2);
    }
    .mp-tag-green {
      background: rgba(109,170,69,0.1);
      color: var(--green, #6daa45);
      border-color: rgba(109,170,69,0.2);
    }
    .mp-tag-red {
      background: rgba(221,105,116,0.1);
      color: var(--red, #dd6974);
      border-color: rgba(221,105,116,0.2);
    }
    .mp-code-tag {
      font-family: 'Fira Code', 'Cascadia Code', monospace;
      font-size: 9px;
      padding: 1px 4px;
      border-radius: 3px;
      background: rgba(255,255,255,0.04);
      color: var(--muted, #797876);
      border: 1px solid rgba(255,255,255,0.06);
    }

    /* Card Actions */
    .mp-card-actions {
      display: flex;
      gap: 2px;
      padding: 4px 10px;
      border-top: 1px solid var(--border, #393836);
      background: rgba(0,0,0,0.1);
    }
    .mp-action-btn {
      background: none;
      border: none;
      font-size: 12px;
      padding: 2px 6px;
      cursor: pointer;
      border-radius: 3px;
      transition: background 0.15s;
    }
    .mp-action-btn:hover {
      background: rgba(255,255,255,0.06);
    }

    /* Edit Form */
    .mp-edit-form {
      padding: 10px;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .mp-edit-label {
      font-size: 10px;
      color: var(--muted, #797876);
      text-transform: uppercase;
      letter-spacing: 0.2px;
    }
    .mp-edit-textarea {
      font: inherit;
      font-size: 12px;
      padding: 6px 8px;
      background: var(--surf2, #201f1d);
      border: 1px solid var(--border, #393836);
      border-radius: var(--radius-sm, 4px);
      color: var(--text, #cdccca);
      resize: vertical;
    }
    .mp-edit-input {
      font: inherit;
      font-size: 12px;
      padding: 4px 8px;
      background: var(--surf2, #201f1d);
      border: 1px solid var(--border, #393836);
      border-radius: var(--radius-sm, 4px);
      color: var(--text, #cdccca);
      width: 80px;
    }
    .mp-edit-actions {
      display: flex;
      gap: 6px;
      margin-top: 4px;
    }

    /* Profile Card */
    .mp-profile-card {
      border: 1px solid var(--border, #393836);
      border-radius: var(--radius-md, 6px);
      background: var(--surf, #1c1b19);
      overflow: hidden;
      transition: border-color 0.15s;
    }
    .mp-profile-card:hover {
      border-color: rgba(232,175,52,0.3);
    }

    .mp-profile-body {
      padding: 8px 10px;
      cursor: pointer;
    }
    .mp-profile-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 4px;
    }
    .mp-profile-domain {
      font-size: 13px;
      font-weight: 700;
      color: var(--text, #cdccca);
    }
    .mp-profile-stats {
      display: flex;
      align-items: center;
      gap: 4px;
      font-size: 10px;
      color: var(--muted, #797876);
    }
    .mp-sep { opacity: 0.4; }

    .mp-profile-details {
      padding: 6px 10px;
      border-top: 1px solid var(--border, #393836);
      background: rgba(0,0,0,0.15);
    }
    .mp-profile-details.hidden { display: none; }

    .mp-notes-text {
      font-size: 11px;
      color: var(--muted, #797876);
      margin: 2px 0 0 0;
      line-height: 1.4;
      font-style: italic;
    }
  `;

  document.head.appendChild(style);
}
