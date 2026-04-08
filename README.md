<div align="center">

# 🤖 BrowserAgent

**An AI-powered Chrome extension that observes, plans, executes, verifies, and learns — fully autonomous browser automation driven by Google Gemini.**

[![Chrome](https://img.shields.io/badge/Chrome-Extension-4285F4?logo=googlechrome&logoColor=white)](https://developer.chrome.com/docs/extensions/)
[![Manifest V3](https://img.shields.io/badge/Manifest-V3-green)](https://developer.chrome.com/docs/extensions/mv3/intro/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Gemini](https://img.shields.io/badge/Google_Gemini-2.0_Flash-8E75B2?logo=google&logoColor=white)](https://ai.google.dev/)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

</div>

---

## 📖 Overview

BrowserAgent is a full-stack Chrome extension that gives you a personal AI agent living in your browser's side panel. You describe a goal in plain English — *"Fill out this contact form with my details"*, *"Find the cheapest plan and click Sign Up"*, *"Answer all MCQ questions on this page"* — and BrowserAgent handles everything else.

The agent doesn't just execute blindly. After every run it **learns** from what it did, builds site-specific profiles, and stores reusable rules so it gets smarter with every use.

```
You type a goal  →  Agent scans the page  →  Gemini builds a plan
     →  Steps execute in your browser  →  Agent verifies success
          →  Rules & memories saved for next time
```

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🧠 **AI Planning** | Google Gemini 2.0 Flash decomposes any goal into atomic browser actions |
| 👁️ **Page Understanding** | DOM scanner extracts interactive elements, forms, text, and MCQ questions |
| ⚡ **Action Execution** | Click, type, scroll, navigate, submit, extract, hover, and more |
| ✅ **Goal Verification** | After execution, Gemini checks whether the goal was actually achieved |
| 📚 **Self-Learning** | Generates reusable rules from every successful run automatically |
| 🗄️ **Memory System** | Four-tier memory: episodic, semantic, site-specific, and user rules |
| 🎓 **Teaching Mode** | Teach the agent rules in plain English — *"Never click ads"* |
| 📋 **MCQ Solver** | Detects and answers multiple-choice questions on any page |
| 🗂️ **Multi-Tab Support** | Aggregate context across all open tabs for complex workflows |
| 🔒 **Policy Engine** | Safety checks before risky actions; user confirmation for destructive steps |
| 🎛️ **Side Panel UI** | Full-featured panel: task history, memory viewer, tab manager, teach interface |

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Chrome Browser                           │
│                                                                 │
│  ┌──────────────────┐        ┌──────────────────────────────┐  │
│  │   Side Panel UI  │◄──────►│   Background Service Worker  │  │
│  │  (sidepanel.js)  │  msgs  │      (background.js)         │  │
│  │                  │        │  • REST client               │  │
│  │  • Task input    │        │  • WebSocket client          │  │
│  │  • Step viewer   │        │  • Step executor             │  │
│  │  • Memory viewer │        │  • Tab manager               │  │
│  │  • Teach panel   │        └──────────┬───────────────────┘  │
│  └──────────────────┘                   │ REST / WS            │
│                                         │                       │
│  ┌──────────────────────────────────┐   │                       │
│  │  Content Scripts (per tab)       │◄──┤ chrome.tabs.send      │
│  │  • dom-sensor.js  (page scan)    │   │                       │
│  │  • action-runner.js (execute)    │   │                       │
│  │  • annotation.js  (highlights)   │   │                       │
│  │  • mcq-detector.js (MCQ parsing) │   │                       │
│  └──────────────────────────────────┘   │                       │
└─────────────────────────────────────────┼───────────────────────┘
                                          │ HTTP :8765
                          ┌───────────────▼─────────────────┐
                          │      FastAPI Backend             │
                          │   (agent-server/)                │
                          │                                  │
                          │  POST /plan   → Planner          │
                          │  POST /verify → Verifier         │
                          │  POST /teach  → Policy Engine    │
                          │  POST /mcq    → MCQ Solver       │
                          │  GET  /memory → Memory CRUD      │
                          │                                  │
                          │  ┌────────────┐  ┌───────────┐  │
                          │  │  Google    │  │  SQLite   │  │
                          │  │  Gemini    │  │ + HNSWLIB │  │
                          │  │  2.0 Flash │  │ (vectors) │  │
                          │  └────────────┘  └───────────┘  │
                          └──────────────────────────────────┘
```

---

## 🛠️ Tech Stack

**Backend**
- **[FastAPI](https://fastapi.tiangolo.com/)** — Async Python web framework
- **[Google Gemini 2.0 Flash](https://ai.google.dev/)** — Main LLM (free tier compatible)
- **[Google Gemini 2.0 Flash-Lite](https://ai.google.dev/)** — Fast model for verification
- **[SQLite + aiosqlite](https://github.com/omnilib/aiosqlite)** — Async persistent storage
- **[HNSWLIB](https://github.com/nmslib/hnswlib)** — Vector similarity search for memory
- **[Pydantic v2](https://docs.pydantic.dev/)** — Data validation and settings

**Extension**
- **Chrome Manifest V3** — Service worker, content scripts, side panel
- **Vanilla ES Modules** — No framework, minimal footprint
- **WebSocket** — Real-time streaming from backend

---

## 📋 Prerequisites

- **Google Chrome** 116 or later
- **Python** 3.11 or later
- **Google Gemini API key** (free at [aistudio.google.com](https://aistudio.google.com/app/apikey))

---

## 🚀 Installation

### 1. Clone the Repository

```bash
git clone https://github.com/abhaykshinil-cyber/BrowserAgent.git
cd BrowserAgent
```

### 2. Set Up the Backend

```bash
cd agent-server
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Create your environment file:

```bash
copy .env.example .env       # Windows
# OR
cp .env.example .env         # macOS / Linux
```

Open `.env` and paste your Gemini API key:

```env
GEMINI_API_KEY=AIzaSy...your_key_here...
```

Start the server:

```bash
python -m uvicorn app:app --port 8765 --reload
```

You should see:
```
INFO:     Uvicorn running on http://127.0.0.1:8765
INFO:     BrowserAgent server v0.3.0 ready
```

### 3. Load the Chrome Extension

1. Open Chrome and go to `chrome://extensions`
2. Enable **Developer mode** (toggle in the top-right corner)
3. Click **Load unpacked**
4. Select the `extension/` folder inside this repo
5. The BrowserAgent icon will appear in your toolbar

### 4. Open the Side Panel

Click the BrowserAgent toolbar icon, or right-click any page → **Open BrowserAgent**. The status indicator will turn green once the backend is reachable.

---

## ⚙️ Configuration

All configuration is read from environment variables. Create `agent-server/.env` based on the table below:

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | *(required)* | Your Google Gemini API key |
| `BROWSERAGENT_MODEL` | `gemini-2.0-flash` | Main LLM for planning & learning |
| `BROWSERAGENT_EMBEDDING_MODEL` | `models/text-embedding-004` | Embedding model for memory search |
| `BROWSERAGENT_MAX_TOKENS` | `4096` | Maximum tokens per LLM response |
| `BROWSERAGENT_PORT` | `8765` | Backend server port |
| `BROWSERAGENT_DEBUG` | `false` | Enable verbose logging |
| `BROWSERAGENT_DB_PATH` | `./database/agent.db` | SQLite database path |
| `BROWSERAGENT_ACTION_DELAY_MS` | `800` | Delay (ms) between browser actions |

---

## 🎯 Usage

### Running a Task

1. Navigate to any webpage
2. Open the BrowserAgent side panel
3. Type your goal in plain English and press **Run**

**Example goals:**
```
Fill out the contact form with my name and email
Find the pricing page and click the Pro plan
Scroll to the bottom and click Accept Cookies
Answer all the quiz questions on this page
Extract all product names from this page
```

### Teaching the Agent Rules

Switch to the **Teach** tab in the side panel and type a rule:

```
Never click the Sign Up button on GitHub
Always select "Remember me" on login forms
On Amazon, always sort results by lowest price first
```

Rules are parsed by the Policy Engine and applied to all future runs on matching sites.

### Viewing Memories

The **Memory** tab shows everything the agent has learned:
- **Site Profiles** — Per-domain knowledge and preferred actions
- **Semantic Memory** — General facts learned across all runs
- **User Rules** — Teaching rules you've defined

You can search, inspect, and delete any memory entry.

### Multi-Tab Mode

Switch to the **Tabs** tab to see all open tabs. The agent can build cross-tab context — useful for tasks like *"Compare prices across these three tabs and tell me the cheapest"*.

---

## 🔌 API Reference

The backend exposes a REST API on `http://localhost:8765`.

### `GET /health`
Returns server status, model info, and current run state.

```json
{
  "status": "ok",
  "version": "0.3.0",
  "model": "gemini-2.0-flash",
  "running": false
}
```

### `POST /plan`
Generate an action plan for a goal.

```json
// Request
{
  "goal": "Click the login button",
  "page_context": { "url": "...", "elements": [...] },
  "memories": []
}

// Response
{
  "steps": [
    { "action": "CLICK", "selector": "#login-btn", "description": "Click login button" }
  ]
}
```

### `POST /verify`
Verify whether a goal was achieved after execution.

```json
// Request
{ "goal": "Submit the form", "page_context": { ... }, "steps_taken": [...] }

// Response
{ "achieved": true, "confidence": 0.95, "reasoning": "Form submission confirmed by success message" }
```

### `POST /teach`
Store a user-defined rule.

```json
// Request
{ "instruction": "Always click Decline on cookie banners" }

// Response
{ "id": "rule_abc123", "parsed": { "trigger": "cookie banner", "action": "CLICK Decline" } }
```

### `POST /mcq`
Solve multiple-choice questions.

```json
// Request
{
  "questions": [{ "qIdx": 0, "text": "What is 2+2?", "options": [{"idx":0,"text":"3"},{"idx":1,"text":"4"}] }],
  "context": "Basic arithmetic quiz"
}

// Response
{
  "answers": [{ "qIdx": 0, "selected": [1], "confidence": 0.99, "reasoning": "2+2=4" }]
}
```

### `GET /memory`
List stored memories with optional filters.

### `DELETE /memory/{id}`
Delete a specific memory entry.

### `GET /memory/site-profiles`
List all learned site profiles.

---

## 🧠 Memory System

BrowserAgent uses a four-tier memory architecture:

| Type | Scope | Purpose |
|------|-------|---------|
| **Episodic** | Session | What happened in the current/recent run |
| **Semantic** | Global | General knowledge learned across all runs |
| **Site Profile** | Per-domain | Site-specific patterns, selectors, and preferences |
| **User Rule** | Global | Rules you taught via the Teach panel |

Memory retrieval uses **vector similarity search** (HNSWLIB + Gemini embeddings) to find the most relevant memories for each new task — so the right knowledge is always surfaced at the right time.

---

## 🤖 Agent Loop

```
┌─────────────────────────────────────────┐
│  1. SCAN   Capture page DOM + context   │
│  2. PLAN   Gemini generates action plan │
│  3. CHECK  Policy engine safety check   │
│  4. CONFIRM Risky actions need approval │
│  5. EXECUTE Run steps in browser        │
│  6. VERIFY Did we achieve the goal?     │
│  7. LEARN  Store rules + update profile │
└─────────────────────────────────────────┘
```

---

## 🗂️ Project Structure

```
BrowserAgent/
├── agent-server/          Python FastAPI backend
│   ├── app.py             Application entry point
│   ├── config.py          Configuration (env vars)
│   ├── schemas.py         Pydantic data models
│   ├── api/               REST API route handlers
│   │   ├── plan.py
│   │   ├── verify.py
│   │   ├── teach.py
│   │   ├── memory.py
│   │   └── mcq.py
│   ├── core/              Core AI logic
│   │   ├── llm.py         Gemini wrapper
│   │   ├── planner.py     Goal → action plan
│   │   ├── executor.py    Step validation
│   │   ├── verifier.py    Goal verification
│   │   ├── learning_engine.py  Post-run learning
│   │   ├── mcq_solver.py  MCQ answering
│   │   ├── policy_engine.py    Safety rules
│   │   └── tab_context_builder.py
│   ├── memory/            Memory subsystem
│   │   ├── episodic_store.py
│   │   ├── semantic_store.py
│   │   ├── site_profiles.py
│   │   └── retrieval.py
│   ├── prompts/           LLM system prompts
│   ├── requirements.txt
│   └── .env.example
│
├── extension/             Chrome MV3 extension
│   ├── manifest.json
│   ├── background.js      Service worker
│   ├── background/
│   │   └── tab-manager.js
│   ├── content/           Per-tab content scripts
│   │   ├── dom-sensor.js
│   │   ├── action-runner.js
│   │   ├── annotation.js
│   │   └── mcq-detector.js
│   ├── sidepanel/         Side panel UI
│   │   ├── sidepanel.html
│   │   ├── sidepanel.js
│   │   ├── agent-controller.js
│   │   ├── teach-panel.js
│   │   ├── memory-panel.js
│   │   └── tabs-panel.js
│   └── icons/
│
├── shared/                Shared constants
├── docs/                  Architecture docs
└── .gitignore
```

---

## 🔧 Development

### Running Tests

```bash
cd agent-server
python -m pytest test_*.py -v
```

### Watching Logs

Start the server in debug mode for verbose output:

```env
BROWSERAGENT_DEBUG=true
```

### Reloading the Extension

After editing extension files:
1. Go to `chrome://extensions`
2. Click the **↺ refresh** icon on the BrowserAgent card

After editing backend files, uvicorn auto-reloads if started with `--reload`.

---

## 🔐 Security & Privacy

- Your **API key never leaves your machine** — it lives in `.env` (git-ignored) and is only used for direct calls to Google's API from your local server
- **No data is sent to any third party** other than Google Gemini for LLM inference
- The `.gitignore` excludes `.env`, all database files, and logs
- Risky browser actions (form submission, navigation away) trigger a **confirmation prompt** before executing

---

## 🤝 Contributing

Pull requests are welcome. For major changes, please open an issue first.

1. Fork the repo
2. Create your branch: `git checkout -b feature/my-feature`
3. Commit your changes: `git commit -m "Add my feature"`
4. Push: `git push origin feature/my-feature`
5. Open a pull request

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

<div align="center">

Built with ❤️ using [Google Gemini](https://ai.google.dev/) · [FastAPI](https://fastapi.tiangolo.com/) · [Chrome Extensions MV3](https://developer.chrome.com/docs/extensions/mv3/intro/)

</div>
