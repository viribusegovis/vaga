"""Search LinkedIn for jobs worth applying to, and rank them against the CV.

    python scout.py                     run every search in searches.yaml
    python scout.py --only pt-dotnet    run one search
    python scout.py --all               include postings seen on an earlier run
    python scout.py --no-llm            skip the description-reading pass

Reads the saved searches and rules from searches.yaml, pulls cards from
LinkedIn's public jobs-guest endpoint, drops anything hitting a hard filter,
scores the rest against cv/master.yaml, and writes results.md.

The endpoint is the fragment LinkedIn serves to logged-out visitors. It is
undocumented, rate-limited, and can change shape without notice, so every
selector below is best-effort and a parse miss degrades to a lower-confidence
score rather than a crash.
"""

import argparse
import json
import re
import sys
import time
import unicodedata
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode

import httpx
import yaml
from lxml import html

import enrich
import store

# Job titles are Portuguese, Hungarian, German… and Windows consoles default to
# cp1252, where printing one of those characters raises UnicodeEncodeError and
# kills the run. The web UI reads this stdout, so a crash here is a failed fetch.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = store.ROOT
MASTER = store.CV / "master.yaml"
CONFIG = store.CONFIG
RESULTS = store.DATA / "results.md"

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"

# LinkedIn's filter codes. Measured against the guest endpoint: f_TPR genuinely
# filters (any -> 191-day-old posts, 24h -> 1 day, 7d -> 6), but f_E and f_WT are
# ignored — entry/mid-senior/executive return identical results, and so do
# remote/hybrid/onsite. They are still sent in case that changes, but nothing
# may depend on them: seniority and work mode are decided here, not by LinkedIn.
TPR = {"24h": "r86400", "7d": "r604800", "30d": "r2592000", "any": ""}
EXPERIENCE = {
    "internship": "1", "entry": "2", "associate": "3",
    "mid-senior": "4", "director": "5", "executive": "6",
}
WORK_TYPE = {"onsite": "1", "remote": "2", "hybrid": "3"}

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


# --------------------------------------------------------------------- helpers

def fold(s):
    """Lowercase and strip accents, so 'Santarem' matches 'Santarém'."""
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def term_re(term):
    """Word-boundary match that survives '.NET', 'C#', 'C++', 'Node.js'."""
    return re.compile(r"(?<![a-z0-9+#.])" + re.escape(fold(term)) + r"(?![a-z0-9+#])")


def text_of(el):
    return re.sub(r"\s+", " ", el.text_content()).strip() if el is not None else ""


def first(doc, xpath):
    found = doc.xpath(xpath)
    return found[0] if found else None


# --------------------------------------------------------------------- fetching

class LinkedIn:
    """Thin client over the jobs-guest endpoints, with backoff on 429."""

    def __init__(self, cfg):
        self.delay = cfg.get("delay", 1.5)
        self.detail_delay = cfg.get("detail_delay", 1.0)
        self.retries = cfg.get("retries", 3)
        self.client = httpx.Client(
            timeout=cfg.get("timeout", 20),
            follow_redirects=True,
            headers={"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9,pt;q=0.8"},
        )
        self.blocked = False

    def get(self, url):
        wait = self.delay
        for _ in range(self.retries):
            try:
                r = self.client.get(url)
            except httpx.HTTPError as e:
                print("    ! %s, retrying" % type(e).__name__, file=sys.stderr)
                wait *= 2
                time.sleep(wait)
                continue
            if r.status_code == 200:
                time.sleep(self.delay)
                return r.text
            if r.status_code == 429:
                # Rate limited. Back off hard; LinkedIn stays annoyed a while.
                wait *= 3
                print("    ! 429, sleeping %.0fs" % wait, file=sys.stderr)
                time.sleep(wait)
                continue
            if r.status_code in (400, 404):
                return None
            print("    ! HTTP %s" % r.status_code, file=sys.stderr)
            wait *= 2
            time.sleep(wait)
        self.blocked = True
        return None

    def search(self, spec, pages):
        params = {
            "keywords": spec["keywords"],
            "location": spec.get("location", ""),
            "sortBy": "DD",
        }
        tpr = TPR.get(str(spec.get("posted_within", "7d")))
        if tpr:
            params["f_TPR"] = tpr
        levels = spec.get("experience")
        if levels:
            params["f_E"] = ",".join(EXPERIENCE[x] for x in levels if x in EXPERIENCE)
        modes = spec.get("work_type")
        if modes:
            params["f_WT"] = ",".join(WORK_TYPE[m] for m in modes if m in WORK_TYPE)

        # The endpoint pages by whatever it feels like — 10 at a time in
        # practice, not the 25 the URL implies. Stepping by a hardcoded 25 both
        # skipped results 10-24 of every query and tripped a "fewer than 25 means
        # last page" check on page one, so every search only ever returned its
        # first 10 hits and `pages` did nothing. Step by what actually came back.
        out, seen_ids = [], set()
        start = 0
        for _ in range(pages):
            params["start"] = start
            body = self.get(SEARCH_URL + "?" + urlencode(params))
            if not body or not body.strip():
                break
            cards = parse_cards(body)
            if not cards:
                break
            fresh = [c for c in cards if c["id"] not in seen_ids]
            if not fresh:
                break       # the endpoint is repeating itself: no more results
            seen_ids.update(c["id"] for c in fresh)
            out.extend(fresh)
            start += len(cards)
        return out

    def detail(self, job_id):
        # get() already paced itself; detail_delay is an *extra* pause on top,
        # so it defaults to 0. Raise it only if LinkedIn starts 429ing.
        body = self.get(DETAIL_URL.format(job_id))
        if self.detail_delay:
            time.sleep(self.detail_delay)
        return parse_detail(body) if body else {}


def parse_cards(fragment):
    """Pull job records out of one jobs-guest results fragment.

    The endpoint returns bare <li> elements, not a document, so they get wrapped
    before parsing. Every selector is best-effort: LinkedIn changes these class
    names without notice, and a miss should cost one field, not the whole run.
    """
    doc = html.fromstring("<ul>" + fragment + "</ul>")
    jobs = []
    for card in doc.xpath("//*[@data-entity-urn]"):
        job_id = card.get("data-entity-urn", "").rsplit(":", 1)[-1]
        if not job_id.isdigit():
            continue
        link = first(card, ".//a[contains(@class,'base-card__full-link')]/@href")
        posted = first(card, ".//time/@datetime")
        # LinkedIn lazy-loads logos, so the real URL is in data-delayed-url and
        # src is a grey placeholder.
        logo = first(card, ".//img[contains(@class,'artdeco-entity-image')]/@data-delayed-url")
        company_url = first(card, ".//*[contains(@class,'base-search-card__subtitle')]//a/@href")
        jobs.append({
            "id": job_id,
            "logo": logo or "",
            "company_url": (company_url or "").split("?")[0],
            "title": text_of(first(card, ".//*[contains(@class,'base-search-card__title')]")),
            "company": text_of(first(card, ".//*[contains(@class,'base-search-card__subtitle')]")),
            "location": text_of(first(card, ".//*[contains(@class,'job-search-card__location')]")),
            "posted": posted or "",
            "url": (link or "https://www.linkedin.com/jobs/view/" + job_id).split("?")[0],
        })
    return jobs


# LinkedIn writes descriptions with a small, well-behaved tag set. Keeping it
# means the UI can show the poster's own headings and bullet lists instead of
# one flattened wall of text; everything outside the set is unwrapped, and all
# attributes are dropped, so no styling, scripts or tracking pixels come along.
KEEP_TAGS = {"p", "ul", "ol", "li", "strong", "em", "b", "i", "br", "h1", "h2",
             "h3", "h4", "h5", "h6", "blockquote"}


def clean_html(el):
    """Whitelist an element's markup and return its inner HTML."""
    from lxml import etree
    # Unwrapping these would keep their text; they have to go entirely.
    for e in el.xpath(".//script | .//style | .//noscript | .//iframe"):
        e.getparent().remove(e)
    present = {e.tag for e in el.iter() if isinstance(e.tag, str)}
    drop = present - KEEP_TAGS - {el.tag}
    if drop:
        etree.strip_tags(el, *drop)
    for e in el.iter():
        if isinstance(e.tag, str):
            e.attrib.clear()
    inner = (el.text or "") + "".join(
        html.tostring(c, encoding="unicode") for c in el)
    # LinkedIn pads with <p><br></p> for spacing; collapse the runs.
    inner = re.sub(r"(?:<p>\s*<br\s*/?>\s*</p>\s*)+", "", inner)
    return re.sub(r"\s+", " ", inner).strip()


def parse_detail(body):
    doc = html.fromstring(body)
    desc = first(doc, "//*[contains(@class,'show-more-less-html__markup')]")
    if desc is None:
        desc = first(doc, "//*[contains(@class,'description__text')]")
    criteria = {}
    for item in doc.xpath("//*[contains(@class,'description__job-criteria-item')]"):
        k = text_of(first(item, ".//*[contains(@class,'description__job-criteria-subheader')]"))
        v = text_of(first(item, ".//*[contains(@class,'description__job-criteria-text')]"))
        if k:
            criteria[fold(k)] = v
    return {
        "description": text_of(desc),                       # plain, for the LLM
        "description_html": clean_html(desc) if desc is not None else "",
        "seniority": criteria.get("seniority level", ""),
        "employment_type": criteria.get("employment type", ""),
        "job_function": criteria.get("job function", ""),
    }


# ---------------------------------------------------------------------- judging

def hard_reject(job, rules, commute=None):
    """Return the reason this job is not worth scoring, or None."""
    title = fold(job.get("title", ""))
    for term in rules.get("title_terms", []):
        if term_re(term).search(title):
            return "title says %r" % term

    llm = job.get("llm") or {}

    # Where the model has read the posting its answer wins: the regex cannot
    # tell "5 years required" from "5 years preferred", and it only knows the
    # English phrasings. Fall back to the regex when no facts were extracted.
    years = llm.get("years_required")
    if years is not None:
        if years >= rules.get("max_years", 5) and llm.get("years_are_hard_requirement"):
            return "requires %d years" % years
    else:
        desc = fold(job.get("description", ""))
        for pattern in rules.get("description_patterns", []):
            m = re.search(pattern, desc, re.I)
            if m:
                return "wants " + m.group(0).strip()

    speaks = {fold(x) for x in rules.get("languages_spoken", [])}
    for want in llm.get("languages_required", []):
        if speaks and fold(want) not in speaks:
            return "needs " + want

    days = llm.get("onsite_days_per_week")
    if days is not None and days > rules.get("max_onsite_days", 3):
        return "%d days a week on-site" % days

    # Outside a country you can travel in daily, only fully remote is possible.
    # LinkedIn's remote filter is not reliable — a search asking for remote in
    # the EU returned a hybrid role in Budapest — so this checks the posting
    # itself rather than trusting the search that found it.
    countries = rules.get("commutable_countries") or []
    if commute and countries and not locate(job.get("location", ""),
                                            commute, countries)[1]:
        # Abroad. Only remote work is possible at all...
        if job.get("mode") in ("hybrid", "onsite"):
            return "%s, and not commutable from home" % job["mode"]
        # ...and "remote" abroad usually means remote *from that country*.
        # A posting advertised as "Remote — Bulgaria" still wants you living in
        # Bulgaria, so it has to actually permit working from home before it
        # counts. Anything that does not say is treated as not permitting it.
        scope = llm.get("remote_scope", "unclear")
        allowed = [fold(c) for c in llm.get("remote_countries", [])]
        home = [fold(c) for c in countries]
        if scope == "listed" and not any(c in allowed for c in home):
            where = ", ".join(llm.get("remote_countries") or []) or "elsewhere"
            return "remote, but only from " + where
        if scope == "unclear":
            return "abroad, and does not say it can be done from %s" % countries[0]

    # Only pure on-site is a problem; remote/hybrid in the same city is fine.
    if job.get("mode") == "onsite":
        loc = fold(job.get("location", ""))
        for city in rules.get("onsite_cities", []):
            if fold(city) in loc:
                return "on-site in " + city
    return None


def infer_mode(job):
    """Best available reading of where the work happens.

    The model's answer counts only when it can quote the posting for it: asked
    without evidence it will commit to "onsite" from silence, which is how a
    genuinely remote role got rejected for the city its employer sits in. Otherwise fall back to
    the keyword scan, and finally to "unknown".

    There is deliberately no LinkedIn-workplace fallback here. The guest
    endpoint ignores the f_WT filter entirely — remote, hybrid and onsite return
    byte-identical results — so any workplace value derived from it is invented.
    """
    llm = job.get("llm") or {}
    mode = llm.get("work_mode", "unclear")
    if mode in ("remote", "hybrid", "onsite") and llm.get("work_mode_evidence"):
        return mode
    if mode in ("remote", "hybrid", "onsite"):
        return mode
    blob = fold("%s %s %s" % (job.get("title", ""), job.get("location", ""),
                              job.get("description", "")))
    if re.search(r"\bhybrid\b|\bhibrido\b", blob):
        return "hybrid"
    if re.search(r"\b(remote|remoto|teletrabalho|work from home)\b", blob):
        return "remote"
    if re.search(r"\bon[- ]?site\b|\bpresencial\b|\bin[- ]office\b", blob):
        return "onsite"
    return "unknown"


def locate(location, table, countries=()):
    """Return (commute factor, whether daily travel there is possible at all).

    Locations are read left to right, so 'Porto, Portugal' matches Porto and not
    Portugal — LinkedIn writes them most-specific-first, and matching the
    longest key instead would hand a Porto job the country-wide default.

    Two things decide "reachable". A city named in the commute table is
    reachable by definition, which matters because LinkedIn writes plenty of
    locations without a country at all — 'Lisbon Metropolitan Area', 'Greater
    Braga Area'. Otherwise the country has to be one of `countries`. Anything
    else is abroad, and must not score like an unlisted town at home:
    'Budapest, Hungary' was reaching the top of the list on `unknown_city`.
    """
    if not (location or "").strip():
        # No location at all is unknown, not foreign. Dropping a posting for a
        # field LinkedIn simply did not fill in would be the wrong call.
        return table.get("unknown_city", 0.5), True
    for segment in fold(location).split(","):
        segment = segment.strip()
        best = None
        for city, factor in table.items():
            if city in ("unknown_city", "abroad"):
                continue
            if fold(city) in segment and (best is None or len(city) > best[0]):
                best = (len(city), factor)
        if best:
            return best[1], True
    if not countries or any(fold(c) in fold(location) for c in countries):
        return table.get("unknown_city", 0.5), True
    return table.get("abroad", 0.1), False


def commute_factor(location, table, countries=()):
    """How reachable a location is, 0-1, from the commute table in searches.yaml.

    Thin wrapper over locate(), which also returns the matched key; scoring only
    wants the number. See locate() for the left-to-right matching rule.
    """
    return locate(location, table, countries)[0]


def score(job, skills, cfg, countries=()):
    """Rank one posting 0-100 against master.yaml. Returns (score, reasons).

    Four weighted components, all normalised to 0-1 before weighting, except
    seniority which is signed (-1 to 1) so a mid-senior posting actively loses
    points instead of merely failing to gain them.

    Deterministic and cheap: it runs per request, so changing a weight in
    Preferences and reloading re-ranks instantly without refetching anything.
    The LLM never scores — its extracted facts only override the regex guesses
    here, which is what keeps a ranking reproducible and auditable. `reasons` is
    what the "Why 72" panel shows.
    """
    w = cfg["weights"]
    tier_points = cfg["skill_tier_points"]
    blob = fold("%s %s" % (job.get("title", ""), job.get("description", "")))
    llm = job.get("llm") or {}
    reasons = []

    # Skills, tiered: a .NET match counts for more than a Kafka mention.
    hits, points = [], 0
    for tier, names in skills.items():
        for name in names:
            if term_re(name).search(blob):
                hits.append(name)
                points += tier_points.get(tier, 1)
    skills_c = min(points / 18, 1.0)
    if hits:
        reasons.append("matches " + ", ".join(hits[:8]) + ("..." if len(hits) > 8 else ""))

    # Seniority: boost terms, minus LinkedIn's own mid-senior label.
    # Signed, not clamped at zero: a mid-senior posting should lose points, not
    # merely fail to gain them, or a long tech-stack list carries it to the top.
    boost = sum(p for t, p in cfg["boost_terms"].items() if term_re(t).search(blob))
    if fold(job.get("seniority", "")).startswith("mid"):
        boost -= cfg.get("mid_senior_penalty", 8)
    if "junior_suitable" in llm:
        boost += cfg.get("junior_suitable_bonus", 6) * (1 if llm["junior_suitable"] else -2)
    seniority_c = max(-1.0, min(boost / 12, 1.0))
    if boost > 0:
        reasons.append("junior-facing wording")
    elif boost < 0:
        reasons.append("listed as mid-senior")

    # Work mode, discounted by how bad the commute is.
    mode = job.get("mode", "unknown")
    factor = 1.0 if mode == "remote" else commute_factor(job.get("location", ""),
                                                        cfg["commute"], countries)
    mode_c = cfg["mode_weight"].get(mode, 0.6) * factor
    days = llm.get("onsite_days_per_week")
    if days:
        # A stated number of office days beats the mode label: two days in
        # Lisbon is fine, three is workable, four is not.
        mode_c *= cfg.get("onsite_day_factor", {}).get(days, 0.3)
        reasons.append("%s, %d day/wk on-site, commute %.2f" % (mode, days, factor))
    else:
        reasons.append("%s, commute %.2f" % (mode, factor))

    # Freshness: linear decay over 30 days. An unknown date scores neutral, not
    # full marks — otherwise a posting whose date failed to parse quietly
    # outranks one that is genuinely fresh.
    days = job.get("age_days")
    fresh_c = 0.5 if days is None else max(0.0, 1 - days / 30)

    total = (
        w["skills"] * skills_c
        + w["seniority"] * seniority_c
        + w["work_mode"] * mode_c
        + w["freshness"] * fresh_c
    ) / sum(w.values()) * 100
    return max(0, round(total)), reasons


def age_days(posted):
    try:
        d = datetime.fromisoformat(posted).date()
    except (ValueError, TypeError):
        return None
    return max(0, (date.today() - d).days)


# ----------------------------------------------------------------------- output

def slug(job):
    """A stable folder name for cv/applications, e.g. 2026-08-acme-junior-dotnet.

    Dated from when the posting was first seen, not from today. Using today's
    date meant every slug silently changed at the turn of the month, orphaning
    the application folder that had already been written for it — the CV was
    still on disk, but the app no longer believed it existed.
    """
    def clean(s, n):
        return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", fold(s))).strip("-")[:n]
    when = (job.get("first_seen") or date.today().isoformat())[:7]
    stem = "%s-%s-%s" % (when, clean(job.get("company", ""), 20),
                         clean(job.get("title", ""), 30))
    return stem.strip("-")


def entry(job):
    bits = [
        "### %s - %s" % (job["score"], job.get("title", "?")),
        "",
        "**%s** - %s%s" % (
            job.get("company", "?"), job.get("location", ""),
            "" if job.get("age_days") is None else " - posted %dd ago" % job["age_days"]),
        "",
        job.get("url", ""),
        "",
        "- " + "\n- ".join(job["reasons"]),
    ]
    if job.get("employment_type"):
        extra = ", " + job["seniority"] if job.get("seniority") else ""
        bits.append("- " + job["employment_type"] + extra)
    llm = job.get("llm") or {}
    if llm.get("salary"):
        bits.append("- pay: " + llm["salary"])
    if llm.get("summary"):
        bits.append("- " + llm["summary"])
    if not job.get("description"):
        bits.append("- *description not fetched, score is title-only; check manually*")
    elif not llm:
        bits.append("- *not read by the model; scored on keywords alone*")
    bits.append("")
    bits.append("`applications/%s`  <- folder name for the tailor step" % slug(job))
    return "\n".join(bits)


def write_results(kept, rejected, cutoff, blocked):
    good = [j for j in kept if j["score"] >= cutoff]
    borderline = [j for j in kept if j["score"] < cutoff]
    lines = [
        "# Job scout - " + datetime.now().strftime("%Y-%m-%d %H:%M"),
        "",
        "%d worth a look, %d borderline, %d filtered out."
        % (len(good), len(borderline), len(rejected)),
        "",
    ]
    if blocked:
        lines += ["> **Rate-limited partway through** - this run is incomplete. "
                  "Wait an hour and rerun.", ""]
    if good:
        lines += ["## Worth a look", ""] + [entry(j) + "\n" for j in good]
    if borderline:
        lines += ["## Borderline", ""] + [entry(j) + "\n" for j in borderline]
    if rejected:
        lines += ["## Filtered out", ""]
        lines += ["- **%s** - %s - _%s_" % (t, c, r) for t, c, r in rejected]
        lines.append("")
    RESULTS.write_text("\n".join(lines), encoding="utf-8")


# ------------------------------------------------------------------------- main

def judge(jobs, ids, cfg, skills):
    """Filter and score the given job ids. Returns (kept, rejected)."""
    kept, rejected = [], []
    for jid in ids:
        job = jobs[jid]
        job["mode"] = infer_mode(job)
        job["age_days"] = age_days(job.get("posted", ""))
        reason = hard_reject(job, cfg["reject"], cfg["scoring"]["commute"])
        if reason:
            job["rejected"] = reason
            rejected.append((job.get("title", "?"), job.get("company", "?"), reason))
            continue
        job.pop("rejected", None)
        job["score"], job["reasons"] = score(
            job, skills, cfg["scoring"], cfg["reject"].get("commutable_countries") or [])
        kept.append(job)
    kept.sort(key=lambda j: j["score"], reverse=True)
    return kept, rejected


def main():
    ap = argparse.ArgumentParser(description="LinkedIn job scout")
    ap.add_argument("--only", metavar="ID", help="run just this search")
    ap.add_argument("--all", action="store_true", help="include already-seen postings")
    ap.add_argument("--no-llm", action="store_true", help="skip the description pass")
    args = ap.parse_args()

    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    skills = yaml.safe_load(MASTER.read_text(encoding="utf-8"))["skills"]
    jobs = store.load()

    searches = cfg["searches"]
    if args.only:
        searches = [s for s in searches if s["id"] == args.only]
        if not searches:
            sys.exit("no search with id %r" % args.only)

    li = LinkedIn(cfg["fetch"])
    found = {}
    for spec in searches:
        print("  %s: %s in %s" % (spec["id"], spec["keywords"], spec.get("location", "")))
        for job in li.search(spec, spec.get("pages", 2)):
            found.setdefault(job["id"], dict(job, via=spec["id"]))
        print("    %d unique so far" % len(found), flush=True)

    fresh = store.merge(jobs, found)
    print("")
    print("%d found, %d new" % (len(found), len(fresh)), flush=True)

    # Descriptions, for the new ones only — the fetch budget is the scarce bit.
    budget = cfg["fetch"]["max_details"]
    wanted = [jid for jid in sorted(fresh, key=lambda j: jobs[j].get("posted", ""),
                                    reverse=True)
              # Skip the fetch when the title alone already disqualifies it.
              if not hard_reject(jobs[jid],
                                 {"title_terms": cfg["reject"].get("title_terms", [])})]
    wanted = wanted[:budget]
    for n, jid in enumerate(wanted, 1):
        jobs[jid].update(li.detail(jid))
        print("  description %d/%d" % (n, len(wanted)), flush=True)
        # Save as we go: the web UI reads jobs.json live, so postings appear
        # while the rest are still downloading instead of all at the end.
        if n % 5 == 0:
            store.save(jobs)
    store.save(jobs)

    llm_cfg = cfg.get("llm", {})
    if llm_cfg.get("enabled") and not args.no_llm:
        enrich.run(jobs, llm_cfg, save=lambda: store.save(jobs))

    todo = list(jobs) if args.all else fresh
    kept, rejected = judge(jobs, todo, cfg, skills)
    write_results(kept, rejected, cfg["scoring"]["interesting_at"], li.blocked)
    store.save(jobs)

    above = sum(1 for j in kept if j["score"] >= cfg["scoring"]["interesting_at"])
    print("%d worth a look, %d borderline, %d filtered -> %s"
          % (above, len(kept) - above, len(rejected), RESULTS.name))


if __name__ == "__main__":
    main()
