"""Smoke against the live dashboard on 127.0.0.1:8000 until tabs are distinct."""
from __future__ import annotations

import sys
import urllib.error
import urllib.request


BASE = "http://127.0.0.1:8000"


def fetch(path: str) -> str:
    with urllib.request.urlopen(BASE + path, timeout=60) as resp:
        return resp.read().decode("utf-8", errors="replace")


def check(tab: str, html: str) -> list[str]:
    errs: list[str] = []
    has_cloud = 'section-title">Cloud' in html
    has_runners = 'section-title">Runners' in html
    has_bots = 'section-title">Bots' in html
    has_ch = "Companies House" in html and 'data-table="bots"' in html
    has_gh = "GitHub Actions" in html and ('data-table="cloud"' in html or "Host" in html)
    has_start = "Start / stop" in html
    has_find = ">Find</th>" in html
    has_places_row = "Google Places" in html and 'data-table="bots"' in html
    has_gcp = "Google Cloud Run Jobs" in html and 'data-table="cloud"' in html

    if tab == "cloud":
        if not has_cloud:
            errs.append("missing Cloud section")
        if has_runners or has_bots:
            errs.append("Cloud must not show Runners/Bots sections")
        if has_ch:
            errs.append("Cloud must not list Companies House bots table")
        if not has_gh:
            errs.append("Cloud must list GitHub Actions host")
        if not has_find:
            errs.append("Cloud must have Find column")
        if has_start:
            errs.append("Cloud must not have Start/stop")
        if has_gcp:
            errs.append("Cloud must not include Information-hunting GCP host")
        if has_places_row:
            errs.append("Cloud must not list Google Places bot")
    elif tab == "runners":
        if not has_runners:
            errs.append("missing Runners section")
        if has_cloud or has_bots:
            errs.append("Runners must not show Cloud/Bots sections")
        if not has_start:
            errs.append("Runners must have Start/stop")
        if has_find:
            errs.append("Runners must not have Find column")
        if has_ch and 'data-table="bots"' in html:
            errs.append("Runners must not show bots table")
    elif tab == "bots":
        if not has_bots:
            errs.append("missing Bots section")
        if has_cloud or has_runners:
            errs.append("Bots must not show Cloud/Runners sections")
        if not has_ch:
            errs.append("Bots must list Companies House")
        if has_start:
            errs.append("Bots must not have Start/stop")
        if has_find:
            errs.append("Bots must not have Find column")
    return errs


def main() -> int:
    print("LIVE smoke", BASE)
    all_ok = True
    for tab in ("cloud", "runners", "bots"):
        try:
            page = fetch(f"/?tab={tab}")
            partial = fetch(f"/partials/dashboard?tab={tab}")
        except urllib.error.URLError as exc:
            print(f"TAB {tab}: FETCH FAIL {exc}")
            return 1
        pe = check(tab, page)
        qe = check(tab, partial)
        status = "OK" if not pe and not qe else "FAIL"
        if pe or qe:
            all_ok = False
        print(f"TAB {tab}: {status}")
        for e in pe:
            print(f"  page: {e}")
        for e in qe:
            print(f"  partial: {e}")
        print(
            f"  markers page cloud={('section-title\">Cloud' in page)} "
            f"runners={('section-title\">Runners' in page)} "
            f"bots={('section-title\">Bots' in page)} "
            f"len={len(page)}"
        )

    # Legacy workers must not become Bots
    legacy = fetch("/?tab=workers")
    if 'section-title">Runners' not in legacy:
        print("LEGACY workers→runners: FAIL")
        all_ok = False
    else:
        print("LEGACY workers->runners: OK")

    if all_ok:
        print("ALL LIVE CHECKS PASSED")
        return 0
    print("LIVE CHECKS FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
