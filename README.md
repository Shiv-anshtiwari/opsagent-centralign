# OpsAgent: an autonomous AI operations employee

OpsAgent takes a short business request and gets it done in the company's actual systems. For example:

> *"Find the latest invoice from Globex Industries, extract the amount and due date, enter it into our ERP, and tell me once it's done."*

It drives a **real Chromium browser** through two separate company web apps: a supplier portal and an ERP. It reads company files and follows the handbook, which defines procedures and an approval policy. It asks a human for approval when policy requires it and recovers from failures along the way. A **separate, read-only verifier** then checks the outcome, and every run produces an **evidence report** with screenshots.

Built for the CentrAlign AI *AI Engineering Intern* task ("Autonomous AI Task Worker").

---

## Demo

- 🎥 Demo video: **<add link>**
- Sample evidence report: generated in `runs/<timestamp>/report.html` on every run

What the demo shows, all with **the same code and no task-specific logic**:

| Task (natural language) | What the agent works out by itself |
|---|---|
| `invoice`: "Find the latest invoice from Globex…enter it into our ERP" | "Latest" means latest *invoice date*, not list order. It dismisses a cookie overlay that blocks clicks and converts "October 28, 2026" to `28-10-2026`. Because the amount is ₹72,450 (≥ ₹50k), it asks for manager approval first. When the ERP returns a 503 ("NOT saved"), it re-opens the form and resubmits. The verifier then confirms the AP record. |
| `vendor`: "Procurement emailed us about a new supplier…set them up" | It finds the right email in the inbox and extracts name, GSTIN and contact. It reads "payment within 45 days" as **Net 45** (overriding the Net 30 default), gets approval and creates the vendor. The verifier then checks the vendor master. |
| `unknown_vendor`: "Record the latest invoice from Hooli Cloud" | It finds the invoice, but sees that Hooli is **not in the vendor master**. The handbook forbids creating vendors during invoice entry, so it **stops and escalates** instead of doing the wrong thing. |

---

## Setup & run

Requirements: Python 3.10+, and a Gemini API key.

```bash
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env          # then put your GEMINI_API_KEY in .env

# terminal 1: the sandbox company (supplier portal + ERP) on http://127.0.0.1:8000
python -m uvicorn company_app.server:app --port 8000

# terminal 2: give the AI employee a task
python run.py --task invoice --reset          # predefined demo task (watch the browser)
python run.py --task vendor
python run.py --task unknown_vendor
python run.py "Make sure Initech's latest invoice is in AP."   # any free-form request
```

Flags: `--reset` resets the sandbox data · `--auto-approve` grants approvals automatically (unattended mode) · `--headless` · `--max-steps N`.
If the sandbox isn't running, `run.py` starts it in-process automatically.
`CHAOS=0` turns off the injected failures (overlay, 503).

Explore the sandbox yourself at http://127.0.0.1:8000. The ERP login is `ops.agent` / `acme-sandbox-2026` (a mock account).

---

## Architecture

```
            request (natural language)
                     │
          ┌──────────▼───────────┐   company_drive/handbook.md  (company context: systems, procedures, policy)
          │ 1. UNDERSTAND & PLAN │◄─ memory/lessons.md          (long-term memory from previous runs)
          │  intended outcome,   │
          │  success criteria,   │
          │  plan, policies      │
          └──────────┬───────────┘
                     │
   ┌─────────────────▼───────────────────────────┐
   │ 2. EXECUTE → OBSERVE → ADAPT  (tool loop)   │
   │  LLM picks the next tool from the latest    │      ┌──────────────────────────────┐
   │  observation; errors become observations;   │─────►│ POLICY GATE (code, not prompt)│
   │  working memory; plan revision; loop guard  │      │ host allow-list; commit clicks│
   │                                             │      │ blocked until approval exists │
   │ tools: navigate · read_page · click ·       │      └──────────────────────────────┘
   │ type_text · select_option · list_files ·    │
   │ read_file · remember · update_plan ·        │──► Playwright Chromium ──► SupplyHub / Acme ERP
   │ request_approval · ask_human · finish       │──► company_drive/ (sandboxed file access)
   └─────────────────┬───────────────────────────┘──► human (terminal) for approvals / questions
                     │ finish(status, summary, evidence)
          ┌──────────▼───────────┐
          │ 3. INDEPENDENT VERIFY │ separate LLM context, READ-ONLY tools, checks each success
          │                      │ criterion against the real systems → pass/fail + evidence
          └──────────┬───────────┘
                fail │ pass
   feedback to the   │
   same agent ◄──────┘ (up to 3 rounds)
                     │
          ┌──────────▼───────────┐
          │ 4. COMPLETE & LEARN  │ summary + report.html (plan, every step, screenshots,
          │                      │ verdict) + trace.json; lessons appended to long-term memory
          └──────────────────────┘
```

| File | Role |
|---|---|
| `agent/core.py` | The runtime: understand → tool loop → verify → learn. Prompts and tool schemas. |
| `agent/browser.py` | Playwright wrapper. Indexed DOM snapshot as the observation, actions, screenshots. |
| `agent/policy.py` | Approval rules and allowed hosts as **data**, enforced before every navigation and committing click. |
| `agent/llm.py` | Gemini client: function calling, JSON mode, retry with exponential backoff. |
| `agent/report.py` | Evidence output: `trace.json` and a self-contained `report.html`. |
| `company_app/server.py` | The sandbox company: SupplyHub supplier portal and Acme ERP (login, AP, vendor master), with injected failures. |
| `company_drive/` | Company knowledge: the handbook and an inbox of forwarded emails. |
| `run.py` | CLI. |

---

## Key design decisions (and why)

1. **Outcome first, so success criteria come before execution.** The *Understand* step turns a vague request into checkable statements about the final system state, for example "AP contains GX-1057 with amount 72450.00". Those criteria are then what verification checks. "Done" means the world is in the right state, not that the agent ran out of steps.

2. **Generic tools with no task-specific code.** There is no "enter_invoice" tool or hard-coded workflow. The agent has the same primitives a human has (a browser, files, a way to ask). Company-specific knowledge lives in **data**: the handbook, the policy rules and the learned lessons. A new workflow is a new handbook section, not new code. This is the generalization lever. All three demo tasks run through identical code.

3. **The page structure, not pixels, is the observation.** Every action returns a compact, indexed list of interactive elements (labels, values, options, hrefs) plus visible text. Compared with screenshot-based computer use, this is cheaper, faster, deterministic to act on, and it exposes form state directly. Screenshots are still taken after every action, as **human-facing evidence**. Pixel-based control would plug in for apps with no DOM (see *Next*).

4. **Policy is enforced in code, not trusted to the prompt.** The LLM *knows* the approval policy, but `policy.py` independently inspects the form data behind every committing click ("Submit", "Create"…). If a rule matches (for example amount ≥ 50,000) and no matching approval was granted, the click is **blocked** and the block is returned as an observation. Approvals are bound to values: approving ₹72,450 doesn't authorize ₹95,000. Navigation is limited to an allow-list of company hosts.

5. **Verification is independent.** The verifier is a fresh LLM context that sees only the request, the criteria and the agent's *claim*. It has **read-only** tools (no click or type), so it can't "fix" things to make them pass. It checks the system of record and the source document, compares values exactly and checks for duplicates. If verification fails, the failing criteria go back to the operator, which keeps its context, to repair the actual state.

6. **Failures are observations, not exceptions.** Click intercepted by an overlay, validation errors, a 503, a stale element id, a policy block: all of these go back to the model with the fresh page state. A loop guard detects the same failing action repeating and forces a change of approach. LLM calls retry with exponential backoff.

7. **Memory at two levels.**
   - *Working memory* (`remember`) holds the facts found during the run. It's returned with every observation, so old page snapshots can be elided to keep the context small without losing facts.
   - *Long-term memory*: after each run, a reflection step extracts reusable lessons (for example "the portal shows a cookie banner that must be accepted first") into `memory/lessons.md`. The next run loads them, so runs get faster.

8. **Stopping is better than doing the wrong thing.** The agent can end with `blocked` and a clear hand-off when policy or missing information prevents safe completion, as in the Hooli Cloud task.

---

## Models, APIs, frameworks used

- **LLM:** Google Gemini (`gemini-3.8-flash` by default, configurable with `GEMINI_MODEL`) via the `google-genai` SDK, using native function calling and JSON mode. The same model plays three roles with separate contexts: planner, operator and verifier.
- **Browser automation:** Playwright (Chromium).
- **Sandbox apps:** FastAPI and Uvicorn (server-rendered HTML; no JS framework).
- **Terminal UI:** Rich · **Config:** python-dotenv.
- No agent framework (LangChain etc.). The loop is around 150 lines of plain Python, so every decision is inspectable and debuggable.

## Assumptions

- The company systems are web apps. Both are local mock systems built for this task: no real credentials, no third-party systems.
- Company knowledge (systems, procedures, policies) is written down somewhere the agent can read, here `company_drive/handbook.md`.
- The human in the loop is reachable in the terminal. Without a human, approvals are denied and questions return "no human available", so the agent stops instead of guessing.
- One task runs at a time, in one browser session.

## Known limitations

- **Web apps only.** No pixel-level control of desktop apps yet, and no PDF/OCR parsing (inbox files are text).
- **The verifier uses the same model family as the operator.** It's independent in context and permissions, but could share blind spots. Deterministic checks (for example a direct DB/API read) would be stronger where available.
- **The commit detection behind the policy gate is a heuristic** (button text plus form data). A production system would classify actions per connector.
- **Sandbox state is in-memory.** It resets when the server restarts or with `--reset`.
- **Latency.** Each step is an LLM round-trip, so a full task takes about 1–3 minutes. Recorded runs could be replayed as deterministic "skills" (see below).
- **Long-term lessons are appended without curation.** They could grow noisy or go stale.

## What I'd build next

1. **Skill compilation:** turn a verified trace into a reusable, parameterized procedure that replays deterministically, falling back to the LLM only when the page differs. That gives speed, cost savings and reliability for repeated workflows.
2. **Connectors behind the same tool interface:** email/IMAP, Google Drive, REST APIs, plus a vision-based computer-use backend for desktop apps. The agent picks API when available and UI when not.
3. **Async approvals and a web dashboard:** a task queue, background execution, and approvals over Slack or email with a timeout, instead of blocking the terminal. Live trace view.
4. **Stronger verification:** deterministic verifiers per connector (query the record through the API), with an LLM judge only for fuzzy criteria.
5. **Evaluation harness:** a suite of tasks × injected faults (random 5xx, layout changes, missing data), tracking success rate, steps, cost and false-completion rate per model and prompt version.
6. **Memory with structure:** curated, scoped, versioned lessons per company, system and workflow, plus retrieval over company documents instead of one handbook in the prompt.
7. **Security:** per-task scoped credentials from a vault (never in the prompt), an audit log of every action, and prompt-injection defences for content read from web pages and emails.
