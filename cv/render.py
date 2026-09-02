"""Render a tailored CV and cover letter to PDF.

    python render.py applications/2026-08-acme

Reads master.yaml, overlays applications/<slug>/tailored.yaml on top of it,
fills the Jinja templates, and prints each to PDF with headless Chromium.
"""

import base64
import mimetypes
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).parent

BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def find_browser() -> str:
    for p in BROWSERS:
        if Path(p).exists():
            return p
    for name in ("chrome", "msedge", "chromium"):
        if found := shutil.which(name):
            return found
    sys.exit("No Chrome/Edge found. Edit BROWSERS in render.py.")


def data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def pick(pool: list, spec, key="id") -> list:
    """Resolve a tailored.yaml section against the master pool.

    A list of ids selects and orders master entries. A list of dicts with an
    `id` overrides fields on the matching entry; without one it's a new entry.
    Omitted entirely (None) means "use the whole pool, master order".
    """
    if spec is None:
        return pool
    by_id = {e[key]: e for e in pool if key in e}
    out = []
    for item in spec:
        if isinstance(item, str):
            if item not in by_id:
                sys.exit(f"unknown id {item!r} — not in master.yaml")
            out.append(by_id[item])
        elif isinstance(item, dict) and item.get(key) in by_id:
            out.append({**by_id[item[key]], **item})
        else:
            out.append(item)
    return out


def resolve_skills(master_skills, spec) -> tuple[list, list]:
    """Return (skills block, familiar block).

    Master holds tiered pools; a tailored.yaml normally just lists the terms it
    wants. `familiar` is opt-in per posting — it never renders by default, so a
    a tech never used can't leak onto a CV that didn't ask for it.
    """
    if isinstance(master_skills, list):          # flat pool, pre-tier format
        return (spec if spec is not None else master_skills), []
    pool = master_skills.get("core", []) + master_skills.get("working", [])
    if spec is None:
        return pool, []
    if isinstance(spec, list):
        return spec, []
    return spec.get("skills", pool), spec.get("familiar", [])


def to_pdf(browser: str, html: str, out: Path) -> None:
    """Print an HTML string to PDF. Chromium needs a real file to load."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "page.html"
        src.write_text(html, encoding="utf-8")
        out.parent.mkdir(parents=True, exist_ok=True)
        # --no-pdf-header-footer keeps Chromium's date/URL chrome off the page.
        r = subprocess.run(
            [
                browser, "--headless", "--disable-gpu", "--no-sandbox",
                f"--user-data-dir={tmp}/profile",
                "--no-pdf-header-footer",
                "--print-to-pdf-no-header",
                f"--print-to-pdf={out}",
                src.as_uri(),
            ],
            capture_output=True, text=True, timeout=120,
        )
        if not out.exists():
            sys.exit(f"render failed for {out.name}:\n{r.stderr[-2000:]}")


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    app_dir = Path(sys.argv[1])
    if not app_dir.is_absolute():
        app_dir = ROOT / app_dir

    master = yaml.safe_load((ROOT / "master.yaml").read_text(encoding="utf-8"))
    tailored_path = app_dir / "tailored.yaml"
    if not tailored_path.exists():
        sys.exit(f"missing {tailored_path}")
    t = yaml.safe_load(tailored_path.read_text(encoding="utf-8")) or {}

    profile = {**master["profile"], **t.get("profile", {})}
    skills, familiar = resolve_skills(master["skills"], t.get("skills"))
    ctx = {
        "profile": profile,
        "about": pick(master["about"], t.get("about")),
        "languages": t.get("languages", master["languages"]),
        "skills": skills,
        "familiar": familiar,
        "experience": pick(master["experience"], t.get("experience")),
        "education": pick(master["education"], t.get("education")),
        "projects": pick(master["projects"], t.get("projects")),
        "photo_uri": data_uri(ROOT / profile["photo"]),
    }

    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=True)
    browser = find_browser()

    slug = app_dir.name
    # Recruiters see the filename, so lead with the first name — and follow the
    # profile, not a hardcoded one, so demo/placeholder renders aren't mislabelled.
    who = profile["name"].split()[0]
    to_pdf(browser, env.get_template("cv.html.j2").render(**ctx),
           app_dir / f"{who}CV-{slug}.pdf")
    print(f"  CV     -> {who}CV-{slug}.pdf")

    if cover_spec := t.get("cover"):
        cover = {**master["cover"], **cover_spec}
        cover.setdefault("date", date.today().strftime("%d %B %Y").upper())
        if "body" not in cover:
            sys.exit("tailored.yaml: cover needs a `body:` list of paragraphs")
        to_pdf(browser, env.get_template("cover.html.j2").render(profile=profile, cover=cover),
               app_dir / f"{who}Cover-{slug}.pdf")
        print(f"  Cover  -> {who}Cover-{slug}.pdf")


if __name__ == "__main__":
    main()
