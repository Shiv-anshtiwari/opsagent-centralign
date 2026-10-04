"""Demo video v2: premium animated slides + REAL recorded agent footage with a live trace panel.

Footage = Playwright screen recordings of real runs (RECORD_VIDEO=1). Side-panel steps, criteria, verdicts,
approvals and summaries are all read from each run's trace.json. Narration = Kokoro neural TTS (hyperframes tts).

    python demo/make_video_v2.py <invoice_run> <vendor_run> <blocked_run>
"""
import json
import subprocess
import sys
import wave
from concurrent.futures import ThreadPoolExecutor
from html import escape as E
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "demo" / "v2"
OUT.mkdir(parents=True, exist_ok=True)
W, H, FPS, ANIM_S = 1920, 1080, 25, 2.6
VOICE = "af_heart"

runs = [Path(p).resolve() for p in sys.argv[1:4]]
T = [json.loads((r / "trace.json").read_text(encoding="utf-8")) for r in runs]
VID = [next((r / "video").glob("*.webm")) for r in runs]
OFF = [next(e["t"] for e in t["trace"] if e["kind"] == "browser_started") for t in T]
inv, ven, blk = T

# ----------------------------------------------------------------------------------------------- design
CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap');
*{box-sizing:border-box;margin:0;padding:0}
body{width:1920px;height:1080px;overflow:hidden;font-family:Inter,'Segoe UI',sans-serif;color:#e6e9f2;background:#06080f;position:relative}
.bg{position:absolute;inset:0;background:
 radial-gradient(900px 600px at 12% -10%,rgba(99,102,241,.28),transparent 60%),
 radial-gradient(800px 600px at 105% 110%,rgba(34,211,238,.16),transparent 60%),#06080f}
.grid{position:absolute;inset:0;background-image:linear-gradient(rgba(255,255,255,.035) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.035) 1px,transparent 1px);background-size:64px 64px;mask-image:radial-gradient(ellipse at 40% 30%,#000 30%,transparent 80%)}
.wrap{position:absolute;inset:0;padding:96px 120px}
.eyebrow{display:inline-flex;align-items:center;gap:12px;font-size:20px;font-weight:600;letter-spacing:.22em;text-transform:uppercase;color:#a5b4fc}
.eyebrow:before{content:'';width:34px;height:2px;background:linear-gradient(90deg,#818cf8,#22d3ee)}
h1{font-size:104px;font-weight:800;letter-spacing:-.045em;line-height:1.02;margin-top:26px}
h2{font-size:64px;font-weight:800;letter-spacing:-.035em;line-height:1.08;margin-top:22px}
.grad{background:linear-gradient(90deg,#c7d2fe,#818cf8 45%,#22d3ee);-webkit-background-clip:text;color:transparent}
.lead{font-size:30px;line-height:1.5;color:#aab3c8;margin-top:26px;max-width:1500px}
.cards{display:grid;gap:26px;margin-top:52px}
.card{background:linear-gradient(180deg,rgba(255,255,255,.065),rgba(255,255,255,.025));border:1px solid rgba(255,255,255,.1);border-radius:22px;padding:34px 36px;box-shadow:0 30px 60px -30px rgba(0,0,0,.6)}
.card h3{font-size:30px;font-weight:700;letter-spacing:-.01em;margin-bottom:12px;color:#fff}
.card p,.card li{font-size:23px;line-height:1.5;color:#aab3c8}
.card ul{padding-left:24px}.card li{margin:5px 0}
.num{font-family:'JetBrains Mono',monospace;font-size:18px;color:#818cf8;margin-bottom:14px;letter-spacing:.1em}
.ok{color:#4ade80}.bad{color:#f87171}.warn{color:#fbbf24}.cy{color:#67e8f9}.w{color:#fff}
.mono{font-family:'JetBrains Mono',monospace}
.quote{font-size:40px;line-height:1.35;font-weight:600;color:#fff;letter-spacing:-.01em}
.pill{display:inline-block;padding:8px 18px;border-radius:999px;font-size:20px;font-weight:600;border:1px solid rgba(255,255,255,.14);background:rgba(255,255,255,.05);margin:6px 10px 0 0}
pre{font-family:'JetBrains Mono',monospace;font-size:21px;line-height:1.55;color:#c9d1e6;white-space:pre-wrap}
.r{opacity:0;transform:translateY(26px);animation:in .9s cubic-bezier(.2,.8,.2,1) forwards;animation-delay:var(--d,0s)}
@keyframes in{to{opacity:1;transform:none}}
.glow{animation:gl 2.6s ease-in-out forwards}@keyframes gl{from{filter:drop-shadow(0 0 0 rgba(129,140,248,0))}to{filter:drop-shadow(0 0 28px rgba(129,140,248,.55))}}
.flow{display:flex;align-items:center;gap:18px;margin-top:56px}
.node{padding:24px 30px;border-radius:18px;font-size:28px;font-weight:700;background:linear-gradient(180deg,rgba(99,102,241,.35),rgba(99,102,241,.12));border:1px solid rgba(165,180,252,.45)}
.arrow{font-size:34px;color:#67e8f9}
table{border-collapse:collapse;width:100%}td{padding:16px 14px;font-size:24px;border-bottom:1px solid rgba(255,255,255,.08);vertical-align:top;color:#cbd2e3}
.badge{display:inline-block;padding:6px 14px;border-radius:8px;font-size:18px;font-weight:700;letter-spacing:.08em}
.b-ok{background:rgba(74,222,128,.15);color:#4ade80;border:1px solid rgba(74,222,128,.4)}
.b-bad{background:rgba(248,113,113,.15);color:#f87171;border:1px solid rgba(248,113,113,.4)}
.foot{position:absolute;left:120px;right:120px;bottom:54px;display:flex;justify-content:space-between;font-size:18px;color:#5f6a85;letter-spacing:.08em}
"""
FOOT = "<div class='foot'><span>OPSAGENT · AUTONOMOUS AI OPERATIONS EMPLOYEE</span><span>github.com/Shiv-anshtiwari/opsagent-centralign</span></div>"


def page(body, foot=True):
    return f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body><div class='bg'></div><div class='grid'></div><div class='wrap'>{body}</div>{FOOT if foot else ''}</body></html>"


def d(i, base=0.15, step=0.16):
    return f"style='--d:{base + i * step:.2f}s'"


# ----------------------------------------------------------------------------------------------- data from traces
crit = inv["understanding"].get("success_criteria", [])[:5]
plan = inv["understanding"].get("plan", [])[:6]
appr = next((h for h in inv["human_interactions"] if h["type"] == "approval"), {})
verdict = inv["verdicts"][-1] if inv["verdicts"] else {"results": []}
lessons = [l.lstrip("- ").strip() for l in (ROOT / "memory" / "lessons.md").read_text(encoding="utf-8").splitlines() if l.strip()][:4]
email = (ROOT / "company_drive" / "inbox" / "2026-10-02_procurement_new_supplier.txt").read_text(encoding="utf-8").strip()
n_actions = [len([e for e in t["trace"] if e["kind"] == "action"]) for t in T]


def step_label(e):
    a, tool = e.get("args") or {}, e["tool"]
    res = str(e.get("result") or "")
    if tool == "navigate":
        lab = "navigate  " + a.get("url", "").replace("http://127.0.0.1:8000", "")
    elif tool == "click":
        lab = "click  " + (res.split("'")[1] if "'" in res else str(a.get("element_id")))
    elif tool == "type_text":
        t = a.get("text", "")
        lab = "type  '" + ("••••••" if "sandbox" in t else t[:30]) + "'"
    elif tool == "select_option":
        lab = "select  " + a.get("option", "")
    elif tool == "remember":
        lab = "remember  " + a.get("key", "")
    elif tool == "read_file":
        lab = "read_file  " + a.get("path", "").split("/")[-1][:34]
    elif tool == "list_files":
        lab = "list_files  " + a.get("folder", "")
    elif tool == "request_approval":
        lab = f"request_approval  {a.get('category', '')}"
    elif tool == "update_plan":
        lab = "update_plan  (re-plan)"
    else:
        lab = tool
    return lab[:46]


def steps_between(run, t0, t1):
    return [e for e in T[run]["trace"] if e["kind"] in ("action", "verify_action") and t0 - 0.01 <= e["t"] <= t1 + 0.01]


# ----------------------------------------------------------------------------------------------- scenes
S = []


def slide(body, narr):
    S.append(("slide", body, narr))


def footage(run, t0, t1, eyebrow, title, caption, narr):
    S.append(("footage", (run, t0, t1, eyebrow, title, caption), narr))


slide(f"""<div class='eyebrow r' {d(0)}>CentrAlign AI · AI Engineering Intern</div>
<h1 class='r' {d(1)}>One sentence in.<br><span class='grad'>Verified work out.</span></h1>
<div class='card r' {d(3)} style='margin-top:56px;max-width:1500px'><div class='num'>THE REQUEST</div><div class='quote'>"{E(inv['goal'])}"</div></div>
<div class='r' {d(5)} style='margin-top:40px'><span class='pill'>Real browser</span><span class='pill'>Real failures</span><span class='pill'>Human approval</span><span class='pill'>Independent verification</span><span class='pill'>Evidence report</span></div>""",
      "Most AI demos stop at an answer. Ops Agent finishes the job. You give it one sentence, like this one, "
      "and it does the work inside the company's own systems, handles whatever goes wrong, and then proves the work is actually done.")

slide(f"""<div class='eyebrow r' {d(0)}>Meet OpsAgent</div>
<h2 class='r' {d(1)}>An autonomous AI operations employee</h2>
<div class='cards' style='grid-template-columns:repeat(4,1fr)'>
<div class='card r' {d(3)}><div class='num'>01 · UNDERSTAND</div><h3>Works out what “done” means</h3><p>Turns a vague request + the company handbook into checkable success criteria and a plan.</p></div>
<div class='card r' {d(4)}><div class='num'>02 · EXECUTE</div><h3>Operates real software</h3><p>Drives a real Chromium browser and reads company files - no APIs faked, no steps scripted.</p></div>
<div class='card r' {d(5)}><div class='num'>03 · ADAPT</div><h3>Recovers from failure</h3><p>Popups, validation errors, server crashes - every failure becomes an observation it reasons about.</p></div>
<div class='card r' {d(6)}><div class='num'>04 · PROVE</div><h3>Verified, with evidence</h3><p>A separate read-only verifier checks the real systems. Every run ships a report with screenshots.</p></div></div>""",
      "Ops Agent is an autonomous operations employee. It works out what done actually means, operates real software through a real browser, "
      "recovers when things break, and finishes with independent verification and an evidence report. Let's look at the company it works for.")

slide(f"""<div class='eyebrow r' {d(0)}>The sandbox company · Acme Corp</div>
<h2 class='r' {d(1)}>Real apps. <span class='grad'>Deliberately imperfect.</span></h2>
<div class='cards' style='grid-template-columns:repeat(3,1fr)'>
<div class='card r' {d(3)}><div class='num'>SUPPLYHUB</div><h3>Supplier portal</h3><p>Where vendors publish invoices. Mixed date formats, rows not sorted by date.</p></div>
<div class='card r' {d(4)}><div class='num'>ACME ERP</div><h3>System of record</h3><p>Login, Accounts Payable, Vendor master. Strict server-side validation.</p></div>
<div class='card r' {d(5)}><div class='num'>COMPANY DRIVE</div><h3>Knowledge</h3><p>Operations handbook with procedures and approval policy, plus an email inbox.</p></div></div>
<div class='card r' {d(7)} style='margin-top:26px;border-color:rgba(251,191,36,.35)'><h3 class='warn'>Injected real-world failures</h3>
<p>A cookie popup that blocks every click &nbsp;·&nbsp; DD-MM-YYYY dates and plain-number amounts enforced &nbsp;·&nbsp; duplicate detection &nbsp;·&nbsp; <b class='w'>an ERP that crashes with a 503 on the first submit - and loses the data</b></p></div>""",
      "The company is a sandbox I built, so nothing touches real systems. There's a supplier portal, an E R P with accounts payable and a vendor master, "
      "and a shared drive with an operations handbook and an inbox. And I made it deliberately imperfect: a cookie popup that blocks every click, "
      "strict validation, and an E R P that crashes on the first submission and quietly loses your data.")

slide(f"""<div class='eyebrow r' {d(0)}>Architecture</div>
<h2 class='r' {d(1)}>One generic loop. <span class='grad'>Zero task-specific code.</span></h2>
<div class='flow r' {d(3)}><div class='node'>Goal</div><span class='arrow'>→</span><div class='node'>Understand</div><span class='arrow'>→</span><div class='node glow'>Act · Observe · Adapt</div><span class='arrow'>→</span><div class='node'>Verify</div><span class='arrow'>→</span><div class='node'>Learn</div></div>
<div class='cards' style='grid-template-columns:repeat(3,1fr);margin-top:46px'>
<div class='card r' {d(5)}><div class='num'>TOOLS</div><h3>12 generic primitives</h3><p class='mono' style='font-size:19px'>navigate · read_page · click · type_text · select_option · list_files · read_file · remember · update_plan · request_approval · ask_human · finish</p></div>
<div class='card r' {d(6)}><div class='num'>OBSERVATION</div><h3>Page structure, not pixels</h3><p>After every action the model gets an indexed snapshot of interactive elements + visible text. Screenshots are kept as evidence.</p></div>
<div class='card r' {d(7)}><div class='num'>MODEL</div><h3>Gemini function calling</h3><p>Planner, operator and verifier run in separate contexts. No agent framework - ~600 lines of Python.</p></div></div>""",
      "Under the hood it's one generic loop: goal, understand, act, observe, adapt, verify, learn. "
      "The model picks one of twelve generic tools at a time, and after every action it sees a fresh, structured snapshot of the page rather than raw pixels. "
      "That makes it faster, cheaper and more predictable, while screenshots are still saved as evidence. There's no workflow hard-coded anywhere.")

slide(f"""<div class='eyebrow r' {d(0)}>Task 1 · Understand & plan</div>
<div class='card r' {d(1)} style='margin-top:30px'><div class='quote' style='font-size:34px'>"{E(inv['goal'])}"</div></div>
<div class='cards' style='grid-template-columns:1.25fr 1fr;margin-top:30px'>
<div class='card r' {d(3)}><div class='num'>SUCCESS CRITERIA · WRITTEN BY THE AGENT</div><ul>{''.join(f'<li>{E(c)}</li>' for c in crit[:4])}</ul></div>
<div class='card r' {d(4)}><div class='num'>ITS PLAN</div><ul>{''.join(f'<li>{E(p)}</li>' for p in plan[:5])}</ul></div></div>""",
      "Task one. Before touching anything, the agent writes down what success looks like: concrete, checkable statements about the final state of the systems, "
      "plus a plan and the handbook rules that apply. Those criteria are exactly what gets verified at the end.")

footage(0, 10.5, 22.0, "TASK 1 · LIVE RUN", "Find the right invoice",
        "Dismisses the cookie popup → filters to Globex → opens <b>GX-1057</b>: the latest by <b>invoice date</b>, not the first row",
        "Now the live run. On the supplier portal it clears the cookie popup, filters to Globex, and opens invoice G X ten fifty-seven. "
        "Notice it isn't the first row. The handbook says latest means the most recent invoice date, so it compares dates.")
footage(0, 22.0, 34.0, "TASK 1 · LIVE RUN", "Check before writing",
        "Logs into the ERP → confirms the vendor exists → checks Accounts Payable for duplicates → opens a new invoice",
        "Then it signs into the E R P, confirms Globex exists in the vendor master, and checks accounts payable so it never pays the same invoice twice.")

policy_snip = """APPROVAL_RULES = [
  {"category": "high_value_invoice", "field": "amount", "gte": 50000},
  {"category": "vendor_onboarding",  "field": "gstin",  "present": True},
]
# before EVERY Submit / Create click:
block = policy.check_commit(button_text, form_values)
# → blocked unless an approval for this exact amount exists"""
slide(f"""<div class='eyebrow r' {d(0)}>Human-in-the-loop · enforced in code</div>
<h2 class='r' {d(1)}>₹72,450 ≥ ₹50,000 → <span class='warn'>approval required</span></h2>
<div class='cards' style='grid-template-columns:1fr 1.15fr;margin-top:36px'>
<div class='card r' {d(3)}><div class='num'>THE AGENT ASKED</div><p class='w' style='font-size:25px'>{E(appr.get('summary', ''))}</p>
<p style='margin-top:18px'><span class='badge b-ok'>APPROVED</span> &nbsp;<span class='mono' style='font-size:19px'>category={E(str(appr.get('category', '')))}</span></p></div>
<div class='card r' {d(4)}><div class='num'>agent/policy.py</div><pre>{E(policy_snip)}</pre></div></div>""",
      "The amount is seventy-two thousand rupees, over the fifty thousand threshold, so the agent asks for manager approval before it submits. "
      "And this isn't just a line in the prompt. A policy gate in code inspects the form behind every submit click, and blocks it unless an approval exists for that exact amount. "
      "In interactive mode a human approves in the terminal.")

footage(0, 36.0, 47.8, "TASK 1 · LIVE RUN", "Fill the form - the company's way",
        "“October 28, 2026” → <b>28-10-2026</b> · amount as plain <b>72450.00</b> · category from the handbook → Submit",
        "It fills the form the way the company wants it: October twenty-eighth becomes the E R P's day-month-year format, "
        "the amount goes in as a plain number, and the category comes straight from the handbook. Then, submit.")
footage(0, 47.8, 63.5, "TASK 1 · FAILURE RECOVERY", "The ERP crashes. The agent doesn't panic.",
        "<span class='warn'>503: submission NOT saved</span> → re-opens the form → re-enters every field → resubmits → <span class='ok'><b>AP-0003 created</b></span>",
        "And the E R P crashes with a five-oh-three. Submission not saved. A naive agent would report success here. "
        "This one reads the error, re-opens the form, re-enters every field, and submits again. Record A P zero zero zero three is created.")
footage(0, 66.5, 78.0, "TASK 1 · INDEPENDENT VERIFICATION", "Trust, but verify",
        "Fresh context · <b>read-only</b> tools · re-opens the source invoice and the ERP record · compares every value",
        "Then a separate verifier takes over. It gets a fresh context and read-only tools, so it physically cannot fix things to make them pass. "
        "It opens the source invoice and the E R P record, and compares every value.")

slide(f"""<div class='eyebrow r' {d(0)}>Verification result · Task 1</div>
<h2 class='r' {d(1)}><span class='ok'>Completed & verified</span> in {inv['final'].get('duration_s')}s</h2>
<div class='card r' {d(3)} style='margin-top:36px'><table>{''.join(f"<tr><td style='width:120px'><span class='badge {'b-ok' if r.get('passed') else 'b-bad'}'>{'PASS' if r.get('passed') else 'FAIL'}</span></td><td>{E(r.get('criterion', ''))}</td></tr>" for r in verdict.get('results', [])[:5])}</table></div>""",
      "Every criterion passes: the right invoice, the exact amount, the correct date format, the category, and the approval on record. "
      "Seventy-eight seconds, with no human steering.")

S.append(("report", None,
          "And every run leaves a paper trail: an evidence report with the plan, every action and the reasoning behind it, a screenshot per step, "
          "the human approvals, and the verifier's verdict, plus a full JSON trace for debugging."))

slide(f"""<div class='eyebrow r' {d(0)}>Task 2 · same code, different workflow</div>
<div class='card r' {d(1)} style='margin-top:30px'><div class='quote' style='font-size:34px'>"{E(ven['goal'])}"</div></div>
<div class='cards' style='grid-template-columns:1.2fr 1fr;margin-top:30px'>
<div class='card r' {d(3)}><div class='num'>FOUND IN THE INBOX</div><pre style='font-size:18px'>{E(email)}</pre></div>
<div class='card r' {d(4)}><div class='num'>ITS INFERENCE</div><p style='font-size:26px'>“payment within 45 days”</p><p style='font-size:40px;font-weight:800;margin:14px 0' class='ok'>→ Net 45</p>
<p>The handbook default is Net 30 - but the contract wins. Vendor creation needs approval, so it asks first.</p></div></div>""",
      "Task two runs on exactly the same code, with a vaguer request: procurement emailed about a new supplier, get them set up. "
      "Nobody says which email or which fields. The agent searches the inbox, finds the onboarding request, and pulls out the name, G S T number and contact. "
      "The contract says payment within forty-five days, so it picks Net forty-five over the handbook's Net thirty default.")
footage(1, 11.6, 43.6, "TASK 2 · LIVE RUN", "Vendor onboarding",
        "Requests approval → creates <b>Stark Components Pvt Ltd</b> (Net 45) → verifier re-reads the email + vendor master → <span class='ok'><b>VERIFIED</b></span>",
        "It requests approval, creates the vendor, and the verifier independently re-reads the email and checks the vendor master. Verified in forty-four seconds.")
footage(2, 7.4, 49.0, "TASK 3 · JUDGEMENT", "When the right move is to stop",
        "“Record the latest invoice from Hooli Cloud” → finds HC-0501 → Hooli is <b>not</b> in the vendor master → no onboarding request in the inbox",
        "Task three tests judgment. Asked to record a Hooli Cloud invoice, the agent finds it, but Hooli isn't in the vendor master. "
        "It searches the inbox for an onboarding request and finds nothing, and the handbook forbids creating vendors during invoice entry.")

slide(f"""<div class='eyebrow r' {d(0)}>Task 3 · result</div>
<h2 class='r' {d(1)}><span class='warn'>Blocked</span> - and that's the right answer</h2>
<div class='card r' {d(3)} style='margin-top:36px'><div class='num'>THE AGENT'S HAND-OFF TO A HUMAN</div><p style='font-size:25px;color:#dfe5f2'>{E(blk['final'].get('summary', ''))[:620]}</p></div>
<p class='lead r' {d(5)}>Doing the wrong thing confidently is worse than not finishing.</p>""",
      "So it stops, marks the task blocked, and tells a human exactly what's needed to unblock it. "
      "For an AI employee, knowing when not to act matters as much as acting.")

slide(f"""<div class='eyebrow r' {d(0)}>Memory · generalization</div>
<h2 class='r' {d(1)}>It gets better - <span class='grad'>without new code</span></h2>
<div class='cards' style='grid-template-columns:1.2fr 1fr;margin-top:36px'>
<div class='card r' {d(3)}><div class='num'>memory/lessons.md · WRITTEN BY THE AGENT</div><ul>{''.join(f'<li style="font-size:21px">{E(l)}</li>' for l in lessons)}</ul></div>
<div class='card r' {d(4)}><div class='num'>WHERE KNOWLEDGE LIVES</div><ul><li><b class='w'>Tools</b> - generic, reusable</li><li><b class='w'>Procedures</b> - the handbook</li><li><b class='w'>Policy</b> - data in policy.py</li><li><b class='w'>Experience</b> - lessons memory</li></ul>
<p style='margin-top:16px' class='cy'>3 tasks · identical code · only the sentence changed</p></div></div>""",
      "After each run, a reflection step writes reusable lessons into long-term memory, like accept the cookie banner first, and later runs load them. "
      "And nothing in the code is task-specific. Tools are generic, procedures live in the handbook, policy lives in data, and experience lives in memory. "
      "Three different tasks, one codebase.")

slide(f"""<div class='eyebrow r' {d(0)}>Engineering decisions</div>
<h2 class='r' {d(1)}>Built to be trusted</h2>
<div class='cards' style='grid-template-columns:repeat(2,1fr);margin-top:36px'>
<div class='card r' {d(3)}><div class='num'>FAILURES ARE OBSERVATIONS</div><p>Blocked clicks, validation errors, 5xx pages and stale elements flow back to the model with fresh page state. A loop guard forces a new approach after repeats.</p></div>
<div class='card r' {d(4)}><div class='num'>POLICY IN CODE</div><p>Approvals are bound to values, navigation is limited to approved hosts. The model can't talk its way past the gate.</p></div>
<div class='card r' {d(5)}><div class='num'>VERIFIER CAN'T CHEAT</div><p>Separate context, read-only tools, sees the claim, not the reasoning. Failed criteria go back to the operator to repair.</p></div>
<div class='card r' {d(6)}><div class='num'>OBSERVABLE BY DEFAULT</div><p>trace.json, per-step screenshots, browser video and an HTML report for every run - easy to debug and audit.</p></div></div>""",
      "A few decisions make it trustworthy. Failures are observations, not crashes. Policy lives in code, bound to exact values. "
      "The verifier can't cheat, because it can only read. And everything is observable: traces, screenshots, video and a report for every run.")

slide(f"""<div class='eyebrow r' {d(0)}>Honest limits · what's next</div>
<h2 class='r' {d(1)}>From prototype to AI employee</h2>
<div class='cards' style='grid-template-columns:1fr 1.25fr;margin-top:36px'>
<div class='card r' {d(3)}><div class='num'>LIMITATIONS TODAY</div><ul><li>Web apps only - no desktop / canvas UIs yet</li><li>Verifier shares the operator's model family</li><li>45-80s per task: every step is an LLM call</li><li>Policy commit-detection is a heuristic</li></ul></div>
<div class='card r' {d(4)}><div class='num'>NEXT TWO WEEKS</div><ul><li><b class='w'>Skills:</b> compile verified runs into replayable procedures</li><li><b class='w'>Connectors:</b> email, Drive, APIs + vision for desktop apps</li><li><b class='w'>Eval harness:</b> tasks × injected faults, false-“done” rate</li><li><b class='w'>Async approvals</b> via Slack, scoped credentials, audit log</li></ul></div></div>""",
      "It has honest limits. It only handles web apps today, the verifier shares a model family with the operator, and each step is an L L M call. "
      "Next, I'd compile verified runs into replayable skills, add connectors including vision for desktop apps, build an evaluation harness with injected faults, and move approvals to Slack.")

slide(f"""<div class='eyebrow r' {d(0)}>OpsAgent</div>
<h1 class='r' {d(1)}>Don't just answer.<br><span class='grad'>Get it done.</span></h1>
<div class='r' {d(3)} style='margin-top:50px'><span class='pill'>Gemini 3.8 Flash</span><span class='pill'>Playwright</span><span class='pill'>FastAPI sandbox</span><span class='pill'>Python · no agent framework</span><span class='pill'>Built with Claude Code</span></div>
<p class='lead r' {d(5)} style='margin-top:46px'>Code, setup, architecture and sample evidence reports<br><b class='w mono' style='font-size:32px'>github.com/Shiv-anshtiwari/opsagent-centralign</b></p>""",
      "Ops Agent. Don't just answer, get it done. The code, setup instructions and sample evidence reports are on GitHub. Thanks for watching.", )


# ----------------------------------------------------------------------------------------------- footage frame
def footage_page(run, eyebrow, title, caption, steps, active):
    rows = []
    lo = max(0, active - 9)
    for j, e in enumerate(steps[lo:lo + 11], start=lo):
        ver = e["kind"] == "verify_action"
        st = "cur" if j == active else ("done" if j < active else "todo")
        err = e.get("status") == "error"
        rows.append(f"<div class='st {st}{' err' if err else ''}'><span class='ix'>{'V' if ver else '#' + str(e.get('step'))}</span>"
                    f"<span class='lb'>{E(step_label(e))}</span></div>")
    css = CSS + """
.win{position:absolute;left:64px;top:178px;width:1232px;height:812px;border-radius:16px;overflow:hidden;border:1px solid rgba(255,255,255,.14);background:#0d1220;box-shadow:0 40px 80px -30px rgba(0,0,0,.8),0 0 0 6px rgba(99,102,241,.08)}
.chrome{height:44px;background:#151b2c;display:flex;align-items:center;gap:9px;padding:0 16px;border-bottom:1px solid rgba(255,255,255,.08)}
.dot{width:12px;height:12px;border-radius:50%}.url{margin-left:18px;font-family:'JetBrains Mono',monospace;font-size:15px;color:#7d88a6}
.rec{position:absolute;right:16px;top:11px;font-size:13px;font-weight:800;letter-spacing:.12em;color:#fecaca;background:#b91c1c;padding:4px 10px;border-radius:6px}
.side{position:absolute;left:1328px;top:178px;width:528px;height:812px;border-radius:16px;border:1px solid rgba(255,255,255,.1);background:rgba(13,18,32,.85);padding:24px 22px}
.side .num{margin-bottom:16px}
.st{display:flex;gap:12px;align-items:center;padding:11px 12px;border-radius:10px;margin-bottom:6px;font-family:'JetBrains Mono',monospace;font-size:17px;color:#5f6a85}
.st .ix{min-width:44px;color:#4b5571}.st.done{color:#8e98b3}.st.done .ix{color:#4ade80}
.st.cur{background:linear-gradient(90deg,rgba(99,102,241,.35),rgba(99,102,241,.08));color:#fff;border:1px solid rgba(165,180,252,.5)}.st.cur .ix{color:#a5b4fc}
.st.err .lb{color:#f87171}
.hdr{position:absolute;left:64px;top:54px;right:64px}.hdr h2{font-size:52px;margin-top:10px}
.cap{position:absolute;left:64px;right:64px;bottom:22px;font-size:25px;color:#dfe5f2;line-height:1.4}
.cap b{color:#fff}"""
    return f"""<html><head><meta charset='utf-8'><style>{css}</style></head><body><div class='bg'></div><div class='grid'></div>
<div class='hdr'><div class='eyebrow'>{eyebrow}</div><h2>{title}</h2></div>
<div class='win'><div class='chrome'><span class='dot' style='background:#f87171'></span><span class='dot' style='background:#fbbf24'></span><span class='dot' style='background:#4ade80'></span>
<span class='url'>Chromium · driven by OpsAgent via Playwright</span><span class='rec'>● REAL RUN · {T[run]['final'].get('duration_s')}s</span></div></div>
<div class='side'><div class='num'>AGENT TRACE · LIVE</div>{''.join(rows)}</div>
<div class='cap'>{caption}</div></body></html>"""


# ----------------------------------------------------------------------------------------------- pipeline
def tts_one(i):
    out = OUT / f"n{i:02d}.wav"
    if out.exists():
        return
    txt = OUT / f"n{i:02d}.txt"
    txt.write_text(S[i][2], encoding="utf-8")
    subprocess.run(f'npx hyperframes tts "{txt}" --voice {VOICE} --output "{out}"', shell=True, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wav_len(p):
    with wave.open(str(p)) as w:
        return w.getnframes() / w.getframerate()


def ff(args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def render_visuals(durs):
    plans = {}
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": W, "height": H})
        for i, (kind, payload, _) in enumerate(S):
            fdir = OUT / f"f{i:02d}"
            fdir.mkdir(exist_ok=True)
            if kind == "slide":
                pg.set_content(page(payload), wait_until="networkidle")
                pg.evaluate("document.fonts.ready")
                n = int(ANIM_S * FPS)
                for f in range(n + 1):
                    pg.evaluate(f"document.getAnimations().forEach(a=>{{a.pause();a.currentTime={f * 1000 / FPS}}})")
                    pg.screenshot(path=str(fdir / f"{f:04d}.jpg"), type="jpeg", quality=93)
            elif kind == "report":
                pg.goto((runs[0] / "report.html").as_uri())
                pg.set_viewport_size({"width": 1400, "height": 900})
                pg.screenshot(path=str(fdir / "report.png"), full_page=True)
                pg.set_viewport_size({"width": W, "height": H})
            else:
                run, t0, t1, eb, title, cap = payload
                steps = steps_between(run, t0, t1)
                s, e = max(0.0, t0 - OFF[run]), t1 - OFF[run]
                k = min(durs[i] / (e - s), 1.25)
                states = []
                for j in range(max(1, len(steps))):
                    pg.set_content(footage_page(run, eb, title, cap, steps, j), wait_until="networkidle")
                    pg.evaluate("document.fonts.ready")
                    pg.screenshot(path=str(fdir / f"p{j:02d}.png"))
                    st = 0 if j == 0 else max(0.0, (steps[j]["t"] - 1.0 - t0) * k)
                    states.append(st)
                plans[i] = (s, e, k, states)
        b.close()
    return plans


def build_scene(i, durs, plans):
    kind = S[i][0]
    D = durs[i]
    narr = OUT / f"n{i:02d}.wav"
    out = OUT / f"seg{i:02d}.mp4"
    fdir = OUT / f"f{i:02d}"
    aud = ["-af", f"adelay=350|350,apad", "-ar", "44100", "-ac", "2", "-c:a", "aac", "-b:a", "160k"]
    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS)]
    if kind == "slide":
        ff(["-framerate", str(FPS), "-i", str(fdir / "%04d.jpg"), "-i", str(narr), "-filter_complex",
            f"[0:v]tpad=stop_mode=clone:stop_duration={D:.2f},fade=in:st=0:d=0.35[v]", "-map", "[v]", "-map", "1:a",
            "-t", f"{D:.2f}", *enc, *aud, str(out)])
    elif kind == "report":
        # slow scroll down the real report inside the frame
        ff(["-loop", "1", "-framerate", str(FPS), "-i", str(fdir / "report.png"), "-i", str(narr), "-filter_complex",
            f"color=c=0x06080f:s={W}x{H}:r={FPS}[bg];[0:v]scale=1400:-1,crop=1400:900:0:'min(ih-900,t*40)'[r];"
            f"[bg][r]overlay=260:110,fade=in:st=0:d=0.35[v]", "-map", "[v]", "-map", "1:a", "-t", f"{D:.2f}", *enc, *aud, str(out)])
    else:
        s, e, k, states = plans[i]
        lst = fdir / "panel.txt"
        lines = []
        for j, st in enumerate(states):
            nxt = states[j + 1] if j + 1 < len(states) else D
            lines.append(f"file '{(fdir / f'p{j:02d}.png').as_posix()}'\nduration {max(0.04, nxt - st):.3f}\n")
        lines.append(f"file '{(fdir / f'p{len(states) - 1:02d}.png').as_posix()}'\n")
        lst.write_text("".join(lines), encoding="utf-8")
        fc = (f"[1:v]trim={s:.2f}:{e:.2f},setpts=(PTS-STARTPTS)*{k:.4f},fps={FPS},scale=1232:770,"
              f"tpad=stop_mode=clone:stop_duration={D:.2f}[ft];"
              f"[0:v]fps={FPS},scale={W}:{H},tpad=stop_mode=clone:stop_duration={D:.2f}[pn];"
              f"[pn][ft]overlay=64:222:shortest=0,fade=in:st=0:d=0.35[v]")
        ff(["-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(VID[run_of(i)]), "-i", str(narr), "-filter_complex", fc,
            "-map", "[v]", "-map", "2:a", "-t", f"{D:.2f}", *enc, *aud, str(out)])
    return out


def run_of(i):
    return S[i][1][0]


if __name__ == "__main__":
    print(f"{len(S)} scenes - generating narration (Kokoro {VOICE})")
    with ThreadPoolExecutor(4) as ex:
        list(ex.map(tts_one, range(len(S))))
    durs = [wav_len(OUT / f"n{i:02d}.wav") + 0.9 for i in range(len(S))]
    print(f"narration total {sum(durs):.0f}s - rendering visuals")
    plans = render_visuals(durs)
    with ThreadPoolExecutor(4) as ex:
        parts = list(ex.map(lambda i: build_scene(i, durs, plans), range(len(S))))
    lst = OUT / "list.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    final = ROOT / "demo" / "OpsAgent_demo_v2.mp4"
    ff(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(final)])
    print("DONE", final, f"{sum(durs):.0f}s")
