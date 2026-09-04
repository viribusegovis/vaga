"""Bridge from a job posting to a tailored CV and cover letter.

the `cv/` half renders PDFs from `applications/<slug>/tailored.yaml`, which
overlays `master.yaml`. This module writes a first draft of that file from what
we know about a posting, then drives `render.py`.

The seed is deliberately conservative. It selects — it never invents. Skills are
picked because the posting actually mentions them and `master.yaml` actually
claims them; the cover letter is a skeleton with the role and company filled in
and the argument left to you. Nothing here fabricates experience, and every
generated line is meant to be edited before it goes anywhere.
"""

import re
import subprocess
import sys
from datetime import date

import yaml

import scout
import store

TOOLKIT = store.CV
APPS = TOOLKIT / "applications"

# Noise recruiters put in titles that shouldn't end up on a CV.
TITLE_NOISE = re.compile(
    r"\s*(\(m[/|]?f[/|]?[dx]\)|\(all genders\)|\(remote\)|\(hybrid\)|m/f/d"
    r"|\bM/F\b|\(\s*\d+\s*\)|\[[^\]]*\]|–\s*\w+\s*$)", re.I)


def path(slug):
    return APPS / slug


def exists(slug):
    return (path(slug) / "tailored.yaml").exists()


def master():
    return yaml.safe_load((TOOLKIT / "master.yaml").read_text(encoding="utf-8"))


def clean_title(title):
    """Turn a posting title into something that reads on a CV."""
    out = TITLE_NOISE.sub("", title or "").strip(" -–—|·,")
    out = re.sub(r"\s{2,}", " ", out)
    return out[:60] or "Software Developer"


def matched_skills(job, skills):
    """Split master's pools by whether this posting actually mentions them.

    Returns (claimed, familiar). `claimed` is core + working skills the posting
    names — the ones you can defend in an interview. `familiar` is the same test
    against the familiar pool, kept separate because the CV template renders it
    under its own dimmer heading rather than claiming depth you don't have.
    """
    blob = scout.fold("%s %s" % (job.get("title", ""), job.get("description", "")))
    hit = lambda name: bool(scout.term_re(name).search(blob))
    claimed = [s for s in skills.get("core", []) + skills.get("working", []) if hit(s)]
    familiar = [s for s in skills.get("familiar", []) if hit(s)]
    return claimed, familiar


def seed_yaml(job, skills):
    """Build the starter tailored.yaml text for one posting."""
    claimed, familiar = matched_skills(job, skills)
    llm = job.get("llm") or {}
    company = job.get("company", "the company")
    title = clean_title(job.get("title", ""))

    # Matched skills lead, since those are the ones the reader is scanning for.
    # Then top up from the core pool: a posting that names four of your skills
    # would otherwise produce a CV whose SKILLS block looks bare.
    listed = list(claimed)
    for extra in skills.get("core", []) + skills.get("working", []):
        if len(listed) >= 14:
            break
        if extra not in listed:
            listed.append(extra)

    doc = {
        "profile": {"title": title},
        "skills": ({"skills": listed, "familiar": familiar} if familiar
                   else {"skills": listed}),
        "cover": {
            "title": title.upper(),
            "body": [
                "I'm writing about the %s role at %s." % (title, company),
                "DRAFT — replace this paragraph with the specific thing that makes "
                "you a fit for this posting, and cut anything you can't defend in "
                "an interview.",
                "I'd welcome the chance to talk about the role.",
            ],
        },
    }
    body = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False,
                          default_flow_style=False, width=88)

    header = [
        "# %s — %s" % (title, company),
        "# %s" % job.get("url", ""),
        "# seeded %s by Vaga; every line here is a draft, edit before sending" % date.today(),
        "#",
        "# Skills below are the ones this posting mentions AND master.yaml already",
        "# claims. Nothing was invented. Add or drop freely — omit a section",
        "# entirely to fall back to master.yaml's own content and order.",
    ]
    if familiar:
        header.append("# `familiar` renders under its own dimmer heading: the posting")
        header.append("# asks for these and you haven't used them yet.")
    if llm.get("years_required"):
        header.append("# Posting asks for %s year%s%s." % (
            llm["years_required"], "" if llm["years_required"] == 1 else "s",
            " (stated as required)" if llm.get("years_are_hard_requirement")
            else " (preferred, not required)"))
    if llm.get("summary"):
        header.append("# Role: %s" % llm["summary"][:150])
    header.append("")
    return "\n".join(header) + body


def create(job, skills):
    """Write the application folder. Returns (slug, error)."""
    slug = scout.slug(job)
    folder = path(slug)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "tailored.yaml"
        if not target.exists():
            target.write_text(seed_yaml(job, skills), encoding="utf-8")
    except OSError as e:
        return slug, "Could not write %s: %s" % (folder, e)
    return slug, None


def read(slug):
    p = path(slug) / "tailored.yaml"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def write(slug, text):
    """Validate then save. Returns an error message, or None."""
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return "YAML error: %s" % e
    if parsed is not None and not isinstance(parsed, dict):
        return "tailored.yaml must be a mapping, or empty to use master as-is."
    cover = (parsed or {}).get("cover")
    if cover is not None and not cover.get("body"):
        return "cover needs a `body:` list of paragraphs, or drop the cover block."
    (path(slug) / "tailored.yaml").write_text(text, encoding="utf-8")
    return None


def render(slug):
    """Run cv/render.py. Returns (ok, combined output)."""
    if not exists(slug):
        return False, "No tailored.yaml for %s yet." % slug
    try:
        proc = subprocess.run(
            [sys.executable, str(TOOLKIT / "render.py"), "applications/" + slug],
            capture_output=True, text=True, cwd=TOOLKIT, timeout=300)
    except subprocess.TimeoutExpired:
        return False, "render.py timed out after 5 minutes."
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return proc.returncode == 0, out or "render.py finished with no output."


def pdfs(slug):
    """The rendered PDFs for an application, newest first: [(kind, name, mtime)]."""
    folder = path(slug)
    if not folder.is_dir():
        return []
    out = []
    for p in sorted(folder.glob("*.pdf")):
        kind = "cover" if "Cover" in p.name else "cv"
        out.append({"kind": kind, "name": p.name,
                    "mtime": p.stat().st_mtime,
                    "size_kb": round(p.stat().st_size / 1024)})
    return sorted(out, key=lambda d: d["kind"])
