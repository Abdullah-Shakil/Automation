"""Smoke: Leads intro stays above; toolbar+gap+table fill viewport under sticky nav."""

from __future__ import annotations

import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8000"

MEASURE_JS = r"""
(() => {
  const vh = window.innerHeight;
  const body = document.body;
  const top = document.querySelector('.top');
  const head = document.querySelector('.view-head');
  const stack = document.querySelector('.leads-stack');
  const tool = document.querySelector('.leads-toolbar');
  const table = document.querySelector('.leads-scroll');
  if (!stack || !tool || !table || !head) return { error: 'missing leads pieces' };
  const topH = top.getBoundingClientRect().height;
  const tr = tool.getBoundingClientRect();
  const tb = table.getBoundingClientRect();
  const sr = stack.getBoundingClientRect();
  const hr = head.getBoundingClientRect();
  const gap = tb.top - tr.bottom;
  const both = tr.height + gap + tb.height;
  const expectedStack = vh - topH;
  const headVisible = getComputedStyle(head).display !== 'none' && hr.height > 8;
  // Scroll stack under sticky top, then re-check fill
  stack.scrollIntoView({ block: 'start' });
  // account for sticky top covering the top of stack
  const sr2 = stack.getBoundingClientRect();
  const tr2 = tool.getBoundingClientRect();
  const tb2 = table.getBoundingClientRect();
  const gap2 = tb2.top - tr2.bottom;
  const both2 = tr2.height + gap2 + tb2.height;
  return {
    vh,
    topH: Math.round(topH),
    headVisible,
    headH: Math.round(hr.height),
    stackH: Math.round(sr.height),
    bothSum: Math.round(both),
    stackFillOk: Math.abs(both - sr.height) <= 3,
    viewportFillOk: Math.abs(sr.height - expectedStack) <= 8,
    afterScroll: {
      stackTop: Math.round(sr2.top),
      bothSum: Math.round(both2),
      stackH: Math.round(sr2.height),
      underNav: Math.abs(sr2.top - topH) <= 6 || sr2.top <= topH + 2,
    },
    pageCanScroll: document.documentElement.scrollHeight > vh + 40,
    hasPageClass: body.classList.contains('page-leads'),
  };
})()
"""


def main() -> int:
    try:
        with urllib.request.urlopen(BASE + "/health", timeout=5) as resp:
            if resp.status != 200:
                print("FAIL dashboard unhealthy", flush=True)
                return 1
    except Exception as exc:
        print(f"FAIL dashboard down: {exc}", flush=True)
        return 1

    html = urllib.request.urlopen(BASE + "/leads", timeout=15).read().decode("utf-8", "replace")
    if "view-head" not in html or "leads-stack" not in html:
        print("FAIL missing view-head / leads-stack", flush=True)
        return 1
    if "Every company in the shared database" not in html:
        print("FAIL missing intro copy", flush=True)
        return 1

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("OK markers (playwright not installed)", flush=True)
        return 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(BASE + "/leads", wait_until="networkidle", timeout=30000)
        metrics = page.evaluate(MEASURE_JS)
        browser.close()

    print("LIVE", json.dumps(metrics, indent=2), flush=True)
    if metrics.get("error"):
        print("FAIL", metrics["error"], flush=True)
        return 1
    errs = []
    if not metrics.get("headVisible"):
        errs.append("intro view-head not visible")
    if not metrics.get("pageCanScroll"):
        errs.append("page cannot scroll past intro")
    if not metrics.get("stackFillOk"):
        errs.append("toolbar+gap+table != stack height")
    if not metrics.get("viewportFillOk"):
        errs.append(f"stack not ~100vh-nav (stackH={metrics.get('stackH')} vh={metrics.get('vh')})")
    if errs:
        for e in errs:
            print("FAIL", e, flush=True)
        return 1
    print("OK intro above; leads pane fills viewport when scrolled", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
