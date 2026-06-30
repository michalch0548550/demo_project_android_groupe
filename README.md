# AppsFlyer MCP Android Integration Demo

An end-to-end demo that tests whether an AI agent can integrate the **AppsFlyer Android SDK** into a real Android project using the [**AppsFlyer MCP server**](https://www.npmjs.com/package/@appsflyer/sdk-mcp-server), then verify the result by compiling the app.

The repository contains two parts:

1. **Python automation runner** — orchestrates Gemini, the AppsFlyer MCP server, file edits, and a Gradle build inside a LangGraph workflow.
2. **`basic_app/`** — a sample Android app (FEED.ME fruit store) used as the integration target. The original app is never modified; each run works on a sandbox copy.

---

## How it works

When you run `python main.py`, the workflow executes these steps:

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────────┐
│  Setup          │────:arrow_forwards:│  AI Agent        │────:arrow_forwards:│  MCP: integrateSdk  │
│  (sandbox copy) │     │  (Gemini)        │     │  (AppsFlyer server) │
└─────────────────┘     └──────────────────┘     └─────────────────────┘
         │                                                  │
         │                       ┌──────────────────────────┘
         │                       ▼
         │              ┌─────────────────────┐     ┌──────────────────┐
         └─────────────:arrow_forwards:│  Apply SDK changes  │────:arrow_forwards:│  Gradle build    │
                        │  (Gemini → files)   │     │  assembleDebug   │
                        └─────────────────────┘     └──────────────────┘
                                                              │
                                                              ▼
                                                   ┌──────────────────┐
                                                   │  JSON report     │
                                                   │  PASS / FAIL     │
                                                   └──────────────────┘
```

| Step | What happens |
|------|--------------|
| **1. Setup** | Copies `basic_app/` into `sandboxes/run_<timestamp>/`, checks MCP connectivity via `getVersion`, and lists available MCP tools. |
| **2. Agent prompt** | Gemini generates a technical prompt (`PromptsAgent.py`) and is asked to call the `integrateSdk` tool with `platform=android` and `useResponseListener=false`. If the model asks clarifying questions instead, a fallback prompt forces the tool call. |
| **3. MCP activation** | The selected MCP tool is executed against `@appsflyer/sdk-mcp-server` (via `npx`). Integration instructions are returned as text. |
| **4. Apply changes** | Gemini reads the MCP instructions plus current project files and returns updated file contents. Changes are written to the sandbox (`apply_sdk_changes.py`). Gradle is checked for an AppsFlyer dependency. |
| **5. Compilation** | Runs `gradlew assembleDebug` in the sandbox to confirm the project builds. |
| **6. Report** | Writes `mcp_test_report.json` with final status, MCP tools used, modified files, and per-step logs. |

---

## Prerequisites

| Requirement | Purpose |
|-------------|---------|
| **Python 3.10+** | Runs the automation workflow |
| **Node.js + npm** | Launches the AppsFlyer MCP server via `npx` |
| **Android SDK + JDK** | Gradle build step (`assembleDebug`) |
| **AppsFlyer Dev Key** | Passed to the MCP server |
| **Google Gemini API key** | Powers the AI agent and file-applier steps |

---

## Quick start

### 1. Clone the repository

```bash
git clone <your-repo-url>
cd demo_project_android_groupe
```

### 2. Configure environment variables

Create a `.env` file in the project root (this file is git-ignored):

```env
DEV_KEY="your_appsflyer_dev_key"
APP_ID="com.appsflyer.onelink.appsflyeronelinkbasicapp"
GEMINI_API_KEY="your_gemini_api_key"

# Optional
GEMINI_MODEL="gemini-2.5-flash"
USE_MOCK_AGENT=false
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY="your_langsmith_key"
LANGCHAIN_PROJECT="appsflyer-mcp-test"
```

> **Security:** Never commit real Dev Keys or API keys to a public repository.

### 3. Set up Python

```bash
# Windows — use the py launcher if `python` opens the Microsoft Store
py -m venv .venv

# macOS / Linux
python3 -m venv .venv
```

Activate the virtual environment:

```powershell
# Windows (PowerShell)
.\.venv\Scripts\Activate.ps1
```

```bash
# macOS / Linux
source .venv/bin/activate
```

Install dependencies:

```bash
pip install langgraph langchain-google-genai langchain-core python-dotenv mcp
```

### 4. Run the automation

```bash
python main.py
```

On success you should see `Test finished. Status: PASS` and a report at `mcp_test_report.json`.

---

## Project structure

```
demo_project_android_groupe/
├── main.py                 # Entry point — runs the LangGraph workflow
├── workflow_builder.py     # Graph definition, routing, initial state, summary output
├── workflow_nodes.py       # Step implementations (setup, agent, MCP, build, report)
├── mcp_client.py           # AppsFlyer MCP server client (stdio via npx)
├── agent_runner.py         # Gemini LLM with integrateSdk tool binding
├── apply_sdk_changes.py    # Applies MCP instructions to Android project files
├── PromptsAgent.py         # Generates dynamic agent prompts via Gemini
├── android_project.py      # Sandbox copy, Gradle wrapper, build helpers
├── config.py               # Shared config and tool schema
├── test_state.py           # TypedDict state schema for the workflow
├── responseAgent.py        # Standalone helper for answering SDK install questions (not wired into main flow)
├── basic_app/              # Sample Android app (integration target)
│   └── README.md           # Details about the FEED.ME demo app
├── sandboxes/              # Generated per-run copies of basic_app (git-ignored)
└── mcp_test_report.json    # Generated test report (git-ignored)
```

---

## Sample Android app (`basic_app/`)

`basic_app` is AppsFlyer's **FEED.ME** fruit store sample — a minimal Android app for demonstrating OneLink deep linking. In this repo it serves as the **target project** for automated SDK integration.

- **Package:** `com.appsflyer.onelink.appsflyeronelinkbasicapp`
- **Min SDK:** 21 | **Target SDK:** 33
- See [`basic_app/README.md`](basic_app/README.md) for app-specific details and manual run instructions.

### Run the Android app manually (without the automation)

```bash
cd basic_app

# Windows
.\gradlew.bat assembleDebug
.\gradlew.bat installDebug

# macOS / Linux
./gradlew assembleDebug
./gradlew installDebug
```

Or open `basic_app/` in Android Studio and run on a device or emulator.

---

## Output and debugging

### Console

During a run, each workflow node prints progress:

```
[1] node: Setup Environment
[2] node: Run Agent Prompt
[3] node: Verify MCP Activation
[4] Node: Apply SDK Changes
[5] node: Check Compilation
[6] node: End Report
```

At the end, a summary lists all MCP tools available on the server, which tools were used, and which files were modified.

### Report file

`mcp_test_report.json` includes:

- `final_status` — `PASS` or `FAIL`
- `mcp_triggered` — whether the MCP tool ran successfully
- `sdk_verified` — whether an AppsFlyer Gradle dependency was detected
- `compilation_passed` — whether `assembleDebug` succeeded
- `applied_files` — list of modified project files
- `detailed_steps` — full log from every workflow node

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `python` only prints `Python` on Windows | Use `py` instead of `python`, or disable **App execution aliases** for Python in Windows Settings |
| `Activate.ps1` not found | Create the venv first: `py -m venv .venv` |
| `pip` not recognized | Activate the venv, or run `py -m pip install ...` |
| MCP server unavailable | Ensure Node.js is installed and `npx` is on your PATH |
| Gradle build fails | Install Android SDK; set `JAVA_HOME`; open the project once in Android Studio to sync SDK components |
| PowerShell blocks activation | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |

---

## Presentation

A ready-to-use PowerPoint deck is included for demos and talks:

| File | Description |
|------|-------------|
| [`AppsFlyer_MCP_Android_Demo.pptx`](AppsFlyer_MCP_Android_Demo.pptx) | 10-slide PowerPoint presentation in the **project root** (double-click to open) |
| [`docs/presentation.html`](docs/presentation.html) | Browser slide deck (arrow keys / space to navigate) |
| [`docs/generate_presentation.py`](docs/generate_presentation.py) | Regenerate the `.pptx` after editing slide content |

To regenerate the PowerPoint file:

```bash
pip install python-pptx
python docs/generate_presentation.py
```

---

## Related links

- [AppsFlyer Android SDK documentation](https://dev.appsflyer.com/hc/docs/android)
- [AppsFlyer MCP server on npm](https://www.npmjs.com/package/@appsflyer/sdk-mcp-server)
- [LangGraph documentation](https://langchain-ai.github.io/langgraph/)

---

## License

See the repository license file for terms. Do not publish AppsFlyer Dev Keys or API credentials in public forks or commits.
