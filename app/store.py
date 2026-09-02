"""The cached job records that scout, enrich and serve all read and write.

One file, `jobs.json`, keyed by LinkedIn job id. It holds the full posting —
description included — so the LLM pass and the web UI can work without going
back to LinkedIn, and so re-scoring after a weights change costs nothing.

Record shape:

    {"id", "title", "company", "location", "url", "posted",
     "description", "seniority", "employment_type",   # from the detail fetch
     "first_seen", "via",                             # bookkeeping
     "status": "new" | "interested" | "applied" | "dismissed",
     "note": "",
     "llm": {...}}                                    # see enrich.py, may be absent
"""

import json
from datetime import date
from pathlib import Path

# The repo layout, defined once and imported everywhere else.
#   app/    this package and its templates
#   cv/     master.yaml, render.py and the rendered applications
#   data/   everything generated: the job cache, logos, the digest
APP = Path(__file__).parent
ROOT = APP.parent
CV = ROOT / "cv"
DATA = ROOT / "data"
CONFIG = ROOT / "searches.yaml"

JOBS = DATA / "jobs.json"
LEGACY_SEEN = DATA / "seen.json"

STATUSES = ("new", "interested", "applied", "dismissed")

# Company logos are cached here rather than hotlinked. LinkedIn's media CDN is
# blocked by most ad and tracker blockers, so an <img> pointing at licdn.com
# silently fails in a normal browser even though the URL is fine; the signed
# URLs also expire. One small JPEG per company, fetched once.
LOGOS = DATA / "logos"


def load():
    if JOBS.exists():
        return json.loads(JOBS.read_text(encoding="utf-8"))
    # First run after the jobs.json change: keep whatever seen.json knew, so a
    # posting already reviewed does not resurface as new. Descriptions are lost
    # (seen.json never had them) and get refetched only if still listed.
    if LEGACY_SEEN.exists():
        old = json.loads(LEGACY_SEEN.read_text(encoding="utf-8"))
        return {jid: dict(rec, id=jid, status="new", note="") for jid, rec in old.items()}
    return {}


def save(jobs):
    DATA.mkdir(exist_ok=True)
    JOBS.write_text(json.dumps(jobs, indent=1, ensure_ascii=False), encoding="utf-8")


def merge(jobs, found):
    """Fold freshly fetched postings into the store, returning the new ids.

    An existing record keeps its status, note and llm facts — a rerun must not
    undo a job already marked applied. Fetched fields are refreshed, but only
    when non-empty, so a failed detail fetch cannot blank a good description.
    """
    today = date.today().strftime("%Y-%m-%d")
    fresh = []
    for jid, job in found.items():
        if jid not in jobs:
            jobs[jid] = {"first_seen": today, "status": "new", "note": ""}
            fresh.append(jid)
        jobs[jid].update({k: v for k, v in job.items() if v not in ("", None)})
        jobs[jid]["id"] = jid
    return fresh
