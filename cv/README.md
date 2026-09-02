# cv — the CV and cover letter renderer

Tailor the CV and cover letter per job posting, render both to PDF.

```
python render.py applications/2026-08-acme   (run from this folder)
```

Writes `<FirstName>CV-<slug>.pdf` and `<FirstName>Cover-<slug>.pdf` there.

## Layout

```
master.yaml              content superset — every bullet, project, skill
profile.md         fuller reference: skill levels, gaps, constraints, bands
templates/cv.html.j2     A4, ported from the Affinity original
templates/cover.html.j2  A4, ported from Cover/*.pdf, recoloured to match the CV
assets/photo.png         extracted from the original PDF
render.py                master + tailored -> HTML -> PDF (headless Chromium)
applications/
  _example/              every knob, commented
  baseline/              full master content; renders ~the Affinity original
```

## How tailoring works

`applications/<slug>/tailored.yaml` overlays `master.yaml`. Per section:

| in tailored.yaml | result |
|---|---|
| omitted | master's content, master's order |
| `[id, id, id]` | those entries, in that order |
| `- {id: x, bullets: [...]}` | that master entry with fields overridden |
| `- {role: ..., org: ...}` (no id) | a new entry not in master |

Master is never edited to fit one job. New material that's reusable goes *into*
master, then gets selected from.

## Skills tiers

`master.yaml` keeps three pools. `core` + `working` feed the SKILLS block —
things actually built with. `familiar` is stuff not used yet; it renders under
its own dimmer FAMILIAR heading and **only when a tailored.yaml opts in**, so it
can't leak onto a CV that didn't ask for it.

```yaml
skills: [C#, .NET, Azure]                            # SKILLS only
skills:
  skills:   [C#, .NET, Azure]                        # SKILLS
  familiar: [Docker, Kubernetes, AWS]                # + FAMILIAR
```

ATS keyword-matching reads the whole page, so a `familiar` term still scores the
filter hit — it just doesn't claim depth you'd have to defend in the interview.
Adding a skill you haven't used? It goes in `familiar`, not `core`/`working`.

## Notes

- Both documents are **A4** and share one palette: field/sidebar `#21405c`, accent
  `#008cff`. The cover keeps its own dark full-bleed layout — it was recoloured off
  the old purple (`#2e2946`/`#463e6a`), not restructured. Its band colour is derived
  from the navy by the ratio the original used between its background and band.
- Fonts: **Rubik** and **Arial**, both already installed system-wide — nothing to
  download, renders offline. The cover's original font was Aptos, which isn't
  installed; Rubik substitutes and unifies it with the CV. Swap `--font` in
  `cover.html.j2` to `"Segoe UI"` to sit closer to the original metrics.
- Chromium is found automatically (Chrome, then Edge). Edit `BROWSERS` in
  `render.py` if neither is where it expects.
- The CV must stay one page. `render.py` won't stop you; check `page_count`.
- Geometry in the templates is in `pt`, lifted from the original PDFs. Line-heights
  are absolute so a line step is exactly `line-height + margin-top`, matching
  Affinity's leading. Changing one to a unitless ratio will drift the whole page.
