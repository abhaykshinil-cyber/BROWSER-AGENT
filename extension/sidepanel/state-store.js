/**
 * BrowserAgent — State Store (Phase 5)
 *
 * Reactive state management for the side panel.  Any mutation via
 * setState() triggers all registered listeners, enabling the UI
 * to re-render in response to state changes.
 *
 * Loaded by sidepanel.js as an ES module.
 */

// ── Default State ────────────────────────────────────────────────────

const INITIAL_STATE = {
  /* Execution flags */
  running:     false,
  paused:      false,
  stopped:     false,

  /* Connection */
  connected:   false,

  /* Current task */
  goal:        "",
  taskId:      null,
  plan:        [],          // ActionStep[]
  currentStep: -1,          // index into plan
  totalSteps:  0,

  /* Step log */
  log: [],
  // Each entry: { id, status:"pending"|"success"|"error"|"warning", icon, text, ts }

  /* Confirmation gate */
  awaitingConfirmation: false,
  confirmationPayload:  null,

  /* Memories injected into the plan context */
  memories: [],

  /* Questions or suggestions the agent is surfacing */
  questions: [],

  /* Verification results */
  lastVerify: null,
};

// ── Internal ─────────────────────────────────────────────────────────

let _state     = { ...INITIAL_STATE };
let _listeners = [];
let _logIdSeq  = 1;

// ── Public API ───────────────────────────────────────────────────────

/**
 * Merge partial updates into state and notify all listeners.
 * Returns the new state snapshot.
 */
export function setState(updates) {
  const prev = { ..._state };
  Object.assign(_state, updates);
  const next = { ..._state };

  for (const cb of _listeners) {
    try {
      cb(next, prev);
    } catch (err) {
      console.error("[StateStore] listener threw:", err);
    }
  }
  return next;
}

/**
 * Return a frozen snapshot of the current state.
 */
export function getState() {
  return { ..._state };
}

/**
 * Register a callback that fires on every setState() call.
 * Returns an unsubscribe function.
 */
export function onStateChange(callback) {
  _listeners.push(callback);
  return function unsubscribe() {
    _listeners = _listeners.filter((cb) => cb !== callback);
  };
}

/**
 * Reset the state to its initial values.
 * Useful when a task completes and you want a clean slate.
 */
export function resetState() {
  _logIdSeq = 1;
  return setState({ ...INITIAL_STATE, log: [], connected: _state.connected });
}

// ── Log Helpers ──────────────────────────────────────────────────────

/**
 * Append an entry to the step log.
 * Triggers a state change so the UI updates.
 */
export function addLogEntry(status, icon, text) {
  const entry = {
    id:     _logIdSeq++,
    status: status || "pending",
    icon:   icon || "•",
    text:   text || "",
    ts:     new Date().toISOString(),
  };
  const newLog = [entry, ..._state.log];
  setState({ log: newLog });
  return entry;
}

/**
 * Update the status & icon of the most recent log entry matching `id`.
 */
export function updateLogEntry(id, updates) {
  const newLog = _state.log.map((entry) =>
    entry.id === id ? { ...entry, ...updates } : entry
  );
  setState({ log: newLog });
}

/**
 * Clear the entire log.
 */
export function clearLog() {
  setState({ log: [] });
}
