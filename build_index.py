#!/usr/bin/env python3
"""
Build a searchable index of every OCA module across all OCA repositories,
for a set of Odoo versions (default 17.0 / 18.0 / 19.0).

Data source: the GitHub API + raw.githubusercontent.com (ground truth).
No external dependencies -- standard library only.

Usage:
    export GITHUB_TOKEN=$(gh auth token)      # optional but strongly recommended
    python3 build_index.py                    # writes oca_index.json
    python3 build_index.py --branches 18.0    # only one version
    python3 build_index.py --limit 20         # crawl only 20 repos (for testing)

Output: oca_index.json  -- consumed by index.html (the search page).
"""

import argparse
import ast
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

ORG = "OCA"
DEFAULT_BRANCHES = ["17.0", "18.0", "19.0"]
API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"

TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()

# Manifest keys we keep. Everything else is dropped to keep the index small.
KEEP_KEYS = (
    "name", "summary", "version", "author", "category",
    "license", "website", "depends", "maintainers", "development_status",
)


def _headers():
    h = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "oca-module-indexer",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h


def _get(url, raw=False, retries=4):
    """GET a URL. Returns (status, body_bytes, headers) or raises on hard fail.

    Handles GitHub primary/secondary rate limiting with backoff.
    """
    last_err = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={} if raw else _headers())
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as e:
            # 404 = not found (missing branch/module); let caller decide.
            if e.code in (404, 451):
                return e.code, b"", dict(e.headers)
            # Rate limited?
            if e.code in (403, 429):
                remaining = e.headers.get("X-RateLimit-Remaining")
                reset = e.headers.get("X-RateLimit-Reset")
                if remaining == "0" and reset:
                    wait = max(0, int(reset) - int(time.time())) + 2
                    wait = min(wait, 900)  # cap at 15 min
                    sys.stderr.write(f"  rate limited; sleeping {wait}s...\n")
                    time.sleep(wait)
                    continue
                # secondary limit -> exponential backoff
                time.sleep(2 ** attempt)
                last_err = e
                continue
            last_err = e
            time.sleep(1 + attempt)
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            time.sleep(1 + attempt)
    if last_err:
        raise last_err
    raise RuntimeError(f"failed to GET {url}")


def list_org_repos(limit=None):
    """Return list of {name, default_branch} for every repo in the OCA org."""
    repos = []
    page = 1
    while True:
        url = f"{API}/orgs/{ORG}/repos?per_page=100&type=public&page={page}"
        status, body, _ = _get(url)
        if status != 200:
            break
        chunk = json.loads(body)
        if not chunk:
            break
        for r in chunk:
            if r.get("archived"):
                continue
            repos.append({"name": r["name"], "default_branch": r.get("default_branch")})
        page += 1
        if limit and len(repos) >= limit:
            return repos[:limit]
    return repos


def list_top_level_dirs(repo, branch):
    """Top-level directory names of a repo at a branch. None if branch missing."""
    url = f"{API}/repos/{ORG}/{repo}/contents/?ref={branch}"
    status, body, _ = _get(url)
    if status == 404:
        return None
    if status != 200:
        return None
    try:
        entries = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(entries, list):
        return None
    return [e["name"] for e in entries if e.get("type") == "dir"]


def fetch_manifest(repo, branch, module):
    """Fetch + parse a module's __manifest__.py from the raw CDN. None if absent."""
    for fname in ("__manifest__.py", "__openerp__.py"):
        url = f"{RAW}/{ORG}/{repo}/{branch}/{module}/{fname}"
        status, body, _ = _get(url, raw=True)
        if status == 200 and body:
            return parse_manifest(body.decode("utf-8", "replace"))
    return None


def parse_manifest(text):
    """Extract the manifest dict from source safely (no code execution)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            try:
                data = ast.literal_eval(node)
            except (ValueError, SyntaxError):
                continue
            if isinstance(data, dict) and ("name" in data or "version" in data):
                return data
    return None


def slim(manifest):
    """Keep only the fields we index; normalise a few types."""
    out = {}
    for k in KEEP_KEYS:
        if k in manifest:
            out[k] = manifest[k]
    # author can be a string or list
    if isinstance(out.get("author"), list):
        out["author"] = ", ".join(str(a) for a in out["author"])
    if isinstance(out.get("maintainers"), list):
        out["maintainers"] = ", ".join(str(m) for m in out["maintainers"])
    if not isinstance(out.get("depends"), list):
        out["depends"] = out.get("depends", []) or []
    return out


def crawl_repo(repo, branches):
    """Return a list of module records for one repo across the given branches."""
    records = []
    for branch in branches:
        dirs = list_top_level_dirs(repo, branch)
        if not dirs:
            continue
        for module in dirs:
            if module.startswith(".") or module in ("setup", "docs"):
                continue
            manifest = fetch_manifest(repo, branch, module)
            if not manifest:
                continue
            m = slim(manifest)
            records.append({
                "module": module,
                "repo": repo,
                "branch": branch,
                "name": str(m.get("name", module)),
                "summary": str(m.get("summary", "")),
                "version": str(m.get("version", "")),
                "author": str(m.get("author", "")),
                "category": str(m.get("category", "")),
                "license": str(m.get("license", "")),
                "website": str(m.get("website", "")),
                "depends": [str(d) for d in m.get("depends", [])],
                "status": str(m.get("development_status", "")),
                "url": f"https://github.com/{ORG}/{repo}/tree/{branch}/{module}",
            })
    return records


def main():
    ap = argparse.ArgumentParser(description="Build the OCA module index.")
    ap.add_argument("--branches", nargs="+", default=DEFAULT_BRANCHES,
                    help="Odoo version branches to crawl (default: 17.0 18.0 19.0)")
    ap.add_argument("--out", default="oca_index.json", help="output file")
    ap.add_argument("--limit", type=int, default=None, help="crawl only N repos (testing)")
    ap.add_argument("--workers", type=int, default=8, help="concurrent repos")
    args = ap.parse_args()

    if not TOKEN:
        sys.stderr.write(
            "WARNING: no GITHUB_TOKEN set -- you get 60 requests/hour and the crawl\n"
            "         will likely stall. Run:  export GITHUB_TOKEN=$(gh auth token)\n\n"
        )

    sys.stderr.write(f"Listing {ORG} repositories...\n")
    repos = list_org_repos(limit=args.limit)
    sys.stderr.write(f"  {len(repos)} repos to crawl across branches {args.branches}\n")

    all_records = []
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(crawl_repo, r["name"], args.branches): r["name"] for r in repos}
        for fut in as_completed(futures):
            repo = futures[fut]
            done += 1
            try:
                recs = fut.result()
            except Exception as e:  # noqa: BLE001 - keep crawling on any single-repo error
                sys.stderr.write(f"  [{done}/{len(repos)}] {repo}: ERROR {e}\n")
                continue
            if recs:
                all_records.extend(recs)
            sys.stderr.write(
                f"  [{done}/{len(repos)}] {repo}: {len(recs)} modules "
                f"(total {len(all_records)})\n"
            )

    all_records.sort(key=lambda r: (r["module"], r["branch"]))
    index = {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "org": ORG,
        "branches": args.branches,
        "repo_count": len(repos),
        "module_count": len(all_records),
        "modules": all_records,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, separators=(",", ":"))
    sys.stderr.write(
        f"\nWrote {args.out}: {len(all_records)} module records "
        f"from {len(repos)} repos.\n"
    )


if __name__ == "__main__":
    main()
