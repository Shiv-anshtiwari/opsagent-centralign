"""OpsAgent CLI.

    python run.py "Record the latest Globex invoice in the ERP"
    python run.py --task invoice            # run a predefined demo task
    python run.py --reset --auto-approve "..."
"""
import argparse
import sys
import threading
import time
import urllib.request

from dotenv import load_dotenv

load_dotenv()

DEMO_TASKS = {
    "invoice": "Find the latest invoice from Globex Industries, extract the amount and due date, enter it into our ERP, and tell me once it's done.",
    "vendor": "Procurement emailed us about a new supplier we signed. Get them set up in the ERP.",
    "unknown_vendor": "Record the latest invoice from Hooli Cloud in AP.",
    "initech": "Make sure Initech's latest invoice is in AP.",
}
BASE = "http://127.0.0.1:8000"


def ensure_server():
    try:
        urllib.request.urlopen(BASE + "/", timeout=2)
        return
    except Exception:
        pass
    import uvicorn
    print("Company sandbox not running - starting it in-process on", BASE)
    srv = uvicorn.Server(uvicorn.Config("company_app.server:app", host="127.0.0.1", port=8000, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(50):
        try:
            urllib.request.urlopen(BASE + "/", timeout=1)
            return
        except Exception:
            time.sleep(0.2)
    sys.exit("Could not start the company sandbox")


def main():
    ap = argparse.ArgumentParser(description="OpsAgent - autonomous AI operations employee")
    ap.add_argument("goal", nargs="?", help="Natural-language request")
    ap.add_argument("--task", choices=DEMO_TASKS, help="Run a predefined demo task")
    ap.add_argument("--reset", action="store_true", help="Reset the sandbox company data first")
    ap.add_argument("--auto-approve", action="store_true", help="Auto-grant approvals (unattended demo mode)")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--max-steps", type=int, default=40)
    a = ap.parse_args()
    goal = DEMO_TASKS.get(a.task) if a.task else a.goal
    if not goal:
        ap.error("give a goal or --task")

    ensure_server()
    if a.reset:
        urllib.request.urlopen(urllib.request.Request(BASE + "/reset", method="POST"))

    from agent.core import Operator
    final = Operator(goal, headless=a.headless, auto_approve=a.auto_approve, max_steps=a.max_steps).run()
    sys.exit(0 if final.get("verified") or final.get("status") == "blocked" else 1)


if __name__ == "__main__":
    main()
