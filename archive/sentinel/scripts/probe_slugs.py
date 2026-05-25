#!/usr/bin/env python3
"""
Probe every ATS slug in config.json and report which ones are live.

Usage (from the sentinel/ directory):
    python scripts/probe_slugs.py
    python scripts/probe_slugs.py --fix        # rewrite config.json with working slugs
    python scripts/probe_slugs.py --extras     # also try common naming variants

Output columns:
    OK      - endpoint returned 200 and a well-formed payload
    EMPTY   - endpoint returned 200 but the board has no jobs (still valid)
    404     - slug does not exist; remove it from config.json
    FAIL    - transient/other error (timeout, 5xx, JSON error). Retry later.

Run this from your Windows machine, not from the sandbox. It needs network.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
from pathlib import Path

try:
    import requests
except ImportError:  # pragma: no cover
    print("ERROR: requests not installed. Run: pip install requests")
    sys.exit(1)

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config.json"

# Common naming variants worth trying when a primary slug 404s.
EXTRA_VARIANTS = {
    "notion":      ["notionhq", "notionlabs"],
    "doordash":    ["doordashjobs", "door-dash", "doordashinc"],
    "square":      ["block", "squareinc", "squareup"],
    "plaid":       ["plaidinc"],
    "hashicorp":   ["hashicorpinc"],
    "snyk":        ["snykinc"],
    "nerdwallet":  ["nerdwalletinc"],
    "wiz-inc":     ["wiz", "wizinc", "wizsecurity", "wizio"],
    "netflix":     ["netflixhq", "netflix-inc"],
    "cruise":      ["getcruise", "gmcruise"],
    "scale":       ["scaleai", "scale-ai"],
    "anduril":     ["anduril-industries", "andurilindustries"],
    "anthropic":   ["anthropicai", "anthropic-ai"],
}

ENDPOINTS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false",
    "lever":      "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby":      "https://api.ashbyhq.com/posting-api/job-board/{slug}",
}

TIMEOUT = 8
UA = "sentinel-slug-probe/1.0"


def probe(ats: str, slug: str) -> tuple[str, str, str, int]:
    """Returns (ats, slug, status, job_count)."""
    url = ENDPOINTS[ats].format(slug=slug)
    try:
        r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": UA})
    except requests.exceptions.Timeout:
        return ats, slug, "FAIL(timeout)", 0
    except requests.exceptions.RequestException as e:
        return ats, slug, f"FAIL({type(e).__name__})", 0

    if r.status_code == 404:
        return ats, slug, "404", 0
    if r.status_code != 200:
        return ats, slug, f"FAIL({r.status_code})", 0

    try:
        data = r.json()
    except ValueError:
        return ats, slug, "FAIL(not-json)", 0

    if ats == "greenhouse":
        count = len(data.get("jobs", []))
    elif ats == "lever":
        count = len(data) if isinstance(data, list) else 0
    else:  # ashby
        count = len(data.get("jobs", []))

    return ats, slug, ("OK" if count > 0 else "EMPTY"), count


def extract_slugs(cfg: dict) -> list[tuple[str, str, str]]:
    """Returns (ats, display_slug, probe_slug) tuples."""
    ingest = cfg.get("ingest", {})
    out: list[tuple[str, str, str]] = []
    for s in ingest.get("greenhouse_companies", []):
        out.append(("greenhouse", s, s))
    for s in ingest.get("lever_companies", []):
        out.append(("lever", s, s))
    for entry in ingest.get("ashby_companies", []):
        if isinstance(entry, list) and len(entry) == 2:
            out.append(("ashby", f"{entry[0]}({entry[1]})", entry[1]))
        else:
            out.append(("ashby", str(entry), str(entry)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--extras", action="store_true",
                    help="Also probe common naming variants for 404 slugs.")
    ap.add_argument("--fix", action="store_true",
                    help="Rewrite config.json: drop 404s, swap in working variants (with --extras).")
    ap.add_argument("--json", action="store_true",
                    help="Emit JSON report instead of a human-readable table.")
    args = ap.parse_args()

    if not CONFIG_FILE.exists():
        print(f"ERROR: {CONFIG_FILE} not found. Run from the sentinel/ directory.")
        sys.exit(1)

    cfg = json.loads(CONFIG_FILE.read_text())
    targets = extract_slugs(cfg)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        primary = list(ex.map(lambda t: probe(t[0], t[2]), targets))

    primary_by_probe = {(a, s): (st, c) for a, s, st, c in primary}

    # For 404s, try variants if --extras.
    variants_results: dict[tuple[str, str], tuple[str, int, str]] = {}
    if args.extras:
        variant_jobs = []
        for (ats, display, probe_slug), (status, _) in zip(targets, primary):
            if status != "404":
                continue
            for variant in EXTRA_VARIANTS.get(probe_slug, []):
                variant_jobs.append((ats, probe_slug, variant))
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            variant_probed = list(ex.map(lambda t: probe(t[0], t[2]) + (t[1],),
                                         variant_jobs))
        for ats, slug, status, count, original in variant_probed:
            key = (ats, original)
            if key not in variants_results and status in ("OK", "EMPTY"):
                variants_results[key] = (slug, count, status)

    if args.json:
        report = {
            "primary": [{"ats": a, "slug": s, "status": st, "jobs": c} for a, s, st, c in primary],
            "variants": {f"{a}:{s}": {"slug": v[0], "jobs": v[1], "status": v[2]}
                         for (a, s), v in variants_results.items()},
        }
        print(json.dumps(report, indent=2))
    else:
        print()
        print(f"  {'ATS':<12}{'SLUG':<28}{'STATUS':<18}{'JOBS':>6}")
        print(f"  {'-'*12}{'-'*28}{'-'*18}{'-'*6}")
        for ats, slug, status, count in primary:
            colour = "\033[92m" if status in ("OK", "EMPTY") else ("\033[93m" if status == "404" else "\033[91m")
            print(f"  {ats:<12}{slug:<28}{colour}{status:<18}\033[0m{count:>6}")

        if variants_results:
            print()
            print("  Working variants for 404'd slugs:")
            for (ats, original), (variant, jobs, status) in variants_results.items():
                print(f"    {ats}: {original} -> {variant}  ({status}, {jobs} jobs)")

    # --fix: rewrite config
    if args.fix:
        ingest = cfg.setdefault("ingest", {})
        gh = ingest.get("greenhouse_companies", [])
        lv = ingest.get("lever_companies", [])
        ab = ingest.get("ashby_companies", [])

        to_drop = {(a, s) for a, s, st, _ in primary if st == "404"}
        replacements = {(a, s): v[0] for (a, s), v in variants_results.items()}

        new_gh = []
        for s in gh:
            if ("greenhouse", s) in replacements:
                new_gh.append(replacements[("greenhouse", s)])
            elif ("greenhouse", s) not in to_drop:
                new_gh.append(s)
        new_lv = []
        for s in lv:
            if ("lever", s) in replacements:
                new_lv.append(replacements[("lever", s)])
            elif ("lever", s) not in to_drop:
                new_lv.append(s)
        new_ab = []
        for entry in ab:
            if isinstance(entry, list) and len(entry) == 2:
                key = ("ashby", entry[1])
                if key in replacements:
                    new_ab.append([entry[0], replacements[key]])
                elif key not in to_drop:
                    new_ab.append(entry)
            else:
                new_ab.append(entry)

        ingest["greenhouse_companies"] = new_gh
        ingest["lever_companies"] = new_lv
        ingest["ashby_companies"] = new_ab

        CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
        print(f"\n  Rewrote {CONFIG_FILE}. {len(to_drop)} slug(s) dropped, {len(replacements)} replaced.")


if __name__ == "__main__":
    main()
