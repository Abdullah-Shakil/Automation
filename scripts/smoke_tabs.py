"""Smoke: each dashboard tab must render distinct content; runners must stay stable under poll."""
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi.testclient import TestClient

from app.main import app
from app.workers import checks as worker_checks


def markers(html: str) -> dict:
    return {
        "h2_cloud": "<h2>Cloud</h2>" in html,
        "h2_runners": "<h2>Runners</h2>" in html,
        "h2_bots": "<h2>Bots</h2>" in html,
        "sec_cloud": 'section-title">Cloud' in html,
        "sec_runners": 'section-title">Runners' in html,
        "sec_bots": 'section-title">Bots' in html,
        "start_stop": "Start / stop" in html,
        "find_col": ">Find</th>" in html,
        "github": "GitHub Actions" in html,
        "serper_start": "/workers/serper/start" in html or "/workers/serper/stop" in html,
        "error": (
            'class="panel tab-error"' in html
            and "Could not refresh the" in html
        )
        or "StaleDataError" in html
        or "expected to update" in html,
    }


def main() -> None:
    worker_checks.clear_check_cache()
    with TestClient(app) as client:
        for tab in ("cloud", "runners", "bots"):
            page = client.get(f"/?tab={tab}")
            partial = client.get(f"/partials/dashboard?tab={tab}")
            assert page.status_code == 200, tab
            assert partial.status_code == 200, tab
            pm = markers(page.text)
            qm = markers(partial.text)
            print(f"TAB {tab}")
            print("  page   ", {k: v for k, v in pm.items() if v})
            print("  partial", {k: v for k, v in qm.items() if v})
            assert not pm["error"] and not qm["error"], (tab, pm, qm)
            if tab == "cloud":
                assert pm["h2_cloud"] and pm["sec_cloud"] and pm["find_col"] and pm["github"]
                assert not pm["sec_runners"] and not pm["sec_bots"] and not pm["start_stop"]
                assert qm["sec_cloud"] and not qm["sec_runners"] and not qm["sec_bots"]
            elif tab == "runners":
                assert pm["h2_runners"] and pm["sec_runners"] and pm["start_stop"]
                assert not pm["sec_cloud"] and not pm["sec_bots"] and not pm["find_col"]
                assert qm["sec_runners"] and not qm["sec_cloud"]
            else:
                assert pm["h2_bots"] and pm["sec_bots"]
                assert "OpenStreetMap" in page.text and "Google Places" in page.text
                assert "ScrapingBee" in page.text
                assert not pm["sec_cloud"] and not pm["sec_runners"]
                assert not pm["start_stop"] and not pm["find_col"]
                assert qm["sec_bots"]

        legacy = client.get("/?tab=workers")
        assert markers(legacy.text)["h2_runners"]

        # Concurrent polls must not StaleDataError
        def hit(tab: str) -> str:
            r = client.get(f"/partials/dashboard?tab={tab}")
            return "err" if markers(r.text)["error"] else "ok"

        fails = 0
        with ThreadPoolExecutor(max_workers=6) as pool:
            futs = [pool.submit(hit, t) for t in ["cloud", "runners", "bots"] * 4]
            for fut in as_completed(futs):
                if fut.result() != "ok":
                    fails += 1
        print(f"concurrent fails: {fails}/12")
        assert fails == 0
        print("OK distinct tabs + stable polls")


if __name__ == "__main__":
    main()
