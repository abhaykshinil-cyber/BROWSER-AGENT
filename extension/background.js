/**
 * BrowserAgent — Background Service Worker (Phase 3)
 *
 * Runs as a Manifest V3 service worker.  Responsibilities:
 *   • Call the REST API for planning, verification, teaching, and memory.
 *   • Maintain an optional WebSocket for real-time updates.
 *   • Route messages between the side-panel, content scripts, & server.
 *   • Execute action plans step-by-step with delay between steps.
 *
 * Phase 3 upgrade: the primary communication channel is now REST
 * (POST /plan, /verify, /teach, /run, /stop, GET /memory).
 * WebSocket is kept for future streaming but is NOT required.
 */

import {
  getAllOpenTabs,
  switchToTab as tmSwitchToTab,
  buildFullContext,
  watchTabChanges,
} from "./background/tab-manager.js";

import {
  AGENT_INIT,
  STATUS_REQUEST,
  STATUS_RESPONSE,
  SERVER_CONNECTED,
  SERVER_DISCONNECTED,
  PAGE_CONTEXT,
  SCAN_PAGE,
  SCAN_RESULT,
  PAGE_CHANGED,
  TASK_START,
  TASK_ACCEPTED,
  TASK_COMPLETE,
  TASK_CANCEL,
  TASK_PROGRESS,
  PLAN_REQUEST,
  PLAN_RESPONSE,
  EXECUTE_ACTION,
  ACTION_RESULT,
  ACTION_CONFIRM_REQUEST,
  ACTION_CONFIRM_RESPONSE,
  VERIFY_REQUEST,
  VERIFY_RESPONSE,
  TEACHING_SUBMIT,
  TEACHING_ACK,
  MEMORY_LIST_REQUEST,
  MEMORY_LIST_RESPONSE,
  MEMORY_DELETE,
  ERROR,
} from "./shared/message-types.js";

// ── Configuration ────────────────────────────────────────────────────

const API_BASE           = "http://localhost:8765";
const WS_URL             = "ws://localhost:8765/ws";
const RECONNECT_MS       = 3000;
const MAX_RECONNECT      = 10;
const STEP_DELAY_MS      = 800;

// ── State ────────────────────────────────────────────────────────────

let isServerOnline  = false;
let currentTaskId   = null;
let cancelRequested = false;
let socket          = null;
let reconnectCount  = 0;
let reconnectTimer  = null;

// ── REST Helpers ─────────────────────────────────────────────────────

async function api(method, path, body = null) {
  const opts = {
    method,
    headers: { "Content-Type": "application/json" },
  };
  if (body !== null) opts.body = JSON.stringify(body);

  const resp = await fetch(`${API_BASE}${path}`, opts);
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`${method} ${path} → ${resp.status}: ${text.slice(0, 300)}`);
  }
  return resp.json();
}

async function checkHealth() {
  try {
    const data = await api("GET", "/health");
    if (!isServerOnline) {
      isServerOnline = true;
      broadcastToExtension({ type: SERVER_CONNECTED, payload: data });
      console.log("[BrowserAgent] Server online:", data.model);
      // Only attempt WebSocket AFTER confirming server is up.
      // This prevents ERR_CONNECTION_REFUSED console errors on cold start.
      connectWebSocket();
    }
    return true;
  } catch (_) {
    if (isServerOnline) {
      isServerOnline = false;
      // Server just went offline — close any open socket so reconnect stops
      if (socket) {
        socket.onclose = null; // prevent scheduleReconnect firing
        socket.close();
        socket = null;
      }
      reconnectCount = 0;
      broadcastToExtension({ type: SERVER_DISCONNECTED });
      console.warn("[BrowserAgent] Server offline");
    }
    return false;
  }
}

// ── WebSocket (optional, for future streaming) ───────────────────────

function connectWebSocket() {
  if (socket && socket.readyState === WebSocket.OPEN) return;
  try {
    socket = new WebSocket(WS_URL);
  } catch (_) {
    scheduleReconnect();
    return;
  }

  socket.onopen = () => {
    console.log("[BrowserAgent] WebSocket connected");
    reconnectCount = 0;
  };
  socket.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      // Forward streaming messages to the extension UI
      broadcastToExtension(msg);
    } catch (_) {}
  };
  socket.onclose = () => {
    socket = null;
    scheduleReconnect();
  };
  socket.onerror = () => {};
}

function scheduleReconnect() {
  // Don't reconnect if server is known offline — wait for health check to confirm it's back.
  if (reconnectCount >= MAX_RECONNECT || !isServerOnline) return;
  reconnectCount++;
  clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(connectWebSocket, RECONNECT_MS * Math.min(reconnectCount, 5));
}

// ── Task Execution Flow ──────────────────────────────────────────────

async function startTask(goal, context, requireConfirmation) {
  if (currentTaskId) {
    broadcastToExtension({
      type: ERROR,
      payload: { detail: "A task is already running. Stop it first." },
    });
    return;
  }

  cancelRequested = false;

  // 1. Register the run with the server
  let runData;
  try {
    runData = await api("POST", "/run", {
      goal,
      context,
      require_confirmation: requireConfirmation,
    });
  } catch (err) {
    broadcastToExtension({ type: ERROR, payload: { detail: err.message } });
    return;
  }

  currentTaskId = runData.task_id;
  broadcastToExtension({
    type: TASK_ACCEPTED,
    payload: { task_id: currentTaskId, goal },
  });

  // 2. Scan the active tab
  const pageContext = await scanActiveTab();
  if (!pageContext) {
    finishTask(false, "Could not scan the active tab.");
    return;
  }

  // 3. Request a plan from the server
  broadcastToExtension({
    type: TASK_PROGRESS,
    payload: { status: "planning", task_id: currentTaskId },
  });

  let planResponse;
  try {
    planResponse = await api("POST", "/plan", {
      task: { task_id: currentTaskId, goal, context },
      page_context: pageContext,
      relevant_memories: [],
    });
  } catch (err) {
    finishTask(false, `Planning failed: ${err.message}`);
    return;
  }

  broadcastToExtension({
    type: PLAN_RESPONSE,
    payload: planResponse,
  });

  if (!planResponse.plan || planResponse.plan.length === 0) {
    finishTask(false, "Planner returned an empty plan.");
    return;
  }

  // 4. Execute steps sequentially
  const results = await executeSteps(planResponse.plan, pageContext);

  // 5. Verify the outcome
  broadcastToExtension({
    type: TASK_PROGRESS,
    payload: { status: "verifying", task_id: currentTaskId },
  });

  const afterContext = await scanActiveTab();
  let verifyResult;
  try {
    verifyResult = await api("POST", "/verify", {
      task: { task_id: currentTaskId, goal, context },
      steps_executed: results,
      page_context: afterContext || pageContext,
      expected_outcome: goal,
    });
  } catch (err) {
    console.warn("[BrowserAgent] Verification failed:", err.message);
    verifyResult = { verified: false, reason: err.message };
  }

  broadcastToExtension({
    type: VERIFY_RESPONSE,
    payload: verifyResult,
  });

  finishTask(verifyResult.verified, verifyResult.reason || "Task complete.");
}

async function executeSteps(plan, initialContext) {
  const results = [];

  for (let i = 0; i < plan.length; i++) {
    if (cancelRequested) {
      console.log("[BrowserAgent] Execution cancelled at step", i);
      break;
    }

    const step = plan[i];

    broadcastToExtension({
      type: TASK_PROGRESS,
      payload: {
        status: "executing_step",
        step_index: i,
        total: plan.length,
        step,
      },
    });

    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) {
      results.push({
        step_id: step.step_id || `step_${i}`,
        success: false,
        action_taken: "No active tab",
        page_changed: false,
        error: "No active tab found",
      });
      continue;
    }

    try {
      const result = await new Promise((resolve, reject) => {
        chrome.tabs.sendMessage(
          tab.id,
          { type: EXECUTE_ACTION, payload: step },
          (response) => {
            if (chrome.runtime.lastError) {
              reject(new Error(chrome.runtime.lastError.message));
              return;
            }
            resolve(
              response || {
                step_id: step.step_id,
                success: false,
                action_taken: "",
                error: "No response from content script",
              }
            );
          }
        );
      });

      results.push({
        step_id: step.step_id || `step_${i}`,
        success: result.success ?? false,
        action_taken: result.action_taken || "",
        page_changed: result.page_changed ?? false,
        error: result.error || null,
      });

      broadcastToExtension({
        type: ACTION_RESULT,
        payload: results[results.length - 1],
      });
    } catch (err) {
      results.push({
        step_id: step.step_id || `step_${i}`,
        success: false,
        action_taken: "Execution error",
        page_changed: false,
        error: err.message,
      });
    }

    // Delay between steps
    if (i < plan.length - 1) {
      await sleep(STEP_DELAY_MS);
    }
  }

  broadcastToExtension({
    type: TASK_PROGRESS,
    payload: { status: "all_steps_complete", total: plan.length, completed: results.length },
  });

  return results;
}

function finishTask(success, message) {
  const taskId = currentTaskId;
  currentTaskId = null;
  cancelRequested = false;

  broadcastToExtension({
    type: TASK_COMPLETE,
    payload: { task_id: taskId, success, message },
  });
}

async function stopTask() {
  cancelRequested = true;
  try {
    await api("POST", "/stop");
  } catch (_) {}
  finishTask(false, "Task cancelled by user.");
}

// ── Page Scanning ────────────────────────────────────────────────────

async function scanActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) return null;

  return new Promise((resolve) => {
    chrome.tabs.sendMessage(tab.id, { type: "SCAN" }, (response) => {
      if (chrome.runtime.lastError || !response?.success) {
        // Fallback scan via SCAN_PAGE
        chrome.tabs.sendMessage(tab.id, { type: "SCAN_PAGE" }, (r2) => {
          if (chrome.runtime.lastError) {
            resolve(null);
            return;
          }
          resolve(r2?.data || r2?.payload || null);
        });
        return;
      }
      resolve(response.data || null);
    });
  });
}

// ── Teaching ─────────────────────────────────────────────────────────

async function submitTeaching(payload) {
  try {
    const memory = await api("POST", "/teach", {
      raw_text: payload.raw_text,
      domain: payload.domain || null,
      scope: payload.scope || "global",
      priority: payload.priority || 5,
    });
    broadcastToExtension({
      type: TEACHING_ACK,
      payload: { stored: true, memory },
    });
  } catch (err) {
    broadcastToExtension({
      type: ERROR,
      payload: { detail: `Teaching failed: ${err.message}` },
    });
  }
}

// ── Memory Management ────────────────────────────────────────────────

async function listMemories() {
  try {
    const items = await api("GET", "/memory");
    broadcastToExtension({
      type: MEMORY_LIST_RESPONSE,
      payload: { items },
    });
  } catch (err) {
    broadcastToExtension({
      type: ERROR,
      payload: { detail: `Memory list failed: ${err.message}` },
    });
  }
}

async function deleteMemory(memoryId) {
  try {
    await api("POST", `/memory/${memoryId}/delete`);
    broadcastToExtension({
      type: MEMORY_LIST_RESPONSE,
      payload: { deleted: memoryId },
    });
  } catch (err) {
    broadcastToExtension({
      type: ERROR,
      payload: { detail: `Memory delete failed: ${err.message}` },
    });
  }
}

// ── Extension Message Router ─────────────────────────────────────────

function handleExtensionMessage(message, sender, sendResponse) {
  const { type, payload } = message;
  console.debug("[BrowserAgent] ←", type, sender?.tab?.id ?? "panel");

  switch (type) {
    case STATUS_REQUEST:
      sendResponse({
        type: STATUS_RESPONSE,
        payload: {
          connected: isServerOnline,
          executing: currentTaskId !== null,
          task_id: currentTaskId,
        },
      });
      return true;

    case TASK_START:
      startTask(
        payload.goal,
        payload.context || null,
        payload.settings?.require_confirmation ?? true
      );
      sendResponse({ received: true });
      return true;

    case TASK_CANCEL:
      stopTask();
      sendResponse({ received: true });
      return true;

    case TEACHING_SUBMIT:
      submitTeaching(payload);
      sendResponse({ received: true });
      return true;

    case MEMORY_LIST_REQUEST:
      listMemories();
      sendResponse({ received: true });
      return true;

    case MEMORY_DELETE:
      deleteMemory(payload.memory_id);
      sendResponse({ received: true });
      return true;

    case PAGE_CONTEXT:
    case PAGE_CHANGED:
      // Logged but no action needed
      break;

    // ── Phase 9: Multi-Tab support ──────────────────────────
    case "GET_ALL_TABS": {
      getAllOpenTabs().then((tabs) => {
        sendResponse({ tabs });
      }).catch((err) => {
        sendResponse({ tabs: [], error: err.message });
      });
      return true;
    }

    case "SWITCH_TAB": {
      const tabId = payload?.tabId || payload?.tab_id;
      if (tabId) {
        tmSwitchToTab(tabId).then(() => {
          sendResponse({ switched: true });
        }).catch((err) => {
          sendResponse({ switched: false, error: err.message });
        });
      } else {
        sendResponse({ switched: false, error: "No tabId provided" });
      }
      return true;
    }

    case "BUILD_TAB_CONTEXT": {
      const activeId = payload?.activeTabId;
      if (activeId) {
        buildFullContext(activeId).then((ctx) => {
          sendResponse(ctx);
        }).catch((err) => {
          sendResponse({ error: err.message });
        });
      } else {
        sendResponse({ error: "No activeTabId" });
      }
      return true;
    }

    // ── Annotation relay: forward to active tab content script ───────
    case "HIGHLIGHT":
    case "SHOW_BADGE":
    case "CLEAR_ANNOTATIONS":
    case "SHOW_THINKING":
    case "HIDE_THINKING":
    case "PAGE_INFO":
    case "DETECT_MCQ":
    case "DETECT_QUESTIONS": {
      chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
        const tab = tabs?.[0];
        if (!tab?.id) { sendResponse({ success: false, error: "No active tab" }); return; }
        chrome.tabs.sendMessage(tab.id, message, (resp) => {
          sendResponse(chrome.runtime.lastError
            ? { success: false, error: chrome.runtime.lastError.message }
            : { success: true, data: resp || null });
        });
      });
      return true;
    }

    case "RELAY_SCAN": {
      // Relay a SCAN request from the side panel to the active tab's content script
      chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
        const tab = tabs?.[0];
        if (!tab?.id) {
          sendResponse({ success: false, error: "No active tab found" });
          return;
        }
        chrome.tabs.sendMessage(tab.id, { type: "SCAN" }, (response) => {
          if (chrome.runtime.lastError) {
            sendResponse({ success: false, error: chrome.runtime.lastError.message });
            return;
          }
          sendResponse({ success: true, data: response?.data || response || null });
        });
      });
      return true;
    }

    case "PING":
      sendResponse({ pong: true });
      return true;

    default:
      console.warn("[BrowserAgent] Unhandled:", type);
  }

  sendResponse({ received: true });
  return true;
}

// ── Broadcast ────────────────────────────────────────────────────────

function broadcastToExtension(message) {
  chrome.runtime.sendMessage(message).catch(() => {});
}

// ── Utilities ────────────────────────────────────────────────────────

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

// ── Extension Lifecycle ──────────────────────────────────────────────

chrome.runtime.onInstalled.addListener((details) => {
  console.log("[BrowserAgent] Installed:", details.reason);
  chrome.sidePanel.setOptions({ enabled: true });
  checkHealth(); // connectWebSocket is called inside checkHealth when server is confirmed up
});

chrome.runtime.onStartup.addListener(() => {
  console.log("[BrowserAgent] Startup");
  checkHealth();
});

chrome.runtime.onMessage.addListener(handleExtensionMessage);

chrome.action.onClicked.addListener(async (tab) => {
  await chrome.sidePanel.open({ windowId: tab.windowId });
});

// Periodic health check (every 15s) to detect server going online/offline
setInterval(checkHealth, 15000);

// Initial boot — connectWebSocket is triggered inside checkHealth when server is confirmed up
checkHealth();

// Phase 9: Watch tab changes and broadcast to UI
watchTabChanges((event) => {
  broadcastToExtension({ type: "TAB_CHANGED", payload: event });
});
