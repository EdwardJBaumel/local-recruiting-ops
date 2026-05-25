"""
Tenant Debug CLI
================

What this is
------------
A throwaway-ish CLI that scrapes ONE tenant with ONE keyword and
prints what the scraper sees. Use it when:

    * A career page changes its DOM and the real ingest starts
      returning zero jobs (or jobs with empty fields).
    * You're adding a new tenant to tenants.py and want to confirm
      your selectors before wiring it into the orchestrator.

Why a separate tool and not part of ingest?
-------------------------------------------
The full ingest pipeline runs LLM classification, storage writes,
archetype tagging, etc. When you're debugging a broken selector you
don't want ANY of that noise -- you want to see the raw dict the
runner pulled out of the DOM so you can tell exactly which field
drifted.

Usage
-----
    # From the sentinel/ folder (NOT the repo root):
    python -m tools.test_tenant apple "product manager"
    python -m tools.test_tenant apple "product manager" --show 10
    python -m tools.test_tenant apple "product manager" --headful

Flags
-----
    --show N    How many job dicts to pretty-print (default 5).
    --headful   Pop a visible browser. Useful for watching the page
                render and copying selectors out of DevTools.
    --timeout   Per-request timeout in seconds (default 25).

Exit codes
----------
    0   ok and at least one job came back
    1   tenant not found in TENANTS
    2   zero jobs returned (likely selector drift or login wall)
    3   Playwright missing (run: pip install playwright)

Reading the output
------------------
For each job we print title / team / location / url / posted, plus a
tiny "HEALTH CHECK" summary at the end: how many jobs had each field
populated. If "location: 0/60" shows up, the location selector broke
-- go fix it in tenants.py.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Sequence


def _print_job(idx: int, job: dict) -> None:
    """Pretty-print one job dict. Keep each field on its own line so
    broken ones are obvious (empty string vs. missing vs. garbage)."""
    print(f"--- job {idx} ---")
    # Known fields first, in a stable order, then anything extra.
    ordered = ["title", "team", "location", "posted", "url", "company", "source"]
    seen = set()
    for k in ordered:
        if k in job:
            print(f"  {k:10s}: {job[k]!r}")
            seen.add(k)
    for k in sorted(job):
        if k not in seen:
            print(f"  {k:10s}: {job[k]!r}")


def _health_check(jobs: list[dict]) -> None:
    """Quick summary: how many jobs had each field populated?
    A zero count is a red flag -- that field's selector drifted."""
    if not jobs:
        return
    print()
    print("=== HEALTH CHECK ===")
    all_keys: set[str] = set()
    for j in jobs:
        all_keys.update(j.keys())
    total = len(jobs)
    for k in sorted(all_keys):
        filled = sum(1 for j in jobs if str(j.get(k, "")).strip())
        mark = "OK " if filled == total else ("!! " if filled == 0 else "   ")
        print(f"  {mark} {k:12s} {filled}/{total} populated")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m tools.test_tenant",
        description="Debug one tenant's scraper with one keyword.",
    )
    ap.add_argument("tenant", help="Tenant key (apple, meta, microsoft, ...)")
    ap.add_argument("keyword", help="Search term, e.g. 'product manager'")
    ap.add_argument("--show", type=int, default=5,
                    help="Jobs to print (default 5). Use 0 to skip printing.")
    ap.add_argument("--headful", action="store_true",
                    help="Pop a visible browser window.")
    ap.add_argument("--timeout", type=int, default=25,
                    help="Per-request timeout in seconds (default 25).")
    ap.add_argument("--json", action="store_true",
                    help="Print all jobs as one JSON blob at the end.")
    args = ap.parse_args(argv)

    # Loud default logging so the user sees the runner's progress.
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Lazy imports so a missing Playwright gives a clean exit code,
    # not a stack trace at import time.
    from agents.tenants import get_tenant, list_tenant_names
    from agents.playwright_runner import fetch_spa

    tenant = get_tenant(args.tenant)
    if tenant is None:
        print(f"ERROR: tenant {args.tenant!r} not found.", file=sys.stderr)
        print(f"Known tenants: {', '.join(list_tenant_names())}", file=sys.stderr)
        return 1

    print(f"Scraping {tenant.get('display_name', args.tenant)!r} for "
          f"{args.keyword!r} (headless={not args.headful}, timeout={args.timeout}s)")
    print()

    try:
        jobs = fetch_spa(
            tenant,
            [args.keyword],
            headless=not args.headful,
            per_request_timeout_s=args.timeout,
        )
    except Exception as e:
        # The runner catches most errors internally, but just in case
        # something escapes (e.g. tenant config key missing), surface it.
        print(f"ERROR: scrape raised: {e}", file=sys.stderr)
        return 3

    if not jobs:
        print("ZERO jobs returned. Likely causes:")
        print("  - wait_for_selector never matched (selector drift)")
        print("  - card_selector matched nothing (selector drift)")
        print("  - login wall / CAPTCHA (check tenant's status note)")
        print("Try --headful to watch the page render.")
        return 2

    print(f"Got {len(jobs)} jobs.")
    for i, j in enumerate(jobs[: args.show]):
        _print_job(i, j)

    _health_check(jobs)

    if args.json:
        print()
        print("=== JSON ===")
        print(json.dumps(jobs, indent=2, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
