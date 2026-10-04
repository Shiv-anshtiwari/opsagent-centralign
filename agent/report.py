"""Writes the run's evidence: trace.json + a self-contained HTML report with screenshots."""
import json
from html import escape


def write(op) -> str:
    d = op.run_dir
    data = {"goal": op.goal, "final": op.final, "understanding": op.understanding, "memory": op.memory,
            "human_interactions": op.human_log, "verdicts": op.verdicts, "trace": op.trace}
    (d / "trace.json").write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    f = op.final
    ok = f.get("status") == "completed" and f.get("verified")
    badge = ("#11633a", "COMPLETED &amp; VERIFIED") if ok else ("#9b1c1c", escape(str(f.get("status", "?")).upper()) + ("" if f.get("verified") is None else " (not verified)"))
    li = lambda xs: "".join(f"<li>{escape(str(x))}</li>" for x in xs)

    rows = []
    for e in op.trace:
        if e["kind"] not in ("action", "verify_action"):
            continue
        shot = f'<a href="shots/{e["shot"]}"><img src="shots/{e["shot"]}"></a>' if e.get("shot") else ""
        cls = "err" if e["status"] == "error" else ("ver" if e["kind"] == "verify_action" else "")
        rows.append(f"<tr class='{cls}'><td>{e['t']}s</td><td>{'VERIFIER' if cls == 'ver' else e['step']}</td>"
                    f"<td><b>{escape(e['tool'])}</b><br><code>{escape(json.dumps(e['args'], ensure_ascii=False))[:300]}</code>"
                    f"{'<br><i>' + escape(e['thought'][:300]) + '</i>' if e.get('thought') else ''}</td>"
                    f"<td>{escape(str(e['result']))[:400]}</td><td>{shot}</td></tr>")

    ver = ""
    for i, v in enumerate(op.verdicts, 1):
        vr = "".join(f"<tr><td>{'✅' if r.get('passed') else '❌'}</td><td>{escape(r.get('criterion', ''))}</td><td>{escape(r.get('evidence', ''))}</td></tr>"
                     for r in v.get("results", []))
        img = f'<img class="big" src="shots/{v["shot"]}">' if v.get("shot") else ""
        ver += f"<h3>Verification round {i}: {'PASSED' if v.get('overall_passed') else 'FAILED'}</h3><table>{vr}</table><p>{escape(v.get('notes', '') or '')}</p>{img}"

    humans = "".join(f"<li><b>{h['type']}</b>: {escape(json.dumps(h, ensure_ascii=False))}</li>" for h in op.human_log) or "<li>none</li>"
    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>OpsAgent run report</title><style>
body{{font-family:Segoe UI,Arial,sans-serif;max-width:1150px;margin:24px auto;padding:0 16px;color:#222}}
.badge{{display:inline-block;padding:6px 14px;border-radius:4px;color:#fff;background:{badge[0]};font-weight:700}}
table{{border-collapse:collapse;width:100%;margin:8px 0}} td,th{{border:1px solid #ddd;padding:6px;vertical-align:top;font-size:13px}}
img{{width:220px;border:1px solid #ccc}} img.big{{width:640px}} tr.err{{background:#fdecec}} tr.ver{{background:#f3effc}}
code{{font-size:12px;color:#444}} .grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
</style></head><body>
<h1>OpsAgent run report</h1><p><span class="badge">{badge[1]}</span> &nbsp; {f.get('duration_s', '?')}s · {len([e for e in op.trace if e['kind']=='action'])} actions</p>
<h2>Goal</h2><p>{escape(op.goal)}</p>
<h2>Outcome</h2><p>{escape(str(f.get('summary', '')))}</p><p><b>Evidence:</b> {escape(str(f.get('evidence', '')))}</p>
<div class="grid"><div><h2>Understanding</h2><p>{escape(op.understanding.get('intended_outcome', ''))}</p>
<h3>Success criteria</h3><ul>{li(op.understanding.get('success_criteria', []))}</ul>
<h3>Initial plan</h3><ol>{li(op.understanding.get('plan', []))}</ol></div>
<div><h2>Working memory</h2><ul>{li(f'{k}: {v}' for k, v in op.memory.items())}</ul>
<h2>Human-in-the-loop</h2><ul>{humans}</ul></div></div>
<h2>Verification</h2>{ver or '<p>Not run.</p>'}
<h2>Step-by-step trace</h2><table><tr><th>t</th><th>step</th><th>action / reasoning</th><th>observation</th><th>screenshot</th></tr>{''.join(rows)}</table>
</body></html>"""
    out = d / "report.html"
    out.write_text(html, encoding="utf-8")
    return str(out)
