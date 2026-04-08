/**
 * BrowserAgent — Agent Controller (Phase 5)
 *
 * The main orchestration loop that drives the full agent lifecycle:
 *   scan → plan → confirm → execute → verify → log → memory.
 *
 * Runs inside the side-panel context as an ES module.  Communicates
 * with the background service worker via chrome.runtime messaging
 * and with the FastAPI backend via fetch() (REST).
 */

/* global chrome */

import {
  setState,
  getState,
  addLogEntry,
  updateLogEntry,
  resetState,
} from "./state-store.js";

// ── Configuration ────────────────────────────────────────────────────

const DEFAULT_API_BASE = "http://localhost:8765";
const STEP_DELAY       = 800;       // ms between steps
const SCAN_TIMEOUT     = 5000;      // ms timeout for scanning
const VERIFY_TIMEOUT   = 15000;     // ms timeout for verify calls

/**
 * Resolve the backend URL at call-time from chrome.storage so it
 * always matches whatever the user has configured in Settings.
 */
async function getApiBase() {
  try {
    const data = await chrome.storage.local.get("backendUrl");
    return (data.backendUrl || DEFAULT_API_BASE).replace(/\/$/, "");
  } catch {
    return DEFAULT_API_BASE;
  }
}

// ── Internal flags ───────────────────────────────────────────────────

let _stopFlag  = false;
let _pauseFlag = false;
let _pauseResolver = null;   // if paused, calling this resumes

// ── Public API ───────────────────────────────────────────────────────

/**
 * Run the full agent loop.
 *
 * @param {string} goal      Natural-language task description.
 * @param {object} settings  { require_confirmation: bool }
 */
export async function runAgent(goal, settings = {}) {
  _stopFlag  = false;
  _pauseFlag = false;

  resetState();
  setState({
    running: true,
    goal,
    taskId: null,
  });
  addLogEntry("pending", "⏳", `Task: "${goal}"`);

  try {
    // ── 1. Register the run ──────────────────────────────────────
    let runData;
    try {
      runData = await api("POST", "/run", {
        goal,
        context: settings.context || null,
        require_confirmation: settings.require_confirmation ?? true,
      });
    } catch (err) {
      addLogEntry("error", "✕", `Server error: ${err.message}`);
      finishRun(false, err.message);
      return;
    }

    const taskId = runData.task_id;
    setState({ taskId });
    addLogEntry("success", "✓", `Run registered (${taskId.slice(0, 8)}…)`);

    // ── 2. Capture page context ──────────────────────────────────
    addLogEntry("pending", "🔍", "Scanning page…");
    const pageContext = await capturePageContext();

    if (!pageContext) {
      addLogEntry("error", "✕", "Could not scan the active tab.");
      finishRun(false, "Scan failed");
      return;
    }
    addLogEntry("success", "✓",
      `Page scanned: ${pageContext.url?.substring(0, 60) || "unknown"}`
    );

    if (_stopFlag) { finishRun(false, "Stopped"); return; }

    // ── 3. Fetch memories ────────────────────────────────────────
    let memories = [];
    try {
      memories = await api("GET", "/memory");
    } catch (_) {
      // Non-fatal; continue without memories
    }
    setState({ memories });

    // ── 4. Request plan from server ──────────────────────────────
    addLogEntry("pending", "🧠", "Generating plan…");
    let planResponse;
    try {
      planResponse = await api("POST", "/plan", {
        task: { task_id: taskId, goal, context: settings.context || null },
        page_context: pageContext,
        relevant_memories: memories,
      });
    } catch (err) {
      addLogEntry("error", "✕", `Planning failed: ${err.message}`);
      finishRun(false, `Planning failed: ${err.message}`);
      return;
    }

    const plan = planResponse.plan || [];
    const confidence = planResponse.confidence || 0;
    const requiresConfirmation = planResponse.requires_confirmation;

    setState({
      plan,
      totalSteps: plan.length,
    });

    if (plan.length === 0) {
      addLogEntry("warning", "⚠", "Planner returned an empty plan.");
      finishRun(false, "Empty plan");
      return;
    }

    addLogEntry("success", "📋",
      `Plan: ${plan.length} steps (confidence: ${Math.round(confidence * 100)}%)`
    );

    // ── 5. Show plan to user ─────────────────────────────────────
    showPlanToUser(plan);

    if (_stopFlag) { finishRun(false, "Stopped"); return; }

    // ── 6. Confirmation gate ─────────────────────────────────────
    if (requiresConfirmation || settings.require_confirmation) {
      addLogEntry("warning", "⏸",
        "Plan requires confirmation. Click ▶ Resume to proceed."
      );
      setState({ awaitingConfirmation: true, confirmationPayload: planResponse });

      const confirmed = await waitForConfirmation();
      setState({ awaitingConfirmation: false, confirmationPayload: null });

      if (!confirmed || _stopFlag) {
        addLogEntry("error", "✕", "Plan rejected by user.");
        finishRun(false, "User rejected plan");
        return;
      }
      addLogEntry("success", "✓", "Plan confirmed — executing…");
    }

    // ── 7. Execute steps ─────────────────────────────────────────
    const stepResults = [];

    for (let i = 0; i < plan.length; i++) {
      if (_stopFlag) {
        addLogEntry("error", "✕", `Stopped at step ${i + 1}`);
        break;
      }

      // Pause gate
      if (_pauseFlag) {
        addLogEntry("warning", "⏸", `Paused before step ${i + 1}`);
        await waitForResume();
        if (_stopFlag) break;
        addLogEntry("success", "▶", "Resumed");
      }

      const step = plan[i];
      setState({ currentStep: i });

      // 7a. Capture "before" state
      const beforeSnap = await capturePageContext();

      // 7b. Execute the step
      const logId = addLogEntry("pending", "⏳",
        `Step ${i + 1}/${plan.length}: [${step.action_type}] ${step.reason || step.target_text || ""}`
      ).id;

      const result = await executeStep(step);
      stepResults.push(result);

      if (result.success) {
        updateLogEntry(logId, {
          status: "success",
          icon: "✓",
          text: `Step ${i + 1}: ${result.action_taken || step.action_type}`,
        });
      } else {
        updateLogEntry(logId, {
          status: "error",
          icon: "✕",
          text: `Step ${i + 1} failed: ${result.error || "Unknown error"}`,
        });
      }

      // 7c. Brief pause to let page settle
      await sleep(STEP_DELAY);

      // 7d. Capture "after" state and verify
      const afterSnap = await capturePageContext();

      if (!result.success) {
        // Quick verify to decide: retry or skip
        const verifyResult = await verifyStep(
          step, taskId, goal, beforeSnap, afterSnap
        );
        setState({ lastVerify: verifyResult });

        if (!verifyResult.verified) {
          addLogEntry("warning", "⚠",
            `Verify: ${verifyResult.reason || "Step did not achieve expected result"}`
          );
          if (verifyResult.retry_suggestion) {
            addLogEntry("warning", "💡", `Suggestion: ${verifyResult.retry_suggestion}`);
          }
          // Continue to next step rather than hard-stopping
        }
      }
    }

    // ── 8. Final verification ────────────────────────────────────
    addLogEntry("pending", "🔎", "Verifying task outcome…");
    const finalContext = await capturePageContext();

    let finalVerify;
    try {
      finalVerify = await api("POST", "/verify", {
        task: { task_id: taskId, goal },
        steps_executed: stepResults,
        page_context: finalContext || pageContext,
        expected_outcome: goal,
      });
    } catch (err) {
      finalVerify = { verified: false, reason: err.message };
    }

    setState({ lastVerify: finalVerify });

    if (finalVerify.verified) {
      addLogEntry("success", "🎉", `Task verified: ${finalVerify.reason || "Goal achieved"}`);
    } else {
      addLogEntry("warning", "⚠",
        `Verification: ${finalVerify.reason || "Could not confirm task success"}`
      );
    }

    // ── 9. Save run to memory ────────────────────────────────────
    try {
      await api("POST", "/teach", {
        raw_text: `Completed task: "${goal}" — ${finalVerify.verified ? "succeeded" : "failed"}`,
        scope: extractDomain(pageContext?.url) || "global",
        domain: extractDomain(pageContext?.url) || null,
        priority: finalVerify.verified ? 3 : 6,
      });
    } catch (_) {
      // Non-fatal
    }

    finishRun(finalVerify.verified, finalVerify.reason || "Run complete");
  } catch (err) {
    console.error("[AgentController] Unexpected error:", err);
    addLogEntry("error", "✕", `Fatal: ${err.message}`);
    finishRun(false, err.message);
  }
}

/**
 * Stop the agent.  Sets the stop flag so the loop exits.
 */
export function stopAgent() {
  _stopFlag = true;
  _pauseFlag = false;

  // If waiting for confirmation or paused, release the gate
  if (_pauseResolver) {
    _pauseResolver();
    _pauseResolver = null;
  }

  setState({ running: false, stopped: true, paused: false });
  addLogEntry("error", "⏹", "Agent stopped by user");

  // Also tell the server
  api("POST", "/stop").catch(() => {});
}

/**
 * Pause the agent between steps.
 */
export function pauseAgent() {
  _pauseFlag = true;
  setState({ paused: true });
}

/**
 * Resume a paused agent.
 */
export function resumeAgent() {
  _pauseFlag = false;
  setState({ paused: false });

  if (_pauseResolver) {
    _pauseResolver();
    _pauseResolver = null;
  }
}

/**
 * Confirm or reject a pending plan confirmation.
 * @param {boolean} accepted
 */
export function confirmPlan(accepted) {
  setState({ awaitingConfirmation: false });

  if (_pauseResolver) {
    _pauseResolver(accepted);
    _pauseResolver = null;
  }
}

// ── Page Context ─────────────────────────────────────────────────────

/**
 * Request a DOM scan + basic info from the active tab's content script.
 * Falls back to chrome.tabs API if the content script doesn't respond.
 *
 * @returns {Promise<object|null>}
 */
async function capturePageContext() {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(null), SCAN_TIMEOUT);

    chrome.runtime.sendMessage(
      { type: "RELAY_SCAN" },
      (response) => {
        clearTimeout(timer);

        if (chrome.runtime.lastError || !response) {
          // Direct fallback: query active tab
          chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
            if (!tabs || !tabs.length) { resolve(null); return; }
            const tab = tabs[0];

            chrome.tabs.sendMessage(tab.id, { type: "SCAN" }, (r2) => {
              if (chrome.runtime.lastError || !r2 || !r2.success) {
                // Minimal context from tabs API
                resolve({
                  url: tab.url || "",
                  title: tab.title || "",
                  body_text: "",
                  visible_elements: [],
                  tabs: [],
                });
                return;
              }
              resolve(r2.data);
            });
          });
          return;
        }

        resolve(response.data || response);
      }
    );
  });
}

// ── Step Execution ───────────────────────────────────────────────────

/**
 * Execute a single action step via the content script.
 * Maps ActionStep fields to the EXECUTE_ACTION message format.
 *
 * @param {object} step  ActionStep from the planner.
 * @returns {Promise<object>}  ActionResult envelope.
 */
function executeStep(step) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      resolve({
        step_id: step.step_id || "",
        success: false,
        action_taken: "",
        page_changed: false,
        error: "Step execution timed out (10s)",
      });
    }, 10000);

    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (!tabs || !tabs.length) {
        clearTimeout(timer);
        resolve({
          step_id: step.step_id || "",
          success: false,
          action_taken: "",
          page_changed: false,
          error: "No active tab",
        });
        return;
      }

      const payload = {
        action:         step.action_type,
        action_type:    step.action_type,
        selector:       step.target_selector || null,
        text:           step.target_text || null,
        value:          step.input_value || null,
        step_id:        step.step_id || null,
      };

      chrome.tabs.sendMessage(
        tabs[0].id,
        { type: "EXECUTE_ACTION", payload },
        (result) => {
          clearTimeout(timer);

          if (chrome.runtime.lastError) {
            resolve({
              step_id: step.step_id || "",
              success: false,
              action_taken: "",
              page_changed: false,
              error: chrome.runtime.lastError.message,
            });
            return;
          }

          resolve(result || {
            step_id: step.step_id || "",
            success: false,
            action_taken: "",
            page_changed: false,
            error: "No response from content script",
          });
        }
      );
    });
  });
}

// ── Verification ─────────────────────────────────────────────────────

/**
 * Call POST /verify to check whether a step achieved its goal.
 */
async function verifyStep(step, taskId, goal, beforeCtx, afterCtx) {
  try {
    const result = await api("POST", "/verify", {
      task: { task_id: taskId, goal },
      steps_executed: [{
        step_id:      step.step_id || "",
        success:      false,
        action_taken: step.action_type,
        page_changed: beforeCtx?.url !== afterCtx?.url,
        error:        null,
      }],
      page_context: afterCtx || beforeCtx || { url: "", title: "" },
      expected_outcome: step.reason || goal,
    });
    return result;
  } catch (err) {
    return {
      verified: false,
      confidence: 0,
      reason: `Verify request failed: ${err.message}`,
      retry_suggestion: null,
    };
  }
}

// ── Plan Display ─────────────────────────────────────────────────────

/**
 * Log each planned step to the UI before execution begins.
 */
function showPlanToUser(plan) {
  for (let i = 0; i < plan.length; i++) {
    const s = plan[i];
    addLogEntry("pending", `${i + 1}`,
      `[${s.action_type}] ${s.reason || s.target_text || s.input_value || ""}`
    );
  }
}

// ── Run Finalisation ─────────────────────────────────────────────────

function finishRun(success, message) {
  _stopFlag = false;
  _pauseFlag = false;
  _pauseResolver = null;

  setState({
    running: false,
    paused: false,
    stopped: false,
    currentStep: -1,
  });

  if (success) {
    addLogEntry("success", "🏁", message || "Task complete");
  } else {
    addLogEntry("error", "🏁", message || "Task ended");
  }
}

// ── Confirmation / Pause Gates ───────────────────────────────────────

function waitForConfirmation() {
  return new Promise((resolve) => {
    _pauseResolver = resolve;
    // The resolve gets called by confirmPlan(true/false)
  });
}

function waitForResume() {
  return new Promise((resolve) => {
    _pauseResolver = resolve;
    // The resolve gets called by resumeAgent()
  });
}

// ── REST Helper ──────────────────────────────────────────────────────

async function api(method, path, body = null) {
  const base = await getApiBase();
  const opts = {
    method,
    headers: { "Content-Type": "application/json" },
  };
  if (body !== null) opts.body = JSON.stringify(body);

  const resp = await fetch(`${base}${path}`, opts);
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`${resp.status}: ${text.slice(0, 200)}`);
  }
  return resp.json();
}

// ── Utilities ────────────────────────────────────────────────────────

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function extractDomain(url) {
  if (!url) return null;
  try {
    return new URL(url).hostname;
  } catch (_) {
    return null;
  }
}
