"""Run Vaga against fictional data, for screenshots and for trying it out.

    python demo/run.py              serve on http://localhost:8010
    python demo/run.py --port 9000
    python demo/run.py --build-only  just write the sandbox and print its path

Nothing here reads or writes your real files. The whole app is copied into a
temporary directory first, and the demo's master.yaml, searches.yaml and
jobs.json are laid over the copy — so `cv/master.yaml`, `searches.yaml`, `data/`
and `cv/applications/` in the real repo are untouched even if the demo writes
to them. Marking a job applied in the demo changes a throwaway file.

That isolation is the point. The alternative — swapping files in place and
swapping them back — leaves your real job search one crash away from being
overwritten by fifteen postings from Contoso.

The demo runs on a different port from the real app (8010, not 8000) so both
can be up at once.
"""

import argparse
import json
import re
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

DEMO = Path(__file__).parent
ROOT = DEMO.parent

# Copied into the sandbox. cv/applications comes along so the _demo application
# is already there to show in the tailoring panel.
COPY = ["app", "cv/templates", "cv/assets", "cv/render.py", "cv/applications"]


def plain(html):
    """Flatten description HTML the way scout stores it for the model.

    scout keeps both: `description_html` for the page, and a whitespace-collapsed
    `description` for the LLM and the keyword scan. The fixtures only write the
    HTML, so the plain form is derived here rather than maintained twice.
    """
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def build_jobs():
    """Turn demo/postings.py into a jobs.json the app can load."""
    sys.path.insert(0, str(DEMO))
    from postings import POSTINGS

    jobs = {}
    for n, p in enumerate(POSTINGS):
        when = date.today() - timedelta(days=p["days_ago"])
        # Ids are stable across runs so a bookmarked demo job page keeps working,
        # and obviously fake so nobody mistakes one for a real LinkedIn posting.
        jid = "90000%03d" % n
        jobs[jid] = {
            "id": jid,
            "title": p["title"],
            "company": p["company"],
            "location": p["location"],
            "url": "https://example.com/jobs/%s" % jid,
            "posted": when.isoformat(),
            "first_seen": when.isoformat(),
            "description": plain(p["html"]),
            "description_html": p["html"],
            "seniority": p["seniority"],
            "employment_type": p["employment_type"],
            "via": "demo",
            "status": "new",
            "note": "",
        }
        if p["llm"]:
            jobs[jid]["llm"] = p["llm"]
    return jobs


def build(target: Path):
    """Lay the app plus the demo's data into `target`."""
    target.mkdir(parents=True, exist_ok=True)
    for rel in COPY:
        src, dst = ROOT / rel, target / rel
        if not src.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pdf"))
        else:
            shutil.copy2(src, dst)

    shutil.copy2(DEMO / "master.yaml", target / "cv" / "master.yaml")
    shutil.copy2(DEMO / "searches.yaml", target / "searches.yaml")

    # profile.md is the assistant's fuller reference. The demo has no secrets to
    # keep out of it, so a short one is written inline rather than shipped.
    (target / "cv" / "profile.md").write_text(
        "# Alex Example\n\n"
        "Fictional. Junior fullstack developer in Lisbon, about three years of\n"
        "combined internship and part-time work. Strongest in C#/.NET and\n"
        "TypeScript. Has not used Kubernetes or AWS in anger.\n"
        "Would take hybrid up to three days in Lisbon, or fully remote.\n",
        encoding="utf-8")

    data = target / "data"
    data.mkdir(exist_ok=True)
    (data / "jobs.json").write_text(
        json.dumps(build_jobs(), indent=1, ensure_ascii=False), encoding="utf-8")
    return target


def main():
    ap = argparse.ArgumentParser(description="Run Vaga on fictional data")
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--build-only", action="store_true",
                    help="write the sandbox and print the path, then exit")
    args = ap.parse_args()

    sandbox = build(Path(tempfile.mkdtemp(prefix="vaga-demo-")))
    jobs = json.loads((sandbox / "data" / "jobs.json").read_text(encoding="utf-8"))
    print("sandbox : %s" % sandbox)
    print("postings: %d" % len(jobs))
    if args.build_only:
        return 0

    # Import serve from the copy, so every path it derives from __file__ points
    # inside the sandbox. uvicorn is run here rather than by serve.py's own
    # __main__ because that one hardcodes port 8000.
    sys.path.insert(0, str(sandbox / "app"))
    import uvicorn

    import serve
    print("Vaga demo — http://localhost:%d" % args.port)
    uvicorn.run(serve.app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
