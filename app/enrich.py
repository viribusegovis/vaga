"""Read job descriptions with a local LLM and pull out the facts scoring needs.

    python enrich.py              enrich every cached job that has no facts yet
    python enrich.py --redo       re-read jobs already enriched
    python enrich.py --limit 10   stop after N

Extraction, not judgement. The model returns structured facts; scout.py's
deterministic scorer decides what they are worth. Asking a model to rate a job
0-100 gives numbers that wobble between runs and cannot be audited — this way a
ranking is always explainable, and a model that is offline or returns garbage
leaves the run scoring exactly as it did before, on regex alone.

Needs Ollama running locally:  https://ollama.com/download
Then pull the model named in searches.yaml under `llm.model`.
"""

import argparse
import json
import re
import sys
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor

import httpx
import yaml

import store

# Job titles are Portuguese, Hungarian, German… and Windows consoles default to
# cp1252, where printing one of those characters raises UnicodeEncodeError and
# kills the run. The web UI reads this stdout, so a crash here is a failed fetch.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

CONFIG = store.CONFIG

# Kept narrow on purpose. Every field is something the scorer or a hard filter
# actually consumes; anything else is a nicety the model can get wrong for free.
SCHEMA = {
    "type": "object",
    "properties": {
        "work_mode": {"type": "string", "enum": ["remote", "hybrid", "onsite", "unclear"]},
        # "Remote" usually comes with a catch: remote *from where*. A posting
        # advertised as "Remote — Bulgaria" wants you living in Bulgaria, which
        # is useless from Portugal, and nothing else in this schema can tell
        # that apart from genuinely location-free remote work.
        # The exact words that establish work_mode, so a reading can be told
        # from a guess. Empty means the posting never says, and the caller
        # falls back to LinkedIn's own workplace filter instead.
        "work_mode_evidence": {"type": "string"},
        "remote_scope": {"type": "string",
                         "enum": ["anywhere", "eu_eea", "listed", "unclear"]},
        "remote_countries": {"type": "array", "items": {"type": "string"}},
        "onsite_days_per_week": {"type": ["integer", "null"], "minimum": 0, "maximum": 5},
        "years_required": {"type": ["integer", "null"], "minimum": 0, "maximum": 30},
        "years_are_hard_requirement": {"type": "boolean"},
        "junior_suitable": {"type": "boolean"},
        "languages_required": {"type": "array", "items": {"type": "string"}},
        "contract_type": {"type": "string",
                          "enum": ["permanent", "contract", "internship", "unclear"]},
        "salary": {"type": ["string", "null"]},
        "summary": {"type": "string"},
    },
    "required": ["work_mode", "work_mode_evidence", "remote_scope", "remote_countries",
                 "onsite_days_per_week", "years_required",
                 "years_are_hard_requirement", "junior_suitable",
                 "languages_required", "contract_type", "salary", "summary"],
}

PROMPT = """You are extracting facts from a job posting. Report only what the \
posting states. Do not infer, do not flatter the posting, do not guess.

Rules:
- work_mode: "remote" only if the posting says fully remote. "hybrid" if it \
mixes office and home. "onsite" if it requires being in the office full time. \
"unclear" if it genuinely does not say. Do not assume onsite from silence.
- work_mode_evidence: the exact phrase from the posting that establishes work_mode, copied verbatim and at most 15 words — "fully remote", "regime híbrido", "based in our Porto office", "2 days per week on site". If the posting never states where the work happens, return an empty string and set work_mode to "unclear". Do not fill this with a guess or a paraphrase: an empty string here is the correct, useful answer for a posting that is silent.
- remote_scope: where the person must live. Decide in this order, and stop at the first that applies:
  (a) "anywhere" — the text says you may work from any country, anywhere in the world, or that the company is globally distributed with no location requirement.
  (b) "eu_eea" — the text says anywhere in the EU, the EEA, or Europe. This wins over the posting's own city: a role listed in Berlin that says "work from anywhere in the European Union" is "eu_eea", NOT "listed".
  (c) "listed" — particular countries are required: living in, residing in, based in, or legally able to work in them. This includes the case where the only signal is the posting's location, e.g. "Remote - Poland", or a remote posting whose only location is Sofia.
  (d) "unclear" — none of the above, or the role is not remote at all.
  Only consider the posting's location under (c). If the text names a region, that region is the answer.
- remote_countries: the countries the person must live in, when remote_scope is "listed". Use the country name, e.g. ["Bulgaria"]. Empty otherwise.
- onsite_days_per_week: only if a number of office days is stated, else null.
- years_required: the minimum years of professional experience asked for, else null.
- years_are_hard_requirement: true if phrased as required/mandatory/minimum. \
false if phrased as preferred, a plus, nice to have, or desirable.
- junior_suitable: true if a candidate with roughly one year of professional \
experience plus internships could plausibly be hired. Graduate programmes, \
trainee roles and "junior" titles are true. Roles wanting an experienced \
engineer are false.
- languages_required: human languages the posting requires. Look for them in \
whatever language the posting itself is written in — "Deutschkenntnisse" or \
"sehr gute Deutsch- und Englischkenntnisse" mean German, "domínio do português" \
and "bom nível de inglês" mean Portuguese and English, "maîtrise du français" \
means French. Include a language whenever fluency, good knowledge or working \
proficiency in it is asked for, even inside a requirements or benefits list. \
Answer with English names, e.g. ["German", "English"]. Never list programming \
languages. Empty list only if the posting really asks for none.
- salary: the stated pay, verbatim, or null. Most postings state none.
- summary: one sentence, max 25 words, on what the role actually involves.

Posting title: {title}
Company: {company}
Location: {location}

Description:
{description}

Return JSON only."""


def facts(client, cfg, job):
    """Ask the model about one posting. Returns the facts dict, or None."""
    desc = (job.get("description") or "").strip()
    if len(desc) < 120:
        return None     # nothing to read; leave it to the regexes

    prompt = PROMPT.format(
        title=job.get("title", ""), company=job.get("company", ""),
        location=job.get("location", ""), description=desc[:cfg.get("max_chars", 6000)],
    )
    body = {
        "model": cfg["model"],
        "messages": [{"role": "user", "content": prompt}],
        "format": SCHEMA,          # Ollama constrains decoding to the schema
        "stream": False,
        # Reasoning-capable models otherwise spend ~1300 tokens thinking before
        # answering — a 5x slowdown for no accuracy gain on schema-bound
        # extraction. Measured on granite4.2:3b: 21.9s -> 4.1s per description.
        "think": False,
        "options": {"temperature": 0, "num_ctx": cfg.get("num_ctx", 8192)},
    }
    url = cfg.get("host", "http://localhost:11434") + "/api/chat"
    try:
        r = client.post(url, json=body)
        if r.status_code == 400:
            # Model has no thinking mode; the parameter is not accepted.
            body.pop("think")
            r = client.post(url, json=body)
        r.raise_for_status()
        raw = r.json()["message"]["content"]
    except httpx.HTTPError as e:
        print("    ! ollama: %s" % e, file=sys.stderr)
        return None
    except (KeyError, ValueError) as e:
        print("    ! bad ollama response: %s" % e, file=sys.stderr)
        return None

    try:
        out = json.loads(raw)
    except ValueError:
        # Schema-constrained decoding should prevent this, but small models
        # sometimes wrap the object in prose. Salvage the outermost braces.
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return None
        try:
            out = json.loads(m.group(0))
        except ValueError:
            return None
    return check_evidence(sane(out), desc)


# Only these count as a language requirement, and every alias maps to one
# canonical English name. Two reasons this is a whitelist rather than a
# blocklist of programming languages:
#   - models leak "C#" and "TypeScript" into this field despite the prompt
#     forbidding it (seen on gemma4:e2b-it-qat), and an unrecognised value here
#     hard-rejects the posting for a language you supposedly do not speak;
#   - a Portuguese posting says "Português", which must match the "Portuguese"
#     in searches.yaml `languages_spoken` or it rejects itself.
LANGUAGES = {
    "portuguese": "Portuguese", "portugues": "Portuguese", "português": "Portuguese",
    "english": "English", "ingles": "English", "inglês": "English",
    "spanish": "Spanish", "espanhol": "Spanish", "español": "Spanish",
    "french": "French", "frances": "French", "français": "French",
    "german": "German", "alemao": "German", "alemão": "German", "deutsch": "German",
    "italian": "Italian", "italiano": "Italian",
    "dutch": "Dutch", "neerlandes": "Dutch", "nederlands": "Dutch",
    "polish": "Polish", "romanian": "Romanian", "russian": "Russian",
    "ukrainian": "Ukrainian", "czech": "Czech", "swedish": "Swedish",
    "danish": "Danish", "norwegian": "Norwegian", "finnish": "Finnish",
    "greek": "Greek", "turkish": "Turkish", "arabic": "Arabic",
    "hebrew": "Hebrew", "hindi": "Hindi", "mandarin": "Mandarin",
    "chinese": "Mandarin", "japanese": "Japanese", "korean": "Korean",
    "catalan": "Catalan", "galician": "Galician",
}


def languages(raw):
    """Keep only real human languages, under one canonical spelling each."""
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, str):
            continue
        # "Fluent English (mandatory)" -> tokens -> "english" -> "English"
        for word in re.split(r"[^\w]+", item.lower()):
            if word in LANGUAGES:
                out.append(LANGUAGES[word])
                break
    return sorted(set(out))[:6]


def fold(text):
    """Lowercase and strip accents. Local copy: scout imports this module."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def quoted_in(phrase, description):
    """Is this phrase really in the posting, ignoring case, accents and spacing?"""
    if not phrase:
        return False
    squash = lambda t: re.sub(r"[^a-z0-9]+", " ", fold(t)).strip()
    return squash(phrase) in squash(description)


def check_evidence(facts_out, description):
    """Drop a work_mode the model could not actually quote the posting for.

    Granite invented "based in our Porto office" for two Sovos postings whose
    text says nothing of the sort, and claimed "onsite" for a third with no
    quote at all. Both would have hard-rejected genuinely remote jobs on their
    city. An unverifiable claim is downgraded to "unclear", which sends
    infer_mode to LinkedIn's own workplace filter instead.
    """
    if facts_out is None:
        return None
    evidence = facts_out.get("work_mode_evidence", "")
    if evidence and not quoted_in(evidence, description):
        facts_out["work_mode_evidence"] = ""
        facts_out["work_mode_fabricated"] = evidence[:120]
    if not facts_out.get("work_mode_evidence"):
        facts_out["work_mode"] = "unclear"
    return facts_out


def sane(out):
    """Clamp the model's answer to values the scorer can trust."""
    if not isinstance(out, dict):
        return None
    mode = str(out.get("work_mode", "unclear")).lower()
    days = out.get("onsite_days_per_week")
    years = out.get("years_required")
    scope = str(out.get("remote_scope", "unclear")).lower()
    evidence = str(out.get("work_mode_evidence", "") or "").strip()[:120]
    return {
        "work_mode_evidence": evidence,
        "work_mode": mode if mode in ("remote", "hybrid", "onsite") else "unclear",
        "remote_scope": scope if scope in ("anywhere", "eu_eea", "listed") else "unclear",
        "remote_countries": [str(x) for x in out.get("remote_countries", [])
                             if isinstance(x, str)][:8],
        "onsite_days_per_week": days if isinstance(days, int) and 0 <= days <= 5 else None,
        "years_required": years if isinstance(years, int) and 0 <= years <= 30 else None,
        "years_are_hard_requirement": bool(out.get("years_are_hard_requirement")),
        "junior_suitable": bool(out.get("junior_suitable", True)),
        "languages_required": languages(out.get("languages_required")),
        "contract_type": str(out.get("contract_type", "unclear")).lower(),
        "salary": out.get("salary") if isinstance(out.get("salary"), str) else None,
        "summary": str(out.get("summary", ""))[:300],
    }


def available(cfg):
    """Is Ollama up with the configured model pulled?  (ok, message)"""
    host = cfg.get("host", "http://localhost:11434")
    try:
        r = httpx.get(host + "/api/tags", timeout=3)
        r.raise_for_status()
    except httpx.HTTPError:
        return False, "Ollama not reachable at %s — is it running?" % host
    names = [m.get("name", "") for m in r.json().get("models", [])]
    want = cfg["model"]
    if not any(n == want or n.startswith(want + ":") for n in names):
        return False, "model %r not pulled. Run: ollama pull %s\nHave: %s" % (
            want, want, ", ".join(names) or "nothing")
    return True, "ok"


def run(jobs, cfg, redo=False, limit=None, save=None):
    """Enrich cached jobs in place. Returns how many were enriched.

    Runs a few descriptions at once: Ollama serves concurrent requests, and
    measured on this machine four in parallel finish ~1.6x faster than four in
    sequence. `save` is called every few jobs so a long pass shows up in the UI
    as it goes rather than only at the end.
    """
    ok, msg = available(cfg)
    if not ok:
        print("  skipping LLM pass — " + msg, file=sys.stderr)
        return 0

    todo = [j for j in jobs.values()
            if j.get("description") and (redo or not j.get("llm"))]
    if limit:
        todo = todo[:limit]
    if not todo:
        return 0

    workers = max(1, int(cfg.get("workers", 3)))
    total = len(todo)
    print("  reading %d descriptions with %s (%d at a time)"
          % (total, cfg["model"], workers), flush=True)

    done = 0
    lock = threading.Lock()

    def one(job):
        nonlocal done
        with httpx.Client(timeout=cfg.get("timeout", 180)) as client:
            got = facts(client, cfg, job)
        with lock:
            done += 1
            if got:
                job["llm"] = got
            # Progress goes to stdout so the web UI can show it live.
            print("    [%d/%d] %s%s" % (done, total, job.get("title", "?")[:48],
                                        "" if got else "  (failed)"), flush=True)
            if save and done % max(1, workers) == 0:
                save()
        return bool(got)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, todo))
    if save:
        save()
    return sum(1 for r in results if r)


def main():
    ap = argparse.ArgumentParser(description="LLM extraction pass over cached jobs")
    ap.add_argument("--redo", action="store_true", help="re-read already-enriched jobs")
    ap.add_argument("--limit", type=int, help="stop after N jobs")
    args = ap.parse_args()

    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8")).get("llm", {})
    if not cfg.get("enabled"):
        sys.exit("llm.enabled is false in searches.yaml")
    jobs = store.load()
    n = run(jobs, cfg, redo=args.redo, limit=args.limit,
            save=lambda: store.save(jobs))
    store.save(jobs)
    print("enriched %d" % n)


if __name__ == "__main__":
    main()
