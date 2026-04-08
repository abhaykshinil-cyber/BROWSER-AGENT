# BrowserAgent — Architecture Overview

> A Chrome-based AI browser agent that observes webpages, plans actions,
> executes them, verifies results, and continuously learns from user
> instructions.

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Chrome Extension                         │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────────────┐ │
│  │ Side     │  │  Background  │  │   Content Script      │ │
│  │ Panel UI │◄►│  Service     │◄►│   (per-tab)           │ │
│  │          │  │  Worker      │  │   • DOM observer      │ │
│  │ • Task   │  │  • WS client │  │   • Element scanner   │ │
│  │   input  │  │  • Router    │  │   • Action executor   │ │
│  │ • Status │  │  • State     │  │   • Mutation listener │ │
│  │ • Memory │  │    machine   │  │                       │ │
│  │ • Teach  │  │              │  │                       │ │
│  └──────────┘  └──────┬───────┘  └───────────────────────┘ │
└─────────────────────── │ ───────────────────────────────────┘
                         │ WebSocket
┌────────────────────────▼────────────────────────────────────┐
│                  Agent Server (FastAPI)                      │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐  ┌───────────┐ │
│  │ Planner  │  │ Executor │  │ Verifier  │  │ Memory    │ │
│  │          │  │ Manager  │  │           │  │ Manager   │ │
│  │ • LLM    │  │          │  │ • State   │  │ • CRUD    │ │
│  │   calls  │  │ • Step   │  │   diff    │  │ • Vector  │ │
│  │ • Prompt │  │   queue  │  │ • Goal    │  │   search  │ │
│  │   build  │  │ • Retry  │  │   check   │  │ • Decay   │ │
│  └──────────┘  └──────────┘  └───────────┘  └─────┬─────┘ │
└────────────────────────────────────────────────────┼────────┘
                                                     │
┌────────────────────────────────────────────────────▼────────┐
│                     Database Layer                          │
│  ┌──────────────────────┐  ┌─────────────────────────────┐ │
│  │   SQLite (agent.db)  │  │  Vector Index (FAISS/hnswlib)│ │
│  │   • tasks            │  │  • memory embeddings         │ │
│  │   • action_history   │  │  • page content embeddings   │ │
│  │   • memories         │  │                              │ │
│  │   • teachings        │  │                              │ │
│  └──────────────────────┘  └─────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

## Core Loop

1. **Observe** — Content script scans the active tab and sends a
   `PageContext` snapshot to the server.
2. **Plan** — The Planner uses the LLM to decompose the user's goal
   into an ordered list of `ActionStep` objects, enriched with relevant
   long-term memories.
3. **Execute** — Steps are sent back to the content script one at a
   time, with a configurable delay (`ACTION_DELAY_MS`) between them.
4. **Verify** — After each step (or the full plan), the Verifier
   compares expected vs. actual page state to decide if the goal was
   met.
5. **Learn** — Successes and failures are stored as episodic memories;
   user teachings are parsed and stored as user-rule memories.

## Directory Map

```
BrowserAgent/
├── extension/               Chrome extension (Manifest V3)
│   ├── manifest.json         Extension manifest
│   ├── background.js         Service worker (WS client, router)
│   ├── content/              Content scripts injected into pages
│   ├── sidepanel/            Side-panel UI (HTML + JS)
│   └── icons/                Extension icons
│
├── agent-server/            Python FastAPI backend
│   ├── config.py             Configuration dataclass
│   ├── schemas.py            Pydantic data models
│   ├── main.py               FastAPI app entry point
│   ├── planner.py            LLM-powered plan generation
│   ├── executor.py           Step execution orchestration
│   ├── verifier.py           Post-action verification
│   └── memory.py             Memory CRUD + vector retrieval
│
├── shared/                  Code shared between extension & server
│   ├── constants.js           Action type constants
│   └── message-types.js       Message type constants
│
├── database/                Persistent storage
│   └── .gitkeep               (agent.db created at runtime)
│
└── docs/                    Documentation
    └── architecture.md        This file
```

## Communication Protocol

All messages between the extension and the server use a simple envelope:

```json
{
  "type": "<MESSAGE_TYPE>",
  "payload": { "..." },
  "timestamp": "2026-04-01T14:00:00Z",
  "request_id": "abc123"
}
```

Message types are defined in `shared/message-types.js` and mirrored in
the Python server's message router.

## Memory System

The agent maintains four types of memory:

| Type        | Scope   | Example                                        |
| ----------- | ------- | ---------------------------------------------- |
| `episodic`  | session | "Last time I clicked Submit it timed out"      |
| `semantic`  | global  | "Login forms usually have username + password"  |
| `site`      | domain  | "GitHub search bar is at the top-right"         |
| `user_rule` | varies  | "Never click 'Sign up' — I already have an account" |

Memories are embedded and stored in a vector index.  At planning time,
the top-*k* most relevant memories are injected into the system prompt.

## Key Design Decisions

- **Manifest V3** — Chrome's latest extension platform; required for
  new extensions.
- **Side Panel** over popup — persistent UI with richer state.
- **WebSocket** over REST — bidirectional streaming lets the server
  push progress updates.
- **SQLite + Vector Index** — lightweight, zero-config persistence that
  ships with the app.
- **Frozen Config Dataclass** — configuration is immutable after load,
  safe for concurrent access.
