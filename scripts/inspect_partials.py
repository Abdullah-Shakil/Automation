from fastapi.testclient import TestClient

from app.main import app
from app.workers import checks as worker_checks

worker_checks.clear_check_cache()
with TestClient(app) as c:
    for tab in ["cloud", "runners", "bots"]:
        r = c.get(f"/partials/dashboard?tab={tab}")
        print("---", tab, r.status_code, "len", len(r.text))
        print(repr(r.text[:800]))
        print("has Cloud section", 'section-title">Cloud' in r.text)
        print("has Runners section", 'section-title">Runners' in r.text)
        print("has Bots section", 'section-title">Bots' in r.text)
        print("error?", "Internal error" in r.text or "tab-error" in r.text)
