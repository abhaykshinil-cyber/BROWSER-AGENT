/**
 * BrowserAgent — Chrome Extension Message Types
 *
 * Every message exchanged between the background service worker,
 * content scripts, side-panel UI, and the agent-server WebSocket
 * must carry a `type` field whose value is one of these constants.
 *
 * By centralising message types here we get:
 *   • compile-time (or at least grep-time) exhaustiveness checking
 *   • a single source of truth for protocol documentation
 *   • easy addition of new message types without hunting through code
 */

// ── Lifecycle ────────────────────────────────────────────────────────

/** Extension has been installed or updated. */
export const AGENT_INIT = "AGENT_INIT";

/** Side-panel requests current connection status from background. */
export const STATUS_REQUEST = "STATUS_REQUEST";

/** Background replies with connection status. */
export const STATUS_RESPONSE = "STATUS_RESPONSE";

/** Background ↔ Server WebSocket connection established. */
export const SERVER_CONNECTED = "SERVER_CONNECTED";

/** Background ↔ Server WebSocket connection lost. */
export const SERVER_DISCONNECTED = "SERVER_DISCONNECTED";

// ── Page Observation ─────────────────────────────────────────────────

/** Content script sends a page context snapshot to background. */
export const PAGE_CONTEXT = "PAGE_CONTEXT";

/** Background requests the content script to re-scan the page. */
export const SCAN_PAGE = "SCAN_PAGE";

/** Content script replies with scan results. */
export const SCAN_RESULT = "SCAN_RESULT";

/** Content script reports the page URL or DOM changed. */
export const PAGE_CHANGED = "PAGE_CHANGED";

// ── Task Management ──────────────────────────────────────────────────

/** Side-panel sends a new task to the background for execution. */
export const TASK_START = "TASK_START";

/** Background acknowledges and begins planning a task. */
export const TASK_ACCEPTED = "TASK_ACCEPTED";

/** A task has completed (successfully or not). */
export const TASK_COMPLETE = "TASK_COMPLETE";

/** The user or system cancelled the running task. */
export const TASK_CANCEL = "TASK_CANCEL";

/** Emitted periodically with progress info while a task runs. */
export const TASK_PROGRESS = "TASK_PROGRESS";

// ── Planning ─────────────────────────────────────────────────────────

/** Background sends a plan request to the server. */
export const PLAN_REQUEST = "PLAN_REQUEST";

/** Server responds with a generated plan. */
export const PLAN_RESPONSE = "PLAN_RESPONSE";

// ── Action Execution ─────────────────────────────────────────────────

/** Background instructs content script to execute an action step. */
export const EXECUTE_ACTION = "EXECUTE_ACTION";

/** Content script reports the result of an executed action. */
export const ACTION_RESULT = "ACTION_RESULT";

/** An action requires user confirmation before proceeding. */
export const ACTION_CONFIRM_REQUEST = "ACTION_CONFIRM_REQUEST";

/** User confirms or denies a pending action. */
export const ACTION_CONFIRM_RESPONSE = "ACTION_CONFIRM_RESPONSE";

// ── Verification ─────────────────────────────────────────────────────

/** Background sends a verification request to the server. */
export const VERIFY_REQUEST = "VERIFY_REQUEST";

/** Server responds with verification results. */
export const VERIFY_RESPONSE = "VERIFY_RESPONSE";

// ── Memory & Teaching ────────────────────────────────────────────────

/** User submits a teaching instruction via the side-panel. */
export const TEACHING_SUBMIT = "TEACHING_SUBMIT";

/** Server acknowledges a teaching instruction was stored. */
export const TEACHING_ACK = "TEACHING_ACK";

/** Side-panel requests stored memories for display. */
export const MEMORY_LIST_REQUEST = "MEMORY_LIST_REQUEST";

/** Background returns a list of memory items. */
export const MEMORY_LIST_RESPONSE = "MEMORY_LIST_RESPONSE";

/** Side-panel requests deletion of a memory item. */
export const MEMORY_DELETE = "MEMORY_DELETE";

// ── Page Annotations ─────────────────────────────────────────────────

/** Background instructs content script to highlight an element. */
export const HIGHLIGHT = "HIGHLIGHT";

/** Background instructs content script to show a status badge on an element. */
export const SHOW_BADGE = "SHOW_BADGE";

/** Background instructs content script to remove all visual annotations. */
export const CLEAR_ANNOTATIONS = "CLEAR_ANNOTATIONS";

/** Background instructs content script to show the thinking indicator. */
export const SHOW_THINKING = "SHOW_THINKING";

/** Background instructs content script to hide the thinking indicator. */
export const HIDE_THINKING = "HIDE_THINKING";

/** Side-panel requests current page metadata (title, url, scroll position). */
export const PAGE_INFO = "PAGE_INFO";

// ── Tab Intelligence ─────────────────────────────────────────────────

/** Side-panel requests a full multi-tab context snapshot from background. */
export const BUILD_TAB_CONTEXT = "BUILD_TAB_CONTEXT";

// ── MCQ Detection ────────────────────────────────────────────────────

/** Background requests the MCQ detector to scan for questions. */
export const DETECT_MCQ = "DETECT_MCQ";

/** Alias accepted alongside DETECT_MCQ for backwards compatibility. */
export const DETECT_QUESTIONS = "DETECT_QUESTIONS";

// ── Error Handling ───────────────────────────────────────────────────

/** Generic error message forwarded to the side-panel for display. */
export const ERROR = "ERROR";

// ── Aggregate Exports ────────────────────────────────────────────────

/**
 * Complete set of all message types.
 * Useful for runtime validation: `if (!ALL_MESSAGE_TYPES.has(msg.type)) …`
 */
export const ALL_MESSAGE_TYPES = new Set([
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
  BUILD_TAB_CONTEXT,
  HIGHLIGHT,
  SHOW_BADGE,
  CLEAR_ANNOTATIONS,
  SHOW_THINKING,
  HIDE_THINKING,
  PAGE_INFO,
  DETECT_MCQ,
  DETECT_QUESTIONS,
  ERROR,
]);
