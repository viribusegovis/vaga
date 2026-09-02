"""Vaga — the local web UI over the cached jobs.

    python serve.py            then open http://localhost:8000

Scoring runs on every request straight out of jobs.json, so changing a weight
in Preferences and reloading re-ranks instantly — nothing is refetched from
LinkedIn and the model is not re-run. Marking, notes and preference edits all
write back to the same files scout.py reads, so the CLI and the UI never
disagree about state.
"""

import os
import subprocess
import sys
import threading
import time
from urllib.parse import urlencode

import httpx
import uvicorn
import yaml
from fastapi import FastAPI, Form, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse, StreamingResponse)
from jinja2 import Environment, FileSystemLoader, select_autoescape

import assistant
import enrich
import prefs
import scout
import store
import tailor

app = FastAPI(title="Vaga")
templates = Environment(
    loader=FileSystemLoader(store.APP / "templates"),
    autoescape=select_autoescape(["html"]),
)

# One background job at a time: two concurrent runs would race on jobs.json and
# get us rate-limited twice as fast.
busy = threading.Lock()
run_state = {"state": "idle", "output": "", "failed": False, "step": ""}

# Bumped every time a run finishes. The jobs page records the value it rendered
# with and polls; when it changes, the page *offers* a refresh instead of taking
# one. Rearranging the list under someone who is reading it is the one thing an
# auto-updating view must not do.
fetch_seq = {"n": 0}


def skills():
    return yaml.safe_load(scout.MASTER.read_text(encoding="utf-8"))["skills"]


def tier(score, cutoff):
    if score >= cutoff:
        return "tier-hot"
    return "tier-warm" if score >= cutoff - 15 else "tier-cool"


def render(name, request, **ctx):
    ctx.setdefault("run", run_state)
    ctx.setdefault("flash", request.query_params.get("msg"))
    ctx.setdefault("flash_kind", request.query_params.get("kind", "ok"))
    return HTMLResponse(templates.get_template(name).render(**ctx))


def back_to(request):
    """Preserve the current filters across a POST redirect."""
    qs = urlencode({k: v for k, v in request.query_params.items() if k != "msg"})
    return "/?" + qs if qs else "/"


# ----------------------------------------------------------------------- jobs

@app.get("/", response_class=HTMLResponse)
def index(request: Request, status: str = "all", q: str = "",
          sort: str = "score", mode: str = ""):
    cfg = prefs.load()
    jobs = store.load()
    kept, rejected = scout.judge(jobs, list(jobs), cfg, skills())
    cutoff = cfg["scoring"]["interesting_at"]

    # Commute is recomputed for display; the score already accounts for it.
    for j in kept:
        j["commute"] = (1.0 if j.get("mode") == "remote" else
                        scout.commute_factor(j.get("location", ""),
                                             cfg["scoring"]["commute"],
                                             cfg["reject"].get("commutable_countries") or []))

    counts = {s: sum(1 for j in kept if j.get("status", "new") == s)
              for s in store.STATUSES}
    counts["all"] = len(kept)

    shown = kept
    if status != "all":
        shown = [j for j in shown if j.get("status", "new") == status]
    if mode == "reachable":
        shown = [j for j in shown if j["commute"] >= 0.5 or j.get("mode") == "remote"]
    elif mode:
        shown = [j for j in shown if j.get("mode") == mode]
    if q:
        needle = scout.fold(q)
        shown = [j for j in shown if needle in scout.fold(
            "%s %s %s" % (j.get("title", ""), j.get("company", ""),
                          j.get("description", "")))]

    if sort == "date":
        shown = sorted(shown, key=lambda j: j.get("posted", ""), reverse=True)
    elif sort == "company":
        shown = sorted(shown, key=lambda j: scout.fold(j.get("company", "")))

    def url(**over):
        args = {"status": status, "q": q, "sort": sort, "mode": mode}
        args.update(over)
        return "/?" + urlencode({k: v for k, v in args.items() if v and v != "all"
                                 or k == "status" and v == "all"})

    # Which postings already have a tailored application, so the list can say so.
    started = {j["id"] for j in shown if tailor.exists(scout.slug(j))}

    return render("index.html", request, page="jobs", jobs=shown, counts=counts,
                  status=status, q=q, sort=sort, mode=mode, rejected=rejected,
                  slug=scout.slug, url=url, back=back_to(request), started=started,
                  seq=fetch_seq["n"], tier=lambda s: tier(s, cutoff))


@app.post("/mark/{job_id}")
def mark(job_id: str, status: str = Form(...), back: str = Form("/")):
    jobs = store.load()
    if job_id in jobs and status in store.STATUSES:
        jobs[job_id]["status"] = status
        store.save(jobs)
    return RedirectResponse(back, status_code=303)


@app.post("/note/{job_id}")
def note(job_id: str, note: str = Form(""), back: str = Form("/")):
    jobs = store.load()
    if job_id in jobs:
        jobs[job_id]["note"] = note.strip()
        store.save(jobs)
    return RedirectResponse(back, status_code=303)


# --------------------------------------------------------------------- logos

@app.get("/logo/{job_id}")
def logo(job_id: str):
    """Serve a company logo from disk, fetching it once on first request.

    Hotlinking media.licdn.com works from a script but not from a browser with
    any tracker blocker installed, which is what made the icons look broken.
    """
    jobs = store.load()
    job = jobs.get(job_id)
    if not job or not job.get("logo"):
        return JSONResponse({"error": "no logo"}, status_code=404)

    store.LOGOS.mkdir(exist_ok=True)
    cached = store.LOGOS / (job_id + ".img")
    if not cached.exists():
        try:
            r = httpx.get(job["logo"], timeout=15, follow_redirects=True,
                          headers={"User-Agent": scout.UA})
            r.raise_for_status()
            if not r.headers.get("content-type", "").startswith("image/"):
                raise httpx.HTTPError("not an image")
            cached.write_bytes(r.content)
        except httpx.HTTPError:
            return JSONResponse({"error": "fetch failed"}, status_code=404)

    return FileResponse(cached, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=604800"})


# ------------------------------------------------------------------ one job

def load_one(job_id):
    """Return the scored job, or None. Scoring is the same path the list uses."""
    cfg = prefs.load()
    jobs = store.load()
    if job_id not in jobs:
        return None, cfg, jobs
    kept, rejected = scout.judge(jobs, [job_id], cfg, skills())
    job = kept[0] if kept else jobs[job_id]
    if not kept:
        # Hard-filtered jobs still get a page; say why rather than 404.
        job["score"] = 0
        job["reasons"] = ["filtered out: %s" % job.get("rejected", "unknown")]
    job["commute"] = (1.0 if job.get("mode") == "remote" else
                      scout.commute_factor(job.get("location", ""),
                                           cfg["scoring"]["commute"],
                                           cfg["reject"].get("commutable_countries") or []))
    return job, cfg, jobs


def detail_rows(job):
    llm = job.get("llm") or {}
    rows = [
        ("Company", job.get("company") or "—"),
        ("Location", job.get("location") or "—"),
        ("Posted", job.get("posted") or "not stated"),
        ("First seen", job.get("first_seen") or "—"),
        ("Employment type", job.get("employment_type") or "—"),
        ("LinkedIn seniority", job.get("seniority") or "—"),
        ("Job function", job.get("job_function") or "—"),
        ("Found by search", job.get("via") or "—"),
        ("Work mode", llm.get("work_mode") or job.get("mode") or "—"),
        ("Office days", llm.get("onsite_days_per_week")
         if llm.get("onsite_days_per_week") is not None else "not stated"),
        ("Years wanted", "%s (%s)" % (llm["years_required"],
         "required" if llm.get("years_are_hard_requirement") else "preferred")
         if llm.get("years_required") else "not stated"),
        ("Junior suitable", llm.get("junior_suitable") if "junior_suitable" in llm else "—"),
        ("Languages", ", ".join(llm.get("languages_required") or []) or "none stated"),
        ("Salary", llm.get("salary") or "not stated"),
        ("Read by model", "yes" if llm else "no — keywords only"),
        ("LinkedIn id", job.get("id")),
    ]
    return [(k, v) for k, v in rows]


def ago(ts):
    import time
    mins = (time.time() - ts) / 60
    if mins < 1:
        return "just now"
    if mins < 60:
        return "%d min ago" % mins
    if mins < 48 * 60:
        return "%d hours ago" % (mins / 60)
    return "%d days ago" % (mins / 1440)


@app.get("/job/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str):
    job, cfg, _ = load_one(job_id)
    if job is None:
        return HTMLResponse("<p style='font:16px system-ui;padding:40px'>"
                            "No such job. <a href='/'>Back</a></p>", status_code=404)
    slug = scout.slug(job)
    files = tailor.pdfs(slug)
    chat = assistant.history(job)
    llm_cfg = cfg.get("llm", {})
    return render("job.html", request, page="jobs", job=job, slug=slug,
                  chat=chat, presets=assistant.PRESETS,
                  chat_model=llm_cfg.get("chat_model") or llm_cfg.get("model", "?"),
                  proposed=assistant.proposed_yaml(chat[-1]["content"]) if chat and
                           chat[-1]["role"] == "assistant" else None,
                  app=tailor.exists(slug), tailored=tailor.read(slug), pdfs=files,
                  rendered_ago=ago(max(f["mtime"] for f in files)) if files else "",
                  details=detail_rows(job), back=request.query_params.get("back", "/"),
                  render_output=request.query_params.get("out", ""),
                  render_ok=request.query_params.get("ok") == "1",
                  tier=lambda sc: tier(sc, cfg["scoring"]["interesting_at"]))


def to_job(job_id, msg=None, kind="ok", out=None, ok=None):
    args = {}
    if msg:
        args.update(msg=msg, kind=kind)
    if out is not None:
        args.update(out=out[-1500:], ok="1" if ok else "0")
    url = "/job/" + job_id + ("?" + urlencode(args) if args else "")
    return RedirectResponse(url, status_code=303)


@app.post("/job/{job_id}/tailor")
def make_application(job_id: str):
    job, _, _ = load_one(job_id)
    if job is None:
        return RedirectResponse("/", status_code=303)
    slug, err = tailor.create(job, skills())
    return to_job(job_id, err or "Created applications/%s." % slug,
                  "err" if err else "ok")


@app.post("/job/{job_id}/tailored")
async def save_tailored(request: Request, job_id: str):
    form = await request.form()
    job, _, _ = load_one(job_id)
    if job is None:
        return RedirectResponse("/", status_code=303)
    err = tailor.write(scout.slug(job), form["text"])
    return to_job(job_id, err or "Saved.", "err" if err else "ok")


@app.post("/job/{job_id}/render")
async def render_pdfs(request: Request, job_id: str):
    form = await request.form()
    job, _, _ = load_one(job_id)
    if job is None:
        return RedirectResponse("/", status_code=303)
    slug = scout.slug(job)
    err = tailor.write(slug, form["text"])
    if err:
        return to_job(job_id, err, "err")
    ok, output = tailor.render(slug)
    return to_job(job_id, out=output, ok=ok)


@app.get("/job/{job_id}/pdf/{kind}")
def serve_pdf(job_id: str, kind: str):
    job, _, _ = load_one(job_id)
    if job is None:
        return JSONResponse({"error": "no such job"}, status_code=404)
    for f in tailor.pdfs(scout.slug(job)):
        if f["kind"] == kind:
            return FileResponse(tailor.path(scout.slug(job)) / f["name"],
                                media_type="application/pdf",
                                headers={"Content-Disposition":
                                         'inline; filename="%s"' % f["name"]})
    return JSONResponse({"error": "not rendered yet"}, status_code=404)


# --------------------------------------------------------------- the assistant

@app.post("/job/{job_id}/ask")
async def ask(request: Request, job_id: str):
    """Stream a grounded reply, saving both sides of the turn when it finishes."""
    payload = await request.json()
    question = (payload.get("q") or "").strip()
    job, cfg, _ = load_one(job_id)
    if job is None or not question:
        return JSONResponse({"error": "no such job, or empty question"}, status_code=400)

    llm_cfg = cfg.get("llm", {})
    ok, msg = enrich.available({**llm_cfg,
                                "model": llm_cfg.get("chat_model") or llm_cfg["model"]})
    if not ok:
        return JSONResponse({"error": msg}, status_code=503)

    slug = scout.slug(job)
    assistant.save_turn(job_id, "user", question)

    def body():
        collected = []
        try:
            for piece in assistant.stream(llm_cfg, job, slug, question):
                collected.append(piece)
                yield piece
        except httpx.HTTPError as e:
            yield "\n\n[the model stopped: %s]" % e
        finally:
            if collected:
                assistant.save_turn(job_id, "assistant", "".join(collected))

    return StreamingResponse(body(), media_type="text/plain; charset=utf-8")


# Job ids whose system prompt has been pushed through the model already.
warmed = set()


@app.post("/job/{job_id}/warm")
def warm_job(job_id: str):
    """Pre-process this job's system prompt so the first question is fast."""
    if job_id in warmed:
        return JSONResponse({"state": "ready"})
    if busy.locked():
        return JSONResponse({"state": "busy"})
    job, cfg, _ = load_one(job_id)
    if job is None:
        return JSONResponse({"state": "unknown"}, status_code=404)
    llm_cfg = cfg.get("llm", {})
    ok, _ = enrich.available({**llm_cfg,
                              "model": llm_cfg.get("chat_model") or llm_cfg["model"]})
    if not ok:
        return JSONResponse({"state": "offline"})

    def work():
        try:
            assistant.warm(llm_cfg, job, scout.slug(job))
            warmed.add(job_id)
        except httpx.HTTPError:
            pass

    threading.Thread(target=work, daemon=True).start()
    return JSONResponse({"state": "warming"})


@app.get("/job/{job_id}/warm")
def warm_state(job_id: str):
    return JSONResponse({"state": "ready" if job_id in warmed else "warming"})


@app.post("/job/{job_id}/chat/clear")
def clear_chat(job_id: str):
    assistant.clear(job_id)
    return to_job(job_id, "Conversation cleared.")


@app.post("/job/{job_id}/apply-yaml")
async def apply_yaml(request: Request, job_id: str):
    """Write a tailored.yaml the assistant proposed, after validating it."""
    form = await request.form()
    job, _, _ = load_one(job_id)
    if job is None:
        return RedirectResponse("/", status_code=303)
    slug = scout.slug(job)
    if not tailor.exists(slug):
        tailor.create(job, skills())
    err = tailor.write(slug, form["text"])
    return to_job(job_id, err or "Applied the proposed tailored.yaml.",
                  "err" if err else "ok")


# ------------------------------------------------------------- background runs

def spawn(script, args, label):
    """Run a CLI script in a worker thread, streaming its progress to the page."""
    if busy.locked():
        return

    def work():
        with busy:
            run_state.update(state="running", output="", failed=False,
                             step="starting…")
            lines = []
            # -u and line buffering so progress reaches the page as it happens
            # rather than all at once when the run ends.
            proc = subprocess.Popen(
                [sys.executable, "-u", str(store.APP / script)] + args,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, cwd=store.APP, bufsize=1)
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    lines.append(line)
                    run_state["step"] = line.strip()
            proc.wait()
            run_state.update(state="idle", failed=proc.returncode != 0, step="",
                             output="\n".join(lines)[-4000:] or label)
            fetch_seq["n"] += 1

    threading.Thread(target=work, daemon=True).start()


@app.post("/search")
def search():
    spawn("scout.py", [], "search finished")
    return RedirectResponse("/", status_code=303)


@app.post("/enrich")
def reenrich():
    spawn("enrich.py", ["--redo"], "descriptions re-read")
    return RedirectResponse("/", status_code=303)


@app.get("/status")
def status():
    """Progress of any running job, plus the counter the jobs page watches."""
    jobs = store.load()
    return JSONResponse({
        "state": run_state["state"],
        "step": run_state.get("step", ""),
        "seq": fetch_seq["n"],
        "total": len(jobs),
        "unread": sum(1 for j in jobs.values()
                      if j.get("description") and not j.get("llm")),
    })


@app.post("/shutdown")
def shutdown():
    """Close the app from its own page, or from `launcher.py stop`.

    Refused while a search or enrichment is running: those write jobs.json as
    they go, and killing one mid-write is the only way to damage the cache.
    """
    if busy.locked():
        return JSONResponse(
            {"error": "a run is in progress — let it finish first"}, status_code=409)

    def bye():
        time.sleep(0.4)          # let this response reach the browser first
        os._exit(0)

    threading.Thread(target=bye, daemon=True).start()
    return JSONResponse({"state": "stopping"})


def auto_fetch_loop():
    """Re-run every saved search on a timer. Off unless configured."""
    while True:
        try:
            minutes = prefs.load().get("fetch", {}).get("auto_every_minutes") or 0
        except (OSError, ValueError, TypeError):
            minutes = 0
        if minutes <= 0:
            time.sleep(60)          # config is re-read, so 0 -> N takes effect
            continue
        time.sleep(minutes * 60)
        if not busy.locked():
            spawn("scout.py", [], "scheduled search finished")


# ------------------------------------------------------------------ preferences

def ollama_models(cfg):
    try:
        r = httpx.get(cfg.get("host", "http://localhost:11434") + "/api/tags", timeout=2)
        r.raise_for_status()
        return sorted(m["name"] for m in r.json().get("models", []))
    except (httpx.HTTPError, KeyError, ValueError):
        return []


@app.get("/settings", response_class=HTMLResponse)
def settings(request: Request):
    cfg = prefs.load()
    jobs = store.load()
    stats = {
        "total": len(jobs),
        "enriched": sum(1 for j in jobs.values() if j.get("llm")),
        "with_desc": sum(1 for j in jobs.values() if j.get("description")),
        "dismissed": sum(1 for j in jobs.values() if j.get("status") == "dismissed"),
    }
    return render("settings.html", request, page="settings",
                  searches=cfg["searches"], reject=cfg["reject"],
                  scoring=cfg["scoring"], weights=cfg["scoring"]["weights"],
                  commute=cfg["scoring"]["commute"], llm=cfg.get("llm", {}),
                  fetch=cfg["fetch"], raw_yaml=prefs.raw(), stats=stats,
                  ollama_models=ollama_models(cfg.get("llm", {})))


def done(error=None, ok="Saved."):
    if error:
        return RedirectResponse("/settings?" + urlencode({"msg": error, "kind": "err"}),
                                status_code=303)
    return RedirectResponse("/settings?" + urlencode({"msg": ok}), status_code=303)


def csv_list(text):
    return [p.strip() for p in text.split(",") if p.strip()]


@app.post("/settings/scoring")
async def save_scoring(request: Request):
    form = await request.form()
    changes = {}
    for key in ("skills", "seniority", "work_mode", "freshness"):
        changes[("scoring", "weights", key)] = int(form["w_" + key])
    changes[("scoring", "interesting_at")] = int(form["interesting_at"])
    changes[("scoring", "mid_senior_penalty")] = int(form["mid_senior_penalty"])
    return done(prefs.update(changes))


@app.post("/settings/filters")
async def save_filters(request: Request):
    form = await request.form()
    err = prefs.update({
        ("reject", "max_onsite_days"): int(form["max_onsite_days"]),
        ("reject", "max_years"): int(form["max_years"]),
        ("reject", "languages_spoken"): csv_list(form["languages_spoken"]),
    })
    if err:
        return done(err)
    for key in ("onsite_cities", "title_terms"):
        err = prefs.update_block(("reject", key), csv_list(form[key]), 4)
        if err:
            return done(err)
    return done()


@app.post("/settings/commute")
async def save_commute(request: Request):
    form = await request.form()
    cities = form.getlist("city")
    factors = form.getlist("factor")
    table = {}
    for city, factor in zip(cities, factors):
        city = city.strip()
        if not city or not str(factor).strip():
            continue
        try:
            table[city] = round(float(factor), 3)
        except ValueError:
            return done("%r is not a number." % factor)
    if "unknown_city" not in table:
        table["unknown_city"] = 0.5
    return done(prefs.update_block(("scoring", "commute"), table, 4))


@app.post("/settings/searches")
async def save_searches(request: Request):
    form = await request.form()
    drop = {int(i) for i in form.getlist("drop")}
    ids = form.getlist("id")
    rows = list(zip(ids, form.getlist("keywords"), form.getlist("location"),
                    form.getlist("posted_within"), form.getlist("experience"),
                    form.getlist("work_type"), form.getlist("pages")))
    out = []
    for i, (sid, kw, loc, posted, exp, wt, pages) in enumerate(rows):
        if i in drop or not sid.strip() or not kw.strip():
            continue
        out.append({
            "id": sid.strip(), "keywords": kw.strip(),
            "location": loc.strip() or (
                (prefs.load().get("reject", {}).get("commutable_countries") or [""])[0]),
            "posted_within": posted, "experience": csv_list(exp),
            "work_type": csv_list(wt), "pages": max(1, int(pages or 2)),
        })
    if not out:
        return done("Keep at least one search.")
    return done(prefs.update_block(("searches",), out, 2))


@app.post("/settings/llm")
async def save_llm(request: Request):
    form = await request.form()
    return done(prefs.update({
        ("llm", "enabled"): "enabled" in form,
        ("llm", "model"): form["model"].strip(),
        ("llm", "chat_model"): form["chat_model"].strip(),
        ("fetch", "delay"): float(form["delay"]),
        ("fetch", "max_details"): int(form["max_details"]),
        ("fetch", "auto_every_minutes"): int(form["auto_every_minutes"]),
        ("llm", "workers"): int(form["workers"]),
    }))


@app.post("/settings/raw")
async def save_raw(request: Request):
    form = await request.form()
    return done(prefs.save_raw(form["text"]), ok="searches.yaml saved.")


@app.post("/settings/forget")
def forget():
    jobs = store.load()
    gone = [k for k, v in jobs.items() if v.get("status") == "dismissed"]
    for k in gone:
        del jobs[k]
    store.save(jobs)
    return done(ok="Forgot %d dismissed job%s." % (len(gone), "" if len(gone) == 1 else "s"))


if __name__ == "__main__":
    threading.Thread(target=auto_fetch_loop, daemon=True).start()
    print("Vaga — http://localhost:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
