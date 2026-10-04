"""Permission & approval policy, enforced in code (not just in the prompt).

The LLM is told the company policy, but we never *trust* it to follow it: every navigation and every
"committing" click is checked here first. A blocked action is returned to the agent as an observation,
so it can recover (e.g. request approval) instead of crashing.
"""
from urllib.parse import urlparse

ALLOWED_HOSTS = {"127.0.0.1", "localhost"}

# Words that make a click "consequential" (it changes state in a system of record).
COMMIT_WORDS = ("submit", "create", "save", "delete", "approve", "pay", "confirm", "send")

# Policy-as-data: if the form being committed matches `when`, an approval of `category` is required.
APPROVAL_RULES = [
    {"category": "high_value_invoice", "field": "amount", "gte": 50000,
     "why": "AP invoices >= INR 50,000 need manager approval before submission"},
    {"category": "vendor_onboarding", "field": "gstin", "present": True,
     "why": "creating a vendor always requires approval"},
]


def _num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


class Policy:
    def __init__(self):
        self.approvals = []  # granted approvals: {category, summary, amount}

    def check_navigate(self, url: str):
        host = urlparse(url).hostname
        if host not in ALLOWED_HOSTS:
            return f"POLICY_BLOCKED: '{host}' is not an approved company system. Allowed hosts: {sorted(ALLOWED_HOSTS)}"
        return None

    def check_commit(self, element_text: str, form: dict):
        """Return None if allowed, else a block reason."""
        if not any(w in element_text.lower() for w in COMMIT_WORDS) or not form:
            return None
        for rule in APPROVAL_RULES:
            val = form.get(rule["field"])
            if val is None or str(val).strip() == "":
                continue
            if "gte" in rule:
                n = _num(val)
                if n is None or n < rule["gte"]:
                    continue
                if any(a["category"] == rule["category"] and a.get("amount") is not None
                       and abs(a["amount"] - n) < 0.01 for a in self.approvals):
                    continue
            elif any(a["category"] == rule["category"] for a in self.approvals):
                continue
            return (f"POLICY_BLOCKED: {rule['why']}. No matching approval on record for this form "
                    f"({rule['field']}={val}). Call request_approval(category='{rule['category']}', ...) first.")
        return None

    def grant(self, category: str, summary: str, amount=None):
        self.approvals.append({"category": category, "summary": summary, "amount": _num(amount) if amount is not None else None})
