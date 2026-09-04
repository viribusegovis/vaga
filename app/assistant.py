"""A local assistant grounded in one job posting and the user's real profile.

Everything it knows comes from three places: `master.yaml` and
`profile.md` for what the user has actually done, the cached posting for what
the employer wants, and the current `tailored.yaml` for what this application
currently claims. The system prompt forbids going outside them.

That grounding is the whole point. A model asked to write a cover letter with no
facts to hand will invent plausible ones — a team size, a technology, an
achievement — and fluent fabrications in a real job application are exactly the
kind you don't catch by skimming. Here it can only recombine things that are
already true, and it is told to name gaps rather than paper over them.

Edits are proposed, never applied. When the model rewrites the application it
returns a whole `tailored.yaml` in a fenced block, and the UI offers a button to
apply it after validation.
"""

import json
import re

import httpx
import yaml

import store
import tailor

RULES = """You are helping {name} with one specific job application. \
You are careful, concrete and honest.

Ground rules, in order of importance:
1. Only ever claim experience, technologies, employers, dates or achievements \
that appear in the PROFILE below. If something is not there, it did not happen. \
Never invent a number, a team size, a metric or a project.
2. When the posting wants something they do not have, say so plainly and \
suggest the nearest true thing. Never paper over a gap.
3. Claim only the seniority the PROFILE supports. Do not write as if they have \
experience it does not show.
4. Write the way he does — plain, specific, European English, no corporate \
filler, no "passionate about leveraging synergies". Short sentences.
5. Keep replies brief unless asked to produce a document.

When asked to change the CV or cover letter, reply with a short explanation and \
then the COMPLETE new tailored.yaml inside a ```yaml fenced block. It overlays \
master.yaml: omit a section to inherit master's content, list ids to select and \
reorder, give a dict with an id to override fields. Only put skills in the \
`skills:` list that the PROFILE actually claims; anything they have not used \
goes under `familiar:`."""


def profile_text():
    """The user's real history: master.yaml plus the fuller reference."""
    master = yaml.safe_load((tailor.TOOLKIT / "master.yaml").read_text(encoding="utf-8"))
    master.pop("cover", None)          # cover boilerplate is not biography
    parts = ["=== PROFILE (the only facts you may use) ===",
             yaml.safe_dump(master, allow_unicode=True, sort_keys=False)]
    extra = tailor.TOOLKIT / "profile.md"
    if extra.exists():
        parts += ["=== FULLER REFERENCE (constraints, gaps, salary bands) ===",
                  extra.read_text(encoding="utf-8")]
    return "\n".join(parts)


def posting_text(job):
    """The posting as the model sees it: extracted facts, then the raw text.

    The facts block carries the score and its reasons, so the assistant and the
    jobs list can't disagree about what the posting says. Empty fields are
    dropped rather than sent as null: the raw description follows immediately
    below, so the model can still read what the extractor missed, and a wall of
    nulls only invites it to comment on them.

    Truncated at 7000 characters. Postings that long are boilerplate by the end
    — benefits, equal-opportunity statements — and the prompt is already ~6k
    tokens before the description is added.
    """
    llm = job.get("llm") or {}
    facts = {
        "title": job.get("title"), "company": job.get("company"),
        "location": job.get("location"), "url": job.get("url"),
        "work_mode": llm.get("work_mode"), "office_days": llm.get("onsite_days_per_week"),
        "years_required": llm.get("years_required"),
        "years_are_hard_requirement": llm.get("years_are_hard_requirement"),
        "junior_suitable": llm.get("junior_suitable"),
        "languages_required": llm.get("languages_required"),
        "salary": llm.get("salary"), "employment_type": job.get("employment_type"),
        "linkedin_seniority": job.get("seniority"),
        "vaga_score": job.get("score"), "why": job.get("reasons"),
    }
    return "\n".join([
        "=== THE POSTING ===",
        json.dumps({k: v for k, v in facts.items() if v not in (None, "", [])},
                   ensure_ascii=False, indent=1),
        "",
        "--- full text of the posting ---",
        (job.get("description") or "")[:7000],
    ])


def whose():
    """The name on the CV, so the prompt is not hardcoded to one person."""
    try:
        master = yaml.safe_load((tailor.TOOLKIT / "master.yaml").read_text(encoding="utf-8"))
        return master["profile"]["name"]
    except (OSError, KeyError, TypeError, yaml.YAMLError):
        return "the applicant"


def system_prompt(job, slug):
    """Assemble the grounding: rules, profile, posting, current application.

    Order matters. The rules go first so they survive a long context, and the
    tailored.yaml goes last because it's the thing most questions are about.

    This is ~6k tokens and rebuilding it is cheap, but making the model *read*
    it is not — hence the warm request the job page fires on open, which pushes
    the prefix through once so Ollama can cache it. Keep the leading blocks
    stable: changing them invalidates that cache and the next reply pays the
    cold cost again.
    """
    blocks = [RULES.format(name=whose()), "", profile_text(), "", posting_text(job)]
    current = tailor.read(slug)
    if current:
        blocks += ["", "=== CURRENT tailored.yaml FOR THIS APPLICATION ===", current]
    else:
        blocks += ["", "There is no tailored.yaml for this application yet."]
    return "\n".join(blocks)


# Buttons on the job page. The text is sent as if the user typed it.
PRESETS = [
    ("Cover letter (plain text)",
     "Write the cover letter for this role as plain text I can paste into a web "
     "form — no YAML, no markdown, no placeholders. Three or four short "
     "paragraphs. Open with the specific reason I fit this posting, be honest "
     "about being early-career, and name one thing I'd have to learn."),
    ("LinkedIn headline",
     "Give me three LinkedIn headline options aimed at this kind of role. Under "
     "220 characters each, no emoji, no buzzwords. Just the three lines."),
    ("LinkedIn About",
     "Write a LinkedIn About section aimed at roles like this one. First person, "
     "four short paragraphs, concrete about what I've actually built."),
    ("Why I fit",
     "Three bullets on why I fit this posting, each naming a specific thing I "
     "built and the technology, drawn only from my profile."),
    ("Gaps to expect",
     "What does this posting ask for that my profile does not cover? List each "
     "gap and the nearest true thing I could say about it in an interview."),
    ("Interview questions",
     "Ten questions this employer is likely to ask me for this specific role, "
     "and for each a one-line note on what I should draw on to answer."),
    ("Questions to ask them",
     "Eight questions worth asking them about this role, avoiding anything "
     "answered in the posting itself."),
    ("Tailor the CV",
     "Rewrite tailored.yaml for this posting: pick and order the experience, "
     "projects and skills that matter most here, retitle the profile, and write "
     "the cover body. Explain your choices briefly first, then give the complete "
     "file in a ```yaml block."),
]


def history(job):
    return job.get("chat") or []


def save_turn(job_id, role, content):
    jobs = store.load()
    if job_id not in jobs:
        return
    chat = jobs[job_id].get("chat") or []
    chat.append({"role": role, "content": content})
    # Keep the transcript bounded; the system prompt carries the real context.
    jobs[job_id]["chat"] = chat[-40:]
    store.save(jobs)


def clear(job_id):
    jobs = store.load()
    if job_id in jobs:
        jobs[job_id].pop("chat", None)
        store.save(jobs)


def warm(cfg, job, slug):
    """Push the system prompt through the model once, generating almost nothing.

    The prompt is ~6k tokens of profile and posting, which takes about three
    minutes to process on this GPU. Ollama caches the KV state for a prefix it
    has already seen, so paying that cost once in the background — while the
    page is still being read — turns the first real question from 178s into 8s.
    The prefix must match byte-for-byte, which is why this builds the prompt the
    same way `stream` does.
    """
    body = {
        "model": cfg.get("chat_model") or cfg["model"],
        "messages": [{"role": "system", "content": system_prompt(job, slug)},
                     {"role": "user", "content": "ok"}],
        "stream": False,
        "think": False,
        "keep_alive": cfg.get("keep_alive", "30m"),
        "options": {"temperature": 0, "num_ctx": cfg.get("chat_ctx", 16384),
                    "num_predict": 1},
    }
    url = cfg.get("host", "http://localhost:11434") + "/api/chat"
    with httpx.Client(timeout=cfg.get("chat_timeout", 600)) as client:
        r = client.post(url, json=body)
        if r.status_code == 400:
            body.pop("think")
            r = client.post(url, json=body)
        r.raise_for_status()
    return True


def stream(cfg, job, slug, question):
    """Yield reply chunks from Ollama. Raises httpx.HTTPError if it can't talk."""
    messages = [{"role": "system", "content": system_prompt(job, slug)}]
    messages += history(job)
    messages.append({"role": "user", "content": question})

    body = {
        "model": cfg.get("chat_model") or cfg["model"],
        "messages": messages,
        "stream": True,
        "think": False,
        # Keep the model and its cached prefix resident between questions.
        "keep_alive": cfg.get("keep_alive", "30m"),
        "options": {
            "temperature": 0.4,          # a little room, this is prose not extraction
            "num_ctx": cfg.get("chat_ctx", 16384),
        },
    }
    url = cfg.get("host", "http://localhost:11434") + "/api/chat"
    with httpx.Client(timeout=cfg.get("chat_timeout", 600)) as client:
        with client.stream("POST", url, json=body) as r:
            if r.status_code == 400:
                # Model has no thinking mode; retry without the flag.
                body.pop("think")
                with client.stream("POST", url, json=body) as r2:
                    r2.raise_for_status()
                    for line in r2.iter_lines():
                        chunk = _piece(line)
                        if chunk:
                            yield chunk
                    return
            r.raise_for_status()
            for line in r.iter_lines():
                chunk = _piece(line)
                if chunk:
                    yield chunk


def _piece(line):
    if not line:
        return None
    try:
        return json.loads(line).get("message", {}).get("content") or None
    except ValueError:
        return None


YAML_BLOCK = re.compile(r"```ya?ml\s*\n(.*?)```", re.S)


def proposed_yaml(text):
    """The last fenced YAML block in a reply, if any."""
    blocks = YAML_BLOCK.findall(text or "")
    return blocks[-1].strip() if blocks else None
