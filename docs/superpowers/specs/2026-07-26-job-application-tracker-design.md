# Job Application Tracker — Design

Date: 2026-07-26
Status: approved (design), pending implementation plan

## Problem

Targeting GNSS, PNT, navigation systems, sensor fusion, and radar engineering roles
across continental Europe, graduating in roughly three months (autumn 2026).

The market is wide but thin: perhaps 40–80 genuinely relevant openings exist across
Europe at any moment. The bottleneck is not application throughput. It is:

- **Discovery** — not knowing which employers and portals to watch in this niche.
- **Tracking** — losing threads: what was sent, in what version, at what stage, when to follow up.
- **Positioning** — framing a fresh-graduate profile consistently and credibly.

Rewriting documents is explicitly *not* a pain point, so this system is not a
document generator. Mass-application automation is a non-goal and would be
counterproductive in a field this small.

## Candidate profile and constraints

- EU citizen — eligible for defence-adjacent and EU-classified programmes
  (Galileo PRS, ESA security-related roles, Thales/Airbus defence units).
- Languages: English, French, Spanish, Portuguese. Italian possible with study.
  German is a genuine constraint; German-requiring postings are flagged, not dropped.
- UK excluded (post-Brexit work authorisation).
- Geography otherwise open: DE, FR, NL, BE, CH, IT, ES, Nordics, and others.
  Munich and Toulouse are expected to dominate results as a reflection of job
  concentration, not personal preference.
- Level: junior/graduate engineer, and funded PhD positions (Germany especially,
  France welcome).
- Role families, ranked: GNSS · PNT · navigation systems engineering ·
  sensor fusion · radar · adjacent.

Many of the best targets — small companies, research groups, PhD positions — do not
post to LinkedIn or the large aggregators. Manual entry is a first-class input path,
not a fallback.

## Non-goals

- Aggregator scraping (LinkedIn, Indeed). ToS violations, IP blocking, and poor
  signal-to-noise in this niche.
- Unattended generation of CVs or cover letters. Tailoring happens in conversation,
  with the tool supplying context and state.
- A web application. No server, no frontend framework.
- A tax engine. Simplified effective rates are sufficient for ranking.

## Architecture

Python 3.11+, SQLite, Typer (CLI), httpx (fetching), Jinja2 (dashboard), pytest.

```
job-searching/
  profile/              # user-owned, hand-written, git-tracked
    evidence.md  positioning.md  answers.md  cv/
  config/               # user-owned, hand-written
    employers.yaml  cities.yaml  comp.yaml  scoring.yaml  deadlines.yaml
  data/
    jobs.db
    postings/           # snapshotted posting text, one .md per job
  src/jobhunt/
    cli.py  store.py  match.py  score.py  report.py  calendar.py
    sources/
      workday.py  smartrecruiters.py  greenhouse.py
      successfactors.py  euraxess.py  manual.py
  tests/
```

Everything the user authors is plain text under version control. The database holds
only derived and operational state. Losing `jobs.db` costs tracking history, not work.

## Data model

Four tables.

**`employers`** — the curated watchlist.
Name, country, city, ATS type, ATS endpoint, careers URL, tags
(`gnss`, `radar`, `academic`, `defense`), poll enabled, last polled, last manual
check, notes.

**`jobs`** — one row per opening, polled or manual.
Employer FK, title, city, country, URL, snapshot path, source
(`workday` / `manual` / `euraxess` / …), first seen, last seen, stage, dismissed
flag, dismissal reason, score components (`role_fit`, `comp`, `qol`, `total`),
language flags, and application fields: base CV used, positioning angle, applied-on
date.

Stages: `spotted → shortlisted → applied → screening → interview → offer`, plus
terminal `rejected`, `withdrawn`, `expired`.

There is deliberately **no separate `applications` table**. A job never applied to
and a job at interview stage are the same entity at different stages. Splitting them
would create two places to look and a synchronisation problem for no benefit at this
volume.

**`events`** — append-only log. Job FK, timestamp, kind (stage change, note,
follow-up sent, dismissal), text. Drives history and staleness.

**`deadlines`** — recurring cycles independent of any live posting. Name, employer,
typical open/close window, source URL, last known dates.

## Discovery

Six source modules behind one interface: `fetch(employer) -> list[RawPosting]`.

| Source | Mechanism | Reliability |
|---|---|---|
| Greenhouse | Documented public JSON board API | High |
| SmartRecruiters | Documented public JSON postings API | High |
| Workday | Undocumented but stable per-tenant JSON endpoint | Medium; fails loudly |
| SuccessFactors | Usually HTML parsing | Low; lowest priority, may be dropped |
| EURAXESS | Research/PhD portal — primary channel for doctoral targets | Medium |
| Manual | `jobs add <url>`, fetch + snapshot + prompt | N/A — first-class |

**Which ATS each employer uses is unknown at design time and is not guessed here.**
Implementation begins with a discovery pass: visit each target employer's careers
page, identify the platform, record it in `employers.yaml`. Employers that cannot be
polled are flagged `manual` and surfaced in a "check these yourself" dashboard
section on a configurable cadence.

Coverage will be partial. The system must be explicit about which employers it is
*not* watching, rather than creating false confidence that the dashboard represents
the whole market.

Seed employer list to be built during implementation. Known relevant targets include:
Airbus Defence & Space, Thales Alenia Space, Thales, Safran (Data Systems /
Navigation & Timing), Rohde & Schwarz, u-blox, Septentrio, GMV, Telespazio, Qascom,
IFEN, NavCert, Trimble, Hexagon, ESA (ESTEC/ESOC, incl. YGT), DLR, Fraunhofer IIS,
CNES, ONERA, Universität der Bundeswehr München (Institute of Space Technology and
Space Applications), ISAE-SUPAERO, TU Delft, KU Leuven, Politecnico di Torino.
Academic PhD channels: EURAXESS, DAAD, ABG/ADUM.

## Matching

Rule-based over title and description. No ML.

- **Hard gates** — country not excluded (UK out); not obviously requiring an
  unavailable language.
- **Role families** from `scoring.yaml`, each with keyword sets and user-set weights.
- **Negative keywords** for this niche's characteristic noise: land surveying, GIS
  technician, sales, account management, field service.
- **Language flags** — German or other requirements are flagged rather than
  filtered, since stated requirements are often softer than written.

Everything below threshold is **stored, not discarded**, and viewable via
`jobs list --all`. The filter must be auditable.

## Scoring

`total = w_comp · comp + w_qol · qol + w_fit · role_fit`

Weights live in `scoring.yaml`. Components are normalised 0–1. The dashboard always
displays the breakdown and the provenance of each compensation figure — never a bare
number. **Score sorts; it never rejects.** A strong role in a mediocre city still
appears.

### Compensation

In descending order of trust:

1. `salary_stated` — when the posting states it. For German public-sector PhD
   positions this is exactly knowable (TV-L E13 at 65% or 75%); the French
   *contrat doctoral* is a fixed national rate. These are not estimates.
2. `salary_estimated` — from a hand-maintained table in `comp.yaml` of entry-level
   engineer gross by country × level, seeded from public benchmarks and corrected as
   real figures emerge from interviews.
3. `net` — simplified effective-tax-rate lookup per country, single filer.
   Accurate to roughly ±3%, which is sufficient for ranking.
4. `purchasing_power` — net ÷ price level index. Eurostat publishes official
   country-level PLIs. A city-level rent adjustment is hand-set for the candidate
   cities.

### Quality of life

Hand-written `cities.yaml` covering the ~20 realistic candidate cities: real annual
sunshine hours, a user-assigned nature/outdoors score, and rent level.

This replaces the originally proposed computed proxies (latitude for sun,
inverse-population for nature). Latitude is a weak predictor of climate — Toulouse
and Milan sit at similar latitudes with different weather — and inverse-population
penalises Munich for being large while ignoring that the Alps are an hour away. At
20 cities, hand-curated data is both more accurate and less code.

## Positioning and content

A `profile/` directory of plain Markdown, owned by the user, read by Claude when
tailoring:

- **`evidence.md`** — atomic factual inventory of work actually done. Not CV bullets:
  raw claims with numbers, tools, datasets, results. Source of truth for everything
  else. Includes the Telespazio internship work, thesis, coursework, papers.
- **`positioning.md`** — 2–4 named angles, each mapped to a role family
  (e.g. integrity/robustness · estimation & sensor fusion · receiver signal
  processing). Same person, different emphasis. These decide whether a fresh graduate
  reads as focused or generic.
- **`cv/`** — a small number of base CVs, one per angle, not one per application.
- **`answers.md`** — reusable answers to recurring EU aerospace application
  questions: motivation, domain interest, availability, salary expectation,
  relocation.

Per application, the tracker records which base CV, which angle, what was tailored,
and the posting snapshot (postings get deleted; the text is needed before interviews).

**Tailoring happens in conversation, not unattended.** The tool remembers state and
supplies context; Claude and the user write the documents together. Unattended
generated cover letters read as such, and in a field this small that is a real cost.

## Dismissal and rejection

Two distinct concepts:

- **`jobs dismiss <id> --reason "..."`** — the user's judgment overrides the score.
  Permanent: a dismissed job will not resurface when the poller re-sees it. Applies
  to any job regardless of source. Reasons are logged as events and reviewable via
  `jobs dismissed --review`, so recurring rejection patterns become the basis for
  tuning negative keywords — by editing config, not by model inference.
- **`jobs reject <id>`** — the employer rejected the candidate. A pipeline stage.

## CLI

```
jobs poll [--employer X]      # manual, on demand — no cron by choice
jobs list [--new|--all|--stage applied]
jobs show <id>                # detail, snapshot, score breakdown
jobs add <url>                # manual entry
jobs stage <id> <stage>       # logged to events
jobs note <id> "..."
jobs dismiss <id> --reason "..."
jobs reject <id>
jobs dismissed --review
jobs due                      # staleness nudges + upcoming deadlines
jobs report                   # regenerate dashboard.html
```

## Dashboard

Static HTML written to disk, opened directly in a browser. Sections:

- New since last poll
- Pipeline by stage
- Follow-ups due
- Upcoming cycle deadlines
- Employers not being polled, with last manual check dates

Rows sorted by total score with the breakdown visible.

## Staleness and deadlines

All thresholds configurable in `scoring.yaml`; nothing hardcoded. Defaults: applied
at 14 days and 30 days; PhD enquiries at 21 and 45 days.

`deadlines.yaml` drives forward-looking warnings independent of live postings, with
configurable lead times. Several high-value targets are cycle-based rather than
rolling: ESA YGT opens roughly annually with a hard deadline, Airbus and Thales
graduate schemes run in intakes, PhD funding calls have fixed dates. For a candidate
three months from graduation this is likely the highest-value feature in the system.

## Error handling

A failing source logs its error and does not abort the poll. The affected employer is
marked stale in the dashboard, so a silently broken fetcher cannot masquerade as "no
new jobs." Network calls are rate-limited and identify themselves honestly in the
user agent.

## Testing

pytest. Recorded fixture responses per ATS so parsers are tested without network
access. Matching and scoring are pure functions over config and are tested directly.
No live-network tests in the suite.
