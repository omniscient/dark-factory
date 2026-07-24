"""Instance identity — single Python source. Env-overridable; defaults = MarketHawk (parity)."""
import os

OWNER = os.environ.get("FACTORY_OWNER", "omniscient")
REPO = os.environ.get("FACTORY_REPO", "markethawk")
SLUG = f"{OWNER}/{REPO}"
PROJECT_ID = os.environ.get("FACTORY_PROJECT_ID", "PVT_kwHOAAFds84BWh4w")
PROJECT_NUMBER: int = int(os.environ.get("FACTORY_PROJECT_NUMBER", "1"))
STATUS_FIELD = os.environ.get("FACTORY_STATUS_FIELD", "PVTSSF_lAHOAAFds84BWh4wzhR1VaA")
STATUS = {
    "ready": os.environ.get("FACTORY_STATUS_READY", "61e4505c"),
    "in_progress": os.environ.get("FACTORY_STATUS_IN_PROGRESS", "47fc9ee4"),
    "in_review": os.environ.get("FACTORY_STATUS_IN_REVIEW", "df73e18b"),
    "blocked": os.environ.get("FACTORY_STATUS_BLOCKED", "93d87b2f"),
    "done": os.environ.get("FACTORY_STATUS_DONE", "98236657"),
    "backlog": os.environ.get("FACTORY_STATUS_BACKLOG", "f75ad846"),
    "refined": os.environ.get("FACTORY_STATUS_REFINED", "0c79ebe5"),
}
PRODUCT_NAME = os.environ.get("FACTORY_PRODUCT_NAME", "MarketHawk")
CLONE_DIR = os.environ.get("FACTORY_CLONE_DIR", os.environ.get("CLONE_DIR", f"/workspace/{REPO}"))

_MARKERS = {
    "factory": "*Posted by {} Dark Factory*",
    "scheduler": "*Posted by {} Backlog Scheduler*",
    "refinement": "*Posted by {} Refinement Pipeline*",
    "autopilot": "*Posted by {} Epic Autopilot*",
    "main_red": "*{} Main-Red Auto-Fix*",
}


def marker(kind: str) -> str:
    return _MARKERS[kind].format(PRODUCT_NAME)


def detection_patterns() -> list[str]:
    posted = [f"Posted by {PRODUCT_NAME} {suffix}" for suffix in
              ("Refinement Pipeline", "Backlog Scheduler", "Dark Factory", "Epic Autopilot")]
    return posted + [f"Updated by {PRODUCT_NAME} Dark Factory", "dark-factory-cost-report"]
