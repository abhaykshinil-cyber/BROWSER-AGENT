/**
 * BrowserAgent — Action Type Constants
 *
 * Canonical list of every atomic browser action the agent can perform.
 * Shared between the Chrome extension (content scripts, background
 * worker) and the Python agent-server (via identical string values).
 *
 * Usage (ES module):
 *   import { CLICK, TYPE } from '../shared/constants.js';
 *
 * Usage (CommonJS / service-worker):
 *   const { CLICK, TYPE } = await import('../shared/constants.js');
 */

/** Scan the page and extract visible elements + text. */
export const SCAN = "SCAN";

/** Visually highlight / select a target element. */
export const SELECT = "SELECT";

/** Simulate a click on the target element. */
export const CLICK = "CLICK";

/** Type text into an input, textarea, or contenteditable element. */
export const TYPE = "TYPE";

/** Scroll the viewport or a scrollable container. */
export const SCROLL = "SCROLL";

/** Navigate the tab to a new URL. */
export const NAVIGATE = "NAVIGATE";

/** Extract structured data from the current page. */
export const EXTRACT = "EXTRACT";

/** Capture a screenshot of the visible viewport. */
export const SCREENSHOT = "SCREENSHOT";

/** Submit the currently focused form. */
export const SUBMIT = "SUBMIT";

/** Wait for a specified duration or for an element to appear. */
export const WAIT = "WAIT";

/** Switch focus to a different browser tab. */
export const SWITCH_TAB = "SWITCH_TAB";

/**
 * Ordered array of every action type.
 * Useful for validation, iteration, and UI dropdowns.
 */
export const ALL_ACTION_TYPES = [
  SCAN,
  SELECT,
  CLICK,
  TYPE,
  SCROLL,
  NAVIGATE,
  EXTRACT,
  SCREENSHOT,
  SUBMIT,
  WAIT,
  SWITCH_TAB,
];

/**
 * Set of action types that mutate page state and may require
 * user confirmation when `require_confirmation` is enabled.
 */
export const MUTATING_ACTIONS = new Set([
  CLICK,
  TYPE,
  SCROLL,
  NAVIGATE,
  SUBMIT,
  SWITCH_TAB,
]);

/**
 * Set of action types that only observe and never change the page.
 */
export const READONLY_ACTIONS = new Set([
  SCAN,
  SELECT,
  EXTRACT,
  SCREENSHOT,
  WAIT,
]);
