"""The operator runtime: Goal -> Understand -> Plan -> Execute/Observe/Adapt -> Verify -> Complete.

Nothing in here is task-specific. The task comes in as natural language; company knowledge comes from
the company drive (handbook) and long-term memory (lessons from previous runs); capability comes from a
small set of generic tools (browser, files, memory, human-in-the-loop).
"""
import json
import time
from datetime import datetime
from pathlib import Path

from google.genai import types
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm

from .browser import Browser, BrowserError
from .llm import LLM, decl, S, N, I
from .policy import Policy
from . import report

ROOT = Path(__file__).resolve().parent.parent
DRIVE = ROOT / "company_drive"
LESSONS = ROOT / "memory" / "lessons.md"
console = Console()

# --------------------------------------------------------------------------- prompts

OPERATOR_SYSTEM = """You are an autonomous AI operations employee at Acme Corp. You turn a short request into
COMPLETED work in the company's real systems, using a real web browser and the company drive.

How you work:
- You act through tools only. Every browser action returns a fresh observation of the page: read it carefully
  before deciding the next action. Element ids like [12] change whenever the page changes.
- Follow the company handbook (below). It defines the systems, procedures and approval policy.
- Never assume an action worked - confirm it from the observation (success message, record page, list).
- When something fails (error message, validation error, server error, blocked click, overlay), work out why
  from the observation and adapt: fix the input, dismiss the obstacle, re-open the form, or try another route.
  Do not repeat the exact same failing action more than twice.
- Use `remember` to store every important fact you discover (ids, amounts, dates, record ids) as soon as you see it.
- If policy requires approval, call `request_approval` BEFORE the committing action. If a human decision or
  missing information is genuinely required and cannot be found in the systems or drive, call `ask_human`.
- If the request cannot be completed safely or correctly (e.g. policy forbids it), stop and call `finish` with
  status "blocked" and explain what a human needs to do. Doing the wrong thing is worse than stopping.
- When done, call `finish` with status "completed", a concise summary and concrete evidence (record ids, values,
  URLs). An independent verifier will check your work against the real systems.

COMPANY HANDBOOK:
{handbook}

LESSONS LEARNED FROM PREVIOUS RUNS (long-term memory):
{lessons}
"""

UNDERSTAND_SYSTEM = """You are the planning module of an AI operations employee at Acme Corp.
Given a request and the company handbook, work out what the requester actually wants done.
Return JSON with keys:
  "intended_outcome": one sentence describing the end state in the company's systems,
  "success_criteria": list of 2-6 concrete, independently checkable statements about the final state of the
      systems (e.g. "AP contains a record for invoice X with amount Y"), using placeholders where values are not
      yet known (e.g. "<amount from the invoice>"),
  "plan": list of 4-10 high-level steps,
  "relevant_policies": list of handbook rules that apply,
  "risks": list of things that could go wrong or need human input.

COMPANY HANDBOOK:
{handbook}
"""

VERIFIER_SYSTEM = """You are an independent QA verifier at Acme Corp. Another agent claims it completed a task.
You do NOT trust its claim. Check the actual state of the company systems yourself with your read-only tools
(navigate by URL, read files) and decide whether each success criterion is really met.
Rules: compare values exactly (invoice numbers, amounts, dates, names). Look at the system of record (ERP), not
just success messages. Also check nothing was done twice (no duplicates).
Be efficient: go straight to the source document(s) and the ERP pages that prove or disprove each criterion
(you are already logged in to the ERP). Do not browse unrelated pages or files. Human approvals are not stored in
the ERP - use the APPROVAL LOG you are given for approval criteria. As soon as every criterion is checked, call
`report_verdict` (you have a limited step budget).

COMPANY HANDBOOK:
{handbook}
"""

LESSONS_SYSTEM = """You maintain the long-term memory of an AI operations employee. Given the trace of a run,
extract 0-3 short, reusable, generalisable lessons (about how the company systems behave or procedures) that
would make future runs faster or more reliable. Do not repeat lessons that already exist.
Return JSON: {"lessons": ["..."]}"""

# --------------------------------------------------------------------------- tool declarations

BROWSER_TOOLS = [
    decl("navigate", "Open a URL in the browser.", {"url": S("Absolute URL")}, ["url"]),
    decl("read_page", "Re-read the current page (fresh element ids + visible text)."),
]
FILE_TOOLS = [
    decl("list_files", "List files in a folder of the company drive.", {"folder": S("Folder relative to the drive root, e.g. 'inbox' or '.'")}, ["folder"]),
    decl("read_file", "Read a text file from the company drive.", {"path": S("Path relative to the drive root, e.g. 'inbox/x.txt'")}, ["path"]),
]
OPERATOR_TOOLS = BROWSER_TOOLS + [
    decl("click", "Click an element by its id from the latest observation.", {"element_id": I("Element id, e.g. 12")}, ["element_id"]),
    decl("type_text", "Replace the content of an input/textarea with text.",
         {"element_id": I("Element id"), "text": S("Text to enter")}, ["element_id", "text"]),
    decl("select_option", "Choose an option (by its visible label) in a <select>.",
         {"element_id": I("Element id"), "option": S("Visible option text")}, ["element_id", "option"]),
] + FILE_TOOLS + [
    decl("remember", "Store an important fact in working memory so it is never lost.",
         {"key": S("Short name"), "value": S("Value")}, ["key", "value"]),
    decl("update_plan", "Revise the plan when reality differs from expectations.",
         {"steps": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "New remaining steps"}, "reason": S("Why the plan changed")},
         ["steps", "reason"]),
    decl("request_approval", "Ask a human manager to approve a consequential action that policy says needs approval.",
         {"category": S("Policy category", enum=["high_value_invoice", "vendor_onboarding", "other"]),
          "summary": S("Exactly what will be done, with all values"), "amount": N("Amount in INR if relevant")},
         ["category", "summary"]),
    decl("ask_human", "Ask the requester a question when information or a decision is genuinely missing.",
         {"question": S("Clear, specific question")}, ["question"]),
    decl("finish", "End the task.",
         {"status": S("Outcome", enum=["completed", "blocked", "failed"]), "summary": S("What was done / why it stopped"),
          "evidence": S("Concrete evidence: record ids, values entered, URLs")}, ["status", "summary", "evidence"]),
]
VERIFIER_TOOLS = BROWSER_TOOLS + FILE_TOOLS + [
    decl("report_verdict", "Report the verification result.",
         {"results": {"type": "ARRAY", "description": "One entry per success criterion", "items": {
             "type": "OBJECT", "properties": {"criterion": S("Criterion"), "passed": {"type": "BOOLEAN"}, "evidence": S("What you observed")},
             "required": ["criterion", "passed", "evidence"]}},
          "overall_passed": {"type": "BOOLEAN"}, "notes": S("Anything else wrong or suspicious")},
         ["results", "overall_passed"]),
]

# --------------------------------------------------------------------------- runtime


def _read_drive_file(rel: str) -> str:
    p = (DRIVE / rel).resolve()
    if DRIVE.resolve() not in p.parents and p != DRIVE.resolve():
        raise PermissionError("Access outside the company drive is not permitted.")
    return p.read_text(encoding="utf-8")


def _list_drive(rel: str) -> list:
    p = (DRIVE / rel).resolve()
    if DRIVE.resolve() not in p.parents and p != DRIVE.resolve():
        raise PermissionError("Access outside the company drive is not permitted.")
    return sorted(str(x.relative_to(DRIVE)).replace("\\", "/") + ("/" if x.is_dir() else "") for x in p.iterdir())


class Operator:
    def __init__(self, goal: str, headless=False, auto_approve=False, max_steps=40):
        self.goal, self.auto_approve, self.max_steps = goal, auto_approve, max_steps
        self.run_dir = ROOT / "runs" / datetime.now().strftime("%Y%m%d_%H%M%S")
        (self.run_dir / "shots").mkdir(parents=True, exist_ok=True)
        self.llm = LLM()
        self.browser = Browser(self.run_dir / "shots", headless=headless)
        self.policy = Policy()
        self.handbook = _read_drive_file("handbook.md")
        self.lessons = LESSONS.read_text(encoding="utf-8") if LESSONS.exists() else "(none yet)"
        self.memory: dict = {}
        self.plan: list = []
        self.trace: list = []
        self.human_log: list = []
        self.understanding: dict = {}
        self.verdicts: list = []
        self.final: dict = {}
        self._fail_streak: dict = {}
        self.t0 = time.time()

    # ---------------------------------------------------------------- logging
    def log(self, kind, **data):
        entry = {"t": round(time.time() - self.t0, 1), "kind": kind, **data}
        self.trace.append(entry)
        return entry

    # ---------------------------------------------------------------- phase 1: understand
    def understand(self):
        console.rule("[bold cyan]UNDERSTAND & PLAN")
        u = self.llm.json(UNDERSTAND_SYSTEM.format(handbook=self.handbook), f"REQUEST: {self.goal}")
        self.understanding, self.plan = u, u.get("plan", [])
        console.print(Panel(u.get("intended_outcome", ""), title="Intended outcome", border_style="cyan"))
        console.print("[bold]Success criteria[/]\n" + "\n".join(f"  [green]âœ“[/] {c}" for c in u.get("success_criteria", [])))
        console.print("[bold]Plan[/]\n" + "\n".join(f"  {i+1}. {s}" for i, s in enumerate(self.plan)))
        if u.get("relevant_policies"):
            console.print("[bold]Policies[/]\n" + "\n".join(f"  [yellow]![/] {p}" for p in u["relevant_policies"]))
        self.log("understand", **u)

    # ---------------------------------------------------------------- tool execution
    def _observe(self, label):
        shot = self.browser.screenshot(label)
        return self.browser.snapshot(), shot

    def execute(self, name: str, args: dict, verifier=False) -> dict:
        """Run one tool call. Returns the observation dict given back to the model."""
        b = self.browser
        if name == "navigate":
            block = self.policy.check_navigate(args["url"])
            if block:
                return {"error": block}
            status = b.navigate(args["url"])
            page, shot = self._observe("navigate")
            return {"result": status, "page": page, "_shot": shot}
        if name == "read_page":
            page, shot = self._observe("read")
            return {"page": page, "_shot": shot}
        if name == "click":
            eid = int(args["element_id"])
            text, form = b.describe(eid), b.form_values(eid)
            block = self.policy.check_commit(text, form)
            if block:
                console.print(f"  [bold red]â›” {block}[/]")
                return {"error": block}
            res = b.click(eid)
            page, shot = self._observe("click")
            return {"result": f"{res} [{eid}] '{text[:40]}'", "page": page, "_shot": shot}
        if name == "type_text":
            res = b.type_text(int(args["element_id"]), args["text"])
            return {"result": res, "page": b.snapshot()}
        if name == "select_option":
            res = b.select(int(args["element_id"]), args["option"])
            return {"result": res, "page": b.snapshot()}
        if name == "list_files":
            return {"files": _list_drive(args.get("folder", "."))}
        if name == "read_file":
            return {"content": _read_drive_file(args["path"])}
        if name == "remember":
            self.memory[args["key"]] = args["value"]
            return {"result": "stored"}
        if name == "update_plan":
            self.plan = list(args["steps"])
            console.print(Panel("\n".join(f"{i+1}. {s}" for i, s in enumerate(self.plan)), title=f"Plan revised: {args['reason']}", border_style="magenta"))
            return {"result": "plan updated"}
        if name == "request_approval":
            return self._approval(args)
        if name == "ask_human":
            console.print(Panel(args["question"], title="ðŸ™‹ Agent needs your input", border_style="yellow"))
            try:
                ans = Prompt.ask("[yellow]Your answer[/]")
            except EOFError:
                ans = "(no human available right now - do not guess; finish as blocked with clear next steps for a human)"
            self.human_log.append({"type": "question", "question": args["question"], "answer": ans})
            return {"answer": ans}
        raise ValueError(f"Unknown tool {name}")

    def _approval(self, args):
        console.print(Panel(f"[bold]{args['summary']}[/]\ncategory: {args['category']}"
                            + (f"\namount: INR {args.get('amount'):,.2f}" if args.get("amount") is not None else ""),
                            title="ðŸ” APPROVAL REQUIRED", border_style="red"))
        if self.auto_approve:
            ok, who = True, "auto-approved (--auto-approve demo mode)"
            console.print(f"  [green]{who}[/]")
        else:
            try:
                ok, who = Confirm.ask("[red]Approve this action?[/]"), "human (terminal)"
            except EOFError:
                ok, who = False, "no approver available"
        try:
            reason = "" if ok else Prompt.ask("Reason for rejection", default="not approved")
        except EOFError:
            reason = "no approver available"
        self.human_log.append({"type": "approval", **args, "approved": ok, "by": who, "reason": reason})
        if ok:
            self.policy.grant(args["category"], args["summary"], args.get("amount"))
            return {"approved": True, "by": who}
        return {"approved": False, "reason": reason, "instruction": "Do not perform this action. Finish as blocked unless the human said otherwise."}

    # ---------------------------------------------------------------- generic tool-calling loop
    def _loop(self, system, first_msg, tools, stop_tool, max_steps, verifier=False, seed=None):
        contents = seed if seed is not None else [types.Content(role="user", parts=[types.Part.from_text(text=first_msg)])]
        page_parts = []  # older page observations get elided to keep context small
        for step in range(1, max_steps + 1):
            resp = self.llm.step(system, contents, tools)
            cand = resp.candidates[0] if resp.candidates else None
            if not cand or not cand.content or not cand.content.parts:
                contents.append(types.Content(role="user", parts=[types.Part.from_text(text="Continue: call a tool.")]))
                continue
            contents.append(cand.content)
            thought = " ".join(p.text for p in cand.content.parts if getattr(p, "text", None) and not getattr(p, "thought", False)).strip()
            if thought:
                console.print(f"  [dim italic]ðŸ’­ {thought[:300]}[/]")
            calls = [p.function_call for p in cand.content.parts if p.function_call]
            if not calls:
                contents.append(types.Content(role="user", parts=[types.Part.from_text(
                    text=f"Do not just describe - act with a tool call. When done, call {stop_tool}.")]))
                continue
            out_parts = []
            for fc in calls:
                args = dict(fc.args or {})
                tag = "[magenta]VERIFY[/]" if verifier else f"[cyan]#{step}[/]"
                console.print(f"{tag} [bold]{fc.name}[/] {json.dumps(args, ensure_ascii=False)[:160]}")
                if fc.name == stop_tool:
                    return args, contents
                sig = fc.name + json.dumps(args, sort_keys=True)
                try:
                    obs = self.execute(fc.name, args, verifier)
                    if "error" in obs:
                        raise BrowserError(obs["error"])
                    self._fail_streak.pop(sig, None)
                    status = "ok"
                except (BrowserError, PermissionError, FileNotFoundError, ValueError, KeyError) as e:
                    n = self._fail_streak[sig] = self._fail_streak.get(sig, 0) + 1
                    obs = {"error": str(e)}
                    if n >= 2:
                        obs["hint"] = "This exact action has now failed repeatedly. Do NOT repeat it: re-read the page and change approach, or ask_human."
                    if not str(e).startswith("POLICY_BLOCKED"):
                        try:
                            obs["page"] = self.browser.snapshot()
                        except Exception:
                            pass
                    console.print(f"  [red]âœ— {str(e)[:200]}[/]")
                    status = "error"
                shot = obs.pop("_shot", None)
                if status == "ok":
                    brief = obs.get("result") or (f"{len(obs['files'])} files" if "files" in obs else "") or ("read" if "content" in obs else "ok")
                    console.print(f"  [green]âœ“[/] {str(brief)[:150]}" + (f"  [dim]{self.browser.page.url}[/]" if "page" in obs else ""))
                self.log("verify_action" if verifier else "action", step=step, tool=fc.name, args=args, status=status,
                         thought=thought, result=obs.get("result") or obs.get("error") or obs.get("answer") or "", shot=shot)
                if not verifier:
                    obs["working_memory"] = self.memory
                obs["steps_left"] = max_steps - step
                part = types.Part.from_function_response(name=fc.name, response=obs)
                if "page" in obs:
                    page_parts.append(part)
                out_parts.append(part)
            for old in page_parts[:-2]:  # keep only the 2 latest full page observations
                r = old.function_response.response
                if "page" in r:
                    r["page"] = "(old observation elided)"
                    r.pop("working_memory", None)
            contents.append(types.Content(role="user", parts=out_parts))
        return None, contents

    # ---------------------------------------------------------------- phase 3: verify
    def verify(self, claim: dict) -> dict:
        console.rule("[bold magenta]INDEPENDENT VERIFICATION")
        criteria = "\n".join(f"- {c}" for c in self.understanding.get("success_criteria", []))
        msg = (f"ORIGINAL REQUEST: {self.goal}\n\nSUCCESS CRITERIA:\n{criteria}\n\n"
               f"THE AGENT CLAIMS: {claim.get('summary')}\nEVIDENCE IT GAVE: {claim.get('evidence')}\n\n"
               f"Facts it recorded (may be wrong): {json.dumps(self.memory)}\n"
               f"APPROVAL / HUMAN-INPUT LOG (recorded by the runtime, trustworthy): {json.dumps(self.human_log)}\n"
               "Verify against the real systems now. Placeholders in criteria should be resolved from the source documents.")
        verdict, _ = self._loop(VERIFIER_SYSTEM.format(handbook=self.handbook), msg, VERIFIER_TOOLS, "report_verdict", 25, verifier=True)
        if verdict is None:  # inconclusive verifier is the verifier's failure, not the agent's: retry once
            console.print("  [yellow]verifier inconclusive - retrying verification[/]")
            verdict, _ = self._loop(VERIFIER_SYSTEM.format(handbook=self.handbook), msg, VERIFIER_TOOLS, "report_verdict", 25, verifier=True)
        verdict = verdict or {"results": [], "overall_passed": False, "notes": "Verifier ran out of steps"}
        verdict["shot"] = self.browser.screenshot("verified")
        for r in verdict.get("results", []):
            mark = "[green]PASS[/]" if r.get("passed") else "[red]FAIL[/]"
            console.print(f"  {mark} {r.get('criterion')}\n       [dim]{r.get('evidence')}[/]")
        console.print(f"[bold]{'âœ… VERIFIED' if verdict.get('overall_passed') else 'âŒ NOT VERIFIED'}[/] {verdict.get('notes', '')}")
        self.verdicts.append(verdict)
        self.log("verdict", **{k: v for k, v in verdict.items() if k != "shot"})
        return verdict

    # ---------------------------------------------------------------- learning
    def learn(self):
        try:
            steps = [f"{e.get('tool')} {json.dumps(e.get('args'))[:120]} -> {e.get('status')}: {str(e.get('result'))[:160]}"
                     for e in self.trace if e["kind"] == "action"]
            out = self.llm.json(LESSONS_SYSTEM, f"EXISTING LESSONS:\n{self.lessons}\n\nTASK: {self.goal}\nOUTCOME: {self.final.get('status')}\n"
                                                f"TRACE:\n" + "\n".join(steps))
            new = [l for l in out.get("lessons", []) if l.strip()]
            if new:
                LESSONS.parent.mkdir(exist_ok=True)
                with LESSONS.open("a", encoding="utf-8") as f:
                    for l in new:
                        f.write(f"- {l}\n")
                console.print("[bold]ðŸ“š Lessons saved to long-term memory:[/]\n" + "\n".join(f"  - {l}" for l in new))
        except Exception as e:
            console.print(f"[dim]lesson extraction skipped: {e}[/]")

    # ---------------------------------------------------------------- main
    def run(self):
        console.print(Panel(self.goal, title="ðŸŽ¯ GOAL", border_style="bold blue"))
        self.browser.start()
        self.log("browser_started")
        try:
            self.understand()
            console.rule("[bold green]EXECUTE Â· OBSERVE Â· ADAPT")
            first = (f"REQUEST: {self.goal}\n\nYOUR UNDERSTANDING:\n{json.dumps(self.understanding, indent=1)}\n\n"
                     "Start executing now. The browser is open on a blank page.")
            system = OPERATOR_SYSTEM.format(handbook=self.handbook, lessons=self.lessons)
            contents = None
            for attempt in range(3):  # finish -> verify -> (fix) -> finish ...
                if contents is None:
                    claim, contents = self._loop(system, first, OPERATOR_TOOLS, "finish", self.max_steps)
                else:
                    claim, contents = self._continue(system, contents, feedback)
                if claim is None:
                    self.final = {"status": "failed", "summary": "Step budget exhausted", "evidence": ""}
                    break
                self.final = claim
                console.print(Panel(f"[bold]{claim.get('status').upper()}[/]: {claim.get('summary')}\n\n[dim]{claim.get('evidence')}[/]",
                                    title="Agent reports", border_style="blue"))
                if claim.get("status") != "completed":
                    break
                verdict = self.verify(claim)
                if verdict.get("overall_passed"):
                    self.final["verified"] = True
                    break
                self.final["verified"] = False
                failed = [r for r in verdict.get("results", []) if not r.get("passed")]
                feedback = ("INDEPENDENT VERIFICATION FAILED. Failing criteria:\n" + json.dumps(failed, indent=1)
                            + f"\nNotes: {verdict.get('notes', '')}\nInvestigate and fix the actual system state, then call finish again.")
                console.rule("[bold yellow]ADAPT: fixing verification failures")
        finally:
            self.final["duration_s"] = round(time.time() - self.t0, 1)
            self.learn()
            path = report.write(self)
            console.print(Panel(f"Status: [bold]{self.final.get('status')}[/]  Verified: [bold]{self.final.get('verified', False)}[/]  "
                                f"Time: {self.final['duration_s']}s\nEvidence report: {path}", title="ðŸ RUN COMPLETE", border_style="bold green"))
            self.browser.close()
        return self.final

    def _continue(self, system, contents, feedback):
        """Resume the same conversation after a failed verification (keeps full context).
        The last model turn ended with a `finish` call; answer it with the verifier's feedback."""
        contents.append(types.Content(role="user", parts=[types.Part.from_function_response(name="finish", response={"error": feedback})]))
        return self._loop(system, None, OPERATOR_TOOLS, "finish", self.max_steps, seed=contents)