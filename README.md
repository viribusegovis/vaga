# Vaga

Finds jobs on LinkedIn, throws out the ones that can't work, and ranks the rest
against your actual CV — then hands the good ones to the `cv/` half to tailor.

*Vaga* is Portuguese for a job opening.

Double-click **`Vaga.cmd`**. It starts Ollama if it isn't up, starts the server,
waits for the port, and opens the browser. Double-clicking it again just opens
the tab. **`Vaga - Stop.cmd`** shuts it down, and so does the **Quit** button in
the app's header.

For a Start Menu or taskbar entry, right-click `Vaga.cmd` → Send to → Desktop,
then pin the shortcut.

```
python launcher.py            same as Vaga.cmd
python launcher.py stop
python launcher.py restart
python launcher.py status     is Vaga up? is Ollama up?
python serve.py               run it in the foreground, to see errors
```

Everything is reachable from the app. The command line still works if you prefer:

```
python scout.py                     run every saved search
python scout.py --only pt-dotnet    run one
python scout.py --all               re-score everything cached, not just new
python scout.py --no-llm            skip the description-reading pass
python enrich.py --redo             re-read cached descriptions with the model
```

## The app

**Jobs** is a ranked list. Each posting shows its company logo, a one-line
summary of what the role actually involves, and a row of chips carrying the
facts you'd otherwise dig for: remote or hybrid and how many office days, how
reachable the commute is, years required and whether they're a real requirement,
salary if stated, languages needed.

- **Why 72** expands the scoring breakdown, so no ranking is a mystery.
- **Full description** shows the posting's own headings and bullet lists, not a
  flattened wall of text.
- **Interested / Applied / Dismissed** and a free-text note per job, saved to
  `jobs.json` — the same file the CLI reads, so both stay in sync.
- Filter by status, work mode, commutability or free text; sort by match, date
  or company.
- **Fetch new** runs a search in the background and refreshes when it lands.
  **Re-read descriptions** re-runs the model over what's already cached.

**Preferences** edits everything that used to mean opening a YAML file: saved
searches, deal-breakers, scoring weights, the commute table, and which model
reads descriptions. Your comments in `searches.yaml` survive — the forms rewrite
individual values and data blocks, never the whole file. A raw YAML editor at
the bottom covers anything the forms don't, and refuses to save invalid YAML.

Scoring runs per request, so changing a weight and reloading re-ranks instantly:
nothing is refetched from LinkedIn and the model is not re-run.

## Setting it up

Your CV content and your search preferences are **not** in this repo — they hold
personal details, so they are gitignored. Copy the examples and fill them in:

```
cp cv/master.example.yaml   cv/master.yaml      your CV: every bullet, project, skill
cp cv/profile.example.md    cv/profile.md       the fuller private reference
cp searches.example.yaml    searches.yaml       what to search for, and your rules
```

Then put a photo at `cv/assets/photo.png`, or point `profile.photo` in
`master.yaml` at the placeholder that ships here. Ollama is optional — without
it everything works, descriptions just get scored on keywords rather than read.

## Layout

Two halves of one system: `app/` finds and judges jobs, `cv/` turns a chosen one
into a tailored CV and cover letter. `app/tailor.py` is the seam between them.

```
searches.yaml         every setting, hand-editable and commented
Vaga.cmd              double-click to start
Vaga - Stop.cmd       double-click to stop

app/
  launcher.py         start/stop/status; what Vaga.cmd calls
  serve.py            the web app
  scout.py            fetch -> filter -> score
  enrich.py           the model reads descriptions, extracts facts
  assistant.py        the grounded chat on a job page
  tailor.py           seeds cv/applications/<slug>/, drives cv/render.py
  prefs.py            reads/writes searches.yaml without destroying comments
  store.py            the repo layout and the job cache
  templates/          base layout, jobs page, job page, preferences

cv/
  master.yaml         every CV bullet, project and skill — the superset
  profile.md          fuller reference: skill levels, gaps, constraints, bands
  render.py           master + tailored -> HTML -> PDF
  templates/          A4 CV and cover letter
  applications/       one folder per job: tailored.yaml, then the PDFs

data/                 generated, gitignored: jobs.json, logos, results.md
```

### Why there's no .exe

Packaging this with PyInstaller would produce a 40–60 MB binary that needs
rebuilding on every change, makes `searches.yaml`, the templates and the
extraction prompt awkward to edit — and would still need Ollama installed
separately, which is the real heavyweight dependency. The launcher gives you the
double-clickable icon without giving up the thing that makes the tool useful,
which is that all of its behaviour is a text file you can open.

**Quit** refuses while a search or enrichment is running, and `launcher.py stop`
reports that rather than forcing it: those passes write `jobs.json` as they go,
and killing one mid-write is the only way to damage the cache. If the server
ever wedges, `stop` falls back to the recorded pid and then to whatever holds
port 8000, so it works regardless of how the server was started.

## Where the data comes from

LinkedIn's `jobs-guest` endpoint — the fragment served to logged-out visitors.
No login, no API key. Two consequences worth knowing:

- **No personalisation.** This is keyword + location + LinkedIn's own filters.
  It cannot see the "Jobs for you" feed, match percentages or applicant counts —
  those need a session. Ranking comes from `master.yaml` instead, which knows
  things LinkedIn doesn't: your skill tiers, and that Porto on-site is not
  commutable.
- **Undocumented and rate-limited.** LinkedIn 429s quickly; requests are spaced
  by `fetch.delay` with exponential backoff, and a throttled run says so at the
  top of the digest. Scripting it is against LinkedIn's ToS, so keep the delays
  and don't run it in a loop.

## Reading descriptions with a local model

Off unless Ollama is running; toggle it in Preferences.

    winget install Ollama.Ollama
    ollama pull granite4.2:8b

The model **extracts facts, it does not score**. It reads each description and
returns work mode, office days, years required *and whether they're actually
required*, languages, contract type, pay. Those feed the same deterministic
scorer below.

That split is the point. A model asked to rate a job 0–100 gives numbers that
wobble between runs and can't be audited; facts feeding a rubric stay
reproducible, and every ranking is explainable from the reasons listed under it.
If Ollama is down the pass is skipped and scoring falls back to regex — a run
degrades, it never breaks.

### Which model

Benchmarked on ten real postings against a hand-written answer key (17
verifiable fields: work mode, years required, junior suitability). Fields that
couldn't be confirmed from the posting text were left unscored.

| model | size | correct | per description |
|---|---|---|---|
| granite4.2:8b | 5.3 GB | **14/16** | 17.7 s |
| **granite4.2:3b** | 2.2 GB | 13/16 | **6.0 s** |
| gemma4:e2b-it-qat | 4.3 GB | 11/15* | ~3 s |

Two models are configured, because the two jobs want different things.
`model` does bulk extraction over every new posting, where throughput decides
whether a fetch takes 4 minutes or 12 — that's `granite4.2:3b`. `chat_model`
answers one question at a time on a job page, where quality matters and volume
is one — that's `granite4.2:8b`. Both are set in Preferences.

Timings are three descriptions at a time; `llm.workers` controls that, and 3
measured about 1.6x faster than one at a time.

The 8B's extra point is `work_mode` on the Fujitsu posting, whose only signal is
"Location Flexibility: Primary Location Only" — no regex would catch that, and
the 3B doesn't either. It drives a hard reject, so the miss puts one
uncommutable job in the list. Set `model: granite4.2:8b` if you'd rather wait
three times as long and catch it.

Both list Portuguese among fully-tested languages, and both read the
Portuguese-language ExpressGlass posting correctly as hybrid in Porto.

\* Two caveats on these numbers. The `gemma4` row is from the original 15-field
key and wasn't re-run. And an earlier version of this table had the 8B at 14/15
against the 3B's 13/15, then both at 13/15 after descriptions were re-fetched
with structure preserved — bullet characters had been arriving mangled and the
location was missing from the prompt. The models are deterministic at
temperature 0 (same posting three times, same answer), so a benchmark is only
valid for the exact inputs it ran on. Re-run it after any change to what gets
fed in, and fix the answer key when you verify a field you'd left unscored —
that correction alone moved the ranking.

`gemma4:e2b-it-qat` lost on both axes and returned `["C#", "TypeScript"]` as
*human* languages — which is why `enrich.py` whitelists that field instead of
trusting the prompt. Without the guard it would have hard-rejected the job for
requiring a language you don't speak.

Not viable on an 8 GB card: `qwen3.8` is 27B dense, `qwen3.6` is 27B/35B,
`qwen3.8-flash-next` is 125B despite the name, `nemotron-3.5-lightning` is a
23 GB MoE.

Ollama reaches the RX 5700 XT through Vulkan — `ollama ps` reports `100% GPU`
despite ROCm not supporting RDNA1 on Windows. The big speed lever is
`think: false`, sent on every request: reasoning models otherwise spend ~1300
tokens thinking first, which measured 45.4 s per description against 2.7 s with
it off, for identical accuracy.

## Asking about an application

Every job page has an assistant, running on the same local Ollama. It is
grounded in three things and told it may use nothing else: `master.yaml` and
`profile.md` for what you have actually done, the cached posting for what
the employer wants, and the current `tailored.yaml` for what this application
claims. The system prompt tells it to name gaps rather than write around them.

That grounding is the point. A model asked to write a cover letter with no facts
to hand invents plausible ones — a team size, a technology, a metric — and a
fluent fabrication in a real application is exactly the kind you don't catch by
skimming.

One-click presets: cover letter as **plain text** for a web form, three LinkedIn
headlines, a LinkedIn About section, why-I-fit bullets, gaps to expect,
interview questions both ways, and a full CV re-tailor. Or just ask it anything.
The transcript is saved per job in `jobs.json`.

When you ask it to change the CV or cover, it returns a complete `tailored.yaml`
in a fenced block and the page offers an **Apply** button. Edits are proposed,
never applied — and the same validation guards the write.

### Speed, honestly

The system prompt is ~6k tokens, and processing it is the slow part on this GPU:

| | first token |
|---|---|
| cold, no warming | ~178 s |
| after the page warmed it | ~35 s |
| follow-up, model still hot | ~8 s |

So opening a job page fires a background request that pushes the prompt through
the model once, and the panel says whether it's ready. Ollama caches the prefix
and `keep_alive` holds the model for 30 minutes. Pick a smaller `chat_model` in
Preferences to trade prose quality for latency.

## Keeping up to date

Set **Search again automatically** in Preferences to a number of minutes and
Vaga re-runs every saved search on a timer in the background.

A background fetch never rearranges the page you're reading. The jobs list
records a counter when it renders and polls for changes; when a run lands it
raises a banner — *New jobs have been fetched, click to refresh* — and waits.
Nothing moves until you say so, so you can't lose your place mid-read.

While a run is going the page shows its live progress (`description 12/38`,
`[7/38] Junior Backend Developer`), because `spawn` reads the subprocess's
stdout line by line instead of waiting for it to exit. Postings are saved to
`jobs.json` every few fetches, so they show up as they arrive.

## How judging works

**Hard filters** drop a posting outright: senior/lead/manager wording in the
title, more office days than `max_onsite_days`, a hard requirement above
`max_years`, a language you don't speak, a pure on-site role in a city that
isn't commutable, or a hybrid/on-site role outside `commutable_countries`. Hybrid and remote in those same cities survive — only on-site
disqualifies. What got dropped, and why, is listed at the bottom of the jobs
page so a filter quietly eating everything stays visible.

**Score**, 0–100, from four weighted parts:

| part | what it reads |
|---|---|
| skills 40 | terms from `master.yaml`, tiered — a `core` hit is worth 3× a `familiar` one |
| seniority 20 | junior wording, **minus** a penalty for "Mid-Senior level" |
| work_mode 30 | remote > hybrid > on-site, times a per-city commute factor and office days |
| freshness 10 | linear decay over 30 days |

Extracted facts override the regexes wherever they exist: a stated number of
office days beats the hybrid/on-site label, and the model's junior-suitability
call moves the seniority component in both directions.

The seniority component is signed, so a mid-senior posting actively loses
points. Without that, a job listing twenty technologies out-scores a real junior
role just by mentioning more of the stack.

### Jobs in other countries

The `remote-eu` search asks LinkedIn for remote roles across the EU, and
LinkedIn's remote filter is not reliable — it returned a **hybrid** role in
Budapest, which then scored 81 and topped the list. Two things had let it
through: `onsite_cities` only named Portuguese places, and an unrecognised
location fell back to `unknown_city`, so Budapest scored like an unlisted
Portuguese town.

The rule now is the real-world one: outside `commutable_countries`, only fully
remote works, checked against what the posting says rather than the search that
found it. Unrecognised foreign locations get the `abroad` factor (0.1) instead
of `unknown_city` (0.5).

Two subtleties worth keeping if you touch this. A city named in the commute
table counts as reachable regardless of country, because LinkedIn writes plenty
of locations with no country in them at all — "Lisbon Metropolitan Area",
"Greater Braga Area" — and a naive `"Portugal" in location` test rejects those.
And a *blank* location is unknown, not foreign; dropping a posting over a field
LinkedIn didn't fill in would be the wrong call.

Foreign postings whose work mode the model couldn't determine are kept but
scored near-zero on location, since they may genuinely be remote. The
**Commutable only** filter on the jobs list hides them.

## Tuning

The commute table matters most. It reads a location left-to-right, so
`Porto, Portugal` matches `Porto` (0.15) and not `Portugal` (0.70); an unlisted
city falls back to `unknown_city`. Edit it in Preferences.

If everything scores 50-something, the skills cap is why — it saturates at 18
tier-points and most full-stack ads clear that. Raise the divisor in `score()`
to spread the range.

## Handing a posting to the tailor step

Each job shows the folder name to use. Create it under
`cv/applications/`, write its `tailored.yaml`, then from the `cv/` half:

```
python render.py applications/2026-08-volkswagen-group-dig-junior-fullstack-developer
```

Vaga deliberately doesn't create those folders — most postings in a digest
aren't worth applying to, and empty folders pile up fast.
