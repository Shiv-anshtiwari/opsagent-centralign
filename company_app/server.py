"""
Acme Corp sandbox: a mock company environment the agent operates in.

Two independent web systems, deliberately built like real (imperfect) business software:
  * SupplyHub  (/portal) - external vendor portal where suppliers publish invoices
  * Acme ERP   (/erp)    - internal system of record (login, accounts payable, vendor master)

Realistic friction is injected on purpose (toggle with CHAOS=0):
  * a cookie-consent overlay that blocks clicks on the portal until dismissed
  * the first invoice submission fails with a 503 and the data is NOT saved
  * strict server-side validation (date format, plain-number amounts, duplicates)
"""
import os
import re
from datetime import datetime
from html import escape

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

CHAOS = os.getenv("CHAOS", "1") == "1"
ERP_USER, ERP_PASS = "ops.agent", "acme-sandbox-2026"

app = FastAPI(title="Acme Corp Sandbox")

# --------------------------------------------------------------------------- data

PORTAL_INVOICES = [
    # deliberately not sorted by date; dates shown in mixed human formats
    {"no": "GX-1049", "vendor": "Globex Industries", "issued": "2026-09-03", "due": "2026-10-03", "amount": 41980.50, "status": "Open",
     "items": [("Steel rods 12mm", 120, 245.00), ("Fasteners kit", 40, 314.51)]},
    {"no": "GX-1057", "vendor": "Globex Industries", "issued": "2026-09-28", "due": "2026-10-28", "amount": 72450.00, "status": "Open",
     "items": [("Aluminium sheets 2mm", 150, 389.00), ("Copper wire 50m", 30, 470.00), ("Freight", 1, 0.00)]},
    {"no": "GX-1041", "vendor": "Globex Industries", "issued": "2026-08-02", "due": "2026-09-01", "amount": 38200.00, "status": "Paid",
     "items": [("Steel rods 8mm", 200, 191.00)]},
    {"no": "IN-0412", "vendor": "Initech Supplies", "issued": "2026-09-30", "due": "2026-10-15", "amount": 8940.00, "status": "Open",
     "items": [("A4 paper (box)", 60, 149.00)]},
    {"no": "IN-0388", "vendor": "Initech Supplies", "issued": "2026-08-30", "due": "2026-09-14", "amount": 5120.00, "status": "Paid",
     "items": [("Printer toner", 8, 640.00)]},
    {"no": "UL-7781", "vendor": "Umbrella Logistics", "issued": "2026-09-25", "due": "2026-10-25", "amount": 23100.00, "status": "Open",
     "items": [("Line haul Mumbai-Pune", 6, 3850.00)]},
    {"no": "HC-0501", "vendor": "Hooli Cloud", "issued": "2026-09-29", "due": "2026-10-29", "amount": 15000.00, "status": "Open",
     "items": [("Cloud credits", 1, 15000.00)]},
]

CATEGORIES = ["Raw Materials", "Office Supplies", "Logistics", "Software & Cloud", "Services"]
TERMS = ["Net 15", "Net 30", "Net 45", "Net 60"]


def fresh_state():
    return {
        "vendors": [
            {"name": "Globex Industries", "gstin": "27AABCG1234F1Z5", "email": "ar@globex.example", "terms": "Net 30"},
            {"name": "Initech Supplies", "gstin": "27AACCI5678K1Z9", "email": "billing@initech.example", "terms": "Net 15"},
            {"name": "Umbrella Logistics", "gstin": "29AAECU9012L1Z3", "email": "finance@umbrella.example", "terms": "Net 30"},
        ],
        "ap": [
            {"id": "AP-0001", "vendor": "Globex Industries", "invoice_number": "GX-1041", "amount": 38200.00, "due_date": "01-09-2026",
             "category": "Raw Materials", "notes": "", "status": "Paid", "created": "2026-08-03 10:12"},
            {"id": "AP-0002", "vendor": "Initech Supplies", "invoice_number": "IN-0388", "amount": 5120.00, "due_date": "14-09-2026",
             "category": "Office Supplies", "notes": "", "status": "Paid", "created": "2026-08-31 16:40"},
        ],
        "submit_attempts": 0,
    }


STATE = fresh_state()

# --------------------------------------------------------------------------- layout helpers

CSS = """
body{font-family:Segoe UI,Arial,sans-serif;margin:0;background:#f4f5f7;color:#222}
header{padding:14px 28px;color:#fff;display:flex;gap:24px;align-items:center}
header a{color:#fff;text-decoration:none;opacity:.9} header b{font-size:18px;margin-right:24px}
main{padding:24px 28px;max-width:1000px}
table{border-collapse:collapse;width:100%;background:#fff} th,td{border:1px solid #ddd;padding:8px;text-align:left;font-size:14px}
th{background:#fafafa} .card{background:#fff;padding:20px;border:1px solid #ddd;border-radius:6px;margin-bottom:16px}
label{display:block;margin-top:12px;font-weight:600;font-size:13px} input,select,textarea{padding:7px;width:320px;margin-top:4px}
button{margin-top:16px;padding:8px 18px;background:#0b5;color:#fff;border:0;border-radius:4px;cursor:pointer}
.error{background:#fde8e8;color:#9b1c1c;padding:10px;border:1px solid #f5b5b5;border-radius:4px;margin-bottom:12px}
.ok{background:#e6f6ec;color:#11633a;padding:10px;border:1px solid #9fd8b4;border-radius:4px;margin-bottom:12px}
#consent{position:fixed;inset:0;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;z-index:99}
#consent .card{width:420px}
"""


def page(title, body, brand, color, nav=""):
    return HTMLResponse(f"""<!doctype html><html><head><title>{escape(title)}</title><style>{CSS}</style></head>
<body><header style="background:{color}"><b>{brand}</b>{nav}</header><main><h2>{escape(title)}</h2>{body}</main></body></html>""")


def human_date(iso, style):
    d = datetime.strptime(iso, "%Y-%m-%d")
    return d.strftime("%d %b %Y") if style == "short" else d.strftime("%B %d, %Y").replace(" 0", " ")


def money(x):
    return f"&#8377;{x:,.2f}"


# --------------------------------------------------------------------------- SupplyHub vendor portal

PORTAL_NAV = '<a href="/portal">All invoices</a>'


@app.get("/", response_class=HTMLResponse)
def root():
    return page("Acme Corp sandbox", '<p><a href="/portal">SupplyHub vendor portal</a> | <a href="/erp">Acme ERP</a></p>', "Acme Corp", "#333")


@app.get("/portal", response_class=HTMLResponse)
def portal(request: Request, vendor: str = ""):
    rows = [i for i in PORTAL_INVOICES if not vendor or vendor.lower() in i["vendor"].lower()]
    vendors = sorted({i["vendor"] for i in PORTAL_INVOICES})
    filt = " | ".join(f'<a href="/portal?vendor={escape(v)}">{escape(v)}</a>' for v in vendors)
    trs = "".join(
        f'<tr><td><a href="/portal/invoice/{i["no"]}">{i["no"]}</a></td><td>{escape(i["vendor"])}</td>'
        f'<td>{human_date(i["issued"], "short")}</td><td>{i["status"]}</td></tr>' for i in rows)
    consent = ""
    if CHAOS and request.cookies.get("consent") != "yes":
        consent = """<div id="consent"><div class="card"><h3>We value your privacy</h3>
<p>SupplyHub uses cookies to improve your experience. You must accept to continue.</p>
<button onclick="document.cookie='consent=yes;path=/';document.getElementById('consent').remove()">Accept all cookies</button></div></div>"""
    body = f"""<p>Filter by supplier: {filt} | <a href="/portal">clear</a></p>
<table><tr><th>Invoice</th><th>Supplier</th><th>Issued</th><th>Status</th></tr>{trs}</table>{consent}"""
    return page("Supplier invoices", body, "SupplyHub", "#5b3cc4", PORTAL_NAV)


@app.get("/portal/invoice/{no}", response_class=HTMLResponse)
def portal_invoice(no: str):
    inv = next((i for i in PORTAL_INVOICES if i["no"] == no), None)
    if not inv:
        return page("Invoice not found", "<p class='error'>No such invoice.</p>", "SupplyHub", "#5b3cc4", PORTAL_NAV)
    items = "".join(f"<tr><td>{escape(d)}</td><td>{q}</td><td>{money(p)}</td></tr>" for d, q, p in inv["items"])
    body = f"""<div class="card">
<p><b>Supplier:</b> {escape(inv["vendor"])}<br><b>Invoice number:</b> {inv["no"]}<br>
<b>Invoice date:</b> {human_date(inv["issued"], "short")}<br><b>Payment due:</b> {human_date(inv["due"], "long")}<br>
<b>Status:</b> {inv["status"]}</p>
<table><tr><th>Item</th><th>Qty</th><th>Unit price</th></tr>{items}</table>
<h3>Total payable: {money(inv["amount"])}</h3></div>"""
    return page(f"Invoice {no}", body, "SupplyHub", "#5b3cc4", PORTAL_NAV)


# --------------------------------------------------------------------------- Acme ERP

ERP_NAV = '<a href="/erp">Home</a><a href="/erp/ap">Accounts Payable</a><a href="/erp/vendors">Vendors</a><a href="/erp/logout">Log out</a>'


def erp_page(title, body):
    return page(title, body, "Acme ERP", "#0a4d8c", ERP_NAV)


def authed(request: Request):
    return request.cookies.get("erp_session") == "valid"


def login_redirect():
    return RedirectResponse("/erp/login", status_code=303)


@app.get("/erp/login", response_class=HTMLResponse)
def erp_login_form(error: str = ""):
    err = f'<div class="error">{escape(error)}</div>' if error else ""
    body = f"""{err}<form method="post" action="/erp/login" class="card">
<label>Username<input name="username"></label><label>Password<input name="password" type="password"></label>
<button type="submit">Sign in</button></form>"""
    return page("Sign in to Acme ERP", body, "Acme ERP", "#0a4d8c")


@app.post("/erp/login")
def erp_login(username: str = Form(""), password: str = Form("")):
    if username.strip() == ERP_USER and password == ERP_PASS:
        r = RedirectResponse("/erp", status_code=303)
        r.set_cookie("erp_session", "valid")
        return r
    return RedirectResponse("/erp/login?error=Invalid+username+or+password", status_code=303)


@app.get("/erp/logout")
def erp_logout():
    r = RedirectResponse("/erp/login", status_code=303)
    r.delete_cookie("erp_session")
    return r


@app.get("/erp", response_class=HTMLResponse)
def erp_home(request: Request):
    if not authed(request):
        return login_redirect()
    body = f"""<div class="card">Welcome, {ERP_USER}.<ul>
<li><a href="/erp/ap">Accounts Payable</a> ({len(STATE["ap"])} invoices)</li>
<li><a href="/erp/vendors">Vendor master</a> ({len(STATE["vendors"])} vendors)</li></ul></div>"""
    return erp_page("Dashboard", body)


@app.get("/erp/ap", response_class=HTMLResponse)
def erp_ap(request: Request):
    if not authed(request):
        return login_redirect()
    trs = "".join(
        f'<tr><td><a href="/erp/ap/{a["id"]}">{a["id"]}</a></td><td>{escape(a["vendor"])}</td><td>{escape(a["invoice_number"])}</td>'
        f'<td>{money(a["amount"])}</td><td>{a["due_date"]}</td><td>{a["status"]}</td></tr>' for a in STATE["ap"])
    body = f"""<p><a href="/erp/ap/new">+ Record new invoice</a></p>
<table><tr><th>Record</th><th>Vendor</th><th>Invoice #</th><th>Amount</th><th>Due (DD-MM-YYYY)</th><th>Status</th></tr>{trs}</table>"""
    return erp_page("Accounts Payable", body)


def ap_form(values=None, error=""):
    v = values or {}
    vopts = "".join(f'<option {"selected" if v.get("vendor") == x["name"] else ""}>{escape(x["name"])}</option>' for x in STATE["vendors"])
    copts = "".join(f'<option {"selected" if v.get("category") == c else ""}>{c}</option>' for c in CATEGORIES)
    err = f'<div class="error">{escape(error)}</div>' if error else ""
    return f"""{err}<form method="post" action="/erp/ap/new" class="card">
<label>Vendor<select name="vendor"><option value="">-- select vendor --</option>{vopts}</select></label>
<label>Invoice number<input name="invoice_number" value="{escape(v.get("invoice_number", ""))}"></label>
<label>Amount (INR)<input name="amount" value="{escape(v.get("amount", ""))}"></label>
<label>Due date<input name="due_date" placeholder="DD-MM-YYYY" value="{escape(v.get("due_date", ""))}"></label>
<label>Expense category<select name="category"><option value="">-- select --</option>{copts}</select></label>
<label>Notes<textarea name="notes">{escape(v.get("notes", ""))}</textarea></label>
<button type="submit">Submit invoice</button></form>"""


@app.get("/erp/ap/new", response_class=HTMLResponse)
def erp_ap_new_form(request: Request):
    if not authed(request):
        return login_redirect()
    return erp_page("Record new invoice", ap_form())


@app.post("/erp/ap/new", response_class=HTMLResponse)
def erp_ap_new(request: Request, vendor: str = Form(""), invoice_number: str = Form(""), amount: str = Form(""),
               due_date: str = Form(""), category: str = Form(""), notes: str = Form("")):
    if not authed(request):
        return login_redirect()
    STATE["submit_attempts"] += 1
    if CHAOS and STATE["submit_attempts"] == 1:
        return HTMLResponse(status_code=503, content=page(
            "503 Service Unavailable",
            '<div class="error">The AP service timed out. Your submission was NOT saved. '
            'Please <a href="/erp/ap/new">open the form again</a> and resubmit.</div>', "Acme ERP", "#0a4d8c").body)
    vals = dict(vendor=vendor, invoice_number=invoice_number.strip(), amount=amount.strip(), due_date=due_date.strip(),
                category=category, notes=notes)
    if vendor not in [x["name"] for x in STATE["vendors"]]:
        return erp_page("Record new invoice", ap_form(vals, "Please select a vendor from the vendor master."))
    if not vals["invoice_number"]:
        return erp_page("Record new invoice", ap_form(vals, "Invoice number is required."))
    if any(a["invoice_number"].lower() == vals["invoice_number"].lower() for a in STATE["ap"]):
        return erp_page("Record new invoice", ap_form(vals, f"Duplicate: invoice {vals['invoice_number']} is already recorded."))
    if not re.fullmatch(r"\d+(\.\d{1,2})?", vals["amount"]):
        return erp_page("Record new invoice", ap_form(vals, "Amount must be a plain number, e.g. 1234.50 (no commas or currency symbols)."))
    if not re.fullmatch(r"\d{2}-\d{2}-\d{4}", vals["due_date"]):
        vals["due_date"] = ""
        return erp_page("Record new invoice", ap_form(vals, "Due date must be in DD-MM-YYYY format."))
    if category not in CATEGORIES:
        return erp_page("Record new invoice", ap_form(vals, "Please select an expense category."))
    rec_id = f"AP-{len(STATE['ap']) + 1:04d}"
    STATE["ap"].append({"id": rec_id, "vendor": vendor, "invoice_number": vals["invoice_number"], "amount": float(vals["amount"]),
                        "due_date": vals["due_date"], "category": category, "notes": notes, "status": "Pending payment",
                        "created": datetime.now().strftime("%Y-%m-%d %H:%M")})
    return RedirectResponse(f"/erp/ap/{rec_id}?created=1", status_code=303)


@app.get("/erp/ap/{rec_id}", response_class=HTMLResponse)
def erp_ap_record(request: Request, rec_id: str, created: str = ""):
    if not authed(request):
        return login_redirect()
    a = next((x for x in STATE["ap"] if x["id"] == rec_id), None)
    if not a:
        return erp_page("Not found", "<div class='error'>Record not found.</div>")
    ok = f'<div class="ok">Invoice recorded successfully as {rec_id}.</div>' if created else ""
    body = f"""{ok}<div class="card"><table>
<tr><th>Record ID</th><td>{a["id"]}</td></tr><tr><th>Vendor</th><td>{escape(a["vendor"])}</td></tr>
<tr><th>Invoice number</th><td>{escape(a["invoice_number"])}</td></tr><tr><th>Amount</th><td>{money(a["amount"])}</td></tr>
<tr><th>Due date</th><td>{a["due_date"]}</td></tr><tr><th>Category</th><td>{a["category"]}</td></tr>
<tr><th>Notes</th><td>{escape(a["notes"])}</td></tr><tr><th>Status</th><td>{a["status"]}</td></tr>
<tr><th>Created</th><td>{a["created"]}</td></tr></table></div>"""
    return erp_page(f"AP record {rec_id}", body)


@app.get("/erp/vendors", response_class=HTMLResponse)
def erp_vendors(request: Request, created: str = ""):
    if not authed(request):
        return login_redirect()
    ok = f'<div class="ok">Vendor "{escape(created)}" created.</div>' if created else ""
    trs = "".join(f"<tr><td>{escape(v['name'])}</td><td>{v['gstin']}</td><td>{escape(v['email'])}</td><td>{v['terms']}</td></tr>"
                  for v in STATE["vendors"])
    body = f"""{ok}<p><a href="/erp/vendors/new">+ Add vendor</a></p>
<table><tr><th>Name</th><th>GSTIN</th><th>AP contact email</th><th>Payment terms</th></tr>{trs}</table>"""
    return erp_page("Vendor master", body)


def vendor_form(v=None, error=""):
    v = v or {}
    topts = "".join(f'<option {"selected" if v.get("terms") == t else ""}>{t}</option>' for t in TERMS)
    err = f'<div class="error">{escape(error)}</div>' if error else ""
    return f"""{err}<form method="post" action="/erp/vendors/new" class="card">
<label>Legal name<input name="name" value="{escape(v.get("name", ""))}"></label>
<label>GSTIN<input name="gstin" value="{escape(v.get("gstin", ""))}"></label>
<label>AP contact email<input name="email" value="{escape(v.get("email", ""))}"></label>
<label>Payment terms<select name="terms"><option value="">-- select --</option>{topts}</select></label>
<button type="submit">Create vendor</button></form>"""


@app.get("/erp/vendors/new", response_class=HTMLResponse)
def erp_vendor_new_form(request: Request):
    if not authed(request):
        return login_redirect()
    return erp_page("Add vendor", vendor_form())


@app.post("/erp/vendors/new", response_class=HTMLResponse)
def erp_vendor_new(request: Request, name: str = Form(""), gstin: str = Form(""), email: str = Form(""), terms: str = Form("")):
    if not authed(request):
        return login_redirect()
    v = dict(name=name.strip(), gstin=gstin.strip().upper(), email=email.strip(), terms=terms)
    if not v["name"]:
        return erp_page("Add vendor", vendor_form(v, "Legal name is required."))
    if any(x["name"].lower() == v["name"].lower() for x in STATE["vendors"]):
        return erp_page("Add vendor", vendor_form(v, "A vendor with this name already exists."))
    if not re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]", v["gstin"]):
        return erp_page("Add vendor", vendor_form(v, "GSTIN must be a valid 15-character GST number."))
    if "@" not in v["email"]:
        return erp_page("Add vendor", vendor_form(v, "A valid AP contact email is required."))
    if terms not in TERMS:
        return erp_page("Add vendor", vendor_form(v, "Please select payment terms."))
    STATE["vendors"].append(v)
    return RedirectResponse(f"/erp/vendors?created={v['name']}", status_code=303)


@app.post("/reset")
def reset():
    STATE.clear()
    STATE.update(fresh_state())
    return {"ok": True}
