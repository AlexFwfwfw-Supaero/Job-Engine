# Web UI — Design

Date: 2026-07-26
Status: approved (design), pending implementation plan
Extends: `2026-07-26-job-application-tracker-design.md`

## Problem

The core tracker works, but every state change goes through the CLI while the
dashboard is a read-only file that goes stale the moment anything changes. Day to
day the friction is triage — deciding whether a spotted job is interesting,
recording why, and moving it along — and that is exactly the part that costs two
context switches per job today.

This supersedes the original decision to keep the dashboard static. That call was
right when nothing existed yet; it is wrong now that the tracking is the daily
work.

## Non-goals

- Remote access. The server binds to `127.0.0.1` only.
- Authentication, multi-user, or deployment. This is a local single-user tool.
- Any JavaScript. Rendering is server-side; forms are plain HTML.
- Replacing the CLI. Both interfaces call the same store functions.

## Data model changes

Four additive columns on `jobs`, applied by an idempotent migration at startup:

| Column | Type | Meaning |
|---|---|---|
| `notes` | TEXT, default `''` | The operator's standing description of the job. Freely editable. |
| `priority` | INTEGER, default `0` | 1–5, operator-assigned. 0 means unset. |
| `link_status` | TEXT, default `'unknown'` | `live`, `dead`, or `unknown`. |
| `last_checked` | TEXT, nullable | ISO timestamp of the last link check. |

`notes` and the `events` table are deliberately distinct. Events are append-only
history and drive staleness; the note is mutable operator context. Conflating them
would either make history editable or make the note a growing log.

`priority` lives on every job regardless of stage, so a priority set while a job is
in Interested survives the move to Applied.

The migration reads `PRAGMA table_info(jobs)` and issues `ALTER TABLE ... ADD
COLUMN` only for columns that are absent. Safe to run on every startup and on a
fresh database.

## Concept mapping

Existing stages already cover the requested lists. No new stage values.

| UI concept | Storage |
|---|---|
| Job Search list | `stage = spotted` |
| Interested | `stage = shortlisted` |
| Applied | `stage in (applied, screening, interview, offer)` |
| Archive | `dismissed = 1`, plus terminal stages `rejected`, `expired`, `withdrawn` |

`dismiss` is renamed to `archive` throughout the UI and CLI, with `dismiss` kept as
a CLI alias so existing habits and docs keep working.

## Tabs

1. **Overview** — one card per tab: purpose, count, and the most useful single
   figure. Follow-ups due and upcoming deadlines appear here too; they are
   time-sensitive and must not be buried a click deep.
2. **Job Search** — search controls (`Search now`, `Update current jobs`), the
   add-job form, and every `spotted` job. Per row: outbound link, and
   `Interested` / `Applied` / `Archive` buttons. The `Applied` button is the
   "I already applied to this" flag.
3. **Interested** — shortlisted jobs sorted by priority then total score, with
   inline priority and note editing.
4. **Applied** — `applied` through `offer`, grouped by stage, showing staleness
   warnings and stage-advance controls.
5. **Archive** — archived, rejected, and expired jobs with their reasons and a
   `Restore` button. Doubles as the review surface for tuning negative keywords.
6. **Deadlines** — the verified recurring cycles.

## Server

FastAPI served by uvicorn, started with `jobs serve`. Templates ship inside the
package. Rendering is server-side Jinja2 with plain HTML forms; there is no
JavaScript and no build step.

Routes:

| Method | Path | Effect |
|---|---|---|
| GET | `/` | Overview |
| GET | `/tab/{name}` | Tab body (HTMX partial) |
| POST | `/jobs` | Add a job from a URL |
| POST | `/jobs/{id}/stage` | Set stage |
| POST | `/jobs/{id}/note` | Replace the note |
| POST | `/jobs/{id}/priority` | Set priority |
| POST | `/jobs/{id}/archive` | Archive with a reason |
| POST | `/jobs/{id}/restore` | Un-archive |
| POST | `/actions/search` | Run discovery across enabled sources |
| POST | `/actions/refresh` | Re-check every live job's URL |

Mutating routes are POST-redirect-GET: they perform the change and redirect to the
tab named in the form's `return_to` field, which is validated against the known tab
list so it cannot become an open redirect. This was chosen over HTMX fragments
because vendoring a JS file to avoid a sub-100ms local page reload is not a trade
worth making on a single-user local tool.

## Link checking

`Update current jobs` issues a request per non-archived job and records
`link_status` and `last_checked`.

A dead link **flags, never auto-archives**. A posting can 404 because it was filled,
because the ATS moved it, or because of a transient error, and silently discarding
a job the operator was tracking is worse than showing a stale row. Requests are
rate-limited and run in a background task so the page stays responsive.

## Search

`Search now` calls the source layer. Until the ATS polling plan lands, only manual
entry exists, and the tab states that plainly rather than showing an empty result
that implies the market was searched and found bare.

## CLI parity

Every UI action has a CLI equivalent and vice versa; both call the same store
functions, so there is one implementation and one set of tests.

| UI action | CLI |
|---|---|
| — | `jobs serve` |
| Update current jobs | `jobs refresh` |
| Set priority | `jobs priority <id> <1-5>` |
| Edit note | `jobs note <id> "<text>"` (now sets the field and logs an event) |
| Archive | `jobs archive <id> --reason "..."` (alias: `dismiss`) |
| Restore | `jobs restore <id>` |

## Testing

FastAPI's `TestClient` against a temporary database, no network. Link checking and
job fetching take an injected HTTP client via the `Deps` bundle. Route tests assert
both the storage effect and the redirect target.
