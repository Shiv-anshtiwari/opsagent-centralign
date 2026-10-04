"""Builds the narrated demo video from REAL recorded agent runs.

Footage = Playwright screen recordings of the browser during actual runs (RECORD_VIDEO=1).
Slides/captions are rendered from each run's trace.json (real plan, criteria, verdicts).
Narration = offline Windows TTS (System.Speech). Assembly = ffmpeg.

    python demo/make_video.py <invoice_run_dir> <vendor_run_dir> <blocked_run_dir>
"""
import json
import subprocess
import sys
import wave
from html import escape
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "demo" / "build"
OUT.mkdir(parents=True, exist_ok=True)
W, H = 1280, 800

runs = [Path(p).resolve() for p in sys.argv[1:4]]
T = [json.loads((r / "trace.json").read_text(encoding="utf-8")) for r in runs]
VID = [next((r / "video").glob("*.webm")) for r in runs]
OFF = [next(e["t"] for e in t["trace"] if e["kind"] == "browser_started") for t in T]

CSS = """*{box-sizing:border-box;margin:0}body{width:1280px;height:800px;font-family:'Segoe UI',Arial,sans-serif;
background:radial-gradient(circle at 20% 0%,#1d2a4a 0%,#0b1020 55%);color:#e8ecf5;padding:64px 80px;overflow:hidden}
h1{font-size:54px;font-weight:700;letter-spacing:-1px}h2{font-size:38px;font-weight:700;margin-bottom:22px}
.k{color:#7aa2ff;font-weight:600;font-size:18px;letter-spacing:3px;text-transform:uppercase;margin-bottom:14px}
p,li{font-size:23px;line-height:1.5;color:#c9d2e6}ul{padding-left:26px}li{margin:6px 0}
.box{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);border-radius:14px;padding:22px 28px;margin-top:18px}
.req{font-size:27px;color:#fff;font-style:italic}.ok{color:#4ade80}.bad{color:#f87171}.warn{color:#fbbf24}
code{font-family:Consolas,monospace;color:#9fe0ff;font-size:20px}
.flow{display:flex;gap:10px;align-items:center;margin:26px 0;flex-wrap:wrap}
.flow div{background:#1f3a8a;border:1px solid #3b5bdb;border-radius:10px;padding:12px 16px;font-size:20px;font-weight:600}
.flow span{color:#7aa2ff;font-size:24px}table{border-collapse:collapse;width:100%}td{padding:8px 10px;font-size:19px;border-bottom:1px solid rgba(255,255,255,.1);vertical-align:top;color:#d6dcea}
"""


def slide_html(body):
    return f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{body}</body></html>"


CAP_CSS = """*{margin:0;box-sizing:border-box}body{width:1280px;height:800px;background:transparent;font-family:'Segoe UI',Arial,sans-serif}
.bar{position:absolute;left:0;right:0;bottom:0;background:rgba(10,14,30,.88);border-top:3px solid #7aa2ff;padding:16px 28px;color:#fff;font-size:24px;line-height:1.35}
.tag{position:absolute;top:14px;right:16px;background:#dc2626;color:#fff;font-size:15px;font-weight:700;padding:6px 12px;border-radius:6px;letter-spacing:1px}"""


def caption_html(text, tag):
    return f"<html><head><meta charset='utf-8'><style>{CAP_CSS}</style></head><body><div class='tag'>{tag}</div><div class='bar'>{text}</div></body></html>"


inv, ven, blk = T
crit = inv["understanding"].get("success_criteria", [])[:5]
plan = inv["understanding"].get("plan", [])[:7]
appr = next((h for h in inv["human_interactions"] if h["type"] == "approval"), {})
verdict = inv["verdicts"][-1] if inv["verdicts"] else {"results": []}
ven_verdict = ven["verdicts"][-1] if ven["verdicts"] else {"results": []}
lessons = (ROOT / "memory" / "lessons.md").read_text(encoding="utf-8").strip().splitlines()[:5]
email = (ROOT / "company_drive" / "inbox" / "2026-10-02_procurement_new_supplier.txt").read_text(encoding="utf-8")


def ttag(i):
    return f"LIVE AGENT RUN · REAL BROWSER · {T[i]['final'].get('duration_s')}s total"


# (kind, payload, narration). footage payload: (run_idx, trace_t_start, trace_t_end, caption)
SEGMENTS = [
    ("slide", f"""<div class='k'>CentrAlign AI · AI Engineering Intern submission</div><h1>OpsAgent</h1>
<p style='font-size:30px;margin-top:10px;color:#fff'>An autonomous AI operations employee</p>
<div class='box'><p>One-line request → plans → acts in a <b>real browser</b> → recovers from failures → asks for approval → <b>independently verified</b> → evidence report</p></div>
<p style='margin-top:40px;font-size:20px'>github.com/Shiv-anshtiwari/opsagent-centralign</p>""",
     "This is Ops Agent, an AI employee that turns a one-line business request into finished, verified work inside real company systems. "
     "Everything you'll see is a real recorded agent run, trimmed and sped up. Nothing is scripted."),
    ("slide", """<div class='k'>Architecture</div><h2>One generic loop, no task-specific code</h2>
<div class='flow'><div>Goal</div><span>→</span><div>Understand</div><span>→</span><div>Act · Observe · Adapt</div><span>→</span><div>Verify</div><span>→</span><div>Learn</div></div>
<ul><li><b>Understand:</b> request + company handbook → success criteria, plan, applicable policies</li>
<li><b>Act:</b> LLM picks one generic tool at a time (navigate, click, type, read_file, request_approval, ask_human…) and sees a fresh page snapshot after every action</li>
<li><b>Policy gate in code:</b> every Submit/Create click is checked against approval rules, not trusted to the prompt</li>
<li><b>Independent verifier:</b> separate context, read-only tools, checks the real systems</li>
<li><b>Learn:</b> lessons saved to long-term memory · every run writes an evidence report</li></ul>""",
     "The architecture is one loop. First, the agent turns the request and the company handbook into success criteria and a plan. "
     "Then it acts one tool at a time in a real browser, observing the page after every action and adapting when something goes wrong. "
     "Risky actions pass through a policy gate written in code, not in the prompt. When the agent claims it's done, a separate, read-only verifier checks the real systems. "
     "Finally, it saves lessons for the next run."),
    ("slide", """<div class='k'>The sandbox company · Acme Corp</div><h2>Real apps, real friction</h2>
<ul><li><b>SupplyHub</b> - supplier portal where vendors publish invoices</li>
<li><b>Acme ERP</b> - login, Accounts Payable, Vendor master (system of record)</li>
<li><b>Company drive</b> - operations handbook (procedures, approval policy) + email inbox</li></ul>
<div class='box'><p class='warn'>Injected failures</p><ul><li>Cookie popup that blocks every click</li><li>Strict validation: DD-MM-YYYY dates, plain-number amounts, duplicates</li>
<li>ERP crashes with a 503 on the first submission - and loses the data</li></ul></div>""",
     "The company is a sandbox I built: a supplier portal, an E R P with accounts payable and a vendor master, and a shared drive with an operations handbook and an email inbox. "
     "I deliberately added real-world friction: a cookie popup that blocks clicks, strict validation, and an E R P that crashes on the first submission and loses the data."),
    ("slide", f"""<div class='k'>Task 1 · invoice entry</div><div class='box'><p class='req'>"{escape(inv['goal'])}"</p></div>
<h2 style='font-size:26px;margin-top:26px'>Agent's own understanding (from the run trace)</h2>
<ul style='columns:1'>{''.join(f'<li style="font-size:19px">{escape(c)}</li>' for c in crit)}</ul>""",
     "Task one. The request is one sentence: find the latest Globex invoice and enter it into the E R P. "
     "Before acting, the agent works out what done actually means: concrete success criteria, the handbook rules that apply, and a plan."),
    ("footage", (0, 10.5, 22.0, "<b>Portal:</b> dismisses the cookie popup → filters to Globex → opens <b>GX-1057</b>, the latest by <i>invoice date</i> (not the first row)"),
     "On the supplier portal it dismisses the cookie banner, filters to Globex, and opens invoice G X ten fifty-seven. "
     "That's not the first row. The handbook says latest means the most recent invoice date, so it compares the dates."),
    ("footage", (0, 22.0, 34.0, "<b>ERP:</b> logs in with the service account → checks the vendor master and the AP list for duplicates → opens a new invoice form"),
     "It logs into the E R P, confirms Globex exists in the vendor master, and checks the accounts payable list so it never records a duplicate."),
    ("slide", f"""<div class='k'>Human-in-the-loop · policy enforced in code</div><h2>🔐 Approval required</h2>
<div class='box'><p style='color:#fff'>{escape(appr.get('summary', ''))}</p><p style='margin-top:10px'>category: <code>{escape(str(appr.get('category', '')))}</code> · amount: <code>INR {appr.get('amount', 0):,.2f}</code> · <span class='ok'>approved</span></p></div>
<ul style='margin-top:22px'><li>Handbook: invoices <b>≥ ₹50,000</b> need manager approval <b>before</b> submission</li>
<li><code>policy.py</code> inspects the form behind every Submit click and <b class='bad'>blocks</b> it unless an approval for that <b>exact amount</b> exists</li>
<li>Interactive mode: a human types y / n in the terminal</li></ul>""",
     "The amount is seventy-two thousand rupees, above the fifty thousand approval threshold, so the agent requests manager approval before submitting. "
     "This isn't just a prompt instruction. The policy gate inspects the form behind every submit click and blocks it unless an approval for that exact amount exists. "
     "In this recording approvals were automatic. Interactively, a human types yes or no."),
    ("footage", (0, 36.0, 47.8, "<b>Fills the form:</b> 'October 28, 2026' → <b>28-10-2026</b> · amount as plain number <b>72450.00</b> · category per handbook → Submit"),
     "It fills the form, converting October twenty-eighth into the E R P's day-month-year format, entering the amount as a plain number, and choosing the category the handbook prescribes. Then it submits."),
    ("footage", (0, 47.8, 63.5, "<span style='color:#fbbf24'>⚠ ERP returns <b>503 - submission NOT saved</b></span> → agent re-opens the form, re-enters every field, resubmits → <b style='color:#4ade80'>AP-0003 created</b>"),
     "The E R P crashes with a five-oh-three and says the submission was not saved. The agent doesn't assume success. "
     "It reads the error, re-opens the form, re-enters every field and submits again. This time record A P zero zero zero three is created."),
    ("footage", (0, 66.5, 78.0, "<b>Independent verifier</b> (fresh context, read-only tools): re-opens the source invoice and the ERP record and compares every value"),
     "Now the independent verifier takes over. It has a fresh context and read-only tools, so it can't fix anything to make it pass. "
     "It opens the source invoice and the E R P record, and compares every value."),
    ("slide", f"""<div class='k'>Verification result · task 1</div><h2><span class='ok'>✅ COMPLETED & VERIFIED</span> in {inv['final'].get('duration_s')}s</h2>
<table>{''.join(f"<tr><td>{'<span class=ok>PASS</span>' if r.get('passed') else '<span class=bad>FAIL</span>'}</td><td>{escape(r.get('criterion', ''))}</td></tr>" for r in verdict.get('results', [])[:6])}</table>""",
     "Every criterion passes: the right invoice, the exact amount, the correct date format, the category, and the approval on record. Task one is complete and verified."),
    ("image", "report", "Each run also produces an evidence report: the plan, every action with its reasoning and a screenshot, human approvals, and the verification result, plus a full JSON trace for debugging."),
    ("slide", f"""<div class='k'>Task 2 · same code, different workflow</div><div class='box'><p class='req'>"{escape(ven['goal'])}"</p></div>
<div class='box'><pre style='font-family:Consolas,monospace;font-size:16px;color:#c9d2e6;white-space:pre-wrap'>{escape(email.strip())}</pre></div>
<p style='margin-top:14px'>"payment within 45 days" → <b class='ok'>Net 45</b> (handbook default would be Net 30)</p>""",
     "Task two runs on exactly the same code with a vaguer request: procurement emailed about a new supplier, get them set up. "
     "The agent searches the inbox, finds the onboarding email, and extracts the legal name, G S T number and contact. "
     "The email says payment within forty-five days, so it picks Net forty-five instead of the handbook's Net thirty default."),
    ("footage", (1, 11.6, 43.6, "<b>Vendor onboarding:</b> requests approval (policy) → creates <b>Stark Components Pvt Ltd</b>, Net 45 → verifier re-checks the email and the vendor master → <b style='color:#4ade80'>VERIFIED</b>"),
     "Creating a vendor always needs approval, so it asks first, then creates the vendor. "
     "The verifier re-reads the email and checks the vendor master. Verified in about forty-four seconds."),
    ("footage", (2, 7.4, 49.0, "<b>Task 3:</b> 'Record the latest invoice from Hooli Cloud' → finds HC-0501 → Hooli is <b>not</b> in the vendor master → searches the inbox for an onboarding request → none"),
     "Task three tests judgment. Asked to record a Hooli Cloud invoice, the agent finds it, but notices Hooli isn't in the vendor master. "
     "It searches the inbox for an onboarding request and finds none. The handbook forbids creating vendors during invoice entry."),
    ("slide", f"""<div class='k'>Task 3 · knowing when to stop</div><h2><span class='warn'>⛔ BLOCKED - escalated to a human</span></h2>
<div class='box'><p style='font-size:20px'>{escape(blk['final'].get('summary', ''))[:700]}</p></div>
<p style='margin-top:18px'>Doing the wrong thing is worse than not finishing.</p>""",
     "So instead of improvising, it stops with status blocked and tells a human exactly what's needed. Stopping safely is a feature: doing the wrong thing is worse than not finishing."),
    ("slide", f"""<div class='k'>Memory · generalization</div><h2>It learns, and nothing is task-specific</h2>
<div class='box'><p class='warn' style='font-size:18px'>memory/lessons.md - written by the agent after its runs</p><ul>{''.join(f'<li style="font-size:18px">{escape(l.lstrip("- "))}</li>' for l in lessons)}</ul></div>
<ul style='margin-top:14px'><li>Generic tools · company knowledge in the handbook · policy as data</li><li>3 different tasks, identical code - only the sentence changed</li></ul>""",
     "After each run the agent extracts lessons, like accept the cookie banner first, into long-term memory, and later runs load them. "
     "Nothing task-specific is in the code. The tools are generic, company knowledge lives in the handbook, and policy lives in data."),
    ("slide", """<div class='k'>What's next</div><h2>From prototype to AI employee</h2>
<ul><li><b>Skills:</b> compile verified runs into replayable procedures - LLM only when the page changes</li>
<li><b>Connectors:</b> email, Drive, APIs + vision-based control for desktop apps</li>
<li><b>Eval harness:</b> tasks × injected faults, tracking success and false-"done" rates</li>
<li><b>Async approvals</b> via Slack/email, live trace dashboard, scoped credentials, audit log</li></ul>
<div class='box'><p>Code, setup, architecture & sample evidence reports:<br><b style='color:#fff'>github.com/Shiv-anshtiwari/opsagent-centralign</b></p></div>""",
     "Next, I'd compile verified runs into replayable skills, add connectors including desktop apps, build an evaluation harness with injected faults, and move approvals to Slack. "
     "The code, setup instructions and sample evidence reports are on GitHub. Thanks for watching."),
]


def render_pngs():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": W, "height": H})
        for i, (kind, payload, _) in enumerate(SEGMENTS):
            if kind == "slide":
                pg.set_content(slide_html(payload))
                pg.screenshot(path=str(OUT / f"s{i:02d}.png"))
            elif kind == "footage":
                pg.set_content(caption_html(payload[3], ttag(payload[0])))
                pg.screenshot(path=str(OUT / f"c{i:02d}.png"), omit_background=True)
            elif kind == "image":
                pg.goto((runs[0] / "report.html").as_uri())
                pg.screenshot(path=str(OUT / f"s{i:02d}.png"))
        b.close()


def tts():
    items = [{"i": i, "text": n} for i, (_, _, n) in enumerate(SEGMENTS)]
    (OUT / "narr.json").write_text(json.dumps(items), encoding="utf-8")
    ps = f"""Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SelectVoice('Microsoft Zira Desktop'); $s.Rate = 1
foreach ($it in (Get-Content -Raw -Encoding UTF8 '{OUT / "narr.json"}' | ConvertFrom-Json)) {{
  $s.SetOutputToWaveFile(('{OUT}\\n{{0:D2}}.wav' -f [int]$it.i)); $s.Speak($it.text) }}
$s.SetOutputToNull()"""
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)


def wav_len(p):
    with wave.open(str(p)) as w:
        return w.getnframes() / w.getframerate()


def ff(args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def build():
    parts = []
    for i, (kind, payload, _) in enumerate(SEGMENTS):
        narr = OUT / f"n{i:02d}.wav"
        D = wav_len(narr) + 0.6
        out = OUT / f"seg{i:02d}.mp4"
        aud = ["-af", "apad", "-ar", "44100", "-ac", "2", "-c:a", "aac"]
        if kind in ("slide", "image"):
            ff(["-loop", "1", "-framerate", "30", "-i", str(OUT / f"s{i:02d}.png"), "-i", str(narr), "-t", f"{D:.2f}",
                "-vf", f"scale={W}:{H},format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-r", "30", *aud, str(out)])
        else:
            run, t0, t1, _ = payload
            s, e = max(0.0, t0 - OFF[run]), t1 - OFF[run]
            k = min(D / (e - s), 1.25)  # speed footage up to fit narration (never slow-mo much)
            vf = (f"[0:v]trim={s:.2f}:{e:.2f},setpts=(PTS-STARTPTS)*{k:.4f},fps=30,scale={W}:{H},"
                  f"tpad=stop_mode=clone:stop_duration={D:.2f}[v];[v][1:v]overlay=0:0,format=yuv420p[o]")
            ff(["-i", str(VID[run]), "-i", str(OUT / f"c{i:02d}.png"), "-i", str(narr), "-filter_complex", vf,
                "-map", "[o]", "-map", "2:a", "-t", f"{D:.2f}", "-c:v", "libx264", "-preset", "veryfast", "-r", "30", *aud, str(out)])
        parts.append(out)
        print(f"segment {i:02d} {kind:7s} {D:5.1f}s")
    lst = OUT / "list.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    final = ROOT / "demo" / "OpsAgent_demo.mp4"
    ff(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(final)])
    print("DONE", final)


if __name__ == "__main__":
    render_pngs()
    tts()
    build()
